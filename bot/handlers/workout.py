"""Логирование тренировок: голос/текст во время тренировки, завершение."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.database.crud import (
    add_workout_sets,
    create_workout,
    get_or_create_user,
    get_workout_summary,
)
from bot.keyboards.menu import confirm_exercise, exercise_alternatives, workout_menu
from bot.services.exercises import load_exercises
from bot.services.nlp import match_exercise, parse_workout_message
from bot.config import settings
from bot.services.voice import transcribe_voice

router = Router()
logger = logging.getLogger(__name__)


class WorkoutStates(StatesGroup):
    active = State()
    waiting_exercise = State()


async def _exercises_db_with_ids() -> list[dict]:
    """База упражнений из JSON с добавленным id (индекс) для match_exercise."""
    raw = await load_exercises()
    return [{"id": i, "name": ex.get("name", ""), "synonyms": ex.get("synonyms") or []} for i, ex in enumerate(raw)]


async def _process_parsed_workout(
    message: Message,
    state: FSMContext,
    parsed: dict,
    workout_id: int,
    user_id: int,
) -> None:
    """Общая логика после парсинга: уточнение, сопоставление, сохранение, подтверждение."""
    if parsed["clarification_needed"] and parsed.get("clarification_question"):
        await message.answer(parsed["clarification_question"])
        return

    exercises_db = await _exercises_db_with_ids()
    workout_data = await state.get_data()
    current_workout = workout_data.get("workout") or {}

    for exercise_data in parsed["exercises"]:
        name = exercise_data.get("name") or "Упражнение"
        sets_list = exercise_data.get("sets") or []

        matched = await match_exercise(name, exercises_db)
        if matched.get("confidence", 0) < 0.7:
            alts = matched.get("alternatives") or []
            await message.answer(
                "🤔 Не уверен, что правильно понял упражнение.\nТы имел в виду:",
                reply_markup=exercise_alternatives(alts),
            )
            return

        # Формат для add_workout_sets: список {exercise_name, reps, weight_kg}
        flat_sets = []
        for s in sets_list:
            w = s.get("weight")
            if w is not None and not isinstance(w, (int, float)):
                try:
                    w = float(w)
                except (TypeError, ValueError):
                    w = None
            flat_sets.append({
                "exercise_name": matched.get("name") or name,
                "reps": s.get("reps"),
                "weight_kg": w,
            })

        await add_workout_sets(workout_id, flat_sets, user_id=user_id)

        volume = 0.0
        for s in sets_list:
            r, w = s.get("reps"), s.get("weight")
            if r is not None and w is not None:
                try:
                    volume += float(w) * int(r)
                except (TypeError, ValueError):
                    pass

        lines = []
        for s in sets_list:
            w, r = s.get("weight"), s.get("reps")
            if w is not None and r is not None:
                lines.append(f"• {w} кг × {r}")
        text = (
            f"✅ Записал:\n\n<b>{matched.get('name') or name}</b>\n"
            + "\n".join(lines)
            + f"\n\n📊 Объём: {volume:.1f} кг"
        )
        await message.answer(
            text,
            reply_markup=confirm_exercise(matched.get("name") or name, len(sets_list), volume),
            parse_mode="HTML",
        )


# ----- Выбор программы (callback) -----


@router.callback_query(F.data.startswith("program:"))
async def on_program_selected(callback: CallbackQuery, state: FSMContext):
    """После выбора программы или freestyle — создаём тренировку и показываем меню."""
    value = callback.data.split(":", 1)[1]
    program_id = None
    if value != "freestyle" and value != "new":
        try:
            program_id = int(value)
        except ValueError:
            program_id = None

    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    workout = await create_workout(user.telegram_id, program_id=program_id)

    await state.update_data(workout={"id": workout.id, "date": str(workout.date)})
    await state.set_state(WorkoutStates.active)
    await callback.message.edit_text("Тренировка начата. Говори или пиши упражнения и подходы.")
    await callback.message.answer("Меню тренировки:", reply_markup=workout_menu())
    await callback.answer()


# ----- Голос во время тренировки -----


@router.message(F.voice, WorkoutStates.active)
async def handle_voice_during_workout(message: Message, state: FSMContext):
    """
    Обработка голосового сообщения во время тренировки
    1. Распознать через Whisper
    2. Парсить через GPT
    3. Сопоставить с упражнениями
    4. Сохранить в БД
    5. Показать подтверждение
    """
    await message.answer("🎤 Слушаю...")

    text = await transcribe_voice(message.voice.file_id, settings.telegram_bot_token)
    if not text:
        await message.answer("❌ Не смог распознать. Попробуй ещё раз или напиши текстом.")
        return
    await message.answer(f"📝 Распознал: {text}")

    workout_data = await state.get_data()
    workout = workout_data.get("workout") or {}
    workout_id = workout.get("id")
    if not workout_id:
        await message.answer("Тренировка не найдена. Начни заново: 🏋️ Начать тренировку")
        return

    parsed = await parse_workout_message(
        text=text,
        user_id=message.from_user.id,
        current_workout=workout,
        exercises_db=await _exercises_db_with_ids(),
    )

    await _process_parsed_workout(
        message, state, parsed, workout_id, message.from_user.id
    )


# ----- Завершение и отмена (регистрировать до общего F.text) -----


@router.message(F.text == "✅ Завершить тренировку", WorkoutStates.active)
async def finish_workout(message: Message, state: FSMContext):
    """
    Завершение тренировки
    - Считает итоговые объёмы
    - Показывает итоги
    """
    workout_data = await state.get_data()
    workout = workout_data.get("workout") or {}
    workout_id = workout.get("id")

    if not workout_id:
        await message.answer("Тренировка не найдена.")
        await state.clear()
        return

    summary = await get_workout_summary(workout_id)

    date_str = summary["date"].strftime("%d.%m.%Y") if summary.get("date") else "—"
    result = f"""
🏋️ <b>Тренировка завершена!</b>

📅 {date_str}
🔹 Упражнений: {summary['exercises_count']}
🔹 Подходов: {summary['sets_count']}
🔹 Общая нагрузка: {summary['total_volume_kg']:.0f} кг

💪 Отличная работа!
"""
    await message.answer(result, parse_mode="HTML")
    await state.clear()


# ----- Отмена тренировки -----


@router.message(F.text == "🚫 Отменить", WorkoutStates.active)
async def cancel_workout(message: Message, state: FSMContext):
    """Отмена текущей тренировки (без удаления из БД)."""
    await message.answer("Тренировка отменена.")
    await state.clear()


# ----- Текст во время тренировки -----


@router.message(F.text, WorkoutStates.active)
async def handle_text_during_workout(message: Message, state: FSMContext):
    """Обработка текстового сообщения во время тренировки (без Whisper)."""
    workout_data = await state.get_data()
    workout = workout_data.get("workout") or {}
    workout_id = workout.get("id")
    if not workout_id:
        await message.answer("Тренировка не найдена. Начни заново: 🏋️ Начать тренировку")
        return

    parsed = await parse_workout_message(
        text=message.text or "",
        user_id=message.from_user.id,
        current_workout=workout,
        exercises_db=await _exercises_db_with_ids(),
    )

    await _process_parsed_workout(
        message, state, parsed, workout_id, message.from_user.id
    )
