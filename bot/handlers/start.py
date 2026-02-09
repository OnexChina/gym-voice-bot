from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from bot.keyboards.menu import main_menu
from bot.database.engine import get_session
from bot.database.crud import get_or_create_user

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message):
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
    from bot.keyboards.menu import program_selection
    programs = []
    await message.answer("Выбери программу или начни свободную тренировку", reply_markup=program_selection(programs))

@router.message(F.text == "📋 Мои программы")
async def show_programs(message: Message):
    await message.answer("📋 Твои программы тренировок (в разработке)")

@router.message(F.text == "📊 Статистика")
async def show_stats(message: Message):
    await message.answer("📊 Статистика (в разработке)")

@router.message(F.text == "➕ Добавить упражнение")
async def add_exercise(message: Message):
    await message.answer("➕ Добавление упражнения (в разработке)")

@router.message(F.text == "⚙️ Настройки")
async def show_settings(message: Message):
    await message.answer("⚙️ Настройки (в разработке)")

@router.message(F.text == "◀️ Главное меню")
async def back_to_main(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🏠 Главное меню", reply_markup=main_menu())
