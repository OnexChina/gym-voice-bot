"""Создание и выбор программ тренировок."""

from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

from bot.database.engine import get_session
from bot.database.crud import get_or_create_user, get_user_programs, create_program

router = Router()


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
