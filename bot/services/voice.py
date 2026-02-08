"""Обработка голосовых сообщений: скачивание из Telegram и распознавание через Whisper."""

import asyncio
import io
import logging
from typing import Optional

import aiohttp
from openai import AsyncOpenAI, APITimeoutError, APIError

from bot.config import settings

logger = logging.getLogger(__name__)
_client: Optional[AsyncOpenAI] = None

TELEGRAM_FILE_BASE = "https://api.telegram.org"
WHISPER_TIMEOUT = 30.0


def _get_openai_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def transcribe_voice(voice_file_id: str, bot_or_token: str) -> str:
    """
    Скачивает голосовое сообщение из Telegram по file_id,
    отправляет в Whisper API и возвращает распознанный текст.
    При ошибке возвращает пустую строку.

    bot_or_token: либо токен бота (str) — тогда используется aiohttp для скачивания;
                  либо экземпляр aiogram Bot — тогда скачивание через bot.get_file.
    """
    # Вызов с объектом Bot (совместимость с обработчиками aiogram)
    if hasattr(bot_or_token, "get_file") and hasattr(bot_or_token, "download_file"):
        result = await transcribe_voice_with_bot(voice_file_id, bot_or_token)
        return result or ""

    bot_token = bot_or_token
    if not voice_file_id or not bot_token:
        logger.warning("transcribe_voice: empty file_id or bot_token")
        return ""

    file_path = await _get_telegram_file_path(bot_token, voice_file_id)
    if not file_path:
        return ""

    audio_bytes = await _download_telegram_file(bot_token, file_path)
    if not audio_bytes:
        return ""

    return await _whisper_transcribe(audio_bytes)


async def _get_telegram_file_path(bot_token: str, file_id: str) -> Optional[str]:
    """Получает file_path через getFile. При ошибке возвращает None."""
    url = f"{TELEGRAM_FILE_BASE}/bot{bot_token}/getFile"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={"file_id": file_id}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning("Telegram getFile failed: status=%s", resp.status)
                    return None
                data = await resp.json()
    except aiohttp.ClientError as e:
        logger.warning("Telegram getFile request failed: %s", e)
        return None
    except asyncio.TimeoutError:
        logger.warning("Telegram getFile timeout")
        return None

    if not data.get("ok"):
        logger.warning("Telegram getFile not ok: %s", data)
        return None

    result = data.get("result") or {}
    path = result.get("file_path")
    if not path:
        logger.warning("Telegram getFile: no file_path in result")
        return None
    return path


async def _download_telegram_file(bot_token: str, file_path: str) -> Optional[bytes]:
    """Скачивает файл по file_path. При ошибке возвращает None."""
    url = f"{TELEGRAM_FILE_BASE}/file/bot{bot_token}/{file_path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("Telegram file download failed: status=%s", resp.status)
                    return None
                return await resp.read()
    except aiohttp.ClientError as e:
        logger.warning("Telegram file download failed: %s", e)
        return None
    except asyncio.TimeoutError:
        logger.warning("Telegram file download timeout")
        return None


async def _whisper_transcribe(audio_bytes: bytes) -> str:
    """Отправляет байты аудио в Whisper, возвращает текст или пустую строку."""
    if not audio_bytes:
        return ""

    buf = io.BytesIO(audio_bytes)
    buf.name = "voice.ogg"

    try:
        client = _get_openai_client()
        response = await client.audio.transcriptions.create(
            model="whisper-1",
            file=buf,
            timeout=WHISPER_TIMEOUT,
        )
        return (response.text or "").strip()
    except APITimeoutError as e:
        logger.warning("Whisper API timeout: %s", e)
        return ""
    except APIError as e:
        logger.warning("Whisper API error: %s", e)
        return ""
    except Exception as e:
        logger.exception("Whisper transcription failed: %s", e)
        return ""


# Совместимость со старым вызовом: transcribe_voice(file_id, bot: Bot)
async def transcribe_voice_with_bot(voice_file_id: str, bot) -> Optional[str]:
    """
    Вариант с передачей экземпляра Bot (для обработчиков aiogram).
    Скачивает файл через bot.get_file и отправляет в Whisper.
    """
    try:
        file = await bot.get_file(voice_file_id)
        data = await bot.download_file(file.file_path)
        audio_bytes = data.read()
        text = await _whisper_transcribe(audio_bytes)
        return text if text else None
    except Exception as e:
        logger.exception("Whisper transcription (with bot) failed: %s", e)
        return None
