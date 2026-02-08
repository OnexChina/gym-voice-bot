"""Логирование тренировок: голос/текст во время тренировки, завершение."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.database.engine import get_session
from bot.database.crud import (
    add_workout_sets,
    check_and_save_records,
    create_workout,
    delete_last_workout_exercise,
    delete_workout,
    get_or_create_user,
    get_workout_summary,
)
from bot.keyboards.menu import (
    confirm_exercise,
    exercise_alternatives,
    main_menu,
    workout_menu,
    workout_inline_buttons,
)
from bot.services.analytics import format_workout_summary
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

        async with get_session() as session:
            await add_workout_sets(session, workout_id, flat_sets, user_id=user_id)

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

    async with get_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        workout = await create_workout(session, user.telegram_id, program_id=program_id)

    await state.update_data(workout={"id": workout.id, "date": str(workout.date)})
    await state.set_state(WorkoutStates.active)
    await callback.message.edit_text("Тренировка начата. Говори или пиши упражнения и подходы.")
    await callback.message.answer("Меню тренировки:", reply_markup=workout_menu())
    await callback.answer()


# ----- Голос во время тренировки (регистрировать раньше F.voice без состояния) -----


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


# ----- Голос вне тренировки (предложить начать) -----


@router.message(F.voice)
async def handle_voice_no_workout(message: Message, state: FSMContext):
    """Голосовое сообщение вне тренировки: предложить начать тренировку."""
    await state.update_data(pending_voice=message.voice.file_id)
    await message.answer(
        "🎤 Получил голосовое сообщение!\n\nНачнём тренировку?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data="start_workout_from_voice")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="voice_cancel")],
        ]),
    )


@router.callback_query(F.data == "start_workout_from_voice")
async def on_start_workout_from_voice(callback: CallbackQuery, state: FSMContext):
    """Пользователь нажал «Да»: создаём тренировку и обрабатываем сохранённое голосовое."""
    data = await state.get_data()
    file_id = data.get("pending_voice")
    await state.update_data(pending_voice=None)

    if not file_id:
        await callback.message.edit_text("Голосовое сообщение уже обработано или устарело. Начни тренировку через меню.")
        await callback.answer()
        return

    async with get_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        workout = await create_workout(session, user.telegram_id, program_id=None)

    await state.update_data(workout={"id": workout.id, "date": str(workout.date)})
    await state.set_state(WorkoutStates.active)
    await callback.message.edit_text("Тренировка начата. Обрабатываю твоё голосовое...")
    await callback.message.answer("Меню тренировки:", reply_markup=workout_menu())
    await callback.answer()

    await callback.message.answer("🎤 Слушаю...")
    text = await transcribe_voice(file_id, settings.telegram_bot_token)
    if not text:
        await callback.message.answer("❌ Не смог распознать. Попробуй ещё раз или напиши текстом.")
        return
    await callback.message.answer(f"📝 Распознал: {text}")

    workout_data = await state.get_data()
    current_workout = workout_data.get("workout") or {}
    parsed = await parse_workout_message(
        text=text,
        user_id=callback.from_user.id,
        current_workout=current_workout,
        exercises_db=await _exercises_db_with_ids(),
    )
    await _process_parsed_workout(
        callback.message, state, parsed, workout.id, callback.from_user.id
    )


@router.callback_query(F.data == "voice_cancel")
async def on_voice_cancel(callback: CallbackQuery, state: FSMContext):
    """Пользователь нажал «Нет» — не начинаем тренировку."""
    await state.update_data(pending_voice=None)
    await callback.message.edit_text("Ок, не начинаем. Когда захочешь — нажми «🏋️ Начать тренировку» в меню.")
    await callback.answer()


# ----- Подтверждение / удаление / исправление записанного упражнения -----


@router.callback_query(F.data == "confirm_exercise")
async def on_confirm_exercise(callback: CallbackQuery):
    """Пользователь подтвердил — закрыть сообщение."""
    await callback.message.delete()
    await callback.answer("✅ Записано!")


@router.callback_query(F.data == "delete_last_exercise")
async def on_delete_last_exercise(callback: CallbackQuery, state: FSMContext):
    """Удалить последнее добавленное упражнение из тренировки."""
    workout_data = await state.get_data()
    workout_id = workout_data.get("workout", {}).get("id")
    if not workout_id:
        await callback.answer("❌ Тренировка не найдена", show_alert=True)
        return
    async with get_session() as session:
        deleted = await delete_last_workout_exercise(session, workout_id)
    if deleted:
        await callback.message.edit_text("❌ Последнее упражнение удалено.")
        await callback.answer("Удалено!")
    else:
        await callback.answer("Нечего удалять", show_alert=True)


@router.callback_query(F.data == "edit_last_exercise")
async def on_edit_last_exercise(callback: CallbackQuery, state: FSMContext):
    """Исправить: удалить последнее упражнение и предложить записать заново."""
    workout_data = await state.get_data()
    workout_id = workout_data.get("workout", {}).get("id")
    if not workout_id:
        await callback.answer("❌ Тренировка не найдена", show_alert=True)
        return
    async with get_session() as session:
        deleted = await delete_last_workout_exercise(session, workout_id)
    if deleted:
        await callback.message.edit_text("✏️ Последнее упражнение удалено. Запиши его заново голосом или текстом.")
        await callback.answer("Удалено — запиши заново!")
    else:
        await callback.answer("Нечего удалять", show_alert=True)


# ----- Завершение и отмена (регистрировать до общего F.text) -----


async def _do_finish_workout(workout_id: int) -> tuple[str, bool]:
    """Считает рекорды, форматирует итоги. Возвращает (текст_итогов, успех)."""
    if not workout_id:
        return "Тренировка не найдена.", False
    new_records = await check_and_save_records(workout_id)
    summary = await format_workout_summary(workout_id, new_records=new_records)
    return summary, True


@router.callback_query(F.data == "finish_workout")
async def finish_workout_handler(callback: CallbackQuery, state: FSMContext):
    """Завершение тренировки по inline-кнопке."""
    workout_data = await state.get_data()
    workout_id = workout_data.get("workout", {}).get("id")
    summary_text, ok = await _do_finish_workout(workout_id)
    if not ok:
        await callback.answer("Тренировка не найдена", show_alert=True)
        return
    await callback.message.answer(summary_text, parse_mode="HTML", reply_markup=main_menu())
    await state.clear()
    await callback.answer("✅ Тренировка завершена!")


@router.callback_query(F.data == "cancel_workout")
async def cancel_workout_handler(callback: CallbackQuery, state: FSMContext):
    """Отмена тренировки по inline-кнопке (удаление из БД)."""
    workout_data = await state.get_data()
    workout_id = workout_data.get("workout", {}).get("id")
    if workout_id:
        await delete_workout(workout_id)
    await callback.message.answer("❌ Тренировка отменена", reply_markup=main_menu())
    await state.clear()
    await callback.answer("Отменено")


@router.callback_query(F.data == "back_to_workout")
async def back_to_workout_handler(callback: CallbackQuery):
    """Пользователь выбрал «Назад к тренировке»."""
    await callback.message.edit_text("Продолжай записывать упражнения голосом или текстом.")
    await callback.answer("Продолжаем тренировку")


@router.message(F.text == "✅ Завершить тренировку", WorkoutStates.active)
async def finish_workout(message: Message, state: FSMContext):
    """Завершение тренировки по кнопке Reply-клавиатуры."""
    workout_data = await state.get_data()
    workout = workout_data.get("workout") or {}
    workout_id = workout.get("id")
    summary_text, ok = await _do_finish_workout(workout_id)
    if not ok:
        await message.answer(summary_text)
        await state.clear()
        return
    await message.answer(summary_text, parse_mode="HTML", reply_markup=main_menu())
    await state.clear()


# ----- Отмена тренировки -----


@router.message(F.text == "❌ Отменить тренировку", WorkoutStates.active)
async def cancel_workout(message: Message, state: FSMContext):
    """Отмена тренировки по кнопке Reply-клавиатуры (удаление из БД)."""
    workout_data = await state.get_data()
    workout_id = workout_data.get("workout", {}).get("id")
    if workout_id:
        await delete_workout(workout_id)
    await message.answer("❌ Тренировка отменена", reply_markup=main_menu())
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
