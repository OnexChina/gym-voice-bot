"""Создание и выбор программ тренировок."""

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.filters import Command

from bot.database.engine import get_session
from bot.database.crud import (
    create_program,
    delete_program,
    get_or_create_user,
    get_user_custom_exercises,
    get_user_programs,
    update_program_exercises,
)
from bot.keyboards.menu import main_menu
from bot.services.exercises import load_exercises

router = Router()

# Категории для группировки (первая группа мышц из exercises.json)
CATEGORY_ORDER = ["ноги", "спина", "грудь", "плечи", "руки", "трицепс", "бицепс", "пресс", "кора"]


class ProgramStates(StatesGroup):
    waiting_name = State()
    adding_exercises = State()


@router.message(Command("programs"))
async def cmd_programs(message: Message) -> None:
    async with get_session() as session:
        await get_or_create_user(session, message.from_user.id, message.from_user.username)
        programs = await get_user_programs(session, message.from_user.id)
    if not programs:
        await message.answer("У вас пока нет программ. Создайте первую: /newprogram название")
        return
    lines = [f"• {p.name}" for p in programs]
    await message.answer("Ваши программы:\n" + "\n".join(lines))


@router.message(Command("newprogram"), F.text)
async def cmd_new_program(message: Message) -> None:
    name = message.text.replace("/newprogram", "").strip() or "Новая программа"
    async with get_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        await create_program(session, user.telegram_id, name, exercise_ids=[])
    await message.answer(f"Программа «{name}» создана.")


@router.callback_query(F.data.in_(["create_program", "program:new"]))
async def start_create_program(callback: CallbackQuery, state: FSMContext):
    """Начать создание программы: запросить название."""
    await state.set_state(ProgramStates.waiting_name)
    await callback.message.answer(
        "📝 Как назовём программу?\n\n"
        "Например: «БЛОК 1 первый месяц» или «Грудь+Трицепс»"
    )
    await callback.answer()


def _program_edit_keyboard() -> InlineKeyboardMarkup:
    """Кнопки при редактировании программы: Добавить упражнение, Готово, Удалить."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить упражнение", callback_data="create_program_add_ex")],
        [
            InlineKeyboardButton(text="✅ Готово", callback_data="create_program_done"),
            InlineKeyboardButton(text="🗑 Удалить программу", callback_data="create_program_delete"),
        ],
    ])


@router.message(ProgramStates.waiting_name)
async def process_program_name(message: Message, state: FSMContext):
    """Обработать введённое название программы и создать её, затем — добавление упражнений."""
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым. Введи название программы:")
        return

    if len(name) > 100:
        await message.answer("Название слишком длинное (максимум 100 символов). Введи короче:")
        return

    async with get_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        program = await create_program(session, user.telegram_id, name, exercise_ids=[])

    await state.set_state(ProgramStates.adding_exercises)
    await state.update_data(program_id=program.id, program_name=name, program_exercise_ids=[])
    await message.answer(
        f"✅ Программа «<b>{name}</b>» создана!\n\nДобавь упражнения в программу:",
        parse_mode="HTML",
        reply_markup=_program_edit_keyboard(),
    )


@router.callback_query(F.data == "create_program_add_ex", ProgramStates.adding_exercises)
async def program_show_categories(callback: CallbackQuery, state: FSMContext):
    """Показать категории мышц для выбора упражнения."""
    exercises = await load_exercises()
    seen = set()
    for ex in exercises:
        for g in (ex.get("muscle_groups") or []):
            g_lower = (g or "").strip().lower()
            if g_lower and g_lower not in seen:
                seen.add(g_lower)
    order = [c for c in CATEGORY_ORDER if c in seen]
    rest = sorted(seen - set(order))
    cats = order + rest
    buttons = [[InlineKeyboardButton(text=c.capitalize(), callback_data=f"create_program_cat:{c}")] for c in cats]
    buttons.append([InlineKeyboardButton(text="➕ Мои упражнения", callback_data="create_program_cat:custom")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="create_program_back")])
    await callback.message.edit_text(
        "Выбери категорию упражнений:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data == "create_program_back", ProgramStates.adding_exercises)
async def program_back_to_edit(callback: CallbackQuery, state: FSMContext):
    """Вернуться к экрану Добавить/Готово/Удалить."""
    data = await state.get_data()
    name = data.get("program_name") or "Программа"
    await callback.message.edit_text(
        f"Программа «<b>{name}</b>». Добавь упражнения или нажми Готово.",
        parse_mode="HTML",
        reply_markup=_program_edit_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("create_program_cat:"), ProgramStates.adding_exercises)
async def program_show_exercises(callback: CallbackQuery, state: FSMContext):
    """Показать упражнения выбранной категории или «Мои упражнения»."""
    cat = callback.data.split(":", 1)[1].strip().lower()
    if cat == "custom":
        custom = await get_user_custom_exercises(callback.from_user.id)
        if not custom:
            await callback.message.edit_text(
                "У тебя пока нет своих упражнений. Добавь их через «➕ Добавить упражнение» в меню.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="create_program_add_ex")],
                ]),
            )
        else:
            buttons = []
            for i, ex in enumerate(custom):
                name = (ex.name or "Упражнение")[:35]
                if len(ex.name or "") > 35:
                    name = (ex.name or "")[:32] + "…"
                buttons.append([InlineKeyboardButton(text=name, callback_data=f"create_program_pick_custom:{i}")])
            buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="create_program_add_ex")])
            await callback.message.edit_text(
                "Мои упражнения. Выбери:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            )
        await callback.answer()
        return
    exercises = await load_exercises()
    buttons = []
    for i, ex in enumerate(exercises):
        groups = [((g or "").lower()) for g in (ex.get("muscle_groups") or [])]
        if cat in groups:
            name = ex.get("name") or "Упражнение"
            if len(name) > 35:
                name = name[:32] + "…"
            buttons.append([InlineKeyboardButton(text=name, callback_data=f"create_program_pick:{i}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="create_program_add_ex")])
    await callback.message.edit_text(
        f"Категория «{cat.capitalize()}». Выбери упражнение:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("create_program_pick_custom:"), ProgramStates.adding_exercises)
async def program_add_custom_exercise(callback: CallbackQuery, state: FSMContext):
    """Добавить своё упражнение в программу."""
    try:
        idx = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    data = await state.get_data()
    program_id = data.get("program_id")
    current = list(data.get("program_exercise_ids") or [])
    if program_id is None:
        await callback.answer("Программа не найдена", show_alert=True)
        return
    custom = await get_user_custom_exercises(callback.from_user.id)
    if idx < 0 or idx >= len(custom):
        await callback.answer("Упражнение не найдено", show_alert=True)
        return
    ex = custom[idx]
    name = ex.name or "Упражнение"
    current.append({"exercise_id": -1, "name": name, "order": len(current) + 1})
    await update_program_exercises(program_id, current)
    await state.update_data(program_exercise_ids=current)
    await callback.message.edit_text(
        f"✅ Добавлено: <b>{name}</b>. Добавь ещё или нажми Готово.",
        parse_mode="HTML",
        reply_markup=_program_edit_keyboard(),
    )
    await callback.answer("Добавлено!")


@router.callback_query(F.data.startswith("create_program_pick:"), ProgramStates.adding_exercises)
async def program_add_exercise(callback: CallbackQuery, state: FSMContext):
    """Добавить выбранное упражнение в программу."""
    try:
        idx = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    data = await state.get_data()
    program_id = data.get("program_id")
    current = list(data.get("program_exercise_ids") or [])
    if program_id is None:
        await callback.answer("Программа не найдена", show_alert=True)
        return
    exercises = await load_exercises()
    if idx < 0 or idx >= len(exercises):
        await callback.answer("Упражнение не найдено", show_alert=True)
        return
    ex = exercises[idx]
    name = ex.get("name") or "Упражнение"
    current.append({"exercise_id": idx, "name": name, "order": len(current) + 1})
    await update_program_exercises(program_id, current)
    await state.update_data(program_exercise_ids=current)
    await callback.message.edit_text(
        f"✅ Добавлено: <b>{name}</b>. Добавь ещё или нажми Готово.",
        parse_mode="HTML",
        reply_markup=_program_edit_keyboard(),
    )
    await callback.answer("Добавлено!")


@router.callback_query(F.data == "create_program_done", ProgramStates.adding_exercises)
async def program_done(callback: CallbackQuery, state: FSMContext):
    """Завершить редактирование программы."""
    await state.clear()
    await callback.message.edit_text("✅ Программа сохранена. Выбери её при начале тренировки.")
    await callback.message.answer("Главное меню:", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "create_program_delete", ProgramStates.adding_exercises)
async def program_ask_delete(callback: CallbackQuery, state: FSMContext):
    """Спросить подтверждение удаления программы."""
    await callback.message.edit_text(
        "Точно удалить программу? Это нельзя отменить.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Да, удалить", callback_data="create_program_delete_yes")],
            [InlineKeyboardButton(text="Нет", callback_data="create_program_back")],
        ]),
    )
    await callback.answer()
@router.callback_query(F.data == "create_program_delete_yes", ProgramStates.adding_exercises)
async def program_do_delete(callback: CallbackQuery, state: FSMContext):
    """Удалить программу и выйти."""
    data = await state.get_data()
    program_id = data.get("program_id")
    await state.clear()
    if program_id is not None:
        await delete_program(program_id)
    await callback.message.edit_text("Программа удалена.")
    await callback.message.answer("Главное меню:", reply_markup=main_menu())
    await callback.answer()
