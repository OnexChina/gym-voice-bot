"""Подключение к PostgreSQL через asyncpg."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import settings
from bot.database.models import Base

# Создание async engine
engine = create_async_engine(
    settings.database_url,
    echo=False,  # True для отладки SQL
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)

# Session maker
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Обратная совместимость
async_session_maker = AsyncSessionLocal


async def init_db():
    """Создание всех таблиц в БД."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Закрытие пула соединений."""
    await engine.dispose()


@asynccontextmanager
async def get_session():
    """Async context manager для сессии БД."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
