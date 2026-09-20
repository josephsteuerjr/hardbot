#!/usr/bin/env bash
# ДИАГНОСТИКА МАРШРУТОВ. Ничего не меняет.
#
#   cd deploy/units/gps && ./doctor.sh        # только маршруты
#   cd deploy && ./doctor.sh                  # все три единицы сразу
source "$(dirname "$0")/../../_lib.sh"
source "$DEPLOY_DIR/_actions.sh"

doctor_banner || { say ""; exit 0; }
check_docker

doctor_shared
doctor_containers

countries=""
for c in $(env_get COMPOSE_PROFILES | tr ',' ' '); do
  case "$c" in uz|tj|ru) countries="$countries $c" ;; esac
done

# --- Графы -------------------------------------------------------------------
step "Дорожные графы"
if [ -z "$countries" ]; then
  say "  страны не выбраны (COMPOSE_PROFILES пуст) — работает только оптимизатор порядка"
  say "  Дороги нужны — впишите COMPOSE_PROFILES=uz,ru в .env, положите графы и ./install.sh"
fi
for c in $countries; do
  key="OSRM_GRAPH_$(printf '%s' "$c" | tr '[:lower:]' '[:upper:]')"
  graph="$(env_get "$key")"
  if [ -f "osrm/$c/$graph" ]; then
    ok "$c: $graph ($(human_size "osrm/$c"))"
  else
    problem "$c: нет файла графа osrm/$c/$graph — маршрутизатор этой страны работать не будет"
    hint "положите готовый граф в osrm/$c/ или соберите его по $DEPLOY_DIR/geo/README.md"
    hint "если страна не нужна — уберите «$c» из COMPOSE_PROFILES в .env и ./update.sh"
  fi
done

# --- Ответы ------------------------------------------------------------------
step "Ответы маршрутизаторов (проверка с самого сервера)"
# КОД ОТВЕТА БЕРЁМ БЕЗ «|| echo 000». При отказе curl печатает «000» сам — так
# устроен -w '%{http_code}', — а «|| echo 000» дописывал сверху второй. Выходило
# «000000», сравнение с «000» не срабатывало, и доктор бодро рапортовал
# «отвечает» о контейнере, которого вообще нет. Пустую строку (curl не запустился)
# превращаем в «000» уже после подстановки, а не через ||.
http_code() {  # http_code АДРЕС — код ответа или «000», если ответа не было
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1" 2>/dev/null)"
  printf '%s' "${code:-000}"
}
if ! command -v curl >/dev/null 2>&1; then
  say "  curl не установлен — проверку пропускаю (apt install curl)"
else
  for c in $countries; do
    pkey="OSRM_PORT_$(printf '%s' "$c" | tr '[:lower:]' '[:upper:]')"
    port="$(env_get "$pkey")"; port="${port:-5000}"
    code="$(http_code "http://127.0.0.1:${port}/nearest/v1/driving/69.24,41.31")"
    if [ "$code" = "000" ]; then
      problem "osrm-$c: порт $port молчит"
      hint "журнал: docker compose logs --tail=50 osrm-$c (частая причина — неполный или чужой по версии граф)"
    else
      ok "osrm-$c: отвечает (HTTP $code) на 127.0.0.1:$port"
    fi
  done
  vport="$(env_get VROOM_PORT)"; vport="${vport:-3000}"
  code="$(http_code "http://127.0.0.1:${vport}/")"
  if [ "$code" = "000" ]; then
    problem "vroom: порт $vport молчит"
    hint "журнал: docker compose logs --tail=50 vroom"
  elif [ "$code" = "404" ]; then
    # 404 на GET / — штатный ответ vroom-express: задачи он принимает только
    # POST-ом. Не объяснить этого здесь значит отправить инженера чинить
    # исправную службу.
    ok "vroom: отвечает (HTTP 404 на GET — так и задумано, задачи он принимает POST-ом)"
  else
    ok "vroom: отвечает (HTTP $code) на 127.0.0.1:$vport"
  fi
fi

# --- Ядро --------------------------------------------------------------------
# Самая частая тихая поломка этой единицы: всё поднято, но ядро не знает
# адресов — и молча считает по прямой, ни на что не жалуясь.
step "Знает ли о нас ядро"
core_env_file="$(core_env)"
if [ ! -f "$core_env_file" ]; then
  say "  ядро не установлено. Это не поломка: маршруты могут стоять и ждать ядра."
else
  known=0
  for c in $countries; do
    key="OSRM_URL_$(printf '%s' "$c" | tr '[:lower:]' '[:upper:]')"
    value="$(env_file_get "$core_env_file" "$key")"
    if [ -n "$value" ]; then
      ok "$key = $value"
      known=$((known + 1))
      case "$value" in
        *"osrm-$c"*) ;;
        *) warn "адрес не похож на наш сервис osrm-$c — если это чужой маршрутизатор, так и должно быть" ;;
      esac
    else
      problem "$key в ядре пуст — страна «$c» поднята, но ядро ею не пользуется"
      hint "впишите в $core_env_file строку $key=http://osrm-$c:5000 и выполните cd $(unit_dir core) && ./update.sh"
    fi
  done
  vroom_url="$(env_file_get "$core_env_file" VROOM_URL)"
  if [ -n "$vroom_url" ]; then
    ok "VROOM_URL = $vroom_url"
    known=$((known + 1))
  else
    problem "VROOM_URL в ядре пуст — порядок объезда считает упрощённый алгоритм ядра"
    hint "впишите в $core_env_file строку VROOM_URL=http://vroom:3000 и выполните cd $(unit_dir core) && ./update.sh"
  fi
  if [ "$known" -gt 0 ] && ! unit_running core; then
    say "  (ядро сейчас не запущено — адреса заработают, когда его поднимут)"
  fi
fi

doctor_errors vroom
for c in $countries; do doctor_errors "osrm-$c"; done
doctor_tail
doctor_exit
