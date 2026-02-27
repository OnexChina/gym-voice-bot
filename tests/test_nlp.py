"""Unit-тесты для NLP (match_exercise, convert_units) без внешних API."""

import pytest

from bot.services.nlp import match_exercise, convert_units


@pytest.fixture
def exercises_db():
    """База упражнений для тестов."""
    return [
        {"id": 0, "name": "жим штанги лёжа", "synonyms": ["жим лёжа", "bench press"], "muscle_groups": ["грудь"]},
        {"id": 1, "name": "приседания со штангой", "synonyms": ["присед", "squat"], "muscle_groups": ["ноги"]},
        {"id": 2, "name": "разводка гантелей лёжа", "synonyms": ["разводка"], "muscle_groups": ["грудь"]},
    ]


def test_match_exercise_exact_name(exercises_db):
    """Точное совпадение по названию."""
    m = match_exercise("жим штанги лёжа", exercises_db)
    assert m["confidence"] == 1.0
    assert m["name"] == "жим штанги лёжа"
    assert m["exercise_id"] == 0


def test_match_exercise_synonym(exercises_db):
    """Совпадение по синониму."""
    m = match_exercise("жим лёжа", exercises_db)
    assert m["confidence"] == 1.0
    assert m["name"] == "жим штанги лёжа"
    assert m["exercise_id"] == 0


def test_match_exercise_substring_in_name(exercises_db):
    """Подстрока: слово пользователя внутри названия."""
    m = match_exercise("присед", exercises_db)
    assert m["confidence"] == 1.0
    assert m["name"] == "приседания со штангой"


def test_match_exercise_unknown(exercises_db):
    """Неизвестное упражнение."""
    m = match_exercise("какое-то новое упражнение", exercises_db)
    assert m["confidence"] == 0.0
    assert m["exercise_id"] is None


def test_match_exercise_empty_query():
    """Пустой запрос."""
    m = match_exercise("", [{"id": 0, "name": "test", "synonyms": []}])
    assert m["confidence"] == 0.0
    assert m["name"] == ""


def test_match_exercise_empty_db():
    """Пустая база."""
    m = match_exercise("жим лёжа", [])
    assert m["confidence"] == 0.0
    assert m["name"] == "жим лёжа"


def test_convert_units_kg():
    """кг — без изменений."""
    assert convert_units(80, "kg") == 80
    assert convert_units(80, "кг") == 80


def test_convert_units_lb():
    """Фунты в кг."""
    # 1 lb ≈ 0.453592 kg
    result = convert_units(100, "lb")
    assert abs(result - 45.3592) < 0.01


def test_convert_units_none():
    """Без единиц — без изменений."""
    assert convert_units(80, None) == 80
    assert convert_units(80, "") == 80
