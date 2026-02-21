"""История тренировок по неделям, просмотр и удаление."""

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


def _week_range_str(start: date, end: date) -> str:
    return f"{start.day}-{end.day} {MONTHS_SHORT[end.month]} {end.year}"


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _format_workout_line(w) -> str:
    """Одна строка: • Пн 10 фев — 3 упр, 9 подходов, 2,450 кг"""
    d = w.date
    wday = WEEKDAY[d.weekday()]
    month = MONTHS_SHORT[d.month] if d.month < len(MONTHS_SHORT) else str(d.month)
    ex_count = len(getattr(w, "workout_exercises", []) or [])
    sets_count = sum(len(getattr(we, "sets", []) or []) for we in (getattr(w, "workout_exercises", []) or []))
    vol = float(getattr(w, "total_volume_kg") or 0)
    vol_str = f"{vol:,.0f}".replace(",", " ")
    return f"• {wday} {d.day} {month} — {ex_count} упр, {sets_count} подходов, {vol_str} кг"


def _build_weeks_text_and_buttons(workouts: list, limit_per_week: int = 20):
    """Группирует тренировки по неделям, возвращает (текст, inline_кнопки для каждой тренировки)."""
    by_week = defaultdict(list)
    for w in workouts:
        start = _week_start(w.date)
        by_week[start].append(w)
    lines = []
    buttons = []
    for start in sorted(by_week.keys(), reverse=True):
        week_workouts = sorted(by_week[start], key=lambda x: x.date, reverse=True)
        end = start + timedelta(days=6)
        lines.append(f"\nНеделя {_week_range_str(start, end)}")
        for w in week_workouts[:limit_per_week]:
            lines.append(f"  {_format_workout_line(w)}")
            name = f"{WEEKDAY[w.date.weekday()]} {w.date.day} {MONTHS_SHORT[w.date.month]}"
            buttons.append([InlineKeyboardButton(text=name, callback_data=f"history:workout:{w.id}")])
    return "\n".join(lines).strip() if lines else "Нет тренировок.", buttons


@router.message(F.text == "📅 История тренировок")
async def show_history(message: Message):
    """Показать историю тренировок по неделям (последние ~2 месяца)."""
    async with get_session() as session:
        await get_or_create_user(session, message.from_user.id, message.from_user.username)
    workouts = await get_user_workouts(
        message.from_user.id,
        limit=60,
    )
    text, rows = _build_weeks_text_and_buttons(workouts)
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows + [[InlineKeyboardButton(text="📊 Вся история", callback_data="history:all")]])
    await message.answer(f"📅 История тренировок\n\n{text}", reply_markup=keyboard)


@router.callback_query(F.data == "history:all")
async def history_all(callback: CallbackQuery):
    """Показать всю историю тренировок."""
    workouts = await get_user_workouts(callback.from_user.id, limit=200)
    text, rows = _build_weeks_text_and_buttons(workouts)
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    await callback.message.edit_text(f"📊 Вся история\n\n{text}", reply_markup=keyboard)
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
        [InlineKeyboardButton(text="❌ Нет", callback_data="history:delete_no")],
    ])
    await callback.message.edit_text("Точно удалить? Это нельзя отменить.", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "history:delete_no")
async def history_delete_no(callback: CallbackQuery):
    await callback.message.edit_text("Удаление отменено.")
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
