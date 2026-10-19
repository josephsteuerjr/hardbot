#!/usr/bin/env bash
# ДИАГНОСТИКА ЯДРА. Ничего не меняет — только смотрит и говорит, что сделать.
#
#   cd deploy/units/core && ./doctor.sh      # только ядро
#   cd deploy && ./doctor.sh                 # все три единицы сразу
source "$(dirname "$0")/../../_lib.sh"
source "$DEPLOY_DIR/_actions.sh"

doctor_banner || { say ""; exit 0; }
check_docker

doctor_shared
doctor_containers

# --- Приложение ---------------------------------------------------------------
step "Приложение"
port="$(app_port)"
if command -v curl >/dev/null 2>&1; then
  # КОД ОТВЕТА БЕРЁМ БЕЗ «|| echo 000». При отказе curl печатает «000» сам — так
  # устроен -w '%{http_code}'. А «|| echo 000» дописывал второй «000» СВЕРХУ кода,
  # если curl отдал код и при этом завершился ненулевым (медленная страница и
  # --max-time, обрыв тела, ошибка записи в /dev/null). Выходило «200000»,
  # сравнение с «200» не срабатывало, и доктор объявлял живое приложение мёртвым.
  # «|| true» гасит set -e, ничего не дописывая; пустую строку (curl вовсе не
  # запустился) превращаем в «000» уже после подстановки.
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${port}/" 2>/dev/null)" || true
  code="${code:-000}"
  if [ "$code" = "200" ]; then
    ok "http://127.0.0.1:${port}/ → 200"
  else
    problem "http://127.0.0.1:${port}/ → $code (приложение не отвечает по этому адресу)"
    hint "если контейнер здоров — сверьте APP_PORT в .env с тем, что в docker compose ps"
    hint "журнал: docker compose logs --tail=50 app"
  fi
else
  say "  curl не установлен — проверку HTTP пропускаю (apt install curl)"
fi

if [ -n "$(container_id app)" ]; then
  if compose logs --no-color --tail=200 app 2>/dev/null | grep -q "Telegram отключён"; then
    say "  телеграм: выключен (BOT_TOKEN пуст) — приложение работает, пуши и сводки не уходят"
  elif compose logs --no-color --tail=200 app 2>/dev/null | grep -q "Бот запущен"; then
    say "  телеграм: бот запущен"
  fi
fi

# --- Речь ---------------------------------------------------------------------
# Ядро не ходит к речи по сети: сокет и токен лежат в общем томе. Поэтому
# проверяем ровно то, что видит сам контейнер приложения.
step "Голосовые комментарии"
if voice_enabled; then
  if [ -z "$(container_id app)" ]; then
    say "  включены, но контейнер приложения не работает — сперва поднимите ядро"
  elif compose exec -T app python -c "
import socket
s = socket.socket(socket.AF_UNIX); s.settimeout(3)
s.connect('/run/stt/stt.sock'); s.close()
open('/run/stt/stt.token', 'rb').read(1)
" >/dev/null 2>&1; then
    # Проверяем не наличие файлов, а живое соединение: после падения службы
    # файл сокета остаётся лежать в томе, и по нему всё выглядит исправным.
    ok "ядро дозванивается до службы речи через сокет и читает токен — распознавание доступно"
  elif compose exec -T app sh -c '[ -s /run/stt/stt.token ]' >/dev/null 2>&1; then
    problem "токен речи ядро видит, а соединиться со службой не может — она не запущена или упала"
    hint "поднять и проверить: cd $(unit_dir stt) && ./install.sh   (диагностика: cd $(unit_dir stt) && ./doctor.sh)"
  else
    problem "включён голос (VOICE_BACKEND=$(env_get VOICE_BACKEND)), но единицы речи нет"
    hint "либо поставьте речь: cd $(unit_dir stt) && ./install.sh"
    hint "либо выключите голос: VOICE_BACKEND=off в .env и ./update.sh — тогда сотрудники вводят текст руками"
  fi
else
  say "  выключены (VOICE_BACKEND=$(env_get VOICE_BACKEND)) — допустимый режим"
  if unit_running stt; then
    warn "единица речи при этом работает, но ядро её не использует"
    hint "включить: VOICE_BACKEND=stt в .env и ./update.sh"
  fi
fi

# --- Маршруты -----------------------------------------------------------------
step "Маршруты по дорогам"
routes=0
for key in OSRM_URL_UZ OSRM_URL_TJ OSRM_URL_RU OSRM_URL VROOM_URL; do
  url="$(env_get "$key")"
  [ -n "$url" ] || continue
  routes=$((routes + 1))
  host="$(printf '%s' "$url" | sed -E 's#^https?://##; s#/.*$##; s#:.*$##')"
  hport="$(printf '%s' "$url" | sed -nE 's#^https?://[^:/]+:([0-9]+).*#\1#p')"
  hport="${hport:-80}"
  if [ -z "$(container_id app)" ]; then
    say "  $key = $url (проверить не могу: контейнер приложения не работает)"
  elif compose exec -T app python -c "
import socket, sys
socket.create_connection((sys.argv[1], int(sys.argv[2])), 3).close()
" "$host" "$hport" >/dev/null 2>&1; then
    ok "$key = $url — ядро дозванивается"
  else
    problem "$key = $url — ядро НЕ дозванивается до маршрутизатора"
    hint "проверьте единицу маршрутов: cd $(unit_dir gps) && ./doctor.sh"
    hint "имя в адресе должно совпадать с именем сервиса в units/gps/docker-compose.yml (osrm-uz, osrm-tj, osrm-ru, vroom)"
    hint "пока не работает — маршрут считается по прямой, система при этом жива"
  fi
done
if [ "$routes" -eq 0 ]; then say "  не подключены — расстояния по прямой (допустимый режим)"; fi

# --- Настройки ----------------------------------------------------------------
step "Настройки (.env)"
for key in ADMIN_ID MINIAPP_URL ADMIN_API_KEY BOT_TOKEN; do
  value="$(env_get "$key")"
  case "$key" in
    MINIAPP_URL|ADMIN_ID) printf '  %-14s %s\n' "$key" "${value:-ПУСТО}" ;;
    *) printf '  %-14s %s\n' "$key" "$([ -n "$value" ] && echo задан || echo ПУСТО)" ;;
  esac
done
printf '  %-14s %s\n' "VOICE_BACKEND" "$(env_get VOICE_BACKEND)"
printf '  %-14s %s\n' "APP_PORT" "$port"
if [ -n "$(env_get COMPOSE_PROFILES)" ]; then
  printf '  %-14s %s\n' "COMPOSE_PROFILES" "$(env_get COMPOSE_PROFILES)"
fi
if [ -z "$(env_get ADMIN_API_KEY)" ]; then
  problem "ADMIN_API_KEY пуст — пульт аналитика не подключится"
  hint "восстановить: ./install.sh (он сгенерирует ключ) и затем вписать этот ключ в пульт"
fi
owner_state="$(compose exec -T app python -B - <<'PY' 2>/dev/null
import sqlite3

c = sqlite3.connect('file:/data/hardbot.db?mode=ro', uri=True)
tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
latched = False
if 'settings' in tables:
    row = c.execute("SELECT value FROM settings WHERE key='account_cutover_activated_at_utc'").fetchone()
    latched = bool(row and row[0])
state = 'missing' if latched else 'legacy'
needed = {'accounts', 'account_admin_grants', 'account_devices', 'manager_devices', 'account_token_sessions'}
if needed <= tables:
    owner = c.execute("SELECT a.id FROM accounts a JOIN account_admin_grants g ON g.account_id=a.id "
                      "WHERE a.role='analyst' AND a.status='active' AND a.must_change_password=0 "
                      "AND g.granted_by='ssh-root' AND length(g.principal)>0 LIMIT 1").fetchone()
    if owner:
        pc = c.execute("SELECT 1 FROM account_devices d JOIN account_token_sessions s ON s.device_id=d.id "
                       "JOIN manager_devices m ON m.id=s.token_id AND s.token_table='manager_devices' "
                       "WHERE d.account_id=? AND d.kind='desktop' AND d.status='active' "
                       "AND d.token_table='manager_devices' AND length(d.public_key)>0 "
                       "AND m.account_id=d.account_id AND m.revoked_at IS NULL LIMIT 1", owner).fetchone()
        state = 'ready' if pc else 'needs_pc'
c.close()
print(state)
PY
)" || owner_state=unavailable
case "$owner_state" in
  ready) ok "владелец: активный личный профиль и подтверждённый ПК; Telegram ADMIN_ID для этого входа не требуется" ;;
  needs_pc)
    problem "профиль владельца создан, но активный подтверждённый ПК не найден"
    hint "войдите в «Взыскание СНГ — Аналитик» со своим логином и паролем; восстановление доступно в «Подключении» через root или sudo" ;;
  missing)
    problem "после перехода на личные профили активный профиль владельца не найден"
    hint "создайте или восстановите профиль в «Подключении» Аналитика после проверки IP и root/sudo; не открывайте прежние способы входа" ;;
  legacy)
    if [ -z "$(env_get ADMIN_ID)" ]; then
      problem "владелец ещё не настроен"
      hint "создайте первичный профиль в «Подключении» Аналитика после проверки IP и root/sudo"
    fi ;;
  *)
    problem "личный профиль владельца не удалось проверить"
    hint "сначала проверьте доступность приложения и базы; затем повторите диагностику" ;;
esac
url="$(env_get MINIAPP_URL)"
case "$url" in
  https://*) ;;
  "") problem "MINIAPP_URL пуст — мини-приложение не откроется у сотрудников"
      hint "впишите публичный адрес вида https://hard.company.ru в .env и ./update.sh" ;;
  http://*) problem "MINIAPP_URL начинается с http:// — по обычному http не работают камера, гео и офлайн-режим"
      hint "нужен HTTPS: поставьте сертификат на свой nginx или включите COMPOSE_PROFILES=proxy с DOMAIN, затем ./update.sh" ;;
esac

# --- Место и копии ------------------------------------------------------------
step "Место на диске"
df -h "$UNIT_DIR" | tail -n 1 | awk '{printf "  свободно %s из %s (занято %s)\n", $4, $2, $5}'
printf '  %-22s %s\n' "data/hardbot.db" "$(human_size data/hardbot.db)"
printf '  %-22s %s\n' "data/photos" "$(human_size data/photos)"
printf '  %-22s %s\n' "backups" "$(human_size backups)"

step "Резервные копии"
last="$(ls -1d backups/[0-9]*T[0-9]* 2>/dev/null | sort | tail -n 1 || true)"
if [ -n "$last" ]; then
  say "  последняя: $last ($(human_size "$last/hardbot.db"))"
else
  problem "копий базы ещё нет"
  hint "снять сейчас: ./backup.sh   (и поставить в cron: 0 3 * * * cd $UNIT_DIR && ./backup.sh)"
fi
if [ -d backups/photos ]; then say "  зеркало фотографий: backups/photos ($(human_size backups/photos))"; fi

doctor_errors app
doctor_tail
doctor_exit
