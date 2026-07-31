"""Пересылка в беседу (группу): сводки по выездам и файл реестра.
Плюс общий отправщик фото-альбомов (дайджест руководителя и пуш по визиту).

ID беседы берётся из принятой настройки (db.settings['group_chat_id']) — её бот
подхватывает автоматически, когда его добавляют в группу и владелец принимает.
Фолбэк — GROUP_CHAT_ID из .env.
"""

import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramMigrateToChat
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


def _maps_link(lat, lon) -> str:
    return f"https://yandex.ru/maps/?pt={lon},{lat}&z=17&l=map"


def visit_report_text(row, dist_m=None, visit_geo=None, photo_geo=None,
                      photos_n: int = 0, actor: str | None = None) -> str:
    """Отчёт о выезде для беседы — по образцу постов сотрудников (клиент, адрес,
    рассказ), но с доказуемыми точками: гео закрытия визита и гео съёмки фото.
    row — клиент ПОСЛЕ сохранения (sqlite.Row или dict). Бот шлёт с
    parse_mode=HTML, поэтому все данные экранируются: «<» в комментарии не
    должен ронять доставку, а разметка из реестра — рендериться в беседе."""
    def raw(key):
        try:
            return str(row[key] or "")
        except (KeyError, IndexError, TypeError):
            return ""

    def g(key):
        return html.escape(raw(key))
    lines = ["✅ Выезд отработан",
             f"🧑‍💼 {g('responsible') or '—'}",
             f"👤 {g('client') or '—'} ({g('contract_id')})"]
    # Руководитель работает В ПРИЛОЖЕНИИ от имени своего сотрудника, и закрытый им
    # визит выглядел бы визитом этого сотрудника — с координатами руководителя.
    # Кто фактически закрыл, обязано быть видно в отчёте, иначе гео-контроль врёт.
    if actor and actor != raw("responsible"):
        lines.insert(1, f"👤 закрыл: {html.escape(str(actor))} (от имени сотрудника)")
    place = " · ".join(x for x in (g("city"), g("address")) if x)
    if place:
        lines.append(f"🏠 {place}")
    lines.append(f"📝 {g('call_status') or '—'} · {g('agreed_payment') or '—'}")
    comment = g("visit_comment") or g("call_comment")
    if comment:
        lines.append(f"💬 {comment}")
    if visit_geo:
        s = f"📍 Точка закрытия: {_maps_link(*visit_geo)}"
        if dist_m is not None:
            pretty = f"{dist_m / 1000:.1f} км" if dist_m >= 1000 else f"{dist_m} м"
            s += f" ({pretty} от адреса)"
        lines.append(s)
    if photos_n:
        s = f"📸 Фото: {photos_n}"
        if photo_geo:
            s += f" · точка съёмки: {_maps_link(*photo_geo)}"
        lines.append(s)
    return "\n".join(lines)


async def post_visit_report(bot: Bot, chat_ids, text: str, files) -> None:
    """Разослать отчёт по закреплённым беседам; сбой одной беседы не мешает
    остальным и не ломает сохранение визита. Группа, ставшая супергруппой,
    переезжает на новый chat_id и получает отчёт туда же."""
    async def _send(chat_id):
        if files:
            await send_album(bot, chat_id, files, text)
        else:
            await bot.send_message(chat_id, text)

    for chat_id in chat_ids:
        try:
            await _send(chat_id)
        except TelegramMigrateToChat as e:
            new_id = e.migrate_to_chat_id
            db.migrate_report_chat(chat_id, new_id)
            try:
                await _send(new_id)
            except Exception as e2:  # noqa: BLE001
                logging.warning("visit report to migrated chat %s failed: %s",
                                new_id, e2)
        except Exception as e:  # noqa: BLE001
            logging.warning("visit report to chat %s failed: %s", chat_id, e)


CAPTION_MAX = 1024   # телеграмный лимит подписи медиа


async def send_album(bot: Bot, chat_id: int, files, caption: str | None = None) -> None:
    """Файлы альбомами по 10, подпись — на первом. Что не влезло фотографией
    (>10 МБ, HEIC…) — повторяем документом; совсем битое — честно говорим, не молчим.
    Подпись длиннее лимита не роняет весь пост (голосовой комментарий бывает
    длинным): альбом уходит без подписи, текст — отдельным сообщением следом."""
    long_caption = None
    if caption and len(caption) > CAPTION_MAX:
        long_caption, caption = caption, None
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
            except TelegramMigrateToChat:
                raise   # миграцию группы решает вызывающий (post_visit_report)
            except TelegramAPIError as e:
                if cls is InputMediaDocument:
                    logging.warning("send_album failed: %s", e)
                    await bot.send_message(chat_id, "⚠️ Часть фото не удалось отправить.")
    if long_caption:
        await bot.send_message(chat_id, long_caption[:4096])
