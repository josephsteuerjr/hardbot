#!/usr/bin/env bash
# ПОМОЩНИК НА ХОСТЕ для панели инженера.
#
#   ./ops-agent.sh            # работать в цикле (так его запускает служба)
#   ./ops-agent.sh --once     # один круг и выйти (для проверки и для cron)
#
# Зачем он существует. Панель живёт в контейнере и докера не видит — намеренно.
# Чтобы контейнер умел перезапускать соседей и снимать копии, ему пришлось бы
# отдать /var/run/docker.sock, а это root на ВСЁМ сервере, включая чужие
# проекты рядом. Поэтому панель кладёт заявку в общий каталог, а исполняет её
# этот скрипт — и умеет ровно шесть действий, ни одним больше.
#
# ГЛАВНОЕ ПРАВИЛО: ни один символ из заявки не попадает в оболочку. Действие
# выбирается case-ом из закрытого списка, параметры проверяются шаблоном, команда
# собирается здесь, в коде. Заявка не может ни добавить аргумент, ни подставить
# путь, ни выполнить свою строку.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNITS_DIR="$HERE/units"
DATA="${HARD_OPS_DATA:-$UNITS_DIR/core/data}"
OPS="$DATA/ops"
SLEEP_S="${HARD_OPS_INTERVAL:-5}"

log() { printf '%s ops-agent: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*"; }


mkdir -p "$OPS/requests" "$OPS/results" "$OPS/running" 2>/dev/null || true

# --- проверка параметров -----------------------------------------------------
# Шаблоны те же, что и на стороне панели. Проверяем ВТОРОЙ раз намеренно: панель
# и помощник — разные процессы с разными правами, и доверять чужой проверке
# здесь нельзя, даже своей собственной.
valid_unit() { case "$1" in core|stt|gps) return 0 ;; *) return 1 ;; esac; }
valid_backup() { printf '%s' "$1" | grep -qE '^[0-9]{8}T[0-9]{6}$'; }

json_field() {  # json_field ФАЙЛ ПОЛЕ — значение простого строкового поля
  sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$1" | head -n 1
}

# Результат — ДВА файла: короткий json со статусом и рядом простой текст вывода.
# Раньше вывод паковался JSON-строкой прямо в баше, и всё держалось на
# экранировании кавычек и переводов строк из чужого журнала — плюс на внешнем
# питоне. Один раз это уже молча съело весь вывод целиком: «python3» на Windows
# оказался заглушкой из Microsoft Store, которая печатает предложение
# установить питон и возвращает НОЛЬ, так что проверка «|| запасной путь» её не
# поймала. Текст отдельным файлом — экранировать нечего вовсе и зависимостей нет.
write_result() {  # write_result ID КОД ТЕКСТ
  local id="$1" code="$2" text="$3"
  printf '%s' "$text" | tail -c 60000 > "$OPS/results/.$id.out.part"
  mv "$OPS/results/.$id.out.part" "$OPS/results/$id.out"
  printf '{"schema":"hard.ops.result.v2","id":"%s","exit":%s,"finished_at":"%s"}\n' \
    "$id" "$code" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$OPS/results/.$id.part"
  # Статус переименовываем ПОСЛЕДНИМ: панель считает заявку исполненной по нему,
  # и текст к этому моменту уже лежит целиком.
  mv "$OPS/results/.$id.part" "$OPS/results/$id.json"
}

# --- исполнение --------------------------------------------------------------
run_request() {
  local file="$1"
  local id action unit backup photos db_only lines out code dir
  id="$(json_field "$file" id)"
  action="$(json_field "$file" action)"
  unit="$(json_field "$file" unit)"
  [ -n "$id" ] || { rm -f "$file"; return; }
  [ -n "$unit" ] || unit="core"

  if ! valid_unit "$unit"; then
    write_result "$id" 2 "неизвестная единица: $unit"; rm -f "$file"; return
  fi
  dir="$UNITS_DIR/$unit"
  if [ ! -d "$dir" ]; then
    write_result "$id" 2 "единица $unit не установлена"; rm -f "$file"; return
  fi

  log "заявка $id: $action ($unit)"
  case "$action" in
    doctor)
      out="$(cd "$dir" && ./doctor.sh 2>&1)"; code=$? ;;
    logs)
      lines="$(json_field "$file" lines)"
      # Число, и только число: строка отсюда идёт аргументом докеру. Диапазон
      # ограничен сверху, чтобы «покажи журнал» не превращалось в выгрузку всего.
      case "$lines" in
        ''|*[!0-9]*) lines=200 ;;
        *) [ "$lines" -gt 2000 ] && lines=2000; [ "$lines" -lt 10 ] && lines=10 ;;
      esac
      out="$(cd "$dir" && docker compose logs --no-color --tail "$lines" 2>&1)"; code=$? ;;
    backup)
      db_only="$(json_field "$file" db_only)"
      if [ "$db_only" = "1" ]; then
        out="$(cd "$dir" && ./backup.sh --db-only 2>&1)"; code=$?
      else
        out="$(cd "$dir" && ./backup.sh 2>&1)"; code=$?
      fi ;;
    restart)
      out="$(cd "$dir" && docker compose restart 2>&1)"; code=$? ;;
    update)
      out="$(cd "$dir" && ./update.sh 2>&1)"; code=$? ;;
    restore)
      backup="$(json_field "$file" backup)"
      photos="$(json_field "$file" photos)"
      if ! valid_backup "$backup"; then
        write_result "$id" 2 "имя копии не похоже на метку времени: $backup"
        rm -f "$file"; return
      fi
      if [ ! -d "$dir/backups/$backup" ]; then
        write_result "$id" 2 "копии $backup нет в $unit/backups"
        rm -f "$file"; return
      fi
      # --yes обязателен: у помощника нет терминала, вопрос «Продолжить?» повис
      # бы навсегда и выглядел как зависшая операция.
      if [ "$photos" = "1" ]; then
        out="$(cd "$dir" && ./restore.sh "backups/$backup" --photos --yes 2>&1)"; code=$?
      else
        out="$(cd "$dir" && ./restore.sh "backups/$backup" --yes 2>&1)"; code=$?
      fi ;;
    *)
      write_result "$id" 2 "действие «$action» не входит в список помощника"
      rm -f "$file"; return ;;
  esac

  write_result "$id" "$code" "$out"
  log "заявка $id: код $code"
  rm -f "$file"
}

# --- опись хозяйства ----------------------------------------------------------
# Панель сидит в контейнере и видит ровно один каталог — свои данные. Ни списка
# копий, ни соседних единиц, ни свободного места на диске оттуда не видно: всё
# это лежит на хосте. Помощник кладёт опись рядом с заявками каждый круг, и
# панель показывает её вместо того, чтобы запрашивать отдельной заявкой и ждать.
write_inventory() {
  local tmp="$OPS/.inventory.part" first=1 unit backups b
  {
    printf '{"schema":"hard.ops.inventory.v1","at":"%s","units":[' \
      "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    for unit in core stt gps; do
      [ -d "$UNITS_DIR/$unit" ] || continue
      [ "$first" = 1 ] || printf ','
      first=0
      printf '{"unit":"%s","installed":true,"backups":[' "$unit"
      backups=""
      if [ -d "$UNITS_DIR/$unit/backups" ]; then
        # Только каталоги-метки времени: всё остальное копией не является.
        backups="$(ls -1 "$UNITS_DIR/$unit/backups" 2>/dev/null \
          | grep -E '^[0-9]{8}T[0-9]{6}$' | sort -r | head -n 40)"
      fi
      local sep=""
      for b in $backups; do printf '%s"%s"' "$sep" "$b"; sep=","; done
      printf ']}'
    done
    printf '],"disk_free":"%s"}\n' \
      "$(df -h "$UNITS_DIR" 2>/dev/null | tail -n 1 | awk '{print $4}')"
  } > "$tmp"
  mv "$tmp" "$OPS/inventory.json"
}

sweep() {
  touch "$OPS/heartbeat" 2>/dev/null || true
  write_inventory
  for file in "$OPS"/requests/*.json; do
    [ -f "$file" ] || continue
    # Забираем заявку себе ДО исполнения: если помощник запущен дважды, работу
    # сделает тот, кому удалось переименование, а не оба сразу.
    taken="$OPS/running/$(basename "$file")"
    mv "$file" "$taken" 2>/dev/null || continue
    run_request "$taken"
  done
}

if [ "${1:-}" = "--once" ]; then
  sweep
  exit 0
fi

log "запущен, каталог заявок: $OPS"
while :; do
  sweep
  sleep "$SLEEP_S"
done
