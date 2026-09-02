#!/usr/bin/env bash
# Обновление «Выезды» до версии, лежащей в соседних каталогах hardbot/ и stt/.
#
#   cd deploy && ./update.sh
#
# Порядок: копия базы (онлайн-бэкап SQLite, без остановки) → пересборка
# образов → перезапуск → ожидание готовности. Данные в data/ не затрагиваются.
# Тем же скриптом применяются правки .env (например, вписанный BOT_TOKEN).
source "$(dirname "$0")/_lib.sh"

say "== Выезды: обновление =="
check_docker
[ -f .env ] || die "нет .env — сначала ./install.sh"
[ -s secrets/stt.token ] || die "нет secrets/stt.token — сначала ./install.sh"

say "1/4 Резервная копия базы"
./backup.sh --db-only

services="$(services_enabled)"
say "2/4 Сборка образов: $services"
compose build $services

say "3/4 Перезапуск"
compose up -d $services
if ! voice_enabled; then
  compose stop stt >/dev/null 2>&1 || true
fi

say "4/4 Проверка"
if wait_healthy app 180; then
  ok "app: $(health_of app)"
else
  compose logs --tail=40 app || true
  die "приложение не поднялось после обновления. Откат: ./restore.sh backups/<последняя копия>"
fi
if voice_enabled; then
  if wait_healthy stt 900; then
    ok "stt: $(health_of stt)"
  else
    warn "служба речи ещё не готова: $(health_of stt) — проверьте позже: ./doctor.sh"
  fi
fi

# Старые слои наших образов после пересборки висят как <none>; чистим только их.
docker image prune -f --filter "label=ru.vyezdy.service" >/dev/null 2>&1 || true

say ""
say "== Обновлено =="
compose ps
