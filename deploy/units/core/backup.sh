#!/usr/bin/env bash
# Резервная копия ЯДРА: база, audit-spool, фотографии и чеки. Это единственная единица, где есть
# что бэкапить: у речи и маршрутов данных нет, они восстанавливаются установкой.
#
#   cd deploy/units/core
#   ./backup.sh            # база + spool + фотографии + чеки
#   ./backup.sh --db-only  # база + spool + чеки; фотографии остаются в data/
#
# База копируется онлайн-бэкапом SQLite изнутри контейнера — это единственный
# согласованный способ снять работающую базу (обычное cp может дать битый
# файл). Копия ложится в backups/<дата-время>/hardbot.db.
#
# Фотографии и чеки сохраняются в том же каталоге снимка, что и база;
# SHA256SUMS выпускается только после полного копирования всех файлов.
#
# Старые полные каталоги копии удаляются, остаются последние BACKUP_KEEP
# (из .env, по умолчанию 14). Это локальная ротация, не внешний архив.
#
# Копия снимается и с работающим приложением, и с остановленным, и с падающим
# по кругу — то есть ровно тогда, когда она нужнее всего. Как это устроено,
# написано ниже у самой команды docker run.
source "$(dirname "$0")/../../_lib.sh"   # приносит set -euo pipefail
source "$DEPLOY_DIR/_actions.sh"

# Сразу после set: база, фотографии, чеки и spool содержат ПД. Пусть
# всё, что создаёт этот скрипт (каталог копии и временный файл), рождается
# доступным только владельцу (root). Ставим ДО первого mkdir/mktemp.
umask 077

db_only=0
[ "${1:-}" = "--db-only" ] && db_only=1

check_docker
keep="$(env_get BACKUP_KEEP)"
keep="${keep:-14}"
stamp="$(date +%Y%m%dT%H%M%S)"
dest="backups/$stamp"
mkdir -p backups
mkdir "$dest" || die "копия $dest уже существует: одновременный запуск backup запрещён"

# --- База ------------------------------------------------------------------------
# Питон-сниппет один для обоих путей: онлайн-бэкап в /tmp контейнера, затем
# байты в stdout (так на хосте не появляются файлы, принадлежащие root).
read -r -d '' SNIPPET <<'PY' || true
import os, sqlite3, sys
src = sqlite3.connect("file:/data/" + sys.argv[1] + "?mode=ro", uri=True)
dst = sqlite3.connect("/tmp/backup.db")
with dst:
    src.backup(dst)
if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
    raise RuntimeError("SQLite snapshot failed quick_check")
dst.close(); src.close()
with open("/tmp/backup.db", "rb") as f:
    while chunk := f.read(1 << 20):
        sys.stdout.buffer.write(chunk)
os.unlink("/tmp/backup.db")
PY

if [ ! -f data/hardbot.db ]; then
  die "нет data/hardbot.db — базы ещё нет: приложение ни разу не запускалось.
Что сделать: ./install.sh, дождаться «http://127.0.0.1:… отвечает» и повторить ./backup.sh."
fi

# Пишем сначала во ВРЕМЕННЫЙ файл и только проверенный переименовываем в
# hardbot.db. Иначе оборвавшаяся копия оставляет в backups/<время>/ файл на
# 0 байт: каталог выглядит как снятая копия, а восстанавливать из него нечего —
# и узнают об этом в тот единственный день, когда копия понадобилась.
tmp_db="$dest/.hardbot.db.part"
err_log="$(mktemp)"
# Уборка одна на все исходы: после удачи временного файла уже нет, а rmdir не
# трогает непустой каталог с готовой копией.
trap 'rm -f "$tmp_db" "$err_log"; rmdir "$dest" 2>/dev/null || true' EXIT

# die с последними строками от sqlite: голый английский трейсбек инженеру
# чужой ИТ-службы ничего не говорит.
backup_failed() {
  local details=""
  [ -s "$err_log" ] && details="
Что сказал sqlite:
$(tail -n 5 "$err_log" | sed 's/^/  /')"
  die "копия базы не удалась — файл копии не получен или получился не базой.
Что сделать по порядку:
  1) ./doctor.sh — если контейнер приложения перезапускается по кругу,
     чинить надо его: копия сама по себе не сломана;
  2) df -h — под копию базы может не хватать места на диске.$details"
}

if [ "$(health_of app)" != "нет" ] && [ "$(docker inspect -f '{{.State.Running}}' "$(container_id app)" 2>/dev/null)" = "true" ]; then
  backup_sqlite() { compose exec -T app python -c "$SNIPPET" "$1" > "$2" 2>"$err_log" || backup_failed; }
else
  # Контейнер не работает (остановлен или падает по кругу) — читаем базу
  # одноразовым контейнером из того же образа (образ есть после install.sh).
  docker image inspect hard-app >/dev/null 2>&1     || die "приложение не работает, а образ hard-app не собран — снять согласованную копию нечем.
Что сделать: ./install.sh (соберёт образ и поднимет ядро) и повторить ./backup.sh."
  # Том с данными монтируется НА ЗАПИСЬ, хотя мы только читаем, — тот же урок,
  # что и в migrate.sh. База живёт в режиме WAL, и SQLite нужен доступ на
  # запись к служебному файлу -shm: при остановленном приложении -shm рядом с
  # базой нет, а на томе «:ro» создать его нельзя — открытие падает с «unable
  # to open database file». То есть с «:ro» копия не снималась ровно в том
  # случае, ради которого этот путь и написан. Сама база при этом не меняется:
  # соединение открыто как mode=ro, писать в неё сниппету нечем.
  # Берём том ИЗ COMPOSE, а не собираем «-v путь:/data» руками. Под Git Bash на
  # Windows MSYS переписывает пути в аргументах: «:/data» превращалось в
  # «C:/Program Files/Git/data», контейнер монтировал пустоту и sqlite честно
  # отвечал «unable to open database file». Ошибка выглядела как поломка базы,
  # хотя база была цела. У compose путь берётся из файла и через MSYS не идёт.
  backup_sqlite() { compose run --rm --no-deps -T app python -c "$SNIPPET" "$1" > "$2" 2>"$err_log" || backup_failed; }
fi

# Audit drain двигает события только spool -> primary. Сначала снять spool,
# потом primary: каждое старое событие остаётся хотя бы в одном снимке.
if [ -e data/audit-spool.sqlite3 ]; then
  [ -f data/audit-spool.sqlite3 ] && [ ! -L data/audit-spool.sqlite3 ] \
    || die "audit-spool не обычный файл; неполную копию не выпускаю"
  backup_sqlite audit-spool.sqlite3 "$dest/.audit-spool.sqlite3.part"
  mv "$dest/.audit-spool.sqlite3.part" "$dest/audit-spool.sqlite3"
fi
backup_sqlite hardbot.db "$tmp_db"

if [ ! -s "$tmp_db" ] || [ "$(head -c 15 "$tmp_db")" != "SQLite format 3" ]; then
  backup_failed
fi
mv "$tmp_db" "$dest/hardbot.db"
ok "база: $dest/hardbot.db ($(human_size "$dest/hardbot.db"))"

# --- Неизменяемые доказательства -------------------------------------------------
for asset in photos receipts; do
  [ -L "data/$asset" ] && die "data/$asset — ссылка, нельзя выпустить неполную копию"
  if [ -d "data/$asset" ]; then
    if [ -n "$(find "data/$asset" -type l -print -quit)" ]; then
      die "data/$asset содержит ссылку; копия остановлена"
    fi
  fi
done
if [ "$db_only" -eq 0 ]; then
  if [ -d data/photos ]; then
    cp -a data/photos "$dest/photos"
    ok "фотографии: $dest/photos ($(human_size "$dest/photos"))"
  else
    ok "фотографий пока нет"
  fi
fi
if [ -d data/receipts ]; then
  cp -a data/receipts "$dest/receipts"
  ok "чеки: $dest/receipts ($(human_size "$dest/receipts"))"
fi

# Единственный ready-маркер записывается ПОСЛЕ всех копий. Старые копии без
# описи restore отвергает: он не может доказать полноту чеков и audit-spool.
need_cmd sha256sum "нужен coreutils для контроля копии"
# The CRM release points this directory at the same configuration as the serving API.
crm_runtime=/opt/rimborso-crm-runtime/current
if [ -e "$crm_runtime" ] || [ -L "$crm_runtime" ]; then
  /usr/bin/python3 "$crm_runtime/backup_postgres.py" --destination "$dest" \
    --container crm-pilot-postgres --dsn-file "$crm_runtime/secrets/app-dsn" \
    --documents /opt/rimborso-crm-pilot/documents --mobile-state "$crm_runtime/mobile-state" \
    || die "Не удалось сохранить рабочую PostgreSQL CRM. Подробности выше."
fi

( cd "$dest" && find . -type f ! -name SHA256SUMS ! -name .SHA256SUMS.part -print0 | sort -z | xargs -0 sha256sum > .SHA256SUMS.part )
mv "$dest/.SHA256SUMS.part" "$dest/SHA256SUMS"
ok "состав и SHA256: $dest/SHA256SUMS"

# --- Ротация копий базы -----------------------------------------------------------
old="$(ls -1d backups/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9] 2>/dev/null | sort | head -n -"$keep" || true)"
if [ -n "$old" ]; then
  echo "$old" | xargs rm -rf
  ok "удалены старые копии, оставлено последних: $keep"
fi

say "Готово: $dest"
