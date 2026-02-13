import re
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.keyboards.menu import get_main_keyboard, main_menu, program_selection, workout_menu
from bot.database.engine import get_session
from bot.database.crud import (
    add_workout_sets,
    create_custom_exercise,
    create_workout,
    get_exercise_by_name,
    get_or_create_user,
    get_user_programs,
)
from bot.services.analytics import format_weekly_stats
from bot.handlers.workout import WorkoutStates

router = Router()


class AddExerciseStates(StatesGroup):
    waiting_name = State()
    waiting_muscle_group = State()
    waiting_sets = State()


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Старт: создать пользователя (если нужно) и показать главное меню."""
    async with get_session() as session:
        await get_or_create_user(session, message.from_user.id, message.from_user.username)

    welcome_text = f"""👋 Привет, {message.from_user.first_name}!

Я твой голосовой помощник для тренировок в зале.

🎤 Просто говори или пиши что сделал:
- "Жим лёжа 10 на 80, 8 на 85"
- "Разводка 3 по 12 на 20"

Я сам пойму, запишу и посчитаю объёмы! 💪

Начнём?"""

    await message.answer(welcome_text, reply_markup=main_menu())


@router.message(F.text == "🏋️ Начать тренировку")
async def start_workout(message: Message, state: FSMContext):
    """Начать тренировку: если уже есть активная — спросить продолжить или новую."""
    data = await state.get_data()
    workout_id = (data.get("workout") or {}).get("id")
    if workout_id:
        await message.answer(
            "У тебя уже есть активная тренировка 💪 Продолжить её?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Продолжить", callback_data="workout_continue")],
                [InlineKeyboardButton(text="Начать новую", callback_data="workout_start_new")],
            ]),
        )
        return
    async with get_session() as session:
        await get_or_create_user(session, message.from_user.id, message.from_user.username)
        programs = await get_user_programs(session, message.from_user.id)

    program_list = [{"id": p.id, "name": p.name} for p in programs]
    await message.answer(
        "Выбери программу или начни свободную тренировку:",
        reply_markup=program_selection(program_list),
    )


@router.callback_query(F.data == "workout_continue")
async def workout_continue(callback: CallbackQuery, state: FSMContext):
    """Продолжить текущую тренировку."""
    await callback.message.edit_text("Продолжаем. Говори или пиши упражнения и подходы.")
    await callback.message.answer("Меню тренировки:", reply_markup=workout_menu())
    await callback.answer()


@router.callback_query(F.data == "workout_start_new")
async def workout_start_new(callback: CallbackQuery, state: FSMContext):
    """Начать новую тренировку (текущая остаётся в БД незавершённой)."""
    async with get_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        workout = await create_workout(session, user.telegram_id, program_id=None)
    await state.update_data(workout={"id": workout.id, "date": str(workout.date)})
    await state.set_state(WorkoutStates.active)
    await callback.message.edit_text("Новая тренировка начата. Говори или пиши упражнения и подходы.")
    await callback.message.answer("Меню тренировки:", reply_markup=workout_menu())
    await callback.answer()


@router.message(F.text == "📋 Мои программы")
async def show_programs(message: Message):
    """Показать список программ пользователя с кнопкой создания."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    
    async with get_session() as session:
        await get_or_create_user(session, message.from_user.id, message.from_user.username)
        programs = await get_user_programs(session, message.from_user.id)

    if not programs:
        await message.answer(
            "У тебя пока нет программ.\n\nСоздать первую программу?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать программу", callback_data="create_program")],
            ]),
        )
        return

    lines = [f"• {p.name}" for p in programs]
    await message.answer(
        "📋 Твои программы:\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать программу", callback_data="create_program")],
        ]),
    )


@router.message(F.text == "📊 Статистика")
async def show_stats(message: Message):
    """Статистика по неделям и рекорды 1ПМ."""
    async with get_session() as session:
        await get_or_create_user(session, message.from_user.id, message.from_user.username)

    text = await format_weekly_stats(message.from_user.id)
    await message.answer(f"📊 Статистика\n\n{text}")


def _muscle_group_keyboard() -> InlineKeyboardMarkup:
    """Кнопки выбора группы мышц для нового упражнения."""
    groups = ["Грудь", "Спина", "Ноги", "Плечи", "Руки", "Пресс", "Другое"]
    buttons = [
        [InlineKeyboardButton(text=g, callback_data=f"add_exercise_muscle:{g}") for g in groups[:3]],
        [InlineKeyboardButton(text=g, callback_data=f"add_exercise_muscle:{g}") for g in groups[3:6]],
        [InlineKeyboardButton(text=groups[6], callback_data=f"add_exercise_muscle:{groups[6]}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _parse_weight_reps(text: str) -> tuple[float | None, int | None]:
    """Парсит «80 кг × 10» или «80 x 10» → (80.0, 10)."""
    text = (text or "").strip().replace(",", ".")
    numbers = re.findall(r"\d+\.?\d*", text)
    if len(numbers) >= 2:
        try:
            return float(numbers[0]), int(numbers[1])
        except (ValueError, TypeError):
            pass
    if len(numbers) == 1:
        try:
            return None, int(numbers[0])
        except (ValueError, TypeError):
            pass
    return None, None


@router.message(F.text == "➕ Добавить упражнение")
async def add_exercise(message: Message, state: FSMContext):
    """Начать добавление кастомного упражнения (нужна активная тренировка)."""
    data = await state.get_data()
    workout_id = (data.get("workout") or {}).get("id")
    if not workout_id:
        await message.answer("Сначала начни тренировку (🏋️ Начать тренировку).")
        return
    await state.set_state(AddExerciseStates.waiting_name)
    await state.update_data(add_exercise_workout_id=workout_id)
    await message.answer("Введи название упражнения:")


@router.message(F.text, AddExerciseStates.waiting_name)
async def add_exercise_name_entered(message: Message, state: FSMContext):
    """Название введено — спрашиваем группу мышц."""
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым. Введи название:")
        return
    await state.update_data(add_exercise_name=name)
    await state.set_state(AddExerciseStates.waiting_muscle_group)
    await message.answer("Какая группа мышц?", reply_markup=_muscle_group_keyboard())


@router.callback_query(F.data.startswith("add_exercise_muscle:"), AddExerciseStates.waiting_muscle_group)
async def add_exercise_muscle_selected(callback: CallbackQuery, state: FSMContext):
    """Группа мышц выбрана — спрашиваем вес и повторения."""
    muscle = callback.data.split(":", 1)[1]
    await state.update_data(add_exercise_muscle=muscle)
    await state.set_state(AddExerciseStates.waiting_sets)
    await callback.message.edit_text(f"Группа: {muscle}. Теперь введи вес и повторения, например: 80 кг × 10 или 80 x 10")
    await callback.answer()


@router.message(F.text, AddExerciseStates.waiting_sets)
async def add_exercise_sets_entered(message: Message, state: FSMContext):
    """Вес и повторения введены — создаём упражнение и записываем подход."""
    data = await state.get_data()
    name = data.get("add_exercise_name") or "Упражнение"
    muscle = data.get("add_exercise_muscle") or "Другое"
    workout_id = data.get("add_exercise_workout_id")
    if not workout_id:
        await message.answer("Тренировка не найдена. Начни тренировку заново.")
        await state.clear()
        return

    weight_kg, reps = _parse_weight_reps(message.text or "")
    if reps is None and weight_kg is None:
        await message.answer("Не понял. Напиши, например: 80 кг × 10 или 80 x 10")
        return

    existing = await get_exercise_by_name(name)
    if not existing:
        await create_custom_exercise(
            user_id=message.from_user.id,
            name=name,
            muscle_groups=[muscle],
            equipment="—",
            synonyms=None,
        )
    async with get_session() as session:
        await add_workout_sets(
            session,
            workout_id,
            [{"exercise_name": name, "reps": reps, "weight_kg": weight_kg}],
            user_id=message.from_user.id,
        )

    workout_snapshot = data.get("workout")
    await state.clear()
    if workout_snapshot:
        await state.update_data(workout=workout_snapshot)
        await state.set_state(WorkoutStates.active)
    line = f"⚖️ {weight_kg} кг × 🔁 {reps} повт" if weight_kg is not None and reps is not None else f"🔁 {reps} повт" if reps is not None else "—"
    await message.answer(
        f"✅ Упражнение добавлено и подход записан!\n\n{line}",
        reply_markup=get_main_keyboard(bool(workout_id)),
    )


@router.message(F.text == "⚙️ Настройки")
async def show_settings(message: Message):
    """Показать текущие настройки пользователя (язык, единицы)."""
    async with get_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)

    settings = user.settings or {}
    units = settings.get("units", "kg")
    lang = settings.get("language", "ru")

    units_label = "Килограммы" if units == "kg" else "Фунты"
    lang_label = "Русский" if lang == "ru" else "English"

    await message.answer(
        "⚙️ <b>Настройки</b>\n\n"
        f"🇷🇺 Язык: {lang_label}\n"
        f"⚖️ Единицы: {units_label}\n",
        parse_mode="HTML",
    )


@router.message(F.text == "◀️ Главное меню")
async def back_to_main(message: Message, state: FSMContext):
    """Показать главное меню; если есть активная тренировка — кнопка «Закончить тренировку» остаётся видимой."""
    data = await state.get_data()
    workout_active = bool((data.get("workout") or {}).get("id"))
    await message.answer("🏠 Главное меню", reply_markup=get_main_keyboard(workout_active))
