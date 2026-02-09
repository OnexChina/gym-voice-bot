"""Создание и выбор программ тренировок."""

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command

from bot.database.engine import get_session
from bot.database.crud import get_or_create_user, get_user_programs, create_program
from bot.keyboards.menu import main_menu

router = Router()


class ProgramStates(StatesGroup):
    waiting_name = State()


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


@router.message(ProgramStates.waiting_name)
async def process_program_name(message: Message, state: FSMContext):
    """Обработать введённое название программы и создать её."""
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
    
    await state.clear()
    await message.answer(
        f"✅ Программа «<b>{name}</b>» создана!\n\n"
        f"ID программы: {program.id}\n\n"
        "Теперь ты можешь выбрать её при начале тренировки.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )
