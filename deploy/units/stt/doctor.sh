#!/usr/bin/env bash
# ДИАГНОСТИКА СЛУЖБЫ РЕЧИ. Ничего не меняет.
#
#   cd deploy/units/stt && ./doctor.sh       # только речь
#   cd deploy && ./doctor.sh                 # все три единицы сразу
source "$(dirname "$0")/../../_lib.sh"
source "$DEPLOY_DIR/_actions.sh"

doctor_banner || { say ""; exit 0; }
check_docker

doctor_shared
doctor_containers

# --- Живая проверка ----------------------------------------------------------
# Самая честная проверка — та же, что делает healthcheck: постучаться в службу
# через её же сокет с её же токеном. Отвечает — значит работает всё: и сокет,
# и токен, и загруженная модель.
step "Распознавание"
if [ -z "$(container_id stt)" ]; then
  say "  контейнер не работает — проверять нечего"
  hint "поднять: ./install.sh"
elif out="$(compose exec -T stt python server.py --check 2>&1)"; then
  ok "служба отвечает: $out"
  say "  модель: $(env_get STT_MODEL)"
else
  problem "служба не отвечает: $out"
  # Прежняя подсказка советовала подождать, «пока скачается модель». Модель едет
  # в образе и в сеть служба не ходит — совет отправлял ждать того, чего не
  # произойдёт. Ждать тут стоит секунды, а не минуты.
  hint "сразу после запуска дайте службе несколько секунд — модель читается из образа (замер: 2 с)"
  hint "журнал: docker compose logs --tail=50 stt"
  hint "если в журнале «в образе модели нет» — модель не подготовили до сборки пакета: python tools/prepare_stt_model.py и ./update.sh"
  hint "если жалуется на память — возьмите модель поменьше: STT_MODEL=small в .env и ./update.sh"
fi

# --- Общий том ---------------------------------------------------------------
# В томе лежат два файла, по которым ядро и речь находят друг друга. Смотрим
# изнутри контейнера — с хоста этот том не виден обычными средствами.
step "Сокет и токен (общий том $SHARED_STT_VOLUME)"
if [ -n "$(container_id stt)" ]; then
  listing="$(compose exec -T stt sh -c 'ls -l /run/stt' 2>/dev/null || true)"
  if printf '%s' "$listing" | grep -q 'stt.sock'; then ok "сокет stt.sock на месте"; else problem "нет сокета stt.sock — служба не приняла ни одного соединения"; fi
  if printf '%s' "$listing" | grep -q 'stt.token'; then ok "токен stt.token на месте"; else
    problem "нет файла токена — ядро не сможет обратиться к службе"
    hint "создать заново: ./update.sh (токен положит одноразовый контейнер stt-token)"
  fi
else
  say "  контейнер не работает — содержимое тома не смотрю"
fi

# --- Ядро ---------------------------------------------------------------------
step "Связь с ядром"
core_env_file="$(core_env)"
if [ ! -f "$core_env_file" ]; then
  say "  ядро не установлено. Это не поломка: речь может стоять и ждать ядра."
  say "  Когда поставите ядро — впишите в его .env строку VOICE_BACKEND=stt."
else
  current="$(env_file_get "$core_env_file" VOICE_BACKEND)"
  case "$(printf '%s' "$current" | tr '[:upper:]' '[:lower:]')" in
    stt|praxis) ok "ядро настроено на голос (VOICE_BACKEND=$current)" ;;
    *)
      problem "ядро НЕ использует речь: в его .env VOICE_BACKEND=${current:-пусто}"
      hint "впишите VOICE_BACKEND=stt в $core_env_file и выполните: cd $(unit_dir core) && ./update.sh" ;;
  esac
  if ! unit_running core; then
    say "  (сейчас ядро не запущено — голос заработает, когда его поднимут)"
  fi
fi

# --- Место -------------------------------------------------------------------
step "Место на диске"
df -h "$UNIT_DIR" | tail -n 1 | awk '{printf "  свободно %s из %s (занято %s)\n", $4, $2, $5}'
# Модель лежит в образе, а не здесь: ноль в этой строке — норма, а не пропажа.
printf '  %-22s %s\n' "models (кэш, обычно пуст)" "$(human_size models)"

doctor_errors stt
doctor_tail
doctor_exit
