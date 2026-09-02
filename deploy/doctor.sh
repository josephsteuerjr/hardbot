#!/usr/bin/env bash
# Диагностика «Хард»: состояние контейнеров, здоровье, ответ приложения,
# служба речи, диск, память, свежие ошибки. Ничего не меняет — только читает.
#
#   ./doctor.sh
source "$(dirname "$0")/_lib.sh"

say "== Хард: диагностика ($(date '+%Y-%m-%d %H:%M')) =="
check_docker
[ -f .env ] || die "нет .env — сначала ./install.sh"

say ""
say "-- Контейнеры"
compose ps 2>/dev/null || warn "compose ps не отвечает"

say ""
say "-- Здоровье"
for svc in app stt; do
  printf '  %-5s %s\n' "$svc" "$(health_of "$svc")"
done
if ! voice_enabled; then
  say "  (голос выключен: VOICE_BACKEND=$(env_get VOICE_BACKEND) — stt и не должен работать)"
fi

say ""
say "-- Приложение"
port="$(app_port)"
if command -v curl >/dev/null 2>&1; then
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${port}/" || echo "000")"
  if [ "$code" = "200" ]; then
    ok "http://127.0.0.1:${port}/ → HTTP 200"
  else
    warn "http://127.0.0.1:${port}/ → HTTP $code"
  fi
else
  say "  curl не установлен — проверка HTTP пропущена"
fi
if [ -n "$(container_id app)" ]; then
  if compose logs --no-color --tail=200 app 2>/dev/null | grep -q "Telegram отключён"; then
    say "  Telegram: выключен (BOT_TOKEN пуст) — приложение работает, пуши и сводки не уходят"
  elif compose logs --no-color --tail=200 app 2>/dev/null | grep -q "Бот запущен"; then
    say "  Telegram: бот запущен"
  fi
fi

say ""
say "-- Служба речи"
if voice_enabled; then
  if [ "$(health_of stt)" = "нет" ]; then
    warn "контейнер stt не создан — ./install.sh или ./update.sh"
  else
    if out="$(compose exec -T stt python server.py --check 2>&1)"; then
      ok "распознавание: $out (модель $(env_get STT_MODEL))"
    else
      warn "распознавание: $out"
      say "  первые минуты после запуска модель скачивается — это нормально"
    fi
  fi
else
  say "  выключена (VOICE_BACKEND=$(env_get VOICE_BACKEND))"
fi

say ""
say "-- Настройки (.env)"
for key in ADMIN_ID MINIAPP_URL ADMIN_API_KEY BOT_TOKEN; do
  if [ -n "$(env_get "$key")" ]; then state="задан"; else state="ПУСТО"; fi
  case "$key" in
    MINIAPP_URL) printf '  %-14s %s\n' "$key" "$(env_get "$key")" ;;
    *) printf '  %-14s %s\n' "$key" "$state" ;;
  esac
done
printf '  %-14s %s\n' "VOICE_BACKEND" "$(env_get VOICE_BACKEND)"
printf '  %-14s %s\n' "STT_MODEL" "$(env_get STT_MODEL)"
printf '  %-14s %s\n' "APP_PORT" "$port"
[ -n "$(env_get COMPOSE_PROFILES)" ] && printf '  %-14s %s\n' "COMPOSE_PROFILES" "$(env_get COMPOSE_PROFILES)"
if [ -z "$(env_get BOT_TOKEN)" ]; then
  say "  BOT_TOKEN пуст — режим без Telegram (это допустимо)"
fi

say ""
say "-- Диск"
df -h "$DEPLOY_DIR" | tail -n 1 | awk '{printf "  свободно %s из %s (занято %s)\n", $4, $2, $5}'
printf '  %-22s %s\n' "data/hardbot.db" "$(human_size data/hardbot.db)"
printf '  %-22s %s\n' "data/photos" "$(human_size data/photos)"
printf '  %-22s %s\n' "models (речь)" "$(human_size models)"
printf '  %-22s %s\n' "backups" "$(human_size backups)"

say ""
say "-- Память"
if command -v free >/dev/null 2>&1; then
  free -m | awk 'NR==2{printf "  всего %d МБ, доступно %d МБ\n", $2, $7} NR==3{printf "  swap: занято %d из %d МБ\n", $3, $2}'
fi
names=""
for svc in app stt; do
  id="$(container_id "$svc")"
  [ -n "$id" ] && names="$names $id"
done
if [ -n "$names" ]; then
  docker stats --no-stream --format '  {{.Name}}: {{.MemUsage}}, CPU {{.CPUPerc}}' $names 2>/dev/null || true
fi

say ""
say "-- Резервные копии"
last="$(ls -1d backups/[0-9]*T[0-9]* 2>/dev/null | sort | tail -n 1 || true)"
if [ -n "$last" ]; then
  say "  последняя: $last ($(human_size "$last/hardbot.db"))"
else
  warn "копий базы ещё нет — ./backup.sh"
fi
[ -d backups/photos ] && say "  зеркало фотографий: backups/photos ($(human_size backups/photos))"

say ""
say "-- Ошибки за последние сутки (журнал app)"
errors="$(compose logs --no-color --since=24h app 2>/dev/null | grep -iE 'error|traceback|exception|critical' | tail -n 15 || true)"
if [ -n "$errors" ]; then
  echo "$errors" | sed 's/^/  /'
else
  say "  нет"
fi
if voice_enabled && [ -n "$(container_id stt)" ]; then
  stt_errors="$(compose logs --no-color --since=24h stt 2>/dev/null | grep -iE 'error|конфигурация|не загрузилась' | tail -n 5 || true)"
  if [ -n "$stt_errors" ]; then
    say "-- Ошибки службы речи"
    echo "$stt_errors" | sed 's/^/  /'
  fi
fi

say ""
say "Полный журнал: docker compose logs --since=1h app"
