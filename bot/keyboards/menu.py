"""Клавиатуры и меню (aiogram 3.x): Reply и Inline с префиксами callback_data."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


# ----- Reply-клавиатуры -----


def main_menu() -> ReplyKeyboardMarkup:
    """Главное меню (постоянное)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🏋️ Начать тренировку"),
                KeyboardButton(text="📊 Текущая тренировка"),
            ],
            [
                KeyboardButton(text="📋 Мои программы"),
                KeyboardButton(text="📊 Статистика"),
            ],
            [
                KeyboardButton(text="➕ Добавить упражнение"),
                KeyboardButton(text="⚙️ Настройки"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def workout_menu() -> ReplyKeyboardMarkup:
    """Меню во время тренировки: Текущая тренировка, Закончить, Отменить, Главное меню (всегда видимы)."""
    keyboard = [
        [
            KeyboardButton(text="📊 Текущая тренировка"),
            KeyboardButton(text="🏁 Закончить тренировку"),
        ],
        [KeyboardButton(text="❌ Отменить тренировку")],
        [KeyboardButton(text="◀️ Главное меню")],
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
    )


def workout_inline_buttons() -> InlineKeyboardMarkup:
    """Inline-кнопки во время тренировки (завершить / отменить)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Завершить", callback_data="finish_workout"),
                InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_workout"),
            ],
        ]
    )


# ----- Inline: программы -----


def program_selection(programs: list) -> InlineKeyboardMarkup:
    """
    Выбор программы перед тренировкой.
    programs: список dict с ключами id, name (или только name; id для callback).
    """
    buttons = []
    row = []
    for i, p in enumerate(programs):
        name = p.get("name") or p.get("title") or f"Программа {i+1}"
        pid = p.get("id", i)
        row.append(InlineKeyboardButton(text=name[:32], callback_data=f"program:{pid}"))
        if len(row) >= 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="🎯 Freestyle (без программы)", callback_data="program:freestyle")])
    buttons.append([InlineKeyboardButton(text="➕ Создать новую программу", callback_data="program:new")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_program_exercises_keyboard(
    available_exercises: list,
    selected: list,
    page: int = 0,
    per_page: int = 8,
) -> InlineKeyboardMarkup:
    """
    Клавиатура для выбора упражнений при создании/редактировании программы.
    Отмечает выбранные галочкой ✓.
    selected: список id выбранных упражнений.
    """
    selected_ids = set(selected)
    start = page * per_page
    chunk = available_exercises[start : start + per_page]
    buttons = []
    for ex in chunk:
        eid = ex.get("id") or ex.get("name")
        name = ex.get("name") or str(eid)
        label = f"✓ {name}" if eid in selected_ids else name
        if len(label) > 35:
            label = label[:32] + "…"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"exercise:{eid}")])
    total = len(available_exercises)
    total_pages = max(1, (total + per_page - 1) // per_page)
    if total_pages > 1:
        prev_data = f"program_exercises:page:{page - 1}" if page > 0 else "program_exercises:noop"
        next_data = f"program_exercises:page:{page + 1}" if page < total_pages - 1 else "program_exercises:noop"
        nav = [
            InlineKeyboardButton(text="◀️", callback_data=prev_data),
            InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="program_exercises:noop"),
            InlineKeyboardButton(text="▶️", callback_data=next_data),
        ]
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="✅ Готово", callback_data="action:done")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ----- Inline: подтверждение упражнения -----


def confirm_exercise(exercise_name: str, sets_count: int, volume: float) -> InlineKeyboardMarkup:
    """Подтверждение записанного упражнения: верно, удалить, исправить, комментарий."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Верно", callback_data="confirm_exercise"),
                InlineKeyboardButton(text="❌ Удалить", callback_data="delete_last_exercise"),
            ],
            [
                InlineKeyboardButton(text="✏️ Исправить", callback_data="edit_last_exercise"),
                InlineKeyboardButton(text="💬 Комментарий", callback_data="add_comment"),
            ],
        ]
    )


def confirm_sets_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура после добавления подходов к текущему упражнению."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Верно", callback_data="confirm_sets"),
                InlineKeyboardButton(text="❌ Удалить", callback_data="delete_last_sets"),
            ],
            [InlineKeyboardButton(text="✔️ Закончить упражнение", callback_data="finish_exercise")],
        ]
    )


# ----- Inline: альтернативы упражнения -----


def add_exercise_confirm() -> InlineKeyboardMarkup:
    """Кнопки при неизвестном упражнении: добавить в базу или уточнить название."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, добавить", callback_data="add_exercise_yes"),
                InlineKeyboardButton(text="❌ Нет", callback_data="add_exercise_no"),
            ],
        ]
    )


def exercise_alternatives(alternatives: list) -> InlineKeyboardMarkup:
    """
    Выбор из похожих упражнений при неопределённости.
    alternatives: список dict с exercise_id (или id) и name.
    """
    buttons = []
    for alt in alternatives:
        eid = alt.get("exercise_id") or alt.get("id")
        name = alt.get("name") or str(eid)
        if len(name) > 40:
            name = name[:37] + "…"
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"exercise:{eid}")])
    buttons.append([InlineKeyboardButton(text="➕ Создать новое упражнение", callback_data="exercise:new")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="action:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ----- Inline: статистика -----


def stats_menu() -> InlineKeyboardMarkup:
    """Меню статистики."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 Сегодня", callback_data="stats:today"),
                InlineKeyboardButton(text="📆 Эта неделя", callback_data="stats:week"),
            ],
            [
                InlineKeyboardButton(text="📊 Этот месяц", callback_data="stats:month"),
                InlineKeyboardButton(text="🏆 Рекорды", callback_data="stats:records"),
            ],
            [InlineKeyboardButton(text="📈 Прогресс по упражнению", callback_data="stats:progress")],
        ]
    )


# ----- Inline: настройки -----


def settings_menu() -> InlineKeyboardMarkup:
    """Меню настроек (язык, единицы, назад)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🇷🇺 Язык: Русский", callback_data="settings:language")],
            [InlineKeyboardButton(text="⚖️ Единицы: кг", callback_data="settings:units")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")],
        ]
    )


# ----- Вспомогательное: пагинация -----


def create_pagination_keyboard(
    current_page: int,
    total_pages: int,
    callback_prefix: str,
) -> InlineKeyboardMarkup:
    """Кнопки пагинации [◀️ 1/5 ▶️]. callback_data: {prefix}:prev, {prefix}:next (или :noop на границах)."""
    total_pages = max(1, total_pages)
    current_page = max(0, min(current_page, total_pages - 1))
    prev_data = f"{callback_prefix}:prev" if current_page > 0 else f"{callback_prefix}:noop"
    next_data = f"{callback_prefix}:next" if current_page < total_pages - 1 else f"{callback_prefix}:noop"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="◀️", callback_data=prev_data),
                InlineKeyboardButton(text=f"{current_page + 1}/{total_pages}", callback_data=f"{callback_prefix}:noop"),
                InlineKeyboardButton(text="▶️", callback_data=next_data),
            ]
        ]
    )


# ----- Динамическая клавиатура по активной тренировке -----


def get_main_keyboard(workout_active: bool) -> ReplyKeyboardMarkup:
    """
    Главная клавиатура в зависимости от наличия активной тренировки.
    Если workout_active — показывать кнопки «Текущая тренировка» и «Закончить тренировку».
    """
    return workout_menu() if workout_active else main_menu()


# ----- Совместимость со старым кодом -----


def get_main_menu() -> ReplyKeyboardMarkup:
    """Алиас для главного меню (обратная совместимость)."""
    return main_menu()
