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
    get_program_by_id,
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


async def _exercises_db_with_ids(user_id: int | None = None) -> list[dict]:
    """База упражнений: JSON + кастомные пользователя. id = индекс (для JSON), для кастомных — отрицательный."""
    raw = await load_exercises()
    result = [{"id": i, "name": ex.get("name", ""), "synonyms": ex.get("synonyms") or [], "muscle_groups": ex.get("muscle_groups") or []} for i, ex in enumerate(raw)]
    if user_id:
        from bot.database.crud import get_user_custom_exercises
        custom = await get_user_custom_exercises(user_id)
        for i, ex in enumerate(custom):
            result.append({
                "id": -(i + 1),
                "name": ex.name or "",
                "synonyms": list(ex.synonyms) if ex.synonyms else [],
                "muscle_groups": list(ex.muscle_groups) if ex.muscle_groups else [],
            })
    return result


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

    exercises_db = await _exercises_db_with_ids(user_id)
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

        matched = match_exercise(name, exercises_db)
        found = (matched.get("confidence") or 0) >= 0.9
        if not found:
            # Не нашли — сразу предлагаем добавить как новое (без переспроса)
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

        volume = 0.0
        for s in sets_list:
            r, w = s.get("reps"), s.get("weight")
            if r is not None and w is not None:
                try:
                    volume += float(w) * int(r)
                except (TypeError, ValueError):
                    pass

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
        summary = await _format_workout_summary(workout_id)
        if summary:
            await message.answer(summary, parse_mode="HTML")


# ----- Выбор программы (callback) -----


def _program_exercise_buttons(workout_id: int, program_exercises: list[dict], set_counts: dict[str, int]) -> InlineKeyboardMarkup:
    """Кнопки упражнений программы: [🦵 Приседания ✅3] с учётом сделанных подходов."""
    buttons = []
    for i, ex in enumerate(program_exercises):
        name = ex.get("name") or "Упражнение"
        emoji = _emoji_for_muscle_group(ex.get("muscle_groups"))
        count = set_counts.get(name, 0)
        label = f"{emoji} {name} ✅{count}" if count else f"{emoji} {name}"
        if len(label) > 35:
            label = label[:32] + "…"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"prog_ex:{workout_id}:{i}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("program:"))
async def on_program_selected(callback: CallbackQuery, state: FSMContext):
    """После выбора программы или freestyle — создаём тренировку. По программе — показываем кнопки упражнений."""
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

    if program_id:
        program = await get_program_by_id(program_id)
        exercises_list = getattr(program, "exercises", None) or []
        if exercises_list and isinstance(exercises_list, list):
            exercises_db = await _exercises_db_with_ids(callback.from_user.id)
            raw = await load_exercises()
            program_exercises = []
            for ex in exercises_list:
                if isinstance(ex, dict):
                    eid = ex.get("exercise_id", 0)
                    if eid < 0:
                        name = ex.get("name") or "Упражнение"
                        muscle_groups = []
                    else:
                        name = ex.get("name") or (exercises_db[eid].get("name") if eid < len(exercises_db) else "Упражнение")
                        muscle_groups = raw[eid].get("muscle_groups") if eid < len(raw) else []
                    program_exercises.append({"exercise_id": eid, "name": name, "muscle_groups": muscle_groups})
                else:
                    program_exercises.append({"exercise_id": 0, "name": "Упражнение", "muscle_groups": []})
            if program_exercises:
                await state.update_data(program_id=program_id, program_exercises=program_exercises)
                workout_loaded = await get_workout_by_id(workout.id)
                set_counts = {}
                if workout_loaded and workout_loaded.workout_exercises:
                    for we in workout_loaded.workout_exercises:
                        ename = we.exercise.name if we.exercise else ""
                        set_counts[ename] = set_counts.get(ename, 0) + len(we.sets or [])
                kb = _program_exercise_buttons(workout.id, program_exercises, set_counts)
                await callback.message.edit_text(
                    f"Тренировка по программе «{program.name}». Нажми упражнение для записи подхода."
                )
                await callback.message.answer("Меню тренировки:", reply_markup=workout_menu())
                await callback.message.answer("Упражнения программы:", reply_markup=kb)
                await callback.answer()
                return
    await callback.message.edit_text("Тренировка начата. Говори или пиши упражнения и подходы.")
    await callback.message.answer("Меню тренировки:", reply_markup=workout_menu())
    await callback.answer()


def _parse_weight_reps(text: str) -> tuple[float | None, int | None]:
    """Парсит фразу вида «80 10», «80 кг 10 раз» → (weight_kg, reps)."""
    import re
    nums = re.findall(r"\d+(?:[.,]\d+)?", (text or "").replace(",", "."))
    if not nums:
        return None, None
    if len(nums) >= 2:
        try:
            w = float(nums[0])
            r = int(float(nums[1]))
            return w, r
        except (ValueError, TypeError):
            pass
    if len(nums) == 1:
        try:
            r = int(float(nums[0]))
            return None, r  # только повторения (например тело)
        except (ValueError, TypeError):
            pass
    return None, None


@router.callback_query(F.data.startswith("prog_ex:"))
async def on_program_exercise_click(callback: CallbackQuery, state: FSMContext):
    """Нажатие на упражнение программы: просим ввести вес и повторения."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    try:
        workout_id = int(parts[1])
        idx = int(parts[2])
    except ValueError:
        await callback.answer()
        return
    data = await state.get_data()
    program_exercises = data.get("program_exercises") or []
    if idx < 0 or idx >= len(program_exercises):
        await callback.answer("Упражнение не найдено", show_alert=True)
        return
    ex = program_exercises[idx]
    name = ex.get("name") or "Упражнение"
    await state.update_data(pending_program_exercise={"workout_id": workout_id, "exercise_name": name})
    await callback.message.answer(f"Записываю подход для <b>{name}</b>. Сколько кг и повторений? (например: 80 10)", parse_mode="HTML")
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
    pending = workout_data.get("pending_program_exercise")
    if pending:
        workout_id = pending.get("workout_id")
        exercise_name = pending.get("exercise_name") or "Упражнение"
        weight_kg, reps = _parse_weight_reps(text)
        if reps is None and weight_kg is None:
            await message.answer("Не понял. Напиши, например: 80 10 (вес и повторения).")
            return
        await state.update_data(pending_program_exercise=None)
        flat_sets = [{"exercise_name": exercise_name, "reps": reps, "weight_kg": weight_kg}]
        async with get_session() as session:
            await add_workout_sets(session, workout_id, flat_sets, user_id=message.from_user.id)
        vol = (weight_kg or 0) * (reps or 0)
        await message.answer(f"✅ Записал: {exercise_name} — {weight_kg or '—'} кг × {reps or '—'} повт" + (f", объём {vol:.0f} кг" if vol else ""))
        program_exercises = workout_data.get("program_exercises") or []
        if program_exercises:
            workout_loaded = await get_workout_by_id(workout_id)
            set_counts = {}
            if workout_loaded and workout_loaded.workout_exercises:
                for we in workout_loaded.workout_exercises:
                    ename = we.exercise.name if we.exercise else ""
                    set_counts[ename] = set_counts.get(ename, 0) + len(we.sets or [])
            kb = _program_exercise_buttons(workout_id, program_exercises, set_counts)
            await message.answer("Упражнения программы:", reply_markup=kb)
        return

    workout = workout_data.get("workout") or {}
    workout_id = workout.get("id")
    if not workout_id:
        await message.answer("Тренировка не найдена. Начни заново: 🏋️ Начать тренировку")
        return

        parsed = await parse_workout_message(
        text=text,
        user_id=message.from_user.id,
        current_workout=workout,
        exercises_db=await _exercises_db_with_ids(message.from_user.id),
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
        exercises_db=await _exercises_db_with_ids(callback.from_user.id),
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
async def on_confirm_exercise(callback: CallbackQuery, state: FSMContext):
    """Пользователь подтвердил — закрыть сообщение и показать актуальную сводку тренировки."""
    data = await state.get_data()
    workout_id = (data.get("workout") or {}).get("id")
    await callback.message.delete()
    if workout_id:
        summary = await _format_workout_summary(workout_id)
        if summary:
            await callback.message.answer(summary, parse_mode="HTML")
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
    if not workout_id:
        await callback.message.edit_text("Данные устарели. Напиши упражнение и подходы ещё раз.")
        await callback.answer()
        return
    if not sets_list:
        sets_list = [{"reps": None, "weight": None}]
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
    summary = await _format_workout_summary(workout_id)
    if summary:
        await callback.message.answer(summary, parse_mode="HTML")
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
        if not workout_id:
            await callback.message.edit_text("Данные устарели. Напиши упражнение и подходы ещё раз.")
            await callback.answer()
            return
        if not sets_list:
            sets_list = [{"reps": None, "weight": None}]
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
        summary = await _format_workout_summary(workout_id)
        if summary:
            await callback.message.answer(summary, parse_mode="HTML")
        await callback.answer("Добавлено!")
        return

    await state.update_data(pending_clarification=None)
    workout_data = await state.get_data()
    workout_id = workout_data.get("workout", {}).get("id")
    if not workout_id:
        await callback.answer("Тренировка не найдена", show_alert=True)
        return
    
    exercises_db = await _exercises_db_with_ids(callback.from_user.id)
    try:
        idx = int(value)
    except ValueError:
        await callback.answer()
        return
    
    # Найти упражнение по id (индекс для JSON, отрицательный для кастомных)
    selected_ex = next((e for e in exercises_db if e.get("id") == idx), None)
    if selected_ex:
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
            summary = await _format_workout_summary(workout_id)
            if summary:
                await callback.message.answer(summary, parse_mode="HTML")
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


async def _format_workout_summary(workout_id: int) -> str:
    """Форматирует сводку тренировки (упражнения, подходы, комментарии)."""
    workout = await get_workout_by_id(workout_id)
    if not workout or not workout.workout_exercises:
        return ""
    exercises = sorted(workout.workout_exercises, key=lambda we: we.order_num)
    lines = ["📋 <b>Текущая тренировка:</b>", ""]
    for we in exercises:
        name = we.exercise.name if we.exercise else "Упражнение"
        groups = getattr(we.exercise, "muscle_groups", None) if we.exercise else None
        emoji = _emoji_for_muscle_group(groups)
        lines.append(f"{emoji} {name}")
        if we.comment:
            lines.append(f"  💬 {we.comment}")
        for s in sorted(we.sets, key=lambda x: x.set_number):
            if s.weight_kg is not None and s.reps is not None:
                lines.append(f"  └ Подход {s.set_number}: {s.weight_kg} кг × {s.reps} повт")
            elif s.reps is not None:
                lines.append(f"  └ Подход {s.set_number}: {s.reps} мин")
            else:
                lines.append(f"  └ Подход {s.set_number}: —")
        lines.append("")
    total_ex = len(exercises)
    total_sets = sum(len(we.sets) for we in exercises)
    lines.append(f"Итого: {total_ex} упр., {total_sets} подходов")
    return "\n".join(lines)


def _emoji_for_muscle_group(groups: list | None) -> str:
    """Эмодзи по группе мышц: ноги, спина, грудь, плечи, руки, пресс."""
    if not groups:
        return "🏋️"
    g = (groups[0] or "").lower()
    if "ног" in g or "квадр" in g or "бицепс бедр" in g:
        return "🦵"
    if "спин" in g or "тяга" in g or "подтяг" in g or "гиперэкстензия" in g:
        return "🔙"
    if "груд" in g:
        return "💪"
    if "плеч" in g or "дельт" in g:
        return "🏋️"
    if "руки" in g or "бицепс" in g or "трицепс" in g or "предплеч" in g:
        return "💪"
    if "пресс" in g or "кора" in g or "косые" in g:
        return "🎯"
    return "🏋️"


@router.message(F.text == "📊 Текущая тренировка")
async def show_current_workout_summary(message: Message, state: FSMContext):
    """Показать сводку текущей активной тренировки (эмодзи по группе мышц, пустая строка между упражнениями)."""
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
        groups = getattr(we.exercise, "muscle_groups", None) if we.exercise else None
        emoji = _emoji_for_muscle_group(groups)
        lines.append(f"{emoji} {name}")
        if we.comment:
            lines.append(f"  💬 {we.comment}")
        for s in sorted(we.sets, key=lambda x: x.set_number):
            if s.weight_kg is not None and s.reps is not None:
                lines.append(f"  └ Подход {s.set_number}: {s.weight_kg} кг × {s.reps} повт")
            elif s.reps is not None:
                lines.append(f"  └ Подход {s.set_number}: {s.reps} мин")
            else:
                lines.append(f"  └ Подход {s.set_number}: —")
        lines.append("")
    total_ex = len(exercises)
    total_sets = sum(len(we.sets) for we in exercises)
    lines.append(f"Итого: {total_ex} упражнений, {total_sets} подходов")
    await message.answer("\n".join(lines).strip())


# ----- Текст во время тренировки -----


@router.message(F.text, WorkoutStates.active)
async def handle_text_during_workout(message: Message, state: FSMContext):
    """Обработка текстового сообщения во время тренировки (без Whisper)."""
    workout_data = await state.get_data()
    pending = workout_data.get("pending_program_exercise")
    if pending:
        workout_id = pending.get("workout_id")
        exercise_name = pending.get("exercise_name") or "Упражнение"
        weight_kg, reps = _parse_weight_reps(message.text or "")
        if reps is None and weight_kg is None:
            await message.answer("Не понял. Напиши, например: 80 10 (вес и повторения).")
            return
        await state.update_data(pending_program_exercise=None)
        flat_sets = [{"exercise_name": exercise_name, "reps": reps, "weight_kg": weight_kg}]
        async with get_session() as session:
            await add_workout_sets(session, workout_id, flat_sets, user_id=message.from_user.id)
        vol = (weight_kg or 0) * (reps or 0)
        await message.answer(f"✅ Записал: {exercise_name} — {weight_kg or '—'} кг × {reps or '—'} повт" + (f", объём {vol:.0f} кг" if vol else ""))
        program_exercises = workout_data.get("program_exercises") or []
        if program_exercises:
            workout_loaded = await get_workout_by_id(workout_id)
            set_counts = {}
            if workout_loaded and workout_loaded.workout_exercises:
                for we in workout_loaded.workout_exercises:
                    ename = we.exercise.name if we.exercise else ""
                    set_counts[ename] = set_counts.get(ename, 0) + len(we.sets or [])
            kb = _program_exercise_buttons(workout_id, program_exercises, set_counts)
            await message.answer("Упражнения программы:", reply_markup=kb)
        return

    workout = workout_data.get("workout") or {}
    workout_id = workout.get("id")
    if not workout_id:
        await message.answer("Тренировка не найдена. Начни заново: 🏋️ Начать тренировку")
        return

    parsed = await parse_workout_message(
        text=message.text or "",
        user_id=message.from_user.id,
        current_workout=workout,
        exercises_db=await _exercises_db_with_ids(message.from_user.id),
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
    exercises_db = await _exercises_db_with_ids(message.from_user.id)
    matched = match_exercise(text.strip(), exercises_db)
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
    summary = await _format_workout_summary(workout_id)
    if summary:
        await message.answer(summary, parse_mode="HTML")
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
    
    workout_data = await state.get_data()
    workout_id = (workout_data.get("workout") or {}).get("id")
    await message.answer(f"✅ Комментарий добавлен: <i>{text.strip()}</i>", parse_mode="HTML")
    if workout_id:
        summary = await _format_workout_summary(workout_id)
        if summary:
            await message.answer(summary, parse_mode="HTML")
    await state.set_state(WorkoutStates.active)
    await state.update_data(pending_comment_we_id=None)
