"""Пересылка в беседу (группу): сводки по выездам и файл реестра.
Плюс общий отправщик фото-альбомов (дайджест руководителя и пуш по визиту).

ID беседы берётся из принятой настройки (db.settings['group_chat_id']) — её бот
подхватывает автоматически, когда его добавляют в группу и владелец принимает.
Фолбэк — GROUP_CHAT_ID из .env.
"""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile, InputMediaDocument, InputMediaPhoto

import db
from config import GROUP_CHAT_ID

ALBUM = 10   # лимит Телеграма на медиагруппу


def group_chat_id() -> int | None:
    val = db.get_setting("group_chat_id")
    if val:
        try:
            return int(val)
        except ValueError:
            return None
    return GROUP_CHAT_ID


async def post_to_group(bot: Bot, text: str | None = None,
                        document_path: str | None = None,
                        caption: str | None = None) -> bool:
    chat_id = group_chat_id()
    if not chat_id:
        return False
    try:
        if document_path:
            await bot.send_document(chat_id, FSInputFile(document_path), caption=caption)
        if text:
            await bot.send_message(chat_id, text)
        return True
    except Exception as e:  # noqa: BLE001
        logging.warning("post_to_group failed: %s", e)
        return False


async def send_album(bot: Bot, chat_id: int, files, caption: str | None = None) -> None:
    """Файлы альбомами по 10, подпись — на первом. Что не влезло фотографией
    (>10 МБ, HEIC…) — повторяем документом; совсем битое — честно говорим, не молчим."""
    for i in range(0, len(files), ALBUM):
        chunk = files[i:i + ALBUM]
        cap = caption if i == 0 else None
        for cls in (InputMediaPhoto, InputMediaDocument):
            try:
                if len(chunk) == 1:   # медиагруппа = минимум 2 элемента
                    if cls is InputMediaPhoto:
                        await bot.send_photo(chat_id, FSInputFile(str(chunk[0])), caption=cap)
                    else:
                        await bot.send_document(chat_id, FSInputFile(str(chunk[0])), caption=cap)
                else:
                    await bot.send_media_group(chat_id, [
                        cls(media=FSInputFile(str(p)), caption=cap if j == 0 else None)
                        for j, p in enumerate(chunk)])
                break
            except TelegramAPIError as e:
                if cls is InputMediaDocument:
                    logging.warning("send_album failed: %s", e)
                    await bot.send_message(chat_id, "⚠️ Часть фото не удалось отправить.")
