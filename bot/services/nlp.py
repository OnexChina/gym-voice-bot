"""Парсинг голосовых/текстовых сообщений о тренировках (GPT-4o-mini, контекст, уверенность, уточнения)."""

import json
import logging
import re
from typing import Any

from openai import AsyncOpenAI, APITimeoutError, APIError

from bot.config import settings

logger = logging.getLogger(__name__)
_client: AsyncOpenAI | None = None

OPENAI_TIMEOUT = 25.0
KG_PER_LB = 0.453592


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


def _build_system_prompt(
    current_workout: dict | None,
    recent_exercises: list | None,
    available_exercises_names: list[str],
) -> str:
    # Приоритет: кардио и пресс первые (для лучшего распознавания), затем остальные. Макс 150.
    cardio_keywords = ["бег", "плавание", "вело", "эллипс", "дорожка", "гребля", "кардио"]
    press_keywords = ["пресс", "скручивания", "планка", "книжка", "подъём ног"]
    rest = []
    cardio_press = []
    for n in (available_exercises_names or []):
        n_lower = n.lower()
        if any(k in n_lower for k in cardio_keywords) or any(k in n_lower for k in press_keywords):
            cardio_press.append(n)
        else:
            rest.append(n)
    top_names = (cardio_press + rest)[:150]
    exercises_block = "\n".join(f"- {n}" for n in top_names) if top_names else "(список пуст)"

    parts = [
        "Ты помощник для логирования тренировок в зале. Разбери сообщение пользователя и верни строго один валидный JSON без markdown.",
        "",
        "ДОСТУПНЫЕ УПРАЖНЕНИЯ (используй только эти названия или максимально близкие):",
        exercises_block,
        "",
        "Контекст:",
    ]
    if current_workout:
        parts.append(f"- Текущая тренировка: {json.dumps(current_workout, ensure_ascii=False)}")
    if recent_exercises:
        parts.append(f"- Последние упражнения: {json.dumps(recent_exercises, ensure_ascii=False)}")
    parts.extend([
        "",
        "Примеры ввода:",
        'СИЛОВЫЕ:',
        '"Жим лёжа 10 на 80, 8 на 85" → exercises: [{"name": "жим штанги лёжа", "sets": [{"reps": 10, "weight": 80}, {"reps": 8, "weight": 85}]}]',
        '"Разводка 3 по 12 на 20" → exercises: [{"name": "разводка гантелей лёжа", "sets": [{"reps": 12, "weight": 20}, {"reps": 12, "weight": 20}, {"reps": 12, "weight": 20}]}]',
        '"Присед 5x5 100" → exercises: [{"name": "приседания со штангой", "sets": [{"reps": 5, "weight": 100}, ...]}]',
        '',
        'КАРДИО (время вместо веса):',
        '"Бег на дорожке 30 минут" → exercises: [{"name": "бег на дорожке", "sets": [{"reps": 30, "weight": null, "comment": "минут"}]}]',
        '"Running along the track for 30 minutes" → exercises: [{"name": "бег на дорожке", "sets": [{"reps": 30, "weight": null, "comment": "минут"}]}]',
        '"Плавание 20 минут" → exercises: [{"name": "плавание", "sets": [{"reps": 20, "weight": null, "comment": "минут"}]}]',
        '"Swimming 20 minutes" → exercises: [{"name": "плавание", "sets": [{"reps": 20, "weight": null, "comment": "минут"}]}]',
        '"Эллипс 45 минут" → exercises: [{"name": "эллиптический тренажёр", "sets": [{"reps": 45, "weight": null, "comment": "минут"}]}]',
        '"Elliptical 45 minutes" → exercises: [{"name": "эллиптический тренажёр", "sets": [{"reps": 45, "weight": null, "comment": "минут"}]}]',
        '"Вело 60 минут" → exercises: [{"name": "велотренажёр", "sets": [{"reps": 60, "weight": null, "comment": "минут"}]}]',
        '"Bike 60 minutes" → exercises: [{"name": "велотренажёр", "sets": [{"reps": 60, "weight": null, "comment": "минут"}]}]',
        "",
        "Формат ответа — один JSON:",
        '- "exercises": массив. Элемент: {"name": "название ИЗ СПИСКА ВЫШЕ", "sets": [{"reps": N, "weight": N или null, "weight_unit": "kg"|"lb"|null, "comment": строка или null}]}.',
        '- Для КАРДИО упражнений (бег, плавание, эллипс, вело и т.д.): если указано время (минуты), используй reps=количество_минут, weight=null, comment="минут".',
        '- "workout_comment": строка или null.',
        '- "confidence": 0–1. Если НЕ УВЕРЕН в упражнении (разные жимы, приседы и т.д.) — ставь confidence < 0.8 и заполни "alternatives".',
        '- "clarification_needed": true если непонятно или пусто.',
        '- "clarification_question": вопрос пользователю при clarification_needed.',
        '- "alternatives": массив 2–3 вариантов, если confidence < 0.8. Элемент: {"name": "название из списка", "confidence": число}. Пример: [{"name": "жим штанги лёжа", "confidence": 0.7}, {"name": "жим гантелей лёжа", "confidence": 0.6}].',
        '- "action": "add_sets" | "remove_last" | "edit_last" | "add_comment".',
        "",
        "Правила:",
        "- Название упражнения — только из списка ДОСТУПНЫЕ УПРАЖНЕНИЯ или максимально близкая формулировка.",
        "- Если сомневаешься (жим штанги/гантелей, присед/жим ногами) — confidence < 0.8, заполни alternatives 2–3 вариантами.",
        "- Вес в кг по умолчанию; lb — укажи weight_unit: \"lb\".",
    ])
    return "\n".join(parts)


def _default_response() -> dict[str, Any]:
    return {
        "exercises": [],
        "workout_comment": None,
        "confidence": 0.0,
        "clarification_needed": True,
        "clarification_question": "Не удалось разобрать сообщение. Напишите, например: жим лёжа 3 по 10 на 80 кг.",
        "action": "add_sets",
    }


def _parse_gpt_response(content: str) -> dict[str, Any] | None:
    """Извлекает JSON из ответа GPT (убирает markdown при необходимости)."""
    content = (content or "").strip()
    if not content:
        return None
    # Убрать обёртку ```json ... ```
    if "```" in content:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
        if match:
            content = match.group(1).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


def _normalize_name(name: str) -> str:
    """Нормализация для поиска: lower, strip, схлопнуть пробелы."""
    if not name:
        return ""
    return " ".join(name.strip().lower().split())


def match_exercise(exercise_name: str, exercises_db: list[dict]) -> dict[str, Any]:
    """
    Простой поиск упражнения по базе (без fuzzy).
    1. Точное совпадение по name (lower)
    2. Совпадение по synonyms (lower)
    3. Слово пользователя ВНУТРИ названия (substring)
    4. Слово из названия ВНУТРИ фразы пользователя
    Если нашли — возвращаем match с confidence=1.0. Если нет — confidence=0, alternatives=[].
    """
    if not exercise_name or not exercises_db:
        return {"exercise_id": None, "name": exercise_name or "", "confidence": 0.0, "alternatives": []}

    norm_query = _normalize_name(exercise_name)
    if not norm_query:
        return {"exercise_id": None, "name": exercise_name, "confidence": 0.0, "alternatives": []}

    # 1. Точное совпадение по name
    for ex in exercises_db:
        name = ex.get("name") or ""
        norm_name = _normalize_name(name)
        if norm_query == norm_name:
            return {
                "exercise_id": ex.get("id"),
                "name": name,
                "confidence": 1.0,
                "alternatives": [],
            }

    # 2. Совпадение по synonyms
    for ex in exercises_db:
        name = ex.get("name") or ""
        for syn in ex.get("synonyms") or []:
            norm_syn = _normalize_name(str(syn))
            if norm_query == norm_syn:
                return {
                    "exercise_id": ex.get("id"),
                    "name": name,
                    "confidence": 1.0,
                    "alternatives": [],
                }

    # 3. Слово пользователя (или вся фраза) ВНУТРИ названия
    for ex in exercises_db:
        name = ex.get("name") or ""
        norm_name = _normalize_name(name)
        if norm_query in norm_name:
            return {
                "exercise_id": ex.get("id"),
                "name": name,
                "confidence": 1.0,
                "alternatives": [],
            }

    # 4. Слово из названия ВНУТРИ фразы пользователя
    for ex in exercises_db:
        name = ex.get("name") or ""
        norm_name = _normalize_name(name)
        if norm_name in norm_query:
            return {
                "exercise_id": ex.get("id"),
                "name": name,
                "confidence": 1.0,
                "alternatives": [],
            }

    # Проверка по синонимам: подстрока
    for ex in exercises_db:
        name = ex.get("name") or ""
        for syn in ex.get("synonyms") or []:
            norm_syn = _normalize_name(str(syn))
            if norm_query in norm_syn or norm_syn in norm_query:
                return {
                    "exercise_id": ex.get("id"),
                    "name": name,
                    "confidence": 1.0,
                    "alternatives": [],
                }

    return {"exercise_id": None, "name": exercise_name, "confidence": 0.0, "alternatives": []}


def convert_units(weight: float, unit: str) -> float:
    """
    Конвертирует вес в килограммы.
    Поддерживаемые единицы: кг / kg → без изменений; фунт / lb / lbs → умножить на 0.453592.
    """
    if unit is None or unit == "":
        return weight
    u = str(unit).strip().lower()
    if u in ("кг", "kg", "kgs"):
        return weight
    if u in ("фунт", "lb", "lbs", "фунтов"):
        return weight * KG_PER_LB
    return weight


async def parse_workout_message(
    text: str,
    user_id: int,
    current_workout: dict | None = None,
    recent_exercises: list | None = None,
    exercises_db: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Парсит сообщение пользователя о тренировке с учётом контекста.
    Возвращает структурированный JSON с exercises, confidence, clarification и action.
    """
    exercises_db = exercises_db or []
    available_names = [str(e.get("name", "")).strip() for e in exercises_db if e.get("name")]

    if not text or not text.strip():
        out = _default_response()
        out["clarification_question"] = "Напишите, что сделали на тренировке. Например: жим лёжа 3 по 10 на 80 кг."
        return out

    system = _build_system_prompt(current_workout, recent_exercises, available_names)

    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text.strip()},
            ],
            temperature=0.1,
            timeout=OPENAI_TIMEOUT,
        )
    except APITimeoutError as e:
        logger.warning("OpenAI timeout: %s", e)
        out = _default_response()
        out["clarification_question"] = "Ответ занял слишком много времени. Попробуйте короче: например, «жим 10 на 80»."
        return out
    except APIError as e:
        logger.exception("OpenAI API error: %s", e)
        out = _default_response()
        out["clarification_question"] = "Сервис распознавания временно недоступен. Попробуйте позже."
        return out

    content = (response.choices[0].message.content or "").strip()
    parsed = _parse_gpt_response(content)
    if not parsed or not isinstance(parsed, dict):
        logger.warning("Invalid GPT JSON response: %s", content[:200])
        return _default_response()

    # Нормализация структуры
    exercises_raw = parsed.get("exercises")
    if not isinstance(exercises_raw, list):
        exercises_raw = []

    exercises_out = []
    for item in exercises_raw:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip() or "Упражнение"
        sets_raw = item.get("sets") or []
        if not isinstance(sets_raw, list):
            sets_raw = []
        sets_out = []
        for s in sets_raw:
            if not isinstance(s, dict):
                continue
            weight = s.get("weight")
            if weight is not None:
                try:
                    weight = float(weight)
                except (TypeError, ValueError):
                    weight = None
            unit = s.get("weight_unit")
            if weight is not None and unit:
                weight = convert_units(weight, unit)
            sets_out.append({
                "reps": int(s["reps"]) if s.get("reps") is not None else None,
                "weight": weight,
                "comment": (s.get("comment") or "").strip() or None,
            })
        match = match_exercise(name, exercises_db) if exercises_db else {"exercise_id": None, "name": name, "confidence": 0.5, "alternatives": []}
        exercises_out.append({
            "name": match.get("name") or name,
            "exercise_id": match.get("exercise_id"),
            "sets": sets_out,
            "exercise_comment": (item.get("exercise_comment") or "").strip() or None,
        })

    confidence = parsed.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else 0.5
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    clarification_needed = bool(parsed.get("clarification_needed"))
    clarification_question = (parsed.get("clarification_question") or "").strip() or None
    action = (parsed.get("action") or "add_sets").strip()
    if action not in ("add_sets", "remove_last", "edit_last", "add_comment"):
        action = "add_sets"

    workout_comment = (parsed.get("workout_comment") or "").strip() or None

    # Альтернативы от GPT при низкой уверенности
    alternatives_raw = parsed.get("alternatives")
    alternatives_out = []
    if isinstance(alternatives_raw, list):
        for a in alternatives_raw[:5]:
            if isinstance(a, dict) and a.get("name"):
                alternatives_out.append({
                    "name": (a.get("name") or "").strip(),
                    "confidence": float(a.get("confidence", 0.5)) if a.get("confidence") is not None else 0.5,
                })

    return {
        "exercises": exercises_out,
        "workout_comment": workout_comment,
        "confidence": confidence,
        "clarification_needed": clarification_needed,
        "clarification_question": clarification_question,
        "action": action,
        "alternatives": alternatives_out,
    }
