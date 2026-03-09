"""История тренировок: только даты с тренировками, полная статистика за день."""

from collections import defaultdict
from datetime import date, timedelta

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.database.engine import get_session
from bot.database.crud import (
    delete_workout,
    get_or_create_user,
    get_user_workouts,
    get_workout_by_id,
)

router = Router()

WEEKDAY = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_SHORT = ["", "янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
MONTHS_FULL = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _emoji_for_muscle_group(groups: list | None) -> str:
    if not groups:
        return "🏋️"
    g = (groups[0] or "").lower()
    if "ног" in g or "квадр" in g:
        return "🦵"
    if "спин" in g:
        return "🔙"
    if "груд" in g:
        return "💪"
    if "плеч" in g or "дельт" in g:
        return "🏋️"
    if "руки" in g or "бицепс" in g or "трицепс" in g:
        return "💪"
    if "пресс" in g or "кора" in g or "косые" in g:
        return "🎯"
    return "🏋️"


def _date_button_label(d: date) -> str:
    """Подпись кнопки: Пн 10 фев."""
    wday = WEEKDAY[d.weekday()]
    month = MONTHS_SHORT[d.month] if d.month < len(MONTHS_SHORT) else str(d.month)
    return f"{wday} {d.day} {month}"


def _build_dates_text_and_buttons(workouts: list):
    """
    Возвращает только даты, когда были тренировки.
    (текст_сводка, кнопки по датам).
    """
    dates_with_workouts: set[date] = {w.date for w in workouts}
    sorted_dates = sorted(dates_with_workouts, reverse=True)

    lines = ["Нажми на дату, чтобы увидеть все упражнения за этот день:", ""]
    buttons = []
    by_month: dict[tuple[int, int], list[date]] = defaultdict(list)
    for d in sorted_dates:
        by_month[(d.year, d.month)].append(d)

    for (year, month), dates in sorted(by_month.items(), reverse=True):
        month_name = MONTHS_FULL[month] if month < len(MONTHS_FULL) else str(month)
        lines.append(f"📅 {month_name} {year}")
        for d in sorted(dates, reverse=True):
            day_workouts = [w for w in workouts if w.date == d]
            ex_count = sum(len(getattr(w, "workout_exercises", []) or []) for w in day_workouts)
            sets_count = sum(
                sum(len(getattr(we, "sets", []) or []) for we in (getattr(w, "workout_exercises", []) or []))
                for w in day_workouts
            )
            vol = sum(float(getattr(w, "total_volume_kg") or 0) for w in day_workouts)
            vol_str = f"{vol:,.0f}".replace(",", " ")
            lines.append(f"  • {_date_button_label(d)} — {ex_count} упр, {sets_count} подходов, {vol_str} кг")
            buttons.append([InlineKeyboardButton(text=_date_button_label(d), callback_data=f"history:date:{d.isoformat()}")])
        lines.append("")

    text = "\n".join(lines).strip() if lines else "Нет тренировок."
    return text, buttons


async def _format_day_full_detail(user_id: int, day_date: date) -> str:
    """Полная статистика за день: все упражнения, подходы, объёмы."""
    workouts = await get_user_workouts(
        user_id,
        start_date=day_date,
        end_date=day_date,
        limit=20,
    )
    if not workouts:
        return "Нет данных за этот день."

    month_name = MONTHS_FULL[day_date.month] if day_date.month < len(MONTHS_FULL) else str(day_date.month)
    lines = [
        f"📅 {day_date.day} {month_name} {day_date.year}",
        f"({WEEKDAY[day_date.weekday()]})",
        "",
        "Упражнения:",
        "",
    ]
    total_vol = 0.0
    total_sets = 0
    for w in sorted(workouts, key=lambda x: x.created_at):
        for we in sorted(w.workout_exercises, key=lambda x: x.order_num):
            name = we.exercise.name if we.exercise else "Упражнение"
            groups = getattr(we.exercise, "muscle_groups", None) if we.exercise else None
            emoji = _emoji_for_muscle_group(groups)
            vol = float(we.volume_kg or 0)
            total_vol += vol
            lines.append(f"{emoji} {name}")
            for s in sorted(we.sets, key=lambda x: x.set_number):
                if s.weight_kg is not None and s.reps is not None:
                    lines.append(f"  └ Подход {s.set_number}: {s.weight_kg} кг × {s.reps} повт")
                    total_sets += 1
                elif s.reps is not None:
                    lines.append(f"  └ Подход {s.set_number}: {s.reps} мин")
                    total_sets += 1
                else:
                    lines.append(f"  └ Подход {s.set_number}: —")
            if we.comment:
                lines.append(f"  💬 {we.comment}")
            lines.append("")
    total_ex = sum(len(w.workout_exercises) for w in workouts)
    vol_str = f"{total_vol:,.0f}".replace(",", " ")
    lines.append(f"Итого за день: {total_ex} упр., {total_sets} подходов, {vol_str} кг")
    return "\n".join(lines)


@router.message(F.text == "📅 История тренировок")
async def show_history(message: Message):
    """Показать только даты с тренировками (последние ~2 месяца)."""
    async with get_session() as session:
        await get_or_create_user(session, message.from_user.id, message.from_user.username)
    workouts = await get_user_workouts(message.from_user.id, limit=60)
    text, rows = _build_dates_text_and_buttons(workouts)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=rows + [[InlineKeyboardButton(text="📊 Вся история", callback_data="history:all")]]
    )
    await message.answer(f"📅 История тренировок\n\n{text}", reply_markup=keyboard)


@router.callback_query(F.data == "history:all")
async def history_all(callback: CallbackQuery):
    """Показать всю историю — только даты с тренировками."""
    workouts = await get_user_workouts(callback.from_user.id, limit=200)
    text, rows = _build_dates_text_and_buttons(workouts)
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    await callback.message.edit_text(f"📊 Вся история\n\n{text}", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("history:date:"))
async def history_date_detail(callback: CallbackQuery):
    """Полная статистика за день: все упражнения и подходы."""
    date_str = callback.data.replace("history:date:", "")
    try:
        day_date = date.fromisoformat(date_str)
    except ValueError:
        await callback.answer("Ошибка даты", show_alert=True)
        return
    text = await _format_day_full_detail(callback.from_user.id, day_date)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить тренировки за этот день", callback_data=f"history:delete_day_ask:{date_str}")],
        [InlineKeyboardButton(text="◀ Назад к списку", callback_data="history:back")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "history:back")
async def history_back(callback: CallbackQuery):
    """Вернуться к списку дат."""
    workouts = await get_user_workouts(callback.from_user.id, limit=60)
    text, rows = _build_dates_text_and_buttons(workouts)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=rows + [[InlineKeyboardButton(text="📊 Вся история", callback_data="history:all")]]
    )
    await callback.message.edit_text(f"📅 История тренировок\n\n{text}", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("history:workout:"))
async def history_workout_detail(callback: CallbackQuery):
    """Детали тренировки + кнопка удаления."""
    try:
        wid = int(callback.data.split(":")[-1])
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    workout = await get_workout_by_id(wid)
    if not workout or workout.user_id != callback.from_user.id:
        await callback.message.edit_text("Тренировка не найдена.")
        await callback.answer()
        return
    exercises = sorted(workout.workout_exercises, key=lambda we: we.order_num)
    lines = [f"📅 {workout.date.strftime('%d.%m.%Y')}", ""]
    for we in exercises:
        name = we.exercise.name if we.exercise else "Упражнение"
        groups = getattr(we.exercise, "muscle_groups", None) if we.exercise else None
        emoji = _emoji_for_muscle_group(groups)
        lines.append(f"{emoji} {name}")
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
    vol = float(sum((we.volume_kg or 0) for we in workout.workout_exercises))
    lines.append(f"Итого: {total_ex} упр, {total_sets} подходов, {vol:,.0f} кг".replace(",", " "))
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить тренировку", callback_data=f"history:delete_ask:{wid}")],
    ])
    await callback.message.edit_text("\n".join(lines), reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("history:delete_ask:"))
async def history_delete_ask(callback: CallbackQuery):
    """Спросить подтверждение удаления."""
    try:
        wid = int(callback.data.split(":")[-1])
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=f"history:delete_confirm:{wid}")],
        [InlineKeyboardButton(text="❌ Нет", callback_data=f"history:delete_no:{wid}")],
    ])
    await callback.message.edit_text("Точно удалить? Это нельзя отменить.", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "history:delete_no")
async def history_delete_no_legacy(callback: CallbackQuery):
    """Обратная совместимость: отмена удаления без wid."""
    workouts = await get_user_workouts(callback.from_user.id, limit=60)
    text, rows = _build_dates_text_and_buttons(workouts)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=rows + [[InlineKeyboardButton(text="📊 Вся история", callback_data="history:all")]]
    )
    await callback.message.edit_text(f"Удаление отменено.\n\n📅 История тренировок\n\n{text}", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("history:delete_no:"))
async def history_delete_no(callback: CallbackQuery):
    """Отмена удаления — вернуться к деталям тренировки."""
    try:
        wid = int(callback.data.split(":")[-1])
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    workout = await get_workout_by_id(wid)
    if not workout or workout.user_id != callback.from_user.id:
        await callback.message.edit_text("Тренировка не найдена.")
        await callback.answer()
        return
    exercises = sorted(workout.workout_exercises, key=lambda we: we.order_num)
    lines = [f"📅 {workout.date.strftime('%d.%m.%Y')}", ""]
    for we in exercises:
        name = we.exercise.name if we.exercise else "Упражнение"
        groups = getattr(we.exercise, "muscle_groups", None) if we.exercise else None
        emoji = _emoji_for_muscle_group(groups)
        lines.append(f"{emoji} {name}")
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
    vol = float(sum((we.volume_kg or 0) for we in workout.workout_exercises))
    lines.append(f"Итого: {total_ex} упр, {total_sets} подходов, {vol:,.0f} кг".replace(",", " "))
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить тренировку", callback_data=f"history:delete_ask:{wid}")],
    ])
    await callback.message.edit_text("\n".join(lines), reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("history:delete_day_ask:"))
async def history_delete_day_ask(callback: CallbackQuery):
    """Подтверждение удаления всех тренировок за день."""
    date_str = callback.data.replace("history:delete_day_ask:", "")
    try:
        day_date = date.fromisoformat(date_str)
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"history:delete_day_confirm:{date_str}")],
        [InlineKeyboardButton(text="❌ Нет", callback_data=f"history:date:{date_str}")],
    ])
    label = _date_button_label(day_date)
    await callback.message.edit_text(
        f"Удалить все тренировки за {label}? Это нельзя отменить.",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("history:delete_day_confirm:"))
async def history_delete_day_confirm(callback: CallbackQuery):
    """Удалить все тренировки за день."""
    date_str = callback.data.replace("history:delete_day_confirm:", "")
    try:
        day_date = date.fromisoformat(date_str)
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    workouts = await get_user_workouts(
        callback.from_user.id,
        start_date=day_date,
        end_date=day_date,
        limit=50,
    )
    for w in workouts:
        await delete_workout(w.id)
    workouts = await get_user_workouts(callback.from_user.id, limit=60)
    text, rows = _build_dates_text_and_buttons(workouts)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=rows + [[InlineKeyboardButton(text="📊 Вся история", callback_data="history:all")]]
    )
    await callback.message.edit_text(f"📅 История тренировок\n\nТренировки за день удалены.\n\n{text}", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("history:delete_confirm:"))
async def history_delete_confirm(callback: CallbackQuery):
    try:
        wid = int(callback.data.split(":")[-1])
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    workout = await get_workout_by_id(wid)
    if workout and workout.user_id == callback.from_user.id:
        await delete_workout(wid)
    await callback.message.edit_text("Тренировка удалена.")
    await callback.answer()
