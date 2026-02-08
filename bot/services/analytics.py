"""Сервис аналитики: объёмы, рекорды, мотивация, форматирование итогов."""

import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from bot.database.crud import (
    calculate_1rm,
    calculate_workout_volume,
    get_exercise_by_id,
    get_exercise_history,
    get_user_records,
    get_user_workouts,
    get_week_comparison,
    get_workout_by_id,
)

# Русские названия месяцев (родительный падеж для "8 февраля")
MONTHS_RU = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
MONTHS_NOMINATIVE = [
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]

MOTIVATION_PHRASES = [
    "Красавчик 💪",
    "Чемпион 🏆",
    "Вот это прогресс 🚀",
    "Продолжай в том же духе 🔥",
    "Монстр! 👹",
    "Так держать! ⚡",
    "Отличная работа! 🎯",
    "Машина! 🚂",
    "Зверь! 🦁",
    "Легенда! 🌟",
]


def _fmt_num(value: float) -> str:
    """Форматирование числа с пробелом как разделителем тысяч: 7200 → '7 200'."""
    s = f"{value:,.0f}"
    return s.replace(",", " ")


def _fmt_date_short(iso_date: Optional[str]) -> str:
    """Короткая дата: 8 февр."""
    if not iso_date:
        return "—"
    try:
        d = date.fromisoformat(iso_date)
        return f"{d.day} {MONTHS_RU[d.month][:4]}."  # февр.
    except (ValueError, IndexError):
        return iso_date


def _fmt_date_long(iso_date: Optional[str]) -> str:
    """Длинная дата: 8 февраля 2026."""
    if not iso_date:
        return "—"
    try:
        d = date.fromisoformat(iso_date)
        return f"{d.day} {MONTHS_RU[d.month]} {d.year}"
    except (ValueError, IndexError):
        return iso_date


def get_random_motivation() -> str:
    """Возвращает рандомную мотивационную фразу."""
    return random.choice(MOTIVATION_PHRASES)


async def format_workout_summary(
    workout_id: int,
    new_records: Optional[list[dict]] = None,
) -> str:
    """
    Форматирует итоги тренировки с эмодзи.
    new_records — список из check_and_save_records (exercise_name, record_type, value, previous_value).
    """
    data = await calculate_workout_volume(workout_id)
    if not data or data.get("exercises_count", 0) == 0:
        return "🏋️ Нет данных по этой тренировке."

    date_str = _fmt_date_long(data.get("date"))
    total = data.get("total_volume_kg", 0)
    lines = [
        "🏋️ Тренировка завершена!",
        "",
        f"📅 {date_str}",
        f"🔹 Упражнений: {data['exercises_count']}",
        f"🔹 Подходов: {data['sets_count']}",
        f"🔹 Общая нагрузка: {_fmt_num(total)} кг",
        "",
        "📊 По упражнениям:",
    ]
    for ex in data.get("exercises", []):
        lines.append(
            f"• {ex['name']} — {_fmt_num(ex['volume_kg'])} кг ({ex['sets_count']} подхода)"
        )
    if new_records:
        lines.append("")
        lines.append("🚀 Новый рекорд!")
        for r in new_records[:3]:  # не более 3 в сообщении
            name = r.get("exercise_name", "?")
            val = r.get("value")
            rtype = r.get("record_type", "")
            if rtype == "max_weight" and val:
                lines.append(f"{name}: {val:.0f} кг — лучший вес за всё время! 💪")
            elif rtype == "max_volume" and val:
                lines.append(f"{name}: {_fmt_num(val)} кг объёма — рекорд! 💪")
            elif rtype == "max_1rm" and val:
                lines.append(f"{name}: расчётный 1RM {val:.0f} кг — рекорд! 💪")
        if len(new_records) > 3:
            lines.append(f"... и ещё {len(new_records) - 3} рекорд(ов)")
    return "\n".join(lines)


async def get_motivation_message(
    workout_summary: dict,
    new_records: list,
) -> str:
    """
    Генерирует мотивационное сообщение по итогам тренировки.
    """
    if new_records:
        return "🚀 Новый рекорд! " + get_random_motivation()

    total = workout_summary.get("total_volume_kg") or 0
    user_id = workout_summary.get("user_id")
    if user_id and total > 0:
        workouts = await get_user_workouts(user_id, limit=20)
        if workouts:
            volumes = []
            for w in workouts:
                vol = getattr(w, "total_volume_kg", None) or 0
                try:
                    volumes.append(float(vol))
                except (TypeError, ValueError):
                    pass
            if volumes:
                avg = sum(volumes) / len(volumes)
                if total > avg * 1.05:
                    return "💪 Мощная тренировка! " + get_random_motivation()

        week_data = await get_week_comparison(user_id)
        diff = week_data.get("diff_percent", 0)
        if diff >= 10:
            return "🔥 Отличный прогресс! " + get_random_motivation()

    return get_random_motivation()


async def format_week_comparison(user_id: int) -> str:
    """Форматирует сравнение с прошлой неделей."""
    data = await get_week_comparison(user_id)
    cw = data.get("current_week", {})
    pw = data.get("previous_week", {})
    diff = data.get("diff_percent", 0)

    lines = [
        "📊 Сравнение с прошлой неделей",
        "",
        "Эта неделя:",
        f"🏋️ Тренировок: {cw.get('workouts_count', 0)}",
        f"📦 Общий объём: {_fmt_num(cw.get('total_volume_kg', 0))} кг",
        f"🎯 Упражнений: {cw.get('exercises_count', 0)}",
        "",
        "Прошлая неделя:",
        f"🏋️ Тренировок: {pw.get('workouts_count', 0)}",
        f"📦 Общий объём: {_fmt_num(pw.get('total_volume_kg', 0))} кг",
        f"🎯 Упражнений: {pw.get('exercises_count', 0)}",
        "",
    ]
    sign = "+" if diff >= 0 else ""
    emoji = "🔥" if diff >= 10 else "📈" if diff > 0 else "📉"
    lines.append(f"📈 Рост объёма: {sign}{diff}% {emoji}")
    return "\n".join(lines)


async def format_exercise_progress(
    user_id: int,
    exercise_id: int,
    limit: int = 5,
) -> str:
    """Форматирует прогресс по упражнению (последние N тренировок)."""
    exercise = await get_exercise_by_id(exercise_id)
    name = exercise.name if exercise else "Упражнение"
    history = await get_exercise_history(user_id, exercise_id, limit=limit)
    if not history:
        return f"📈 Прогресс: {name}\n\nНет данных за последние тренировки."

    lines = [f"📈 Прогресс: {name}", ""]
    volumes = []
    best_weight = 0.0
    best_1rm = 0.0

    for h in history:
        d = h.get("date")
        sets_str = ", ".join(
            f"{s['weight']:.0f}×{s['reps']}" if s.get("weight") and s.get("reps") else "—"
            for s in h.get("sets", [])
        )
        vol = h.get("volume_kg", 0)
        volumes.append(vol)
        lines.append(f"{_fmt_date_short(d)}: {sets_str} ({_fmt_num(vol)} кг)")
        for s in h.get("sets", []):
            w, r = s.get("weight"), s.get("reps")
            if w and r:
                if w > best_weight:
                    best_weight = w
                rm = calculate_1rm(r, w)
                if rm > best_1rm:
                    best_1rm = rm

    # Динамика объёма за неделю
    if len(volumes) >= 2:
        recent = sum(volumes[: min(3, len(volumes))]) / min(3, len(volumes))
        older = sum(volumes[-3:]) / min(3, len(volumes)) if len(volumes) >= 3 else volumes[-1]
        if older and older > 0:
            pct = ((recent - older) / older) * 100
            sign = "+" if pct >= 0 else ""
            lines.append(f"\n📊 Динамика объёма: {sign}{pct:.0f}% за период")
    lines.append(f"🏆 Лучший вес: {best_weight:.0f} кг")
    if best_1rm > 0:
        lines.append(f"💪 Расчётный 1RM: {best_1rm:.0f} кг")
    return "\n".join(lines)


async def calculate_muscle_group_volume(workout_id: int) -> dict[str, float]:
    """
    Считает объём по группам мышц в тренировке.
    Упражнение может учитываться в нескольких группах (доли не делим — дублируем объём).
    """
    workout = await get_workout_by_id(workout_id)
    if not workout or not workout.workout_exercises:
        return {}

    result = defaultdict(float)
    for we in workout.workout_exercises:
        vol = float(we.volume_kg or 0)
        groups = (we.exercise.muscle_groups or []) if we.exercise else []
        if not groups:
            result["другое"] = result.get("другое", 0) + vol
        else:
            for g in groups:
                if g:
                    result[g.strip()] += vol
    return dict(result)


async def format_records_list(user_id: int, limit: int = 10) -> str:
    """Форматирует список рекордов пользователя."""
    records = await get_user_records(user_id)
    if not records:
        return "🏆 Твои рекорды\n\nПока нет записей о рекордах. Завершай тренировки — рекорды появятся автоматически!"

    # Группируем по exercise_id
    by_exercise: dict[int, list] = defaultdict(list)
    for r in records:
        by_exercise[r.exercise_id].append(r)

    lines = ["🏆 Твои рекорды", ""]
    seen = set()
    count = 0
    for ex_id, recs in by_exercise.items():
        if count >= limit:
            break
        exercise = await get_exercise_by_id(ex_id)
        name = exercise.name if exercise else f"Упражнение #{ex_id}"
        lines.append(name)
        by_type = {r.record_type: r for r in recs}
        for rtype, label in [
            ("max_weight", "Макс вес"),
            ("max_volume", "Макс объём"),
            ("max_1rm", "Расчётный 1RM"),
        ]:
            r = by_type.get(rtype)
            if r:
                val = float(r.value)
                dt = r.achieved_at
                if hasattr(dt, "strftime"):
                    date_str = f"{dt.day} {MONTHS_RU[dt.month]} {dt.year}"
                else:
                    date_str = str(dt)[:10]
                if rtype == "max_weight":
                    lines.append(f"• {label}: {val:.0f} кг — {date_str}")
                elif rtype == "max_volume":
                    lines.append(f"• {label}: {_fmt_num(val)} кг — {date_str}")
                else:
                    lines.append(f"• {label}: {val:.0f} кг")
        lines.append("")
        count += 1
    return "\n".join(lines).strip()


async def format_today_summary(user_id: int) -> str:
    """Итоги за сегодня (все тренировки за день)."""
    today = date.today()
    workouts = await get_user_workouts(
        user_id,
        start_date=today,
        end_date=today,
        limit=20,
    )
    if not workouts:
        return "🏋️ Итоги за сегодня\n\nСегодня тренировок не было."

    lines = ["🏋️ Итоги за сегодня", ""]
    total_vol = 0.0
    for i, w in enumerate(workouts, 1):
        ex_count = len(w.workout_exercises) if w.workout_exercises else 0
        sets_count = sum(len(we.sets) for we in (w.workout_exercises or []))
        vol = float(w.total_volume_kg or 0)
        total_vol += vol
        comment = (w.comment or "").strip() or "Тренировка"
        if len(comment) > 30:
            comment = comment[:27] + "..."
        lines.append(f"Тренировка #{i}:")
        lines.append(f"• {comment}")
        lines.append(f"• {ex_count} упражнений, {sets_count} подходов")
        lines.append(f"• {_fmt_num(vol)} кг")
        lines.append("")
    lines.append(f"💪 Итого за день: {_fmt_num(total_vol)} кг")
    return "\n".join(lines)


async def format_month_summary(user_id: int, year: int, month: int) -> str:
    """Итоги за месяц."""
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)

    workouts = await get_user_workouts(
        user_id,
        start_date=start,
        end_date=end,
        limit=100,
    )
    if not workouts:
        month_name = MONTHS_NOMINATIVE[month] if month <= 12 else str(month)
        return f"📊 Итоги за {month_name} {year}\n\nНет тренировок за этот период."

    total_vol = 0.0
    exercise_volumes: dict[str, float] = defaultdict(float)
    for w in workouts:
        vol = float(w.total_volume_kg or 0)
        total_vol += vol
        for we in w.workout_exercises or []:
            name = we.exercise.name if we.exercise else "?"
            exercise_volumes[name] += float(we.volume_kg or 0)

    month_name = MONTHS_NOMINATIVE[month] if month <= 12 else str(month)
    ex_count = sum(len(w.workout_exercises or []) for w in workouts)
    avg_per_workout = total_vol / len(workouts) if workouts else 0

    records_all = await get_user_records(user_id)
    records_in_month = [
        r for r in records_all
        if getattr(r.achieved_at, "year", None) == year and getattr(r.achieved_at, "month", None) == month
    ]

    lines = [
        f"📊 Итоги за {month_name} {year}",
        "",
        f"🏋️ Тренировок: {len(workouts)}",
        f"📦 Общий объём: {_fmt_num(total_vol)} кг",
        f"🎯 Упражнений: {ex_count}",
        f"📈 Средний объём: {_fmt_num(avg_per_workout)} кг/тренировку",
        "",
        f"🏆 Рекордов в этом месяце: {len(records_in_month)}",
        "",
    ]

    # Топ упражнений по объёму
    if exercise_volumes:
        lines.append("Топ упражнений по объёму:")
        sorted_ex = sorted(
            exercise_volumes.items(),
            key=lambda x: -x[1],
        )[:5]
        for i, (ex_name, vol) in enumerate(sorted_ex, 1):
            lines.append(f"{i}. {ex_name} — {_fmt_num(vol)} кг")
    return "\n".join(lines)


# ----- Обратная совместимость для handlers (принимают list[Workout]) -----


def _volume_per_workout(workout) -> dict[str, float]:
    """Объём по упражнениям за одну тренировку (кг)."""
    vol = defaultdict(float)
    for we in getattr(workout, "workout_exercises", []) or []:
        name = getattr(we.exercise, "name", "?") if we.exercise else "?"
        for s in getattr(we, "sets", []) or []:
            w_kg = getattr(s, "weight_kg", None)
            reps = getattr(s, "reps", None)
            if w_kg is not None and reps is not None:
                vol[name] += float(w_kg) * int(reps)
    return dict(vol)


def _best_sets(workouts: list) -> dict[str, tuple[float, int, float]]:
    """По каждому упражнению: (вес, повторения, примерный 1RM)."""
    best = {}
    for w in workouts:
        for we in getattr(w, "workout_exercises", []) or []:
            name = getattr(we.exercise, "name", "?") if we.exercise else "?"
            for s in getattr(we, "sets", []) or []:
                w_kg, reps = getattr(s, "weight_kg", None), getattr(s, "reps", None)
                if w_kg is None or reps is None:
                    continue
                w_float = float(w_kg)
                one_rm = calculate_1rm(reps, w_float)
                if name not in best or best[name][2] < one_rm:
                    best[name] = (w_float, reps, one_rm)
    return best


async def get_volume_stats(workouts: list) -> str:
    """Суммарный объём по упражнениям за переданные тренировки."""
    total = defaultdict(float)
    for w in workouts:
        for name, v in _volume_per_workout(w).items():
            total[name] += v
    if not total:
        return "Объём: нет данных с весами и повторениями."
    lines = [f"• {name}: {_fmt_num(v)} кг" for name, v in sorted(total.items(), key=lambda x: -x[1])[:10]]
    return "Объём (топ упражнений):\n" + "\n".join(lines)


async def get_pr_stats(workouts: list) -> str:
    """Рекорды (оценка 1RM по подходам)."""
    best = _best_sets(workouts)
    if not best:
        return "Рекорды: нет данных."
    lines = []
    for name, (w, r, one_rm) in sorted(best.items(), key=lambda x: -x[1][2])[:10]:
        lines.append(f"• {name}: {w:.0f} кг x {r} (≈1RM {one_rm:.0f} кг)")
    return "Рекорды (≈1RM):\n" + "\n".join(lines)
