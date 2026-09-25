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
#
# И ещё одно правило, дописанное после разбора: помощник работает от root, а
# каталог заявок лежит на том же томе, куда пишет контейнер. Значит поле заявки
# и подменённый симлинк — тоже «символ из заявки». Идентификатор проверяется
# шаблоном ДО любой работы; рабочие каталоги каждый круг сверяются, что они
# каталоги, а не ссылки наружу; каждая запись идёт во временный файл рядом с
# целью и атомарно переезжает поверх неё, не следуя за симлинком.
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

# Идентификатор заявки уходит в ПУТИ результата ("$OPS/results/$id.out" и
# "$id.json"). Питон-читатель (server/ops_requests.py) проверяет его шаблоном
# при ЧТЕНИИ; помощник обязан проверить тем же шаблоном при ЗАПИСИ, иначе id
# вида "/../../../etc/cron.d/x" уводит запись за пределы ops/ от имени root.
# Шаблон совпадает с ops_requests.result(): ^[A-Za-z0-9_-]{1,64}$.
valid_id() { printf '%s' "$1" | grep -qE '^[A-Za-z0-9_-]{1,64}$'; }

json_field() {  # json_field ФАЙЛ ПОЛЕ — значение простого строкового поля
  sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$1" | head -n 1
}

# Рабочие каталоги — каталоги, а не симлинки. Контейнер пишет в тот же том, и
# подменив каталог ссылкой наружу, он заставил бы root писать результаты и опись
# за пределы ops/. Подмену не «чиним» (это скрыло бы атаку), а честно пропускаем
# круг с записью в журнал: heartbeat перестанет обновляться, и панель покажет
# «помощник молчит».
ops_dirs_ok() {
  local d
  for d in "$OPS" "$OPS/requests" "$OPS/results" "$OPS/running"; do
    if [ -L "$d" ] || [ ! -d "$d" ]; then
      log "каталог $d — не обычный каталог (нет или подменён симлинком); круг пропущен"
      return 1
    fi
  done
  return 0
}

# Запись, не следующая за симлинком. Пишем во временный файл, СОЗДАННЫЙ безопасно
# (mktemp в том же каталоге — он гарантированно обычный файл, не ссылка), затем
# атомарно переносим поверх цели `mv -f -T`. `-T` не даёт mv «зайти внутрь», если
# цель вдруг оказалась ссылкой на каталог, а сам перенос заменяет ссылку-на-файл,
# а не пишет по ней. Симлинк на месте цели снимаем заранее — по нему мы не идём.
# Данные читаются со stdin.
safe_write() {  # safe_write ЦЕЛЬ  (содержимое — со stdin)
  local dest="$1" dir tmp
  dir="$(dirname "$dest")"
  if [ ! -d "$dir" ] || [ -L "$dir" ]; then
    log "каталог $dir — не обычный каталог; запись $dest отменена"
    return 1
  fi
  [ -L "$dest" ] && rm -f "$dest"
  tmp="$(mktemp "$dir/.hardops.XXXXXX")" || { log "не удалось создать временный файл в $dir"; return 1; }
  cat > "$tmp"
  if ! mv -f -T "$tmp" "$dest"; then
    rm -f "$tmp"
    log "не удалось записать $dest"
    return 1
  fi
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
  printf '%s' "$text" | tail -c 60000 | safe_write "$OPS/results/$id.out"
  # Статус пишем ПОСЛЕДНИМ: панель считает заявку исполненной по нему, и текст к
  # этому моменту уже лежит целиком. Обе записи — через safe_write, поэтому ни
  # недописанного файла, ни следования за подменённым симлинком панель не увидит.
  printf '{"schema":"hard.ops.result.v2","id":"%s","exit":%s,"finished_at":"%s"}\n' \
    "$id" "$code" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" | safe_write "$OPS/results/$id.json"
}

# --- исполнение --------------------------------------------------------------
run_request() {
  local file="$1"
  local id action unit backup photos db_only lines out code dir
  id="$(json_field "$file" id)"
  action="$(json_field "$file" action)"
  unit="$(json_field "$file" unit)"
  # Идентификатор проверяем ПЕРВЫМ и ДО любой записи. Невалидный id (пустой или с
  # символами вне [A-Za-z0-9_-], в т.ч. с «/» и «..») — это либо мусор, либо
  # попытка увести путь результата наружу. Заявку удаляем, результат НЕ пишем:
  # писать «$id.json» с таким id и значило бы совершить ту самую подстановку пути.
  if ! valid_id "$id"; then
    log "заявка отклонена: недопустимый идентификатор"
    rm -f "$file"; return
  fi
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
# Печать вынесена в отдельную функцию, чтобы запись шла тем же safe_write, что и
# результаты: опись — тоже файл, который контейнер мог подменить симлинком.
emit_inventory() {
  local first=1 unit backups b sep
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
    sep=""
    for b in $backups; do printf '%s"%s"' "$sep" "$b"; sep=","; done
    printf ']}'
  done
  printf '],"disk_free":"%s"}\n' \
    "$(df -h "$UNITS_DIR" 2>/dev/null | tail -n 1 | awk '{print $4}')"
}
write_inventory() { emit_inventory | safe_write "$OPS/inventory.json"; }

# heartbeat — только отметка времени, по ней панель понимает, что помощник жив.
# Но и её не ставим ПО симлинку: иначе контейнер навёл бы ссылку на чужой файл и
# заставил root обновлять его. Пустая запись через safe_write и обновляет mtime,
# и заменяет любую подложенную ссылку обычным файлом.
write_heartbeat() { safe_write "$OPS/heartbeat" < /dev/null; }

sweep() {
  # Круг начинается со сверки каталогов: если хоть один подменён ссылкой или
  # пропал, ничего не пишем и не исполняем — иначе первая же запись ушла бы
  # наружу от имени root.
  ops_dirs_ok || return
  write_heartbeat
  write_inventory
  for file in "$OPS"/requests/*.json; do
    [ -f "$file" ] || continue
    # Пропускаем симлинки среди заявок: заявка — это файл, который написала
    # панель, а не ссылка на чужой файл, который подсунул контейнер.
    [ -L "$file" ] && { log "заявка-симлинк пропущена: $file"; continue; }
    # Забираем заявку себе ДО исполнения: если помощник запущен дважды, работу
    # сделает тот, кому удалось переименование, а не оба сразу. `-T` не даёт mv
    # «зайти внутрь» подменённой ссылки на каталог; ссылку на месте цели снимаем.
    taken="$OPS/running/$(basename "$file")"
    [ -L "$taken" ] && rm -f "$taken"
    mv -f -T "$file" "$taken" 2>/dev/null || continue
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
