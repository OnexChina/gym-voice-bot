from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot.keyboards.menu import main_menu, program_selection
from bot.database.engine import get_session
from bot.database.crud import (
    get_or_create_user,
    get_user_programs,
    get_user_workouts,
)
from bot.services.analytics import get_volume_stats, get_pr_stats

router = Router()


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
async def start_workout(message: Message):
    """Начать тренировку: выбрать программу или freestyle."""
    async with get_session() as session:
        await get_or_create_user(session, message.from_user.id, message.from_user.username)
        programs = await get_user_programs(session, message.from_user.id)

    program_list = [{"id": p.id, "name": p.name} for p in programs]
    await message.answer(
        "Выбери программу или начни свободную тренировку:",
        reply_markup=program_selection(program_list),
    )


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
    """Показать основную статистику по последним тренировкам."""
    async with get_session() as session:
        await get_or_create_user(session, message.from_user.id, message.from_user.username)

    workouts = await get_user_workouts(message.from_user.id, limit=30)
    if not workouts:
        await message.answer("Пока нет тренировок. Начни логировать — голосом или текстом.")
        return

    volume_text = await get_volume_stats(workouts)
    pr_text = await get_pr_stats(workouts)
    await message.answer(
        f"📊 Статистика (последние {len(workouts)} тренировок)\n\n"
        f"{volume_text}\n\n{pr_text}"
    )


@router.message(F.text == "➕ Добавить упражнение")
async def add_exercise(message: Message):
    """Пока только заглушка для добавления упражнения."""
    await message.answer("➕ Добавление кастомных упражнений пока в разработке.")


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
    """Показать главное меню, не прерывая тренировку."""
    # Состояние не очищаем → можно смотреть меню/статистику и продолжать текущую тренировку
    await message.answer("🏠 Главное меню", reply_markup=main_menu())
