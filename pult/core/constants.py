"""Константы пульта: адрес по умолчанию, виды источника импорта и рубильники
ограниченного теста с их сообщениями STOP."""

from __future__ import annotations

from exchange_contract import IMPORT_SOURCE_PUBLISHED_MASTER

# Адрес сервера вводит оператор на вкладке «Сервер» — он у каждой установки
# свой. Значения по умолчанию нет намеренно: прежнее указывало на адрес,
# снятый провайдером, и пульт молча стучался в никуда. Ключ — X-Admin-Key.
DEFAULT_URL = ""
DEFAULT_KEY = ""

IMPORT_SOURCE_TEST_SUBSET = "test_subset"

# Возвращено владельцем 03.08.2026. Запись в личные книги — это штатная работа
# пульта (и в политике обмена она названа единственным штатным способом перенести
# доказанные визиты бота вниз). На период первого live-baseline её выключали, чтобы
# ничто не писало в книги мимо registry_cycle; baseline опубликован 29.07.
# Что НЕ вернулось: сброс базы из пульта. Он обходит snapshot/export/clearance,
# и это не про удобство, а про доказуемость очистки перед тестом на серверах компании.
LIMITED_TEST_ENRICH_WRITES_ENABLED = True
LIMITED_TEST_MASTER_IMPORT_UI_ENABLED = True
LIMITED_TEST_RESET_UI_ENABLED = False
LIMITED_TEST_ENRICH_DISABLED_MESSAGE = (
    "Запись в личные реестры из пульта отключена. Разрешены только "
    "evidence-run канонического registry_cycle и самостоятельный ночной "
    "registry_cycle. Здесь доступен только предпросмотр."
)
LIMITED_TEST_MASTER_IMPORT_DISABLED_MESSAGE = (
    "Прямой импорт master из пульта отключён: сервер требует отдельную "
    "root-регистрацию exact proof. Используйте двухфазный "
    "limited_test_import.py: prepare-published-master → register → apply. "
    "Ночной registry_cycle пульт не запускает."
)
LIMITED_TEST_RESET_DISABLED_MESSAGE = (
    "Сброс через пульт отключён на период ограниченного теста: он обходит "
    "обязательные snapshot, export и registry clearance. Используйте только "
    "evidence-bound limited-test reset из server/DEPLOY.md."
)

__all__ = [
    "DEFAULT_KEY", "DEFAULT_URL",
    "IMPORT_SOURCE_PUBLISHED_MASTER", "IMPORT_SOURCE_TEST_SUBSET",
    "LIMITED_TEST_ENRICH_DISABLED_MESSAGE", "LIMITED_TEST_ENRICH_WRITES_ENABLED",
    "LIMITED_TEST_MASTER_IMPORT_DISABLED_MESSAGE",
    "LIMITED_TEST_MASTER_IMPORT_UI_ENABLED",
    "LIMITED_TEST_RESET_DISABLED_MESSAGE", "LIMITED_TEST_RESET_UI_ENABLED",
]


# Телеграм в контуре компании не используется: вход только по ссылкам (решение
# владельца 24.09.2026). Всё, что работает лишь через бота, — рассылки и беседы
# («Каналы»), токен бота, проверка доставки, «из писавших боту», привязка по
# Telegram ID, — в пульте спрятано этим рубильником, а не вырезано: вернуть —
# одна строка. Читается во время построения экранов, поэтому тесты Телеграм-
# экранов включают его сами.
TELEGRAM_UI = False
