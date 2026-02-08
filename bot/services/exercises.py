"""Загрузка упражнений из JSON и поиск по названию/синонимам."""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

EXERCISES_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "exercises.json"
_exercises_cache: Optional[List[Dict]] = None


async def load_exercises() -> List[Dict]:
    """
    Загружает упражнения из JSON файла.
    Кеширует результат в памяти.

    Возвращает список словарей с упражнениями.
    """
    global _exercises_cache

    if _exercises_cache is not None:
        return _exercises_cache

    if not EXERCISES_FILE.exists():
        logger.warning("Exercises file not found: %s", EXERCISES_FILE)
        _exercises_cache = []
        return _exercises_cache

    try:
        with open(EXERCISES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _exercises_cache = data if isinstance(data, list) else []
        return _exercises_cache
    except Exception as e:
        logger.warning("Failed to load exercises.json: %s", e)
        _exercises_cache = []
        return _exercises_cache


async def search_exercise(query: str, exercises: Optional[List[Dict]] = None) -> List[Dict]:
    """
    Ищет упражнения по запросу.

    Проверяет:
    - точное совпадение с name
    - совпадение с синонимами
    - частичное совпадение

    Возвращает отсортированный список по релевантности.
    """
    if exercises is None:
        exercises = await load_exercises()

    query_lower = query.lower().strip()
    if not query_lower:
        return []

    results = []

    for exercise in exercises:
        score = 0
        name = (exercise.get("name") or "").lower()
        synonyms = exercise.get("synonyms") or []

        # Точное совпадение названия
        if name == query_lower:
            score = 100
        # Точное совпадение синонима
        elif query_lower in [s.lower() for s in synonyms if s]:
            score = 90
        # Частичное совпадение названия
        elif query_lower in name:
            score = 50
        # Частичное совпадение синонима
        elif any(query_lower in (s or "").lower() for s in synonyms):
            score = 40

        if score > 0:
            results.append({**exercise, "match_score": score})

    results.sort(key=lambda x: x["match_score"], reverse=True)
    return results


# ----- Синхронные обёртки для обратной совместимости -----


def load_exercises_sync() -> List[Dict]:
    """Синхронная загрузка (для кода без async)."""
    global _exercises_cache
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(load_exercises())
    # Если уже в event loop, кеш должен быть заполнен
    if _exercises_cache is not None:
        return _exercises_cache
    raise RuntimeError("load_exercises_sync() called from async context; use await load_exercises()")


def normalize_exercise_name(name: str) -> str:
    """Приводит название к одному виду (можно сопоставлять с базой)."""
    return name.strip().lower() if name else ""


def find_exercise_suggestions(query: str, limit: int = 5) -> List[str]:
    """Возвращает подсказки по названиям упражнений из базы (синхронно, использует кеш)."""
    global _exercises_cache
    if _exercises_cache is None and EXERCISES_FILE.exists():
        try:
            with open(EXERCISES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            _exercises_cache = data if isinstance(data, list) else []
        except Exception:
            _exercises_cache = []
    exercises = _exercises_cache or []
    q = normalize_exercise_name(query)
    names = set()
    for ex in exercises:
        title = ex.get("name") or ex.get("title") or ""
        if q in normalize_exercise_name(title):
            names.add(title)
    return list(names)[:limit]
