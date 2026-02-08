"""Команда /start и главное меню."""

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.database.crud import get_or_create_user, get_user_programs
from bot.keyboards.menu import main_menu, program_selection

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    """
    Обработчик команды /start
    - Создаёт пользователя в БД если его нет
    - Показывает приветствие
    - Выводит главное меню
    """
    await get_or_create_user(message.from_user.id, message.from_user.username)

    welcome_text = f"""
👋 Привет, {message.from_user.first_name}!

Я твой голосовой помощник для тренировок в зале.

🎤 Просто говори или пиши что сделал:
- "Жим лёжа 10 на 80, 8 на 85"
- "Разводка 3 по 12 на 20"

Я сам пойму, запишу и посчитаю объёмы! 💪

Начнём?
"""
    await message.answer(welcome_text, reply_markup=main_menu())


@router.message(F.text == "🏋️ Начать тренировку")
async def start_workout(message: Message):
    """
    Начало новой тренировки
    - Показывает выбор программы или freestyle
    """
    await get_or_create_user(message.from_user.id, message.from_user.username)
    programs = await get_user_programs(message.from_user.id)

    program_list = [{"id": p.id, "name": p.name} for p in programs]
    await message.answer(
        "Выбери программу или начни свободную тренировку:",
        reply_markup=program_selection(program_list),
    )


@router.message(F.text == "📋 Мои программы")
async def show_programs(message: Message):
    """Показывает список программ пользователя"""
    await message.answer("Твои программы тренировок")


@router.message(F.text == "📊 Статистика")
async def show_stats(message: Message):
    """Показывает меню статистики"""
    await message.answer("Статистика")


@router.message(F.text == "➕ Добавить упражнение")
async def add_exercise(message: Message):
    """Добавление кастомного упражнения"""
    await message.answer("Напиши название нового упражнения")


@router.message(F.text == "⚙️ Настройки")
async def show_settings(message: Message):
    """Показывает настройки"""
    await message.answer("Настройки")
