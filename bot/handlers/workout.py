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
    get_last_workout_exercise,
    get_or_create_user,
    get_workout_by_id,
    get_workout_summary,
)
from bot.keyboards.menu import add_exercise_confirm, confirm_exercise, exercise_alternatives, main_menu, workout_menu
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
    waiting_exercise_name = State()  # Для ручного ввода названия упражнения
    waiting_comment = State()  # Для ввода комментария


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

    if not parsed.get("exercises") or len(parsed["exercises"]) == 0:
        await message.answer(
            "❌ Не смог разобрать упражнение из сообщения.\n\n"
            "Попробуй сказать проще, например:\n"
            "• «Бег на дорожке 30 минут»\n"
            "• «Жим лёжа 10 на 80»\n"
            "• «Плавание 20 минут»"
        )
        return

    for exercise_data in parsed["exercises"]:
        name = exercise_data.get("name") or "Упражнение"
        sets_list = exercise_data.get("sets") or []

        matched = await match_exercise(name, exercises_db)
        confidence = matched.get("confidence", 0)
        # Точное или > 90% — записываем без переспроса. 60–90% — варианты. < 60% — предложить добавить.
        if confidence >= 0.9:
            pass  # сразу записываем ниже
        elif confidence >= 0.6:
            alts = matched.get("alternatives") or []
            data = await state.get_data()
            pending_clar = data.get("pending_clarification") or {}
            if pending_clar.get("attempts", 0) >= 2:
                await state.update_data(pending_clarification=None)
                await message.answer(
                    f"Добавить «{name}» как новое упражнение?",
                    reply_markup=add_exercise_confirm(),
                )
                await state.update_data(pending_unknown_exercise={"name": name, "sets_list": sets_list})
                return
            if alts:
                await message.answer(
                    "🤔 Не уверен, что правильно понял упражнение.\nТы имел в виду:",
                    reply_markup=exercise_alternatives(alts),
                )
                await state.update_data(
                    pending_clarification={"name": name, "sets_list": sets_list, "attempts": 1}
                )
                return
            await message.answer(
                f"Добавить «{name}» как новое упражнение?",
                reply_markup=add_exercise_confirm(),
            )
            await state.update_data(pending_unknown_exercise={"name": name, "sets_list": sets_list})
            return
        else:
            # < 60% — не найдено, предложить добавить
            await message.answer(
                f"Добавить «{name}» как новое упражнение?",
                reply_markup=add_exercise_confirm(),
            )
            await state.update_data(pending_unknown_exercise={"name": name, "sets_list": sets_list})
            return

        # Формат для add_workout_sets: список {exercise_name, reps, weight_kg}
        flat_sets = []
        is_cardio = False
        for s in sets_list:
            w = s.get("weight")
            if w is not None and not isinstance(w, (int, float)):
                try:
                    w = float(w)
                except (TypeError, ValueError):
                    w = None
            # Проверка на кардио: если weight=null и есть reps, возможно это время
            if w is None and s.get("reps") is not None:
                comment = s.get("comment") or ""
                if "минут" in comment.lower() or "minute" in comment.lower():
                    is_cardio = True
            
            flat_sets.append({
                "exercise_name": matched.get("name") or name,
                "reps": s.get("reps"),
                "weight_kg": w,
            })

        async with get_session() as session:
            await add_workout_sets(session, workout_id, flat_sets, user_id=user_id)

        # Форматирование вывода
        if is_cardio or (len(sets_list) == 1 and sets_list[0].get("weight") is None):
            # Кардио формат: время вместо веса
            lines = []
            for s in sets_list:
                r = s.get("reps")
                comment = s.get("comment") or ""
                if r is not None:
                    if "минут" in comment.lower() or "minute" in comment.lower():
                        lines.append(f"• {r} минут")
                    else:
                        lines.append(f"• {r} мин" if r else "• —")
                else:
                    lines.append("• —")
            text = (
                f"✅ Записал:\n\n<b>{matched.get('name') or name}</b>\n"
                + "\n".join(lines)
            )
        else:
            # Силовой формат: вес × повторения
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
        await state.update_data(
            last_parsed_data=parsed,
            last_exercise_name=matched.get("name") or name,
            last_sets_data=sets_list,
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


# ----- Голос во время тренировки -----


@router.message(F.voice, WorkoutStates.active)
async def handle_voice_during_workout(message: Message, state: FSMContext):
    """Обработка голосового сообщения во время тренировки."""
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


# ----- Подтверждение / удаление записанного упражнения -----


@router.callback_query(F.data == "confirm_exercise")
async def on_confirm_exercise(callback: CallbackQuery):
    """Пользователь подтвердил — закрыть сообщение."""
    await callback.message.delete()
    await callback.answer("✅ Записано!")


@router.callback_query(F.data == "add_exercise_yes")
async def on_add_exercise_yes(callback: CallbackQuery, state: FSMContext):
    """Пользователь согласился добавить неизвестное упражнение в базу и записать подходы."""
    data = await state.get_data()
    workout_id = (data.get("workout") or {}).get("id")
    pending = data.get("pending_unknown_exercise") or {}
    name = (pending.get("name") or "").strip() or "Упражнение"
    sets_list = pending.get("sets_list") or []
    await state.update_data(pending_unknown_exercise=None)
    if not workout_id or not sets_list:
        await callback.message.edit_text("Данные устарели. Напиши упражнение и подходы ещё раз.")
        await callback.answer()
        return
    flat_sets = []
    for s in sets_list:
        w = s.get("weight") or s.get("weight_kg")
        if w is not None and not isinstance(w, (int, float)):
            try:
                w = float(w)
            except (TypeError, ValueError):
                w = None
        flat_sets.append({
            "exercise_name": name,
            "reps": s.get("reps"),
            "weight_kg": w,
        })
    async with get_session() as session:
        await add_workout_sets(session, workout_id, flat_sets, user_id=callback.from_user.id)
    volume = 0.0
    for s in sets_list:
        r, w = s.get("reps"), s.get("weight") or s.get("weight_kg")
        if r is not None and w is not None:
            try:
                volume += float(w) * int(r)
            except (TypeError, ValueError):
                pass
    lines = [f"• {s.get('weight', s.get('weight_kg', '—'))} кг × {s.get('reps', '—')}" for s in sets_list]
    text = (
        f"✅ Добавил упражнение «{name}» в базу и записал подходы:\n\n"
        + "\n".join(lines)
        + (f"\n\n📊 Объём: {volume:.1f} кг" if volume else "")
    )
    await callback.message.edit_text(text, reply_markup=confirm_exercise(name, len(sets_list), volume), parse_mode="HTML")
    await callback.answer("Добавлено!")


@router.callback_query(F.data == "add_exercise_no")
async def on_add_exercise_no(callback: CallbackQuery, state: FSMContext):
    """Пользователь отказался добавлять упражнение — просим уточнить название."""
    await state.update_data(pending_unknown_exercise=None)
    await callback.message.edit_text("Уточни название упражнения и напиши ещё раз.")
    await callback.answer()


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
    """Исправить название упражнения: удалить последнее и попросить ввести название вручную."""
    workout_data = await state.get_data()
    workout_id = workout_data.get("workout", {}).get("id")
    if not workout_id:
        await callback.answer("❌ Тренировка не найдена", show_alert=True)
        return
    
    # Сохранить данные последнего упражнения для восстановления подходов
    async with get_session() as session:
        last_we = await get_last_workout_exercise(session, workout_id)
        if not last_we:
            await callback.answer("Нечего исправлять", show_alert=True)
            return
        
        # Сохранить подходы в state для восстановления
        sets_data = []
        for s in last_we.sets:
            sets_data.append({
                "reps": s.reps,
                "weight_kg": float(s.weight_kg) if s.weight_kg else None,
            })
        
        # Удалить упражнение
        await delete_last_workout_exercise(session, workout_id)
    
    await state.update_data(
        pending_sets=sets_data,
        pending_workout_id=workout_id,
    )
    await state.set_state(WorkoutStates.waiting_exercise_name)
    
    await callback.message.edit_text(
        "✏️ Последнее упражнение удалено.\n\n"
        "Напиши или скажи правильное название упражнения:"
    )
    await callback.answer()


@router.callback_query(F.data == "add_comment")
async def on_add_comment(callback: CallbackQuery, state: FSMContext):
    """Добавить комментарий к последнему упражнению."""
    workout_data = await state.get_data()
    workout_id = workout_data.get("workout", {}).get("id")
    if not workout_id:
        await callback.answer("❌ Тренировка не найдена", show_alert=True)
        return
    
    async with get_session() as session:
        last_we = await get_last_workout_exercise(session, workout_id)
        if not last_we:
            await callback.answer("Нечего комментировать", show_alert=True)
            return
        
        await state.update_data(pending_comment_we_id=last_we.id)
    
    await state.set_state(WorkoutStates.waiting_comment)
    await callback.message.answer(
        "💬 Напиши или скажи комментарий к упражнению:\n\n"
        "Например: «Тяжело», «Легко», «Хорошо пошло»"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("exercise:"))
async def on_exercise_selected(callback: CallbackQuery, state: FSMContext):
    """Пользователь выбрал упражнение из альтернатив или «Создать новое»."""
    value = callback.data.split(":", 1)[1]
    if value == "new":
        # «Создать новое упражнение» — добавляем текущее название и подходы как новое упражнение
        data = await state.get_data()
        pending_clar = data.get("pending_clarification") or {}
        name = (pending_clar.get("name") or "").strip() or "Упражнение"
        sets_list = pending_clar.get("sets_list") or []
        workout_id = (data.get("workout") or {}).get("id")
        await state.update_data(pending_clarification=None)
        if not workout_id or not sets_list:
            await callback.message.edit_text("Данные устарели. Напиши упражнение и подходы ещё раз.")
            await callback.answer()
            return
        flat_sets = []
        for s in sets_list:
            w = s.get("weight") or s.get("weight_kg")
            if w is not None and not isinstance(w, (int, float)):
                try:
                    w = float(w)
                except (TypeError, ValueError):
                    w = None
            flat_sets.append({"exercise_name": name, "reps": s.get("reps"), "weight_kg": w})
        async with get_session() as session:
            await add_workout_sets(session, workout_id, flat_sets, user_id=callback.from_user.id)
        volume = 0.0
        for s in sets_list:
            r, w = s.get("reps"), s.get("weight") or s.get("weight_kg")
            if r is not None and w is not None:
                try:
                    volume += float(w) * int(r)
                except (TypeError, ValueError):
                    pass
        lines = [f"• {s.get('weight', s.get('weight_kg', '—'))} кг × {s.get('reps', '—')}" for s in sets_list]
        text = f"✅ Упражнение добавлено и подход записан!\n\n<b>{name}</b>\n" + "\n".join(lines) + (f"\n\n📊 Объём: {volume:.1f} кг" if volume else "")
        await callback.message.edit_text(text, reply_markup=confirm_exercise(name, len(sets_list), volume), parse_mode="HTML")
        await callback.answer("Добавлено!")
        return

    await state.update_data(pending_clarification=None)
    workout_data = await state.get_data()
    workout_id = workout_data.get("workout", {}).get("id")
    if not workout_id:
        await callback.answer("Тренировка не найдена", show_alert=True)
        return
    
    exercises_db = await _exercises_db_with_ids()
    try:
        idx = int(value)
    except ValueError:
        await callback.answer()
        return
    
    # Найти упражнение по индексу
    if 0 <= idx < len(exercises_db):
        selected_ex = exercises_db[idx]
        exercise_name = selected_ex.get("name", "")
        
        # Получить сохранённые данные из state (если есть)
        parsed_data = workout_data.get("last_parsed_data")
        if parsed_data and parsed_data.get("exercises"):
            ex_data = parsed_data["exercises"][0]
            sets_list = ex_data.get("sets") or []
            
            flat_sets = []
            for s in sets_list:
                w = s.get("weight")
                if w is not None and not isinstance(w, (int, float)):
                    try:
                        w = float(w)
                    except (TypeError, ValueError):
                        w = None
                flat_sets.append({
                    "exercise_name": exercise_name,
                    "reps": s.get("reps"),
                    "weight_kg": w,
                })
            
            async with get_session() as session:
                await add_workout_sets(session, workout_id, flat_sets, user_id=callback.from_user.id)
            
            volume = 0.0
            for s in sets_list:
                r, w = s.get("reps"), s.get("weight")
                if r is not None and w is not None:
                    try:
                        volume += float(w) * int(r)
                    except (TypeError, ValueError):
                        pass
            
            lines = [f"• {s.get('weight', '—')} кг × {s.get('reps', '—')}" for s in sets_list]
            text = f"✅ Записал:\n\n<b>{exercise_name}</b>\n" + "\n".join(lines) + f"\n\n📊 Объём: {volume:.1f} кг"
            await callback.message.edit_text(
                text,
                reply_markup=confirm_exercise(exercise_name, len(sets_list), volume),
                parse_mode="HTML",
            )
            await state.update_data(last_parsed_data=None)
            await callback.answer("Записано!")
        else:
            await callback.answer("Данные не найдены", show_alert=True)
    else:
        await callback.answer("Ошибка выбора", show_alert=True)


# ----- Завершение и отмена -----


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


@router.message(
    F.text.in_(["✅ Завершить тренировку", "🏁 Закончить тренировку"]),
    WorkoutStates.active,
)
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


@router.message(F.text == "❌ Отменить тренировку", WorkoutStates.active)
async def cancel_workout(message: Message, state: FSMContext):
    """Отмена тренировки по кнопке Reply-клавиатуры (удаление из БД)."""
    workout_data = await state.get_data()
    workout_id = workout_data.get("workout", {}).get("id")
    if workout_id:
        await delete_workout(workout_id)
    await message.answer("❌ Тренировка отменена", reply_markup=main_menu())
    await state.clear()


@router.message(F.text == "📊 Текущая тренировка")
async def show_current_workout_summary(message: Message, state: FSMContext):
    """Показать сводку текущей активной тренировки или сообщение об отсутствии."""
    data = await state.get_data()
    workout_id = (data.get("workout") or {}).get("id")
    if not workout_id:
        await message.answer("Нет активной тренировки. Нажми Начать тренировку")
        return
    workout = await get_workout_by_id(workout_id)
    if not workout:
        await message.answer("Тренировка не найдена.")
        return
    exercises = sorted(workout.workout_exercises, key=lambda we: we.order_num)
    lines = []
    for we in exercises:
        name = we.exercise.name if we.exercise else "Упражнение"
        lines.append(f"🏋️ {name}")
        for s in sorted(we.sets, key=lambda x: x.set_number):
            if s.weight_kg is not None and s.reps is not None:
                lines.append(f"  Подход {s.set_number}: {s.weight_kg} кг × {s.reps} повт")
            elif s.reps is not None:
                lines.append(f"  Подход {s.set_number}: {s.reps} мин")
            else:
                lines.append(f"  Подход {s.set_number}: —")
        lines.append("")
    total_ex = len(exercises)
    total_sets = sum(len(we.sets) for we in exercises)
    lines.append(f"Итого: {total_ex} упражнений, {total_sets} подходов")
    await message.answer("\n".join(lines).strip())


# ----- Текст во время тренировки -----


# Слова/фразы, которые считаем отказом от предложенных альтернатив ("нет, не то")
CLARIFICATION_REFUSAL = frozenset({"нет", "не то", "no", "не", "другое", "ничего из этого"})


@router.message(F.text, WorkoutStates.active)
async def handle_text_during_workout(message: Message, state: FSMContext):
    """Обработка текстового сообщения во время тренировки (без Whisper)."""
    workout_data = await state.get_data()
    workout = workout_data.get("workout") or {}
    workout_id = workout.get("id")
    if not workout_id:
        await message.answer("Тренировка не найдена. Начни заново: 🏋️ Начать тренировку")
        return

    # Проверка: ждём повторный ввод названия после "нет" (макс 2 попытки)
    pending_clar = workout_data.get("pending_clarification") or {}
    if pending_clar:
        text_lower = (message.text or "").strip().lower()
        if pending_clar.get("attempts") == 1 and text_lower in CLARIFICATION_REFUSAL:
            await state.update_data(
                pending_clarification={**pending_clar, "attempts": 2}
            )
            await message.answer(
                "Уточни название упражнения (осталась 1 попытка). Напиши его ещё раз — после этого мы либо найдём его, либо предложим добавить как новое.",
                reply_markup=workout_menu(),
            )
            return
        if pending_clar.get("attempts") == 2:
            # Второй ввод: используем сообщение как новое название, подходы берём из pending_clarification
            new_name = (message.text or "").strip() or "Упражнение"
            sets_list = pending_clar.get("sets_list") or []
            await state.update_data(pending_clarification=None)
            if not sets_list:
                await message.answer("Данные устарели. Напиши упражнение и подходы ещё раз.")
                return
            exercises_db = await _exercises_db_with_ids()
            matched = await match_exercise(new_name, exercises_db)
            if matched.get("confidence", 0) >= 0.9:
                flat_sets = []
                for s in sets_list:
                    w = s.get("weight") or s.get("weight_kg")
                    if w is not None and not isinstance(w, (int, float)):
                        try:
                            w = float(w)
                        except (TypeError, ValueError):
                            w = None
                    flat_sets.append({
                        "exercise_name": matched.get("name") or new_name,
                        "reps": s.get("reps"),
                        "weight_kg": w,
                    })
                async with get_session() as session:
                    await add_workout_sets(session, workout_id, flat_sets, user_id=message.from_user.id)
                volume = 0.0
                for s in sets_list:
                    r, w = s.get("reps"), s.get("weight") or s.get("weight_kg")
                    if r is not None and w is not None:
                        try:
                            volume += float(w) * int(r)
                        except (TypeError, ValueError):
                            pass
                lines = [f"• {s.get('weight', s.get('weight_kg', '—'))} кг × {s.get('reps', '—')}" for s in sets_list]
                text = f"✅ Записал:\n\n<b>{matched.get('name') or new_name}</b>\n" + "\n".join(lines)
                if volume:
                    text += f"\n\n📊 Объём: {volume:.1f} кг"
                await message.answer(
                    text,
                    reply_markup=confirm_exercise(matched.get("name") or new_name, len(sets_list), volume),
                    parse_mode="HTML",
                )
                return
            # Снова не нашли — предлагаем добавить как новое
            await message.answer(
                f"Добавить «{new_name}» как новое упражнение?",
                reply_markup=add_exercise_confirm(),
            )
            await state.update_data(pending_unknown_exercise={"name": new_name, "sets_list": sets_list})
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


# ----- Ручной ввод названия упражнения (после "Исправить") -----


@router.message(F.text, WorkoutStates.waiting_exercise_name)
@router.message(F.voice, WorkoutStates.waiting_exercise_name)
async def handle_manual_exercise_name(message: Message, state: FSMContext):
    """Обработка ручного ввода названия упражнения (текст или голос)."""
    text = message.text or ""
    if message.voice:
        text = await transcribe_voice(message.voice.file_id, settings.telegram_bot_token)
        if not text:
            await message.answer("❌ Не смог распознать. Попробуй ещё раз или напиши текстом.")
            return
        await message.answer(f"📝 Распознал: {text}")
    
    if not text or not text.strip():
        await message.answer("Название не может быть пустым. Введи название упражнения:")
        return
    
    workout_data = await state.get_data()
    workout_id = workout_data.get("pending_workout_id")
    sets_data = workout_data.get("pending_sets", [])
    
    if not workout_id:
        await message.answer("Ошибка: тренировка не найдена.")
        await state.clear()
        return
    
    # Найти упражнение по названию
    exercises_db = await _exercises_db_with_ids()
    matched = await match_exercise(text.strip(), exercises_db)
    exercise_name = matched.get("name") or text.strip()
    
    # Восстановить подходы с новым названием
    flat_sets = []
    for s in sets_data:
        flat_sets.append({
            "exercise_name": exercise_name,
            "reps": s.get("reps"),
            "weight_kg": s.get("weight_kg"),
        })
    
    async with get_session() as session:
        await add_workout_sets(session, workout_id, flat_sets, user_id=message.from_user.id)
    
    volume = 0.0
    for s in sets_data:
        r, w = s.get("reps"), s.get("weight_kg")
        if r is not None and w is not None:
            try:
                volume += float(w) * int(r)
            except (TypeError, ValueError):
                pass
    
    lines = [f"• {s.get('weight_kg', '—')} кг × {s.get('reps', '—')}" for s in sets_data]
    text_msg = (
        f"✅ Исправлено и записано:\n\n<b>{exercise_name}</b>\n"
        + "\n".join(lines)
        + f"\n\n📊 Объём: {volume:.1f} кг"
    )
    await message.answer(
        text_msg,
        reply_markup=confirm_exercise(exercise_name, len(sets_data), volume),
        parse_mode="HTML",
    )
    await state.set_state(WorkoutStates.active)
    await state.update_data(
        pending_sets=None,
        pending_workout_id=None,
        last_exercise_name=exercise_name,
        last_sets_data=sets_data,
    )


# ----- Ввод комментария -----


@router.message(F.text, WorkoutStates.waiting_comment)
@router.message(F.voice, WorkoutStates.waiting_comment)
async def handle_comment_input(message: Message, state: FSMContext):
    """Обработка ввода комментария к упражнению (текст или голос)."""
    text = message.text or ""
    if message.voice:
        text = await transcribe_voice(message.voice.file_id, settings.telegram_bot_token)
        if not text:
            await message.answer("❌ Не смог распознать. Попробуй ещё раз или напиши текстом.")
            return
        await message.answer(f"📝 Распознал: {text}")
    
    if not text or not text.strip():
        await message.answer("Комментарий не может быть пустым. Введи комментарий:")
        return
    
    workout_data = await state.get_data()
    we_id = workout_data.get("pending_comment_we_id")
    
    if not we_id:
        await message.answer("Ошибка: упражнение не найдено.")
        await state.set_state(WorkoutStates.active)
        return
    
    from bot.database.crud import add_exercise_comment
    await add_exercise_comment(we_id, text.strip())
    
    await message.answer(f"✅ Комментарий добавлен: <i>{text.strip()}</i>", parse_mode="HTML")
    await state.set_state(WorkoutStates.active)
    await state.update_data(pending_comment_we_id=None)
