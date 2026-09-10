#!/usr/bin/env bash
# УСТАНОВКА МАРШРУТОВ (OSRM по странам + оптимизатор порядка) одной командой:
#
#   cd deploy/units/gps && ./install.sh
#
# Это надстройка: без неё система работает полностью, только расстояния
# считаются по прямой, а порядок объезда — упрощённым алгоритмом.
#
# Собирать здесь нечего — образы готовые, они просто скачиваются. Зато нужны
# дорожные графы: их готовят заранее по рецепту deploy/geo/README.md и кладут
# в osrm/<страна>/. Скрипт проверяет их ДО запуска: маршрутизатор без графа
# уходит в вечный перезапуск, и по журналу это выглядит непонятно.
source "$(dirname "$0")/../../_lib.sh"
source "$DEPLOY_DIR/_actions.sh"

banner "установка"
check_docker
ok "докер и compose v2 на месте"

# --- 1. Настройки ------------------------------------------------------------
step "Настройки"
create_env

countries=""
for c in $(env_get COMPOSE_PROFILES | tr ',' ' '); do
  case "$c" in
    uz|tj|ru) countries="$countries $c" ;;
    "") ;;
    *) warn "в COMPOSE_PROFILES указано «$c» — такой страны нет, строка пропущена"
       hint "допустимы только uz, tj, ru — через запятую, например COMPOSE_PROFILES=uz,ru" ;;
  esac
done
countries="$(printf '%s' "$countries" | sed 's/^ //')"
if [ -n "$countries" ]; then
  say "  страны: $countries"
else
  say "  страны не выбраны — поднимется только оптимизатор порядка объезда (vroom)"
  say "  Чтобы поднять дороги, впишите в .env строку вида COMPOSE_PROFILES=uz,ru и повторите ./install.sh"
fi

# --- 2. Графы ----------------------------------------------------------------
step "Дорожные графы"
for c in $countries; do
  key="OSRM_GRAPH_$(printf '%s' "$c" | tr '[:lower:]' '[:upper:]')"
  graph="$(env_get "$key")"
  mkdir -p "osrm/$c"
  if [ -z "$graph" ]; then
    die "для страны «$c» не задано имя файла графа ($key в .env).
Что сделать: впишите в .env строку $key=<имя файла>.osrm — ровно так, как называется файл в osrm/$c/."
  fi
  if [ ! -f "osrm/$c/$graph" ]; then
    die "не найден дорожный граф страны «$c»: $UNIT_DIR/osrm/$c/$graph
Что сделать — одно из трёх:
  1) положите готовый граф: ВСЕ файлы вида $graph* (их около десятка) в каталог osrm/$c/;
  2) соберите граф сами по рецепту $DEPLOY_DIR/geo/README.md (несколько часов машинного времени и ~10 ГБ диска);
  3) если эта страна пока не нужна — уберите «$c» из COMPOSE_PROFILES в .env и запустите ./install.sh снова.
Без графа маршрутизатор этой страны будет падать и перезапускаться по кругу, поэтому дальше не иду."
  fi
  ok "$c: граф $graph на месте ($(human_size "osrm/$c"))"
done

# --- 3. Образы и запуск ------------------------------------------------------
step "Образы"
pull_images
step "Запуск"
start_unit

# --- 4. Готовность -----------------------------------------------------------
step "Проверка"
if ! wait_unit; then
  die "маршрутизаторы не поднялись.
Что сделать по порядку:
  1) чаще всего дело в графе: файлы неполные или собраны другой версией OSRM — пересоберите по $DEPLOY_DIR/geo/README.md;
  2) причина видна в журнале: docker compose logs --tail=50 <сервис>;
  3) ядро при этом работает: маршрут считается по прямой, пока эта единица не поднялась."
fi

probe() {  # probe ИМЯ АДРЕС — «отвечает» или замечание
  local name="$1" url="$2" code
  command -v curl >/dev/null 2>&1 || return 0
  # Без «|| echo 000»: curl при отказе печатает «000» сам, второй «000» сверху
  # давал «000000» — и проверка молчания не срабатывала, установка сообщала
  # «отвечает» о мёртвом сервисе. Пустой вывод превращаем в «000» подстановкой.
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null)"
  code="${code:-000}"
  if [ "$code" = "000" ]; then
    warn "$name не отвечает на $url"
    hint "смотрите журнал: docker compose logs --tail=50 $name"
  else
    ok "$name отвечает (HTTP $code)"
  fi
}
for c in $countries; do
  pkey="OSRM_PORT_$(printf '%s' "$c" | tr '[:lower:]' '[:upper:]')"
  port="$(env_get "$pkey")"
  probe "osrm-$c" "http://127.0.0.1:${port:-5000}/nearest/v1/driving/69.24,41.31"
done
vroom_port="$(env_get VROOM_PORT)"
probe "vroom" "http://127.0.0.1:${vroom_port:-3000}/"

# --- 5. Ядро -----------------------------------------------------------------
# Сама по себе эта единица ядру ничего не сообщает: ядро ходит по адресам из
# СВОЕГО .env. Пустые адреса дописываем сами — иначе получится «поставили
# маршруты, а маршрутов нет». Заполненные не трогаем: там мог быть чужой OSRM.
step "Связь с ядром"
core_env_file="$(core_env)"
if [ ! -f "$core_env_file" ]; then
  say "  ядро ещё не установлено — это нормально, порядок единиц не важен."
  say "  Когда поставите, впишите в его .env:"
  for c in $countries; do
    say "      OSRM_URL_$(printf '%s' "$c" | tr '[:lower:]' '[:upper:]')=http://osrm-$c:5000"
  done
  say "      VROOM_URL=http://vroom:3000"
else
  changed=0
  for c in $countries; do
    key="OSRM_URL_$(printf '%s' "$c" | tr '[:lower:]' '[:upper:]')"
    if env_file_set_if_empty "$core_env_file" "$key" "http://osrm-$c:5000"; then
      ok "в .env ядра вписано $key=http://osrm-$c:5000"
      changed=1
    else
      say "  $key в ядре уже задан ($(env_file_get "$core_env_file" "$key")) — не трогаю"
    fi
  done
  if env_file_set_if_empty "$core_env_file" VROOM_URL "http://vroom:3000"; then
    ok "в .env ядра вписано VROOM_URL=http://vroom:3000"
    changed=1
  else
    say "  VROOM_URL в ядре уже задан ($(env_file_get "$core_env_file" VROOM_URL)) — не трогаю"
  fi
  if [ "$changed" = "1" ]; then
    say ""
    say "  ОСТАЛСЯ ОДИН ШАГ — применить адреса в ядре:"
    say "      cd $(unit_dir core) && ./update.sh"
  fi
fi

say ""
say "==== Маршруты установлены ===="
compose ps | sed 's/^/  /'
say ""
say "Полезное:"
say "  проверка:          ./doctor.sh"
say "  добавить страну:   допишите её в COMPOSE_PROFILES в .env, положите граф в osrm/<страна>/ и ./install.sh"
say "  порты на 127.0.0.1 опубликованы только для проверки руками; ядро ходит по именам сервисов"
