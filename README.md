# Gym Voice Bot

Telegram-бот — тренировочный помощник с поддержкой голосового ввода и аналитикой тренировок.

## Возможности

- **Голосовой ввод** — распознавание тренировок через Whisper (OpenAI)
- **Парсинг тренировок** — GPT-4o-mini для извлечения упражнений, подходов и весов
- **Программы тренировок** — создание и выбор программ
- **Статистика** — объёмы, 1RM, рекорды

## Требования

- Python 3.11+
- PostgreSQL
- Telegram Bot Token
- OpenAI API Key

## Установка

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Отредактируйте .env — укажите TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, DATABASE_URL
```

## Миграции БД

```bash
alembic upgrade head
```

## Запуск

```bash
python -m bot.main
```

## Деплой на Railway

Проект настроен для деплоя на Railway: `railway.json`, `Procfile`. Добавьте переменные окружения в настройках сервиса.
