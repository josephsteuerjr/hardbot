#!/usr/bin/env bash
# Установка «Выезды» одной командой. Повторный запуск безопасен: существующие
# .env, секреты и данные не трогаются, образы пересобираются, сервисы
# поднимаются заново.
#
#   cd deploy && ./install.sh
#
# Что делает: проверяет Docker и Compose v2, создаёт .env из .env.example
# (с генерацией ADMIN_API_KEY), токен службы речи в secrets/stt.token,
# каталоги data/ models/ backups/, собирает образы, запускает и ждёт
# готовности обоих сервисов.
source "$(dirname "$0")/_lib.sh"

say "== Выезды: установка =="
check_docker
ok "docker и compose v2 на месте"

# --- 1. .env ---------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  ok "создан .env из .env.example"
else
  chmod 600 .env 2>/dev/null || true
  ok ".env уже есть — оставляю как есть"
fi
if [ -z "$(env_get ADMIN_API_KEY)" ]; then
  env_set ADMIN_API_KEY "$(gen_secret)"
  ok "ADMIN_API_KEY сгенерирован (значение — в .env, он нужен пульту)"
fi

# --- 2. Секрет службы речи ---------------------------------------------------
mkdir -p secrets
chmod 750 secrets 2>/dev/null || true
if [ -d secrets/stt.token ]; then
  die "secrets/stt.token — каталог (так бывает, если compose запустили раньше install.sh). Удалите его: rm -r secrets/stt.token — и запустите снова"
fi
if [ ! -s secrets/stt.token ]; then
  (umask 027; gen_secret > secrets/stt.token)
  chmod 640 secrets/stt.token
  ok "создан secrets/stt.token (0640)"
fi

# --- 3. Каталоги данных ---------------------------------------------------------
mkdir -p data data/backups models backups
ok "каталоги data/ models/ backups/ на месте"

# --- 4. Сборка и запуск -----------------------------------------------------------
services="$(services_enabled)"
say "Сборка образов: $services (первый раз занимает несколько минут)…"
compose build $services
say "Запуск…"
compose up -d $services
if ! voice_enabled; then
  compose stop stt >/dev/null 2>&1 || true
fi

# --- 5. Ожидание готовности ---------------------------------------------------------
say "Жду готовности приложения…"
if wait_healthy app 180; then
  ok "app: $(health_of app)"
else
  compose logs --tail=40 app || true
  die "приложение не поднялось (см. журнал выше; обычно дело в .env)"
fi

port="$(app_port)"
if command -v curl >/dev/null 2>&1; then
  if curl -fsS -o /dev/null "http://127.0.0.1:${port}/"; then
    ok "http://127.0.0.1:${port}/ отвечает"
  else
    warn "http://127.0.0.1:${port}/ не отвечает, хотя контейнер здоров — проверьте APP_PORT"
  fi
fi

if voice_enabled; then
  say "Жду готовности службы речи (первый старт скачивает модель $(env_get STT_MODEL) — до 15 минут)…"
  if wait_healthy stt 900; then
    ok "stt: $(health_of stt)"
  else
    warn "служба речи ещё не готова: $(health_of stt). Проверьте позже: ./doctor.sh"
    warn "приложение при этом работает — голос временно недоступен, остаётся ручной ввод"
  fi
else
  ok "голос выключен (VOICE_BACKEND=$(env_get VOICE_BACKEND)) — контейнер stt не запущен"
fi

# --- 6. Итог -------------------------------------------------------------------------
say ""
say "== Готово =="
compose ps
say ""
say "Что дальше:"
step=1
if [ -z "$(env_get ADMIN_ID)" ]; then
  say "  $step. Заполните ADMIN_ID в .env (Telegram-id владельца) и выполните ./update.sh"; step=$((step+1))
fi
if [ -z "$(env_get MINIAPP_URL)" ]; then
  say "  $step. Заполните MINIAPP_URL в .env — публичный HTTPS-адрес приложения"; step=$((step+1))
fi
say "  $step. Настройте HTTPS: обратный прокси на http://127.0.0.1:${port}/ (README, «HTTPS обязателен»)"; step=$((step+1))
if [ -z "$(env_get BOT_TOKEN)" ]; then
  say "  $step. Telegram выключен (BOT_TOKEN пуст). Чтобы включить — впишите токен от @BotFather и ./update.sh"; step=$((step+1))
fi
say "  $step. Подключите пульт аналитика: адрес сервера и ADMIN_API_KEY из .env"; step=$((step+1))
say "  $step. Проверка состояния в любой момент: ./doctor.sh; копия базы: ./backup.sh"
