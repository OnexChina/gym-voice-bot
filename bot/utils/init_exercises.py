"""
Инициализация упражнений при старте (деплой на Railway).
- Создаёт data/ и exercises.json при отсутствии.
- Заполняет таблицу exercises из data/exercises.json, если записей ещё нет.
"""

import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy import delete, select

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


async def seed_exercises_from_json(force: bool = False) -> tuple[int, int]:
    """
    Загружает упражнения из JSON в БД.
    Если упражнение с таким name уже есть — обновляет synonyms, muscle_groups, equipment, name_en.
    При --force: удаляет из БД все упражнения с именами из JSON и заново вставляет (полная перезагрузка).
    Возвращает (added, updated).
    """
    ensure_exercises_file()
    if not EXERCISES_FILE.exists():
        return 0, 0
    try:
        data = json.loads(EXERCISES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Could not load exercises.json: %s", e)
        return 0, 0
    if not isinstance(data, list) or not data:
        return 0, 0

    added = 0
    updated = 0
    async with get_session() as session:
        if force:
            names_from_json = [(item.get("name") or "").strip() for item in data if (item.get("name") or "").strip()]
            if names_from_json:
                await session.execute(delete(Exercise).where(Exercise.name.in_(names_from_json)))
                await session.flush()

        for item in data:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            result = await session.execute(select(Exercise).where(Exercise.name == name))
            ex = result.scalar_one_or_none()
            name_en = (item.get("name_en") or "").strip() or None
            synonyms = item.get("synonyms") or None
            muscle_groups = item.get("muscle_groups") or None
            equipment = (item.get("equipment") or "").strip() or None

            if ex is None:
                ex = Exercise(
                    name=name,
                    name_en=name_en,
                    synonyms=synonyms,
                    muscle_groups=muscle_groups,
                    equipment=equipment,
                    is_custom=False,
                    created_by=None,
                )
                session.add(ex)
                added += 1
            else:
                ex.name_en = name_en
                ex.synonyms = synonyms
                ex.muscle_groups = muscle_groups
                ex.equipment = equipment
                updated += 1
            await session.flush()
    return added, updated


async def main() -> None:
    import sys
    force = "--force" in sys.argv
    logger.info("Running init_exercises...")
    await init_db()
    ensure_exercises_file()
    added, updated = await seed_exercises_from_json(force=force)
    logger.info("init_exercises done. Added %s, updated %s exercises from JSON.", added, updated)


if __name__ == "__main__":
    asyncio.run(main())
