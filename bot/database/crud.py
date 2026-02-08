"""CRUD операции с БД. Все функции async, используют get_session() из engine."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.database.engine import get_session
from bot.database.models import (
    Exercise,
    Program,
    Record,
    Set,
    User,
    Workout,
    WorkoutExercise,
)


# ============= USERS =============


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str] = None,
) -> User:
    """Получает пользователя или создаёт, если не существует. Возвращает объект User."""
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_id=telegram_id, username=username)
        session.add(user)
        await session.flush()
        await session.refresh(user)
    return user


async def update_user_settings(telegram_id: int, settings: dict) -> User:
    """Обновляет настройки пользователя (единицы измерения, язык и т.д.)."""
    async with get_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError(f"User with telegram_id {telegram_id} not found")
        user.settings = settings
        await session.flush()
        await session.refresh(user)
        return user


# ============= EXERCISES =============


async def get_exercise_by_id(exercise_id: int) -> Optional[Exercise]:
    """Получает упражнение по ID."""
    async with get_session() as session:
        result = await session.execute(select(Exercise).where(Exercise.id == exercise_id))
        return result.scalar_one_or_none()


async def get_exercise_by_name(name: str) -> Optional[Exercise]:
    """Получает упражнение по точному названию."""
    async with get_session() as session:
        result = await session.execute(select(Exercise).where(Exercise.name == name))
        return result.scalar_one_or_none()


async def create_custom_exercise(
    user_id: int,
    name: str,
    muscle_groups: list,
    equipment: str,
    synonyms: Optional[list] = None,
) -> Exercise:
    """Создаёт кастомное упражнение для пользователя."""
    async with get_session() as session:
        ex = Exercise(
            name=name,
            muscle_groups=muscle_groups or [],
            equipment=equipment,
            synonyms=synonyms or [],
            is_custom=True,
            created_by=user_id,
        )
        session.add(ex)
        await session.flush()
        await session.refresh(ex)
        return ex


async def get_user_custom_exercises(user_id: int) -> list[Exercise]:
    """Получает все кастомные упражнения пользователя."""
    async with get_session() as session:
        result = await session.execute(
            select(Exercise).where(Exercise.created_by == user_id).order_by(Exercise.name)
        )
        return list(result.scalars().all())


async def search_exercises_in_db(query: str, user_id: Optional[int] = None) -> list[Exercise]:
    """
    Ищет упражнения в БД (стандартные + кастомные пользователя).
    LIKE по name и по синонимам (через array_to_string).
    """
    if not query or not query.strip():
        return []
    q = f"%{query.strip()}%"
    async with get_session() as session:
        stmt = select(Exercise).where(
            or_(
                Exercise.name.ilike(q),
                func.coalesce(func.array_to_string(Exercise.synonyms, " "), "").ilike(q),
            )
        )
        if user_id is not None:
            stmt = stmt.where(or_(Exercise.is_custom == False, Exercise.created_by == user_id))
        else:
            stmt = stmt.where(Exercise.is_custom == False)
        stmt = stmt.order_by(Exercise.name)
        result = await session.execute(stmt)
        return list(result.scalars().all())


# ============= PROGRAMS =============


async def create_program(
    session: AsyncSession,
    user_id: int,
    name: str,
    exercise_ids: list[int],
) -> Program:
    """
    Создаёт программу тренировок.
    exercise_ids сохраняется в JSONB как [{"exercise_id": 1, "order": 1}, ...]
    """
    exercises_json = [{"exercise_id": eid, "order": i + 1} for i, eid in enumerate(exercise_ids)]
    program = Program(user_id=user_id, name=name, exercises=exercises_json)
    session.add(program)
    await session.flush()
    await session.refresh(program)
    return program


async def get_user_programs(session: AsyncSession, user_id: int) -> list[Program]:
    """Получает все программы пользователя."""
    result = await session.execute(
        select(Program).where(Program.user_id == user_id).order_by(Program.created_at.desc())
    )
    return list(result.scalars().all())


async def get_program_by_id(program_id: int) -> Optional[Program]:
    """Получает программу по ID."""
    async with get_session() as session:
        result = await session.execute(select(Program).where(Program.id == program_id))
        return result.scalar_one_or_none()


async def delete_program(program_id: int) -> None:
    """Удаляет программу."""
    async with get_session() as session:
        await session.execute(delete(Program).where(Program.id == program_id))


# ============= WORKOUTS =============


async def create_workout(
    session: AsyncSession,
    user_id: int,
    program_id: Optional[int] = None,
    comment: Optional[str] = None,
) -> Workout:
    """Создаёт новую тренировку с датой = сегодня. Возвращает объект Workout."""
    workout = Workout(
        user_id=user_id,
        date=date.today(),
        program_id=program_id,
        comment=comment,
    )
    session.add(workout)
    await session.flush()
    await session.refresh(workout)
    return workout


async def get_current_workout(user_id: int) -> Optional[Workout]:
    """Получает текущую активную тренировку пользователя (сегодняшняя) или None."""
    async with get_session() as session:
        result = await session.execute(
            select(Workout)
            .where(Workout.user_id == user_id, Workout.date == date.today())
            .order_by(Workout.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def get_workout_by_id(workout_id: int) -> Optional[Workout]:
    """Получает тренировку по ID с загрузкой упражнений и подходов."""
    async with get_session() as session:
        result = await session.execute(
            select(Workout)
            .where(Workout.id == workout_id)
            .options(
                selectinload(Workout.workout_exercises).selectinload(WorkoutExercise.sets),
                selectinload(Workout.workout_exercises).selectinload(WorkoutExercise.exercise),
            )
        )
        return result.scalar_one_or_none()


async def finish_workout(workout_id: int) -> Optional[Workout]:
    """
    Завершает тренировку: обновляет updated_at, пересчитывает total_volume_kg, проверяет рекорды.
    Возвращает обновлённый Workout.
    """
    async with get_session() as session:
        result = await session.execute(
            select(Workout)
            .where(Workout.id == workout_id)
            .options(selectinload(Workout.workout_exercises))
        )
        workout = result.scalar_one_or_none()
        if not workout:
            return None
        total = sum((we.volume_kg or Decimal("0")) for we in workout.workout_exercises)
        workout.total_volume_kg = total
        workout.updated_at = datetime.utcnow()
        await session.flush()
        await session.refresh(workout)
        return workout


async def delete_workout(workout_id: int) -> None:
    """Удаляет тренировку (каскадно удалятся workout_exercises и sets)."""
    async with get_session() as session:
        await session.execute(delete(Workout).where(Workout.id == workout_id))


async def get_workout_summary(session: AsyncSession, workout_id: int) -> dict:
    """Возвращает сводку по тренировке: date, exercises_count, sets_count, total_volume_kg."""
    result = await session.execute(
        select(Workout)
        .where(Workout.id == workout_id)
        .options(selectinload(Workout.workout_exercises).selectinload(WorkoutExercise.sets))
    )
    workout = result.scalar_one_or_none()
    if not workout:
        return {"date": None, "exercises_count": 0, "sets_count": 0, "total_volume_kg": 0}
    return {
        "date": workout.date,
        "exercises_count": len(workout.workout_exercises),
        "sets_count": sum(len(we.sets) for we in workout.workout_exercises),
        "total_volume_kg": float(sum((we.volume_kg or Decimal("0")) for we in workout.workout_exercises)),
    }


async def get_user_workouts(
    user_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = 10,
) -> list[Workout]:
    """
    Получает тренировки пользователя за период.
    Если даты не указаны — последние N тренировок.
    """
    async with get_session() as session:
        stmt = (
            select(Workout)
            .where(Workout.user_id == user_id)
            .options(
                selectinload(Workout.workout_exercises).selectinload(WorkoutExercise.sets),
                selectinload(Workout.workout_exercises).selectinload(WorkoutExercise.exercise),
            )
            .order_by(Workout.date.desc(), Workout.created_at.desc())
            .limit(limit)
        )
        if start_date is not None:
            stmt = stmt.where(Workout.date >= start_date)
        if end_date is not None:
            stmt = stmt.where(Workout.date <= end_date)
        result = await session.execute(stmt)
        return list(result.scalars().all())


# ============= WORKOUT EXERCISES & SETS =============


async def add_workout_sets(
    session: AsyncSession,
    workout_id: int,
    sets: list[dict],
    user_id: Optional[int] = None,
) -> None:
    """
    Добавляет подходы: список dict с exercise_name, reps?, weight_kg?.
    Группирует по упражнению, создаёт Exercise при необходимости.
    """
    await add_workout_sets_with_session(session, workout_id, sets, user_id)


async def add_workout_exercise(
    workout_id: int,
    exercise_id: int,
    sets: list[dict],
    comment: Optional[str] = None,
) -> Optional[WorkoutExercise]:
    """
    Добавляет упражнение в тренировку с подходами.
    sets: [{"reps": 10, "weight": 80, "comment": "тяжело"}, ...]
    Определяет order_num, создаёт Set, считает volume_kg.
    Возвращает WorkoutExercise с загруженными sets.
    """
    async with get_session() as session:
        r = await session.execute(
            select(func.coalesce(func.max(WorkoutExercise.order_num), -1)).where(
                WorkoutExercise.workout_id == workout_id
            )
        )
        next_order = (r.scalar() or -1) + 1
        we = WorkoutExercise(
            workout_id=workout_id,
            exercise_id=exercise_id,
            order_num=next_order,
            comment=comment,
        )
        session.add(we)
        await session.flush()
        volume = Decimal("0")
        for i, s in enumerate(sets):
            reps = s.get("reps")
            weight = s.get("weight") or s.get("weight_kg")
            if weight is not None and reps is not None:
                volume += Decimal(str(weight)) * int(reps)
            set_row = Set(
                workout_exercise_id=we.id,
                set_number=i + 1,
                reps=reps,
                weight_kg=Decimal(str(weight)) if weight is not None else None,
                comment=(s.get("comment") or "").strip() or None,
            )
            session.add(set_row)
        we.volume_kg = volume if volume else None
        await session.flush()
        await session.refresh(we)
        await session.execute(
            select(WorkoutExercise)
            .where(WorkoutExercise.id == we.id)
            .options(selectinload(WorkoutExercise.sets))
        )
        return we


async def delete_last_workout_exercise(session: AsyncSession, workout_id: int) -> bool:
    """
    Удаляет последнее добавленное упражнение из тренировки (по order_num desc).
    Каскадно удаляются подходы (sets). Возвращает True если что-то удалено, False если нечего удалять.
    """
    result = await session.execute(
        select(WorkoutExercise)
        .where(WorkoutExercise.workout_id == workout_id)
        .order_by(WorkoutExercise.order_num.desc())
        .limit(1)
    )
    last_exercise = result.scalar_one_or_none()
    if not last_exercise:
        return False
    await session.delete(last_exercise)
    await session.flush()
    return True


async def remove_last_set(workout_id: int) -> bool:
    """Удаляет последний подход из тренировки. Возвращает True если удалено, иначе False."""
    async with get_session() as session:
        workout = (
            await session.execute(
                select(Workout)
                .where(Workout.id == workout_id)
                .options(
                    selectinload(Workout.workout_exercises).selectinload(WorkoutExercise.sets)
                )
            )
        ).scalar_one_or_none()
        if not workout or not workout.workout_exercises:
            return False
        last_we = workout.workout_exercises[-1]
        if not last_we.sets:
            return False
        last_set = last_we.sets[-1]
        await session.delete(last_set)
        # Пересчитать volume_kg у last_we
        new_vol = sum(
            (s.weight_kg or Decimal("0")) * (s.reps or 0)
            for s in last_we.sets
            if s.id != last_set.id
        )
        last_we.volume_kg = new_vol if new_vol else None
        await session.flush()
        return True


async def add_set_comment(set_id: int, comment: str) -> None:
    """Добавляет комментарий к подходу."""
    async with get_session() as session:
        await session.execute(update(Set).where(Set.id == set_id).values(comment=comment))


async def add_exercise_comment(workout_exercise_id: int, comment: str) -> None:
    """Добавляет комментарий к упражнению в тренировке."""
    async with get_session() as session:
        await session.execute(
            update(WorkoutExercise).where(WorkoutExercise.id == workout_exercise_id).values(comment=comment)
        )


async def add_workout_comment(workout_id: int, comment: str) -> None:
    """Добавляет общий комментарий к тренировке."""
    async with get_session() as session:
        await session.execute(update(Workout).where(Workout.id == workout_id).values(comment=comment))


# ============= ANALYTICS & RECORDS =============


async def calculate_workout_volume(workout_id: int) -> dict:
    """
    Считает объёмы тренировки.
    Возвращает date, exercises_count, sets_count, total_volume_kg, exercises (name, volume_kg, sets_count).
    """
    async with get_session() as session:
        result = await session.execute(
            select(Workout)
            .where(Workout.id == workout_id)
            .options(
                selectinload(Workout.workout_exercises).selectinload(WorkoutExercise.sets),
                selectinload(Workout.workout_exercises).selectinload(WorkoutExercise.exercise),
            )
        )
        workout = result.scalar_one_or_none()
        if not workout:
            return {
                "date": None,
                "exercises_count": 0,
                "sets_count": 0,
                "total_volume_kg": 0,
                "exercises": [],
            }
        exercises_detail = []
        total_vol = Decimal("0")
        sets_count = 0
        for we in workout.workout_exercises:
            vol = we.volume_kg or Decimal("0")
            total_vol += vol
            sets_count += len(we.sets)
            name = we.exercise.name if we.exercise else "?"
            exercises_detail.append({
                "name": name,
                "volume_kg": float(vol),
                "sets_count": len(we.sets),
            })
        return {
            "date": workout.date.isoformat() if workout.date else None,
            "exercises_count": len(workout.workout_exercises),
            "sets_count": sets_count,
            "total_volume_kg": float(total_vol),
            "exercises": exercises_detail,
        }


async def get_exercise_history(
    user_id: int,
    exercise_id: int,
    limit: int = 10,
) -> list[dict]:
    """История выполнения упражнения (последние N тренировок)."""
    async with get_session() as session:
        result = await session.execute(
            select(WorkoutExercise, Workout)
            .join(Workout, Workout.id == WorkoutExercise.workout_id)
            .where(Workout.user_id == user_id, WorkoutExercise.exercise_id == exercise_id)
            .options(selectinload(WorkoutExercise.sets))
            .order_by(Workout.date.desc())
            .limit(limit)
        )
        rows = result.all()
        out = []
        for we, w in rows:
            sets_info = [
                {"reps": s.reps, "weight": float(s.weight_kg) if s.weight_kg else None, "comment": s.comment}
                for s in we.sets
            ]
            out.append({
                "date": w.date.isoformat() if w.date else None,
                "workout_id": w.id,
                "sets": sets_info,
                "volume_kg": float(we.volume_kg) if we.volume_kg else 0,
            })
        return out


def calculate_1rm(reps: int, weight: float) -> float:
    """Расчёт одноповторного максимума по формуле Эпли: 1RM = weight × (1 + reps / 30)."""
    if reps <= 0:
        return float(weight)
    if reps == 1:
        return float(weight)
    return weight * (1 + reps / 30.0)


async def check_and_save_records(workout_id: int) -> list[dict]:
    """
    Проверяет новые рекорды после завершения тренировки.
    Проверяет max_weight, max_volume, max_1rm. Сохраняет в records при улучшении.
    Возвращает список новых рекордов с exercise_name, record_type, value, previous_value.
    """
    async with get_session() as session:
        result = await session.execute(
            select(Workout)
            .where(Workout.id == workout_id)
            .options(
                selectinload(Workout.workout_exercises).selectinload(WorkoutExercise.sets),
                selectinload(Workout.workout_exercises).selectinload(WorkoutExercise.exercise),
            )
        )
        workout = result.scalar_one_or_none()
        if not workout:
            return []
        user_id = workout.user_id
        new_records = []

        for we in workout.workout_exercises:
            if not we.exercise:
                continue
            ex_id = we.exercise_id
            ex_name = we.exercise.name

            # Текущие значения по тренировке
            max_weight = None
            for s in we.sets:
                if s.weight_kg is not None:
                    w = float(s.weight_kg)
                    if max_weight is None or w > max_weight:
                        max_weight = w
            volume = float(we.volume_kg or 0)
            max_1rm = None
            for s in we.sets:
                if s.reps is not None and s.weight_kg is not None:
                    r1 = calculate_1rm(s.reps, float(s.weight_kg))
                    if max_1rm is None or r1 > max_1rm:
                        max_1rm = r1

            # Текущие рекорды пользователя по этому упражнению
            rec_result = await session.execute(
                select(Record).where(
                    Record.user_id == user_id,
                    Record.exercise_id == ex_id,
                )
            )
            existing = {r.record_type: r for r in rec_result.scalars().all()}

            for record_type, value in [
                ("max_weight", max_weight),
                ("max_volume", volume),
                ("max_1rm", max_1rm),
            ]:
                if value is None:
                    continue
                prev = existing.get(record_type)
                prev_val = float(prev.value) if prev else None
                if prev_val is None or value > prev_val:
                    rec = Record(
                        user_id=user_id,
                        exercise_id=ex_id,
                        record_type=record_type,
                        value=Decimal(str(value)),
                        workout_id=workout_id,
                    )
                    session.add(rec)
                    new_records.append({
                        "exercise_name": ex_name,
                        "record_type": record_type,
                        "value": value,
                        "previous_value": prev_val,
                    })

        await session.flush()
        return new_records


async def get_user_records(
    user_id: int,
    exercise_id: Optional[int] = None,
) -> list[Record]:
    """Получает рекорды пользователя. Если exercise_id указан — только для этого упражнения."""
    async with get_session() as session:
        stmt = select(Record).where(Record.user_id == user_id)
        if exercise_id is not None:
            stmt = stmt.where(Record.exercise_id == exercise_id)
        stmt = stmt.order_by(Record.achieved_at.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_week_comparison(user_id: int) -> dict:
    """
    Сравнение с прошлой неделей.
    Возвращает current_week, previous_week (workouts_count, total_volume_kg, exercises_count), diff_percent.
    """
    today = date.today()
    # Текущая неделя: понедельник — сегодня
    start_cw = today - timedelta(days=today.weekday())
    end_cw = today
    # Прошлая неделя
    start_pw = start_cw - timedelta(days=7)
    end_pw = start_cw - timedelta(days=1)

    async with get_session() as session:
        async def _week_stats(start: date, end: date) -> dict:
            stmt = (
                select(Workout)
                .where(
                    Workout.user_id == user_id,
                    Workout.date >= start,
                    Workout.date <= end,
                )
                .options(selectinload(Workout.workout_exercises))
            )
            r = await session.execute(stmt)
            workouts = list(r.scalars().all())
            total_vol = sum(float(w.total_volume_kg or 0) for w in workouts)
            ex_count = sum(len(w.workout_exercises) for w in workouts)
            return {
                "workouts_count": len(workouts),
                "total_volume_kg": total_vol,
                "exercises_count": ex_count,
            }

        current_week = await _week_stats(start_cw, end_cw)
        previous_week = await _week_stats(start_pw, end_pw)

    prev_vol = previous_week["total_volume_kg"] or 0
    curr_vol = current_week["total_volume_kg"] or 0
    if prev_vol == 0:
        diff_percent = 100.0 if curr_vol > 0 else 0.0
    else:
        diff_percent = ((curr_vol - prev_vol) / prev_vol) * 100

    return {
        "current_week": current_week,
        "previous_week": previous_week,
        "diff_percent": round(diff_percent, 1),
    }


# ----- Обратная совместимость: функции с session (для существующих вызовов) -----


async def get_or_create_user_with_session(
    session: AsyncSession, telegram_id: int, username: Optional[str] = None
) -> User:
    """Вариант get_or_create_user с передачей сессии (для использования в одной транзакции)."""
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_id=telegram_id, username=username)
        session.add(user)
        await session.flush()
        await session.refresh(user)
    return user


async def create_workout_with_session(
    session: AsyncSession,
    user_id: int,
    workout_date: Optional[date] = None,
    program_id: Optional[int] = None,
    comment: Optional[str] = None,
) -> Workout:
    """Вариант create_workout с передачей сессии."""
    workout_date = workout_date or date.today()
    workout = Workout(user_id=user_id, date=workout_date, program_id=program_id, comment=comment)
    session.add(workout)
    await session.flush()
    await session.refresh(workout)
    return workout


async def add_workout_sets_with_session(
    session: AsyncSession,
    workout_id: int,
    sets: list[dict],
    user_id: Optional[int] = None,
) -> None:
    """Добавляет подходы по exercise_name (группирует по упражнению). С передачей сессии."""
    by_exercise: dict[str, list[dict]] = {}
    for s in sets:
        name = (s.get("exercise_name") or "").strip() or "Упражнение"
        by_exercise.setdefault(name, []).append(s)
    order_num = 0
    for exercise_name, exercise_sets in by_exercise.items():
        ex_result = await session.execute(select(Exercise).where(Exercise.name == exercise_name))
        ex = ex_result.scalar_one_or_none()
        if ex is None:
            ex = Exercise(
                name=exercise_name,
                is_custom=user_id is not None,
                created_by=user_id,
            )
            session.add(ex)
            await session.flush()
            await session.refresh(ex)
        we = WorkoutExercise(workout_id=workout_id, exercise_id=ex.id, order_num=order_num)
        session.add(we)
        await session.flush()
        volume = Decimal("0")
        for i, s in enumerate(exercise_sets):
            reps = s.get("reps")
            weight_kg = s.get("weight_kg")
            if weight_kg is not None and reps is not None:
                volume += Decimal(str(weight_kg)) * int(reps)
            set_row = Set(
                workout_exercise_id=we.id,
                set_number=i + 1,
                reps=reps,
                weight_kg=Decimal(str(weight_kg)) if weight_kg is not None else None,
            )
            session.add(set_row)
        we.volume_kg = volume if volume else None
        order_num += 1
    await session.flush()


async def get_workout_summary_with_session(session: AsyncSession, workout_id: int) -> dict:
    """Возвращает сводку по тренировке (для обратной совместимости с передачей session)."""
    result = await session.execute(
        select(Workout)
        .where(Workout.id == workout_id)
        .options(selectinload(Workout.workout_exercises).selectinload(WorkoutExercise.sets))
    )
    workout = result.scalar_one_or_none()
    if not workout:
        return {"date": None, "exercises_count": 0, "sets_count": 0, "total_volume_kg": 0}
    exercises_count = len(workout.workout_exercises)
    sets_count = sum(len(we.sets) for we in workout.workout_exercises)
    total_volume = sum((we.volume_kg or Decimal("0")) for we in workout.workout_exercises)
    return {
        "date": workout.date,
        "exercises_count": exercises_count,
        "sets_count": sets_count,
        "total_volume_kg": float(total_volume),
    }


async def get_user_programs_with_session(session: AsyncSession, user_id: int) -> list[Program]:
    """Вариант get_user_programs с передачей сессии."""
    result = await session.execute(
        select(Program).where(Program.user_id == user_id).order_by(Program.created_at.desc())
    )
    return list(result.scalars().all())
