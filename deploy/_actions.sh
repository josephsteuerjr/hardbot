#!/usr/bin/env bash
# Общие ШАГИ установки, обновления и диагностики: то, что у трёх единиц
# (core, stt, gps) одинаково. Подключается ПОСЛЕ _lib.sh:
#
#   source "$(dirname "$0")/../../_lib.sh"
#   source "$DEPLOY_DIR/_actions.sh"
#
# Почему один файл на три единицы, а не три копии кода: одинакового в них
# больше, чем разного (проверить докер, сделать .env, поднять, дождаться,
# показать состояние). Разное — чем единица занимается — остаётся в её
# собственных install.sh/update.sh/doctor.sh, и эти скрипты читаются сверху
# вниз без единого перехода в сторону. Три копии кода разошлись бы через месяц.
#
# Правило текстов в этом файле: сообщение об ошибке говорит, ЧТО СДЕЛАТЬ.
# «Не удалось запустить контейнер» без продолжения — это не сообщение.

# --- Офлайн-режим -------------------------------------------------------------
# Единственная точка, где закрытый контур подключается к установке. Раньше
# offline/mode.sh не вызывал никто: обещание «скрипты увидят отметку и ничего
# собирать не будут» было написано в deploy/offline/README.md и не выполнялось.
# Подключаем здесь, потому что через этот файл проходят ОБА выхода в интернет —
# сборка (build_images) и скачивание готовых образов (pull_images).
# Если каталог offline/ из поставки вырезали, работаем как раньше: заглушка
# говорит «мы не офлайн», и ни один вызов ниже не меняет поведения.
if [ -f "$DEPLOY_DIR/offline/mode.sh" ]; then
  source "$DEPLOY_DIR/offline/mode.sh"
else
  hard_offline() { return 1; }
  hard_images_present() { return 0; }
fi

# --- Счётчик замечаний -------------------------------------------------------
# Диагностика ничего не чинит, она только считает найденное. Код возврата
# нужен верхнему doctor.sh: по нему он показывает, в какой единице искать.
PROBLEMS=0
problem() { PROBLEMS=$((PROBLEMS + 1)); warn "$*"; }
# hint — строка «что сделать». Всегда идёт следом за problem/warn.
hint() { printf '       → %s\n' "$*" >&2; }
step() { printf '\n-- %s\n' "$*"; }

banner() {
  say "==== Хард · единица «$UNIT_NAME» · $1 ===="
  say "     $UNIT_TITLE"
  say "     каталог: $UNIT_DIR"
}

# --- Чужой .env ---------------------------------------------------------------
# env_get/env_set из _lib.sh работают со СВОИМ .env. Соседний нужен только
# для двух вещей: понять, знает ли ядро о нас, и дописать ему пустую строку.
env_file_get() {
  local file="$1" key="$2"
  [ -f "$file" ] || return 0
  sed -n -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" "$file" \
    | tail -n 1 \
    | sed -E "s/^\"(.*)\"[[:space:]]*$/\1/; s/^'(.*)'[[:space:]]*$/\1/; s/[[:space:]]+$//"
}

# ПРАВИЛО ПРАВКИ СОСЕДА. Заполненное значение — решение человека, его не
# трогаем. Пустую строку (и заводское значение «выключено», которое человек не
# выбирал, а получил из .env.example) дописываем сами: этот шаг маленький,
# забывается первым, а его цена — «поставили, а не работает, и непонятно
# почему». Каждая такая правка ГРОМКО объявляется, а применяется всё равно
# руками — ./update.sh соседа сами не запускаем.
env_file_set() {
  local file="$1" key="$2" value="$3"
  [ -f "$file" ] || return 1
  if grep -qE "^[[:space:]]*${key}[[:space:]]*=" "$file"; then
    sed -i -E "s|^[[:space:]]*${key}[[:space:]]*=.*|${key}=${value}|" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
  return 0
}

env_file_set_if_empty() {
  local file="$1" key="$2" value="$3"
  [ -f "$file" ] || return 1
  [ -z "$(env_file_get "$file" "$key")" ] || return 1
  env_file_set "$file" "$key" "$value"
}

# --- Соседи -------------------------------------------------------------------
unit_dir()       { printf '%s\n' "$DEPLOY_DIR/units/$1"; }
unit_installed() { [ -f "$DEPLOY_DIR/units/$1/.env" ]; }
core_env()       { printf '%s\n' "$DEPLOY_DIR/units/core/.env"; }

# Имя compose-проекта единицы (поле name: в её docker-compose.yml). По нему
# соседа видно, даже когда его каталог не читается, — контейнеры общие.
project_of() {
  case "$1" in
    core) echo hard-core ;;
    stt)  echo hard-stt ;;
    gps)  echo hard-gps ;;
    *)    echo "hard-$1" ;;
  esac
}

# Какие сервисы соседа сейчас РАБОТАЮТ (по метке compose, а не по имени
# контейнера: жёстких имён у нас больше нет).
project_services_running() {
  docker ps --filter "label=com.docker.compose.project=$1" \
            --format '{{.Label "com.docker.compose.service"}}' 2>/dev/null | sort -u
}
unit_running() { [ -n "$(project_services_running "$(project_of "$1")")" ]; }

# --- Проверки до работы -------------------------------------------------------
# Пакет для ИТ собирается сборщиком, и из него легко потерять каталог с
# исходниками (сборщик режет тяжёлое). Тогда docker ругается на несуществующий
# контекст сборки — по этому тексту непонятно ничего.
require_sources() {
  local dir
  for dir in "$@"; do
    [ -d "$DEPLOY_DIR/../$dir" ] || die "рядом с deploy/ нет каталога «$dir/» — распакована только часть пакета.
Что сделать: распакуйте архив целиком (в нём рядом лежат server/, stt/, deploy/) и запустите ./install.sh из распакованного deploy/units/$UNIT_NAME."
  done
}

create_env() {
  if [ ! -f .env ]; then
    [ -f .env.example ] || die "в каталоге единицы нет ни .env, ни .env.example — каталог неполный.
Что сделать: распакуйте пакет заново, каталог deploy/units/$UNIT_NAME должен содержать docker-compose.yml, unit.conf и .env.example."
    cp .env.example .env
    chmod 600 .env 2>/dev/null || true
    ok "создан .env из .env.example (дальше правки вносить в .env, не в .env.example)"
  else
    chmod 600 .env 2>/dev/null || true
    ok ".env уже есть — оставляю как есть (повторная установка ничего не затирает)"
  fi
}

require_env() {
  [ -f .env ] || die "в каталоге $UNIT_DIR нет .env — эта единица ещё не установлена.
Что сделать: ./install.sh (он и создаст .env из .env.example)."
}

# Свободное место. Образы и модель занимают ощутимо, а «no space left on
# device» посреди сборки выглядит как поломка программы, хотя это диск.
warn_if_low_disk() {
  local need_gb="$1" free_gb
  free_gb="$(df -P -BG "$UNIT_DIR" 2>/dev/null | awk 'NR==2{gsub("G","",$4); print $4}')" || return 0
  [ -n "$free_gb" ] || return 0
  if [ "$free_gb" -lt "$need_gb" ]; then
    warn "на диске свободно ${free_gb} ГБ, а этой единице нужно около ${need_gb} ГБ"
    hint "освободите место заранее: docker image prune -a (удалит НЕиспользуемые образы) или подключите диск побольше"
  fi
}

# --- Образы -------------------------------------------------------------------
unit_images() {
  case "$UNIT_NAME" in
    core) echo "hard-app" ;;
    stt)  echo "hard-stt" ;;
    *)    echo "" ;;
  esac
}

# Сборка идёт в терминал (иначе инженер несколько минут смотрит в пустоту) и
# одновременно в файл — чтобы разобрать причину, когда она уехала вверх.
build_images() {
  local log
  # Закрытый контур: собирать нечем (сборка идёт в Docker Hub и в PyPI) и нечего
  # — образы туда привозят готовыми и загружают через deploy/offline/load.sh.
  # Проверку «а привезли ли их» делает сам compose_build_unless_offline: сказать
  # «сборка пропущена» и промолчать про отсутствующий образ было бы хуже отказа.
  if hard_offline; then
    say "Офлайн-режим: образы должны быть привезены готовыми, сборка не запускается."
    compose_build_unless_offline
    return 0
  fi
  log="$(mktemp -t hard-build-XXXXXX.log 2>/dev/null || echo /tmp/hard-build.log)"
  say "Сборка образов. Первый раз это несколько минут и нужен интернет."
  if compose build 2>&1 | tee "$log"; then
    ok "образы собраны"
    rm -f "$log"
    return 0
  fi
  say ""
  if grep -qiE 'dial tcp|no such host|TLS handshake|i/o timeout|temporary failure|proxyconnect|certificate' "$log"; then
    die "сборка не удалась: сервер не может скачать базовые образы и библиотеки (нет интернета или мешает прокси).
Что сделать — одно из двух:
  1) открыть серверу доступ к registry-1.docker.io и pypi.org (и вписать прокси в /etc/docker/daemon.json, если он есть);
  2) собрать образ на машине с интернетом и перенести файлом:
       там:      cd deploy/units/$UNIT_NAME && docker compose build && docker save $(unit_images) -o hard-images.tar
       здесь:    docker load -i hard-images.tar && ./install.sh
Полный текст сборки: $log"
  fi
  if grep -qiE 'no space left on device' "$log"; then
    die "сборка не удалась: на диске кончилось место.
Что сделать: освободите место (docker image prune -a удалит неиспользуемые образы) и запустите ./install.sh снова.
Полный текст сборки: $log"
  fi
  die "сборка образов не удалась. Причина — в последних строках выше.
Что сделать: пришлите разработчику файл $log — в нём весь вывод сборки."
}

# Готовые образы (единица gps). Отдельно от сборки: там нечего собирать.
pull_images() {
  local log
  # То же, что и в build_images: в закрытом контуре качать неоткуда, образы уже
  # загружены из офлайн-пакета. Если их нет — hard_images_present назовёт
  # поимённо, каких именно, и что с этим делать.
  if hard_offline; then
    say "Офлайн-режим: готовые образы должны быть уже загружены, из интернета ничего не качаю."
    hard_images_present || exit 1
    ok "образы на месте"
    return 0
  fi
  log="$(mktemp -t hard-pull-XXXXXX.log 2>/dev/null || echo /tmp/hard-pull.log)"
  say "Скачиваю готовые образы (нужен интернет; уже скачанные не качаются заново)…"
  if compose pull 2>&1 | tee "$log"; then
    ok "образы на месте"
    rm -f "$log"
    return 0
  fi
  say ""
  warn "не удалось скачать образы (нет интернета, мешает прокси — или у образа не стало такого тега)"
  hint "если образы уже есть на сервере — установка продолжится на них"
  hint "если их нет: перенесите файлом с машины с интернетом — docker save ghcr.io/project-osrm/osrm-backend:latest vroomvrp/vroom-docker:v1.13.0 -o gps-images.tar, затем здесь docker load -i gps-images.tar"
  hint "полный текст: $log"
  return 0
}

# --- Запуск -------------------------------------------------------------------
# Поднимаем ВСЕ сервисы включённых профилей (без списка): иначе не стартуют
# ни caddy по профилю proxy, ни одноразовый контейнер токена речи.
start_unit() {
  local out
  ensure_shared
  say "Запуск контейнеров…"
  if out="$(compose up -d 2>&1)"; then
    printf '%s\n' "$out" | sed 's/^/  /'
    return 0
  fi
  printf '%s\n' "$out" | sed 's/^/  /'
  say ""
  case "$out" in
    *"port is already allocated"*|*"address already in use"*|*"Ports are not available"*|*"bind: "*)
      die "порт на сервере уже занят другой программой (его номер — в строке ошибки выше).
Что сделать — одно из двух:
  1) найдите, кто держит порт (ss -ltnp | grep <номер>), и остановите ту программу;
  2) поменяйте порт в .env этой единицы (у ядра это APP_PORT, у маршрутов — OSRM_PORT_* и VROOM_PORT) и запустите ./install.sh снова." ;;
    *"declared as external"*)
      die "нет общей сети или общего тома, которые связывают единицы.
Что сделать: docker network create $SHARED_NET && docker volume create $SHARED_STT_VOLUME — и ./install.sh снова.
(Так бывает, только если запускали docker compose руками, минуя install.sh: обычно их создаёт он.)" ;;
    *"dependency failed to start"*|*"service_completed_successfully"*)
      die "не отработал подготовительный контейнер — тот, что кладёт токен доступа в общий том.
Что сделать: посмотрите его причину (docker compose logs stt-token), затем ./install.sh снова.
Если он жалуется на сам токен — удалите старый и повторите: docker run --rm -v $SHARED_STT_VOLUME:/run/stt hard-stt rm -f /run/stt/stt.token" ;;
    *"no space left"*)
      die "на диске кончилось место.
Что сделать: освободите место (docker image prune -a удалит неиспользуемые образы) и запустите ./install.sh снова." ;;
    *"failed to resolve reference"*|*"manifest unknown"*|*"pull access denied"*|*"not found: manifest"*)
      die "докер не нашёл нужный образ.
Что сделать — по порядку:
  1) если сервер без интернета: перенесите образы файлом (на машине с интернетом docker pull, затем docker save … -o images.tar; здесь docker load -i images.tar);
  2) если интернет есть, а реестр отвечает «not found» — у образа больше нет такого тега. Точное имя образа видно в строке ошибки выше; сообщите его разработчику: тег правится в docker-compose.yml этой единицы." ;;
    *"permission denied"*|*"Cannot connect to the Docker daemon"*)
      die "нет прав на docker или демон не запущен.
Что сделать: запустите от root (sudo ./install.sh) или добавьте пользователя в группу docker (usermod -aG docker \$USER, затем перелогиниться)." ;;
    *)
      die "контейнеры не запустились, текст ошибки — выше.
Что сделать: покажите этот текст разработчику; состояние в любой момент — ../../doctor.sh" ;;
  esac
}

# Ждём готовности каждого сервиса, который ДОЛЖЕН работать (список — свойство
# единицы, см. unit.conf). Ожидание — wait_healthy из _lib.sh: он отличает
# «греется» от «падает по кругу» и не молчит пятнадцать минут.
wait_unit() {
  local svc failed=0
  for svc in $(services_enabled); do
    say "Жду готовности: $svc (до $UNIT_WAIT_SECONDS с)…"
    if wait_healthy "$svc" "$UNIT_WAIT_SECONDS"; then
      ok "$svc: $(health_of "$svc")"
    else
      failed=1
      say "  последние строки журнала $svc:"
      compose logs --no-color --tail=30 "$svc" 2>/dev/null | sed 's/^/  | /' || true
    fi
  done
  return "$failed"
}

# --- Диагностика (кирпичи для doctor.sh каждой единицы) -----------------------
doctor_banner() {
  say "==== единица «$UNIT_NAME» — $UNIT_TITLE ===="
  if [ ! -f .env ]; then
    say "  не установлена (нет .env). Если она не нужна — так и должно быть."
    say "  Поставить: cd $UNIT_DIR && ./install.sh"
    return 1
  fi
  return 0
}

doctor_containers() {
  step "Контейнеры"
  compose ps 2>/dev/null | sed 's/^/  /' || problem "docker compose ps не отвечает — проверьте, работает ли докер: docker info"
  step "Здоровье"
  local svc state
  for svc in $(services_enabled); do
    state="$(health_of "$svc")"
    printf '  %-10s %s\n' "$svc" "$state"
    case "$state" in
      healthy|running) ;;
      нет)
        problem "$svc: контейнера нет — единица не запускалась или её остановили"
        hint "поднять: cd $UNIT_DIR && ./install.sh" ;;
      starting)
        say "  ($svc ещё греется — это нормально первые минуты после запуска)" ;;
      *)
        problem "$svc: состояние «$state»"
        hint "смотреть причину: cd $UNIT_DIR && docker compose logs --tail=50 $svc" ;;
    esac
  done
}

# Общие сеть и том: без них единицы друг друга не видят.
doctor_shared() {
  step "Общее хозяйство"
  if [ "${UNIT_NEEDS_NET:-0}" = "1" ]; then
    if docker network inspect "$SHARED_NET" >/dev/null 2>&1; then
      ok "сеть $SHARED_NET есть"
    else
      problem "нет общей сети $SHARED_NET — ядро не увидит маршрутизаторы"
      hint "создать: docker network create $SHARED_NET, затем ./install.sh этой единицы"
    fi
  fi
  if [ "${UNIT_NEEDS_STT_VOL:-0}" = "1" ]; then
    if docker volume inspect "$SHARED_STT_VOLUME" >/dev/null 2>&1; then
      ok "том $SHARED_STT_VOLUME есть"
    else
      problem "нет общего тома $SHARED_STT_VOLUME — ядро и речь не свяжутся"
      hint "создать: docker volume create $SHARED_STT_VOLUME, затем ./install.sh этой единицы"
    fi
  fi
}

doctor_errors() {
  local svc errors
  for svc in "$@"; do
    [ -n "$(container_id "$svc")" ] || continue
    errors="$(compose logs --no-color --since=24h "$svc" 2>/dev/null \
              | grep -iE 'error|traceback|exception|critical|не удалось' | tail -n 10 || true)"
    if [ -n "$errors" ]; then
      step "Ошибки за сутки: $svc"
      printf '%s\n' "$errors" | sed 's/^/  /'
    fi
  done
}

doctor_tail() {
  say ""
  if [ "$PROBLEMS" -eq 0 ]; then
    say "Итог по единице «$UNIT_NAME»: замечаний нет."
  else
    say "Итог по единице «$UNIT_NAME»: замечаний — $PROBLEMS (см. строки [!!] выше, у каждой написано, что сделать)."
  fi
}

# Код возврата диагностики: 0 — чисто, 1 — есть замечания. Верхний doctor.sh
# по нему показывает, в какой единице искать причину.
doctor_exit() { [ "$PROBLEMS" -eq 0 ] && exit 0 || exit 1; }
