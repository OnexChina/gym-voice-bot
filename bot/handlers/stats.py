"""Статистика и аналитика тренировок."""

from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

from bot.database.crud import get_or_create_user, get_user_workouts
from bot.services.analytics import get_volume_stats, get_pr_stats

router = Router()


@router.message(Command("stats"))
@router.message(F.text == "📊 Статистика")
async def cmd_stats(message: Message) -> None:
    await get_or_create_user(message.from_user.id, message.from_user.username)
    workouts = await get_user_workouts(message.from_user.id, limit=30)
    if not workouts:
        await message.answer("Пока нет тренировок. Начните логировать — голосом или текстом.")
        return
    volume_text = await get_volume_stats(workouts)
    pr_text = await get_pr_stats(workouts)
    await message.answer(f"📊 Статистика (последние {len(workouts)} тренировок)\n\n{volume_text}\n\n{pr_text}")
