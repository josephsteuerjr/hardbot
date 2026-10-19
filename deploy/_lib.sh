#!/usr/bin/env bash
# Общие функции скриптов установки «Хард». Подключается через source,
# сам по себе не запускается.
#
# Библиотека одна на три единицы (core, stt, gps). Единица — это каталог с
# docker-compose.yml, .env и unit.conf; скрипты лежат ВНУТРИ него и подключают
# библиотеку так:  source "$(dirname "$0")/../../_lib.sh"
#
# Отсюда правило: рабочим каталогом становится каталог ВЫЗЫВАЮЩЕГО скрипта, а
# не каталог библиотеки — иначе `docker compose` брал бы чужой compose-файл и
# чужой .env. Чем единицы отличаются друг от друга, описано в их unit.conf.
set -euo pipefail

# DEPLOY_DIR — каталог самой библиотеки (общее для всех единиц: geo/, README).
# ВНИМАНИЕ: данные единицы лежат НЕ здесь, а в UNIT_DIR. Пути вида
# "$DEPLOY_DIR/data" теперь ошибочны и опасны молчанием: docker создаст пустой
# каталог и копия окажется пустой. Для данных всегда UNIT_DIR.
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Каталог единицы: где лежит скрипт, который нас подключил. HARD_UNIT_DIR —
# ручной обход для нестандартных случаев (запуск библиотеки из другого места).
UNIT_DIR="${HARD_UNIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[1]:-$0}")" && pwd)}"
cd "$UNIT_DIR"
ENV_FILE="$UNIT_DIR/.env"

# Общие имена, по которым единицы находят друг друга. Менять их нельзя:
# они прописаны в compose-файлах всех трёх единиц.
SHARED_NET="hard-net"          # сеть: ядро → маршрутизаторы (по HTTP)
SHARED_STT_VOLUME="hard-stt-run"  # том: ядро ↔ служба речи (Unix-сокет и токен)

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

# Сервисы, которые должны работать после установки. Это свойство единицы:
# список объявлен в её unit.conf (там же учитываются профили — встроенный
# HTTPS у ядра, страны у маршрутов). Если unit.conf почему-то не задал список,
# спрашиваем сам compose: он знает состав с учётом включённых профилей.
services_enabled() {
  if [ -n "${UNIT_SERVICES:-}" ]; then
    printf '%s\n' "$UNIT_SERVICES"
    return 0
  fi
  compose config --services 2>/dev/null | tr '\n' ' '
}

# Общие сеть и том с фиксированными именами. Создаются при установке ЛЮБОЙ из
# единиц — потому и порядок установки не важен: кто пришёл первым, тот и создал.
# Обе команды безвредны при повторе.
ensure_shared() {
  if [ "${UNIT_NEEDS_NET:-0}" = "1" ] && ! docker network inspect "$SHARED_NET" >/dev/null 2>&1; then
    docker network create "$SHARED_NET" >/dev/null \
      || die "не удалось создать общую сеть $SHARED_NET"
    ok "создана общая сеть $SHARED_NET"
  fi
  if [ "${UNIT_NEEDS_STT_VOL:-0}" = "1" ] && ! docker volume inspect "$SHARED_STT_VOLUME" >/dev/null 2>&1; then
    docker volume create "$SHARED_STT_VOLUME" >/dev/null \
      || die "не удалось создать общий том $SHARED_STT_VOLUME"
    ok "создан общий том $SHARED_STT_VOLUME"
  fi
}

# Профили из .env: proxy требует DOMAIN, иначе Caddy стартует с пустым адресом.
check_profiles() {
  local profiles
  profiles="$(env_get COMPOSE_PROFILES)"
  case ",${profiles}," in
    *,proxy,*)
      [ -n "$(env_get DOMAIN)" ] || die "в .env включён профиль proxy, но не задан DOMAIN"
      ;;
  esac
  # This overlay is opt-in and must bind a specific corporate IPv4 address.
  case "$(env_get COMPOSE_FILE)" in
    *docker-compose.lan.yml*|*docker-compose.lan-tls.yml*)
      local address a b c d part
      address="$(env_get APP_LAN_IP)"
      [[ "$address" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] \
        || die "APP_LAN_IP должен быть приватным IPv4 интерфейса ВМ"
      IFS=. read -r a b c d <<< "$address"
      for part in "$a" "$b" "$c" "$d"; do
        ((10#$part <= 255)) || die "некорректный APP_LAN_IP"
      done
      if ! ((10#$a == 10 || (10#$a == 172 && 10#$b >= 16 && 10#$b <= 31) ||
             (10#$a == 192 && 10#$b == 168))); then
        die "APP_LAN_IP должен быть в диапазоне 10/8, 172.16/12 или 192.168/16"
      fi
      ;;
  esac
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

# inspect_field СЕРВИС ШАБЛОН → поле docker inspect, пусто если контейнера нет
inspect_field() {
  local id
  id="$(container_id "$1")"
  [ -n "$id" ] || return 0
  docker inspect -f "$2" "$id" 2>/dev/null || true
}

# wait_healthy СЕРВИС СЕКУНДЫ — ждать healthy (или running, если healthcheck нет)
#
# Отдельно ловим цикл перезапусков. Контейнер, который стартует и сразу падает,
# со стороны healthcheck выглядит как «ещё греется»: статус starting, ошибок
# нет. Раньше на этом молча выжидались все 15 минут — типовой случай для службы
# речи с испорченным токеном. Признаков два: состояние restarting и счётчик
# перезапусков, выросший за время ожидания (старые перезапуски не в счёт —
# контейнер мог законно перезапускаться неделю назад).
wait_healthy() {
  local svc="$1" limit="${2:-120}" waited=0 status state restarts base=""
  while :; do
    status="$(health_of "$svc")"
    case "$status" in
      healthy|running) return 0 ;;
      exited|dead|нет)
        warn "$svc: контейнер остановился ($status) — смотрите журнал: docker compose logs --tail=50 $svc"
        return 1 ;;
    esac

    state="$(inspect_field "$svc" '{{.State.Status}}')"
    restarts="$(inspect_field "$svc" '{{.RestartCount}}')"
    [ -n "$base" ] || base="${restarts:-0}"
    if [ "$state" = "restarting" ] || [ "${restarts:-0}" -gt "${base:-0}" ]; then
      warn "$svc: контейнер падает и перезапускается по кругу (перезапусков: ${restarts:-?})."
      warn "Ждать бесполезно — это почти всегда ошибка настройки. Причина в последних строках:"
      warn "  docker compose logs --tail=50 $svc"
      return 1
    fi

    [ "$waited" -ge "$limit" ] && {
      warn "$svc: не дождались готовности за $limit с (состояние: $status)"
      return 1
    }
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

# --- Свойства единицы -------------------------------------------------------
# Подключается последним: unit.conf пользуется функциями выше (env_get), чтобы
# учитывать профили из .env. Если файла нет — работаем как раньше, состав
# сервисов тогда спросим у compose.
if [ -f "$UNIT_DIR/unit.conf" ]; then
  # shellcheck source=/dev/null
  . "$UNIT_DIR/unit.conf"
fi
UNIT_NAME="${UNIT_NAME:-$(basename "$UNIT_DIR")}"
UNIT_TITLE="${UNIT_TITLE:-$UNIT_NAME}"
UNIT_WAIT_SECONDS="${UNIT_WAIT_SECONDS:-180}"
