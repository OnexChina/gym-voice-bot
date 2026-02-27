"""Unit-тесты для функций CRUD без БД (calculate_1rm)."""

import pytest

from bot.database.crud import calculate_1rm


def test_calculate_1rm_single_rep():
    """1 повторение — вес = 1RM."""
    assert calculate_1rm(1, 100) == 100.0


def test_calculate_1rm_multiple_reps():
    """Формула Эпли: 1RM = weight × (1 + reps/30)."""
    # 10 повторов × 80 кг
    result = calculate_1rm(10, 80)
    expected = 80 * (1 + 10 / 30)  # 80 * 1.333... ≈ 106.67
    assert abs(result - expected) < 0.01


def test_calculate_1rm_zero_reps():
    """0 повторений — возвращает вес."""
    assert calculate_1rm(0, 50) == 50.0


def test_calculate_1rm_five_by_five():
    """5×5 @ 100 кг."""
    result = calculate_1rm(5, 100)
    expected = 100 * (1 + 5 / 30)
    assert abs(result - expected) < 0.01
