#!/usr/bin/env bash
# УСТАНОВКА ЯДРА одной командой:
#
#   cd deploy/units/core && ./install.sh
#
# Ядро — это приложение: мини-приложение выездника, административное API для
# пульта, база и фотографии. Речь (units/stt) и маршруты (units/gps) — соседние
# единицы, они не обязательны и ставятся в любой момент, до или после ядра.
#
# Повторный запуск безопасен: .env, база и фотографии не трогаются, образ
# пересобирается, контейнеры поднимаются заново.
source "$(dirname "$0")/../../_lib.sh"
source "$DEPLOY_DIR/_actions.sh"

banner "установка"
check_docker
ok "докер и compose v2 на месте"
require_sources server
warn_if_low_disk 3

# --- 1. Настройки ------------------------------------------------------------
step "Настройки"
create_env
if [ -z "$(env_get ADMIN_API_KEY)" ]; then
  env_set ADMIN_API_KEY "$(gen_secret)"
  ok "ADMIN_API_KEY сгенерирован — он лежит в .env, его вводят в пульт аналитика"
fi
check_profiles

# --- 2. Каталоги данных ------------------------------------------------------
# Создаём заранее сами: если этого не сделать, каталоги создаст докер от root,
# и потом резервное копирование упрётся в права.
step "Каталоги"
mkdir -p data data/photos backups
ok "data/ (база и фотографии) и backups/ (копии) на месте"

# --- 3. Образ и запуск -------------------------------------------------------
step "Образ"
build_images
step "Запуск"
start_unit

# --- 4. Готовность -----------------------------------------------------------
step "Проверка"
if ! wait_unit; then
  die "приложение не поднялось.
Что сделать по порядку:
  1) посмотрите строки журнала выше — почти всегда там прямо написана причина;
  2) частая причина — опечатка в .env (лишние кавычки, пробел в числе);
  3) полная картина: cd $DEPLOY_DIR && ./doctor.sh"
fi

port="$(app_port)"
if command -v curl >/dev/null 2>&1; then
  # Судим ПО КОДУ ОТВЕТА, а не по коду возврата curl. Прежняя проверка
  # (curl -fsS -o /dev/null) объявляла приложение мёртвым при любой неудаче
  # curl — в том числе когда сервер уже ответил 200, а тело не удалось
  # записать: «curl: (23) client returned ERROR on write of ... bytes».
  # Получалось, что последним, что инженер читает после успешной установки,
  # была ложная тревога о живом приложении.
  #
  # Без «|| echo 000»: при отказе curl печатает «000» сам, дописанный сверху
  # второй «000» дал бы склейку вида «200000». Пустая строка (curl не
  # запустился) подменяется на «000» ниже.
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:${port}/" 2>/dev/null)" || true
  case "${code:-000}" in
    000)
      warn "контейнер здоров, но http://127.0.0.1:${port}/ не отвечает"
      hint "проверьте APP_PORT в .env — возможно, порт занят и приложение слушает другой" ;;
    2??|3??)
      ok "http://127.0.0.1:${port}/ отвечает (HTTP ${code})" ;;
    *)
      warn "http://127.0.0.1:${port}/ отвечает, но кодом ${code} — это не похоже на рабочее приложение"
      hint "последние строки журнала: docker compose logs --tail=50" ;;
  esac
fi

# --- 5. Соседи ---------------------------------------------------------------
# Ядро работает и в одиночку. Но если в .env что-то включено, а соседа нет —
# об этом надо сказать сейчас, а не через неделю, когда «голос не работает».
step "Соседние единицы"
if voice_enabled; then
  if unit_running stt; then
    ok "речь: единица stt работает, голосовые комментарии будут распознаваться"
  else
    warn "в .env включён голос (VOICE_BACKEND=$(env_get VOICE_BACKEND)), но единица речи не работает"
    hint "поставить речь: cd $(unit_dir stt) && ./install.sh"
    hint "пока её нет, всё остальное работает — сотрудник вводит текст руками"
  fi
else
  say "  речь выключена (VOICE_BACKEND=$(env_get VOICE_BACKEND)) — это допустимый режим."
  if unit_running stt; then
    warn "при этом единица речи РАБОТАЕТ, но ядро её не использует"
    hint "включить: впишите VOICE_BACKEND=stt в $UNIT_DIR/.env и выполните ./update.sh"
  fi
fi

routes="$(env_get OSRM_URL_UZ)$(env_get OSRM_URL_TJ)$(env_get OSRM_URL_RU)$(env_get OSRM_URL)$(env_get VROOM_URL)"
if [ -n "$routes" ]; then
  if unit_running gps; then
    ok "маршруты: единица gps работает, порядок объезда считается по дорогам"
  else
    warn "в .env заданы адреса маршрутизаторов, но единица gps не работает"
    hint "поставить маршруты: cd $(unit_dir gps) && ./install.sh"
    hint "пока её нет, расстояния считаются по прямой — система работает, маршрут просто грубее"
  fi
elif unit_running gps; then
  warn "единица gps работает, но ядро о ней не знает — адреса в .env пусты"
  for svc in $(project_services_running "$(project_of gps)"); do
    case "$svc" in
      osrm-uz) hint "впишите в .env: OSRM_URL_UZ=http://osrm-uz:5000" ;;
      osrm-tj) hint "впишите в .env: OSRM_URL_TJ=http://osrm-tj:5000" ;;
      osrm-ru) hint "впишите в .env: OSRM_URL_RU=http://osrm-ru:5000" ;;
      vroom)   hint "впишите в .env: VROOM_URL=http://vroom:3000" ;;
    esac
  done
  hint "после правки: ./update.sh"
else
  say "  маршруты по дорогам не подключены — расстояния считаются по прямой (допустимый режим)."
fi

# --- 6. Что дальше -----------------------------------------------------------
say ""
say "==== Ядро установлено ===="
compose ps | sed 's/^/  /'
say ""
say "Что осталось сделать руками:"
n=1
if [ -z "$(env_get ADMIN_ID)" ]; then
  say "  $n. Впишите ADMIN_ID в .env — телеграм-id владельца системы (узнать: команда /whoami боту)."; n=$((n+1))
fi
if [ -z "$(env_get MINIAPP_URL)" ]; then
  say "  $n. Впишите MINIAPP_URL в .env — публичный адрес приложения, обязательно https://…"; n=$((n+1))
fi
if [ -z "$(env_get BOT_TOKEN)" ]; then
  say "  $n. Телеграм выключен (BOT_TOKEN пуст). Нужен бот — впишите токен от @BotFather."; n=$((n+1))
fi
say "  $n. Любые правки .env применяются одной командой: ./update.sh"; n=$((n+1))
say "  $n. HTTPS обязателен: поставьте перед приложением свой nginx на http://127.0.0.1:${port}/"
say "     или включите встроенный: COMPOSE_PROFILES=proxy и DOMAIN в .env, затем ./update.sh"; n=$((n+1))
say "  $n. Пульту аналитика нужны адрес сервера и ADMIN_API_KEY из .env."; n=$((n+1))
say "  $n. Копия базы: ./backup.sh   Состояние всей системы: $DEPLOY_DIR/doctor.sh"
