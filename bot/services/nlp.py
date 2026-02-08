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
    parts = [
        "Ты ассистент для логирования тренировок в зале. Твоя задача — разобрать сообщение пользователя и вернуть строго один валидный JSON-объект без markdown и без пояснений.",
        "",
        "Контекст:",
    ]
    if current_workout:
        parts.append(f"- Текущая тренировка: {json.dumps(current_workout, ensure_ascii=False)}")
    if recent_exercises:
        parts.append(f"- Последние упражнения сегодня: {json.dumps(recent_exercises, ensure_ascii=False)}")
    if available_exercises_names:
        parts.append(f"- Упражнения из программы/базы (для сопоставления названий): {json.dumps(available_exercises_names[:80], ensure_ascii=False)}")
    parts.extend([
        "",
        "Примеры ввода пользователя:",
        '- "Жим лёжа 10 на 80, 8 на 85, 6 на 90" → добавление подходов',
        '- "Разводка 3 по 12 на 20" → три подхода по 12 повторений с весом 20 кг',
        '- "Последний подход был тяжёлый" → комментарий к последнему подходу (action: add_comment)',
        '- "Убери последний подход" → удаление последнего подхода (action: remove_last)',
        '- "Сделал как в прошлый раз плюс 5 кг" → редактирование/добавление с учётом контекста (action: edit_last или add_sets)',
        '- "Присед 5x5 100 кг" → пять подходов по 5 повторений по 100 кг',
        "",
        "Формат ответа — только один JSON-объект со следующими полями:",
        '- "exercises": массив. Каждый элемент: {"name": "нормализованное название", "sets": [{"reps": число или null, "weight": число или null, "weight_unit": "kg" или "lb" или null, "comment": строка или null}], "exercise_comment": строка или null}. Если действие не add_sets — exercises может быть пустым.',
        '- "workout_comment": строка или null — общий комментарий к тренировке.',
        '- "confidence": число от 0 до 1 — уверенность в распознавании.',
        '- "clarification_needed": true если текст непонятен, неоднозначен или пуст.',
        '- "clarification_question": строка или null — вопрос пользователю при clarification_needed (например: "Имеется в виду жим штанги или гантелей?").',
        '- "action": одна из строк: "add_sets", "remove_last", "edit_last", "add_comment".',
        "",
        "Правила:",
        "- Если вес не указан — в sets используй weight: null, weight_unit: null.",
        "- Если единица веса фунты (фунт, lb, lbs) — укажи weight_unit: \"lb\".",
        "- Если текст пустой или не про тренировку — clarification_needed: true, confidence: 0, exercises: [], action: \"add_sets\".",
        "- Названия упражнений нормализуй на русском, как в зале (жим лёжа, присед, тяга и т.д.).",
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
    return name.strip().lower() if name else ""


def _exercise_name_matches(a: str, b: str) -> bool:
    """Проверка совпадения названий (точное после нормализации или вхождение)."""
    na, nb = _normalize_name(a), _normalize_name(b)
    return na == nb or na in nb or nb in na


async def match_exercise(exercise_name: str, exercises_db: list[dict]) -> dict[str, Any]:
    """
    Сопоставляет название упражнения с базой (синонимы, опечатки, близость).
    exercises_db: список dict с ключами id (опционально), name, synonyms (опционально), name_en (опционально).
    """
    if not exercise_name or not exercises_db:
        return {"exercise_id": None, "name": exercise_name or "", "confidence": 0.0, "alternatives": []}

    norm_query = _normalize_name(exercise_name)
    if not norm_query:
        return {"exercise_id": None, "name": exercise_name, "confidence": 0.0, "alternatives": []}

    candidates: list[tuple[float, dict]] = []  # (score, exercise_dict)

    for ex in exercises_db:
        name = ex.get("name") or ""
        name_en = ex.get("name_en") or ""
        synonyms = ex.get("synonyms") or []
        ex_id = ex.get("id")
        norm_name = _normalize_name(name)
        norm_en = _normalize_name(name_en)

        if norm_query == norm_name or norm_query == norm_en:
            candidates.append((1.0, {"exercise_id": ex_id, "name": name, "confidence": 1.0}))
            continue
        if norm_query in norm_name or norm_name in norm_query:
            candidates.append((0.95, {"exercise_id": ex_id, "name": name, "confidence": 0.9}))
            continue
        if norm_query in norm_en or norm_en in norm_query:
            candidates.append((0.9, {"exercise_id": ex_id, "name": name, "confidence": 0.85}))
            continue
        for syn in synonyms:
            norm_syn = _normalize_name(str(syn))
            if norm_query == norm_syn or norm_query in norm_syn or norm_syn in norm_query:
                candidates.append((0.85, {"exercise_id": ex_id, "name": name, "confidence": 0.8}))
                break

    if not candidates:
        return {"exercise_id": None, "name": exercise_name, "confidence": 0.0, "alternatives": []}

    candidates.sort(key=lambda x: -x[0])
    best = candidates[0][1]
    alternatives = [c[1] for c in candidates[1:6]]
    return {
        "exercise_id": best.get("exercise_id"),
        "name": best.get("name", exercise_name),
        "confidence": best.get("confidence", 0.8),
        "alternatives": alternatives,
    }


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
        match = await match_exercise(name, exercises_db) if exercises_db else {"exercise_id": None, "name": name, "confidence": 0.5, "alternatives": []}
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

    return {
        "exercises": exercises_out,
        "workout_comment": workout_comment,
        "confidence": confidence,
        "clarification_needed": clarification_needed,
        "clarification_question": clarification_question,
        "action": action,
    }
