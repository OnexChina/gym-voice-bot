"""Тест подключения к БД."""

import asyncio
from bot.database.engine import init_db, get_session
from bot.database.crud import get_or_create_user


async def test():
    print("🔧 Инициализация БД...")
    await init_db()
    print("✅ БД инициализирована")

    print("\n🧪 Тест создания пользователя...")
    async with get_session() as session:
        user = await get_or_create_user(session, 12345, "test_user")
    print(f"✅ Пользователь создан: {user.telegram_id}")

    print("\n🎉 Всё работает!")


if __name__ == "__main__":
    asyncio.run(test())
