"""
Инициализация упражнений при старте (деплой на Railway).
- Создаёт data/ и exercises.json при отсутствии.
- Заполняет таблицу exercises из data/exercises.json, если записей ещё нет.
"""

import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy import select

from bot.database.engine import get_session, init_db
from bot.database.models import Exercise

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EXERCISES_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "exercises.json"


def ensure_exercises_file() -> None:
    """Создаёт data/ и пустой exercises.json, если их нет."""
    EXERCISES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not EXERCISES_FILE.exists():
        EXERCISES_FILE.write_text("[]", encoding="utf-8")
        logger.info("Created %s", EXERCISES_FILE)


async def seed_exercises_from_json() -> int:
    """Загружает упражнения из JSON в БД (только новые по name). Возвращает количество добавленных."""
    ensure_exercises_file()
    if not EXERCISES_FILE.exists():
        return 0
    try:
        data = json.loads(EXERCISES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Could not load exercises.json: %s", e)
        return 0
    if not isinstance(data, list) or not data:
        return 0

    added = 0
    async with get_session() as session:
        for item in data:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            result = await session.execute(select(Exercise).where(Exercise.name == name))
            if result.scalar_one_or_none() is not None:
                continue
            ex = Exercise(
                name=name,
                name_en=(item.get("name_en") or "").strip() or None,
                synonyms=item.get("synonyms") or None,
                muscle_groups=item.get("muscle_groups") or None,
                equipment=(item.get("equipment") or "").strip() or None,
                is_custom=False,
                created_by=None,
            )
            session.add(ex)
            await session.flush()
            added += 1
    return added


async def main() -> None:
    logger.info("Running init_exercises...")
    await init_db()
    ensure_exercises_file()
    n = await seed_exercises_from_json()
    logger.info("init_exercises done. Added %s exercises from JSON.", n)


if __name__ == "__main__":
    asyncio.run(main())
