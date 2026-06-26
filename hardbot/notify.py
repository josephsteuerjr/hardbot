"""Пересылка в беседу (группу): сводки по выездам и файл реестра.

ID беседы берётся из принятой настройки (db.settings['group_chat_id']) — её бот
подхватывает автоматически, когда его добавляют в группу и владелец принимает.
Фолбэк — GROUP_CHAT_ID из .env.
"""

import logging

from aiogram import Bot
from aiogram.types import FSInputFile

import db
from config import GROUP_CHAT_ID


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
