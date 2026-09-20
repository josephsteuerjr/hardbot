#!/usr/bin/env bash
# Приёмка машины перед установкой: что есть, куда пускают, что помешает.
# Ничего не устанавливает и не меняет. Запускать первым, до install.sh.
#
#   ./preflight.sh                                  — базовая проверка
#   HARD_EDGE=1.2.3.4:22 ./preflight.sh             — плюс проверка канала до кромки
#   ./preflight.sh --pull                           — плюс настоящая загрузка образа
set -u

ok=0; bad=0; warn=0
green() { printf '  [ ок ] %s\n' "$1"; ok=$((ok+1)); }
red()   { printf '  [ !! ] %s\n' "$1"; bad=$((bad+1)); }
note()  { printf '  [ -- ] %s\n' "$1"; warn=$((warn+1)); }
head_() { printf '\n%s\n' "$1"; }

head_ "Машина"
. /etc/os-release 2>/dev/null || true
os="${PRETTY_NAME:-неизвестно}"
case "${ID:-}" in
  ubuntu|debian) green "система: $os" ;;
  *)             note  "система: $os — скрипты проверены на Ubuntu и Debian" ;;
esac
[ -d /run/systemd/system ] && green "systemd есть" \
  || red "systemd нет — служба помощника панели не встанет"
cores=$(nproc 2>/dev/null || echo 0)
[ "$cores" -ge 2 ] && green "ядер: $cores" || red "ядер: $cores — нужно от 2"
mem=$(awk '/MemTotal/{printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)
[ "$mem" -ge 3500 ] && green "память: ${mem} МБ" || red "память: ${mem} МБ — нужно от 4 ГБ"
free=$(df -Pm /opt 2>/dev/null | awk 'NR==2{print $4}' || echo 0)
[ "${free:-0}" -ge 100000 ] && green "свободно на /opt: ${free} МБ" \
  || note "свободно на /opt: ${free} МБ — рекомендуется от 100 ГБ"
case "$(pwd)" in
  *[![:ascii:]]*|*\ *) red "путь установки содержит пробелы или кириллицу: $(pwd)" ;;
  *)                   green "путь установки без пробелов и кириллицы" ;;
esac

head_ "Время"
if command -v timedatectl >/dev/null 2>&1; then
  sync=$(timedatectl show -p NTPSynchronized --value 2>/dev/null)
  [ "$sync" = "yes" ] && green "часы синхронизированы (NTP)" \
    || note "часы НЕ синхронизированы — сломается проверка сертификатов и сроки токенов"
else
  note "timedatectl недоступен — синхронизацию часов проверить нечем"
fi

head_ "Docker"
if command -v docker >/dev/null 2>&1; then
  green "docker: $(docker --version 2>&1 | head -1)"
  docker info >/dev/null 2>&1 && green "демон отвечает, прав хватает" \
    || red "демон не отвечает или не хватает прав — нужен sudo или группа docker"
  if docker compose version >/dev/null 2>&1; then
    green "compose: $(docker compose version --short 2>&1)"
  else
    red "нет Docker Compose v2 — поставить пакет docker-compose-plugin"
  fi
else
  red "docker не установлен"
fi

head_ "Исходящий доступ"
probe() {
  code=$(curl -s -o /dev/null -m 12 -w '%{http_code}' "https://$1/" 2>/dev/null)
  if [ "${code:-000}" = "000" ]; then red "$1 — не отвечает ($2)"
  else green "$1 — отвечает, код $code ($2)"; fi
}
if command -v curl >/dev/null 2>&1; then
  probe registry-1.docker.io   "образы"
  probe auth.docker.io         "образы"
  probe pypi.org               "библиотеки"
  probe files.pythonhosted.org "библиотеки"
  probe api.telegram.org       "бот, если используется"
else
  red "нет curl — проверить исходящий доступ нечем"
fi

head_ "Подмена сертификатов (SSL-инспекция)"
if command -v openssl >/dev/null 2>&1; then
  iss=$(echo | timeout 12 openssl s_client -connect pypi.org:443 -servername pypi.org 2>/dev/null \
        | openssl x509 -noout -issuer 2>/dev/null)
  case "$iss" in
    "")        note "проверить не удалось — соединение не установилось" ;;
    *Fortinet*|*Forti*|*Proxy*|*proxy*|*Firewall*|*firewall*)
               red "соединения вскрываются: $iss" ;;
    *)         green "цепочка не подменяется (${iss#issuer=})" ;;
  esac
else
  note "нет openssl — подмену сертификатов проверить нечем"
fi

head_ "Канал до кромки"
if [ -n "${HARD_EDGE:-}" ]; then
  eh=${HARD_EDGE%%:*}; ep=${HARD_EDGE##*:}; [ "$ep" = "$eh" ] && ep=22
  if timeout 10 bash -c "cat < /dev/null > /dev/tcp/$eh/$ep" 2>/dev/null; then
    green "$eh:$ep — открыт"
  else
    red "$eh:$ep — закрыт, исходящий туннель не поднимется"
  fi
else
  note "адрес кромки не задан — запустите как HARD_EDGE=адрес:порт ./preflight.sh"
fi

head_ "Порт приложения"
if timeout 3 bash -c 'cat < /dev/null > /dev/tcp/127.0.0.1/8080' 2>/dev/null; then
  red "127.0.0.1:8080 уже занят — сменить APP_PORT в .env ядра"
else
  green "127.0.0.1:8080 свободен"
fi

if [ "${1:-}" = "--pull" ]; then
  head_ "Настоящая загрузка образа"
  if docker pull --quiet hello-world >/dev/null 2>&1; then
    green "образ скачался — установка пройдёт обычным путём"
    docker rmi hello-world >/dev/null 2>&1 || true
  else
    red "образ не скачался — ставить придётся привезённым бандлом (deploy/offline)"
  fi
fi

printf '\nИтог: успешно %d, замечаний %d, отказов %d\n' "$ok" "$warn" "$bad"
[ "$bad" -eq 0 ] || printf 'Отказы выше решаются ИТ-службой — см. deploy/ТРЕБОВАНИЯ-ИТ.md\n'
exit 0
