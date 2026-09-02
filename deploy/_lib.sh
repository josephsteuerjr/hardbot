#!/usr/bin/env bash
# Общие функции скриптов установки «Выезды». Подключается через source,
# сам по себе не запускается.
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DEPLOY_DIR"
ENV_FILE="$DEPLOY_DIR/.env"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  [ок] %s\n' "$*"; }
warn() { printf '  [!!] %s\n' "$*" >&2; }
die()  { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "не найдена команда «$1» — $2"; }

check_docker() {
  need_cmd docker "установите Docker Engine: https://docs.docker.com/engine/install/"
  docker info >/dev/null 2>&1 \
    || die "docker недоступен: демон не запущен или нет прав (запустите от root или добавьте пользователя в группу docker)"
  docker compose version >/dev/null 2>&1 \
    || die "нужен Docker Compose v2 (команда «docker compose», не «docker-compose»)"
}

compose() { docker compose "$@"; }

# env_get КЛЮЧ — значение из .env (кавычки сняты), пусто если строки нет
env_get() {
  local key="$1"
  [ -f "$ENV_FILE" ] || return 0
  sed -n -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" \
    | tail -n 1 \
    | sed -E "s/^\"(.*)\"[[:space:]]*$/\1/; s/^'(.*)'[[:space:]]*$/\1/; s/[[:space:]]+$//"
}

# env_set КЛЮЧ ЗНАЧЕНИЕ — заменить строку в .env или добавить в конец
env_set() {
  local key="$1" value="$2"
  if grep -qE "^[[:space:]]*${key}[[:space:]]*=" "$ENV_FILE"; then
    sed -i -E "s|^[[:space:]]*${key}[[:space:]]*=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

# Случайный секрет: 64 шестнадцатеричных символа (годится и для ADMIN_API_KEY,
# и для токена службы речи — там нужно 32+ печатных ASCII).
gen_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets; print(secrets.token_hex(32))'
  else
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
    echo
  fi
}

voice_enabled() {
  local voice
  voice="$(env_get VOICE_BACKEND | tr '[:upper:]' '[:lower:]')"
  case "$voice" in stt|praxis) return 0 ;; *) return 1 ;; esac
}

# Базовые сервисы, которые должны работать: stt — только при включённом голосе.
services_enabled() {
  if voice_enabled; then echo "app stt"; else echo "app"; fi
}

container_id() { compose ps -q "$1" 2>/dev/null || true; }

# health_of СЕРВИС → healthy | starting | unhealthy | running | exited | нет
health_of() {
  local id
  id="$(container_id "$1")"
  [ -n "$id" ] || { echo "нет"; return 0; }
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null \
    || echo "нет"
}

# wait_healthy СЕРВИС СЕКУНДЫ — ждать healthy (или running, если healthcheck нет)
wait_healthy() {
  local svc="$1" limit="${2:-120}" waited=0 status
  while :; do
    status="$(health_of "$svc")"
    case "$status" in
      healthy|running) return 0 ;;
      exited|dead|нет) return 1 ;;
    esac
    [ "$waited" -ge "$limit" ] && return 1
    sleep 5
    waited=$((waited + 5))
    if [ $((waited % 60)) -eq 0 ]; then
      printf '  … %s: %s, прошло %d с\n' "$svc" "$status" "$waited"
    fi
  done
}

app_port() {
  local port
  port="$(env_get APP_PORT)"
  echo "${port:-8080}"
}

human_size() {  # human_size ПУТЬ → «1,2G» или «—»
  if [ -e "$1" ]; then du -sh "$1" 2>/dev/null | cut -f1; else echo "—"; fi
}
