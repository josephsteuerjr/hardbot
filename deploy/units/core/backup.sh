#!/usr/bin/env bash
# Резервная копия ЯДРА: база и фотографии. Это единственная единица, где есть
# что бэкапить: у речи и маршрутов данных нет, они восстанавливаются установкой.
#
#   cd deploy/units/core
#   ./backup.sh            # база + фотографии
#   ./backup.sh --db-only  # только база (секунды; так делает update.sh)
#
# База копируется онлайн-бэкапом SQLite изнутри контейнера — это единственный
# согласованный способ снять работающую базу (обычное cp может дать битый
# файл). Копия ложится в backups/<дата-время>/hardbot.db.
#
# Фотографии не меняются после записи, поэтому держим одно зеркало
# backups/photos/ и досылаем в него только новое (rsync, без него — cp -u).
#
# Старые копии базы удаляются, остаются последние BACKUP_KEEP (из .env, по
# умолчанию 14). Зеркало фотографий не ротируется.
#
# Копия снимается и с работающим приложением, и с остановленным, и с падающим
# по кругу — то есть ровно тогда, когда она нужнее всего. Как это устроено,
# написано ниже у самой команды docker run.
source "$(dirname "$0")/../../_lib.sh"   # приносит set -euo pipefail
source "$DEPLOY_DIR/_actions.sh"

# Сразу после set: копия базы и зеркало фотографий — это все ПД должников. Пусть
# всё, что создаёт этот скрипт (каталог копии, временный файл, зеркало), рождается
# доступным только владельцу (root). Ставим ДО первого mkdir/mktemp.
umask 077

db_only=0
[ "${1:-}" = "--db-only" ] && db_only=1

check_docker
keep="$(env_get BACKUP_KEEP)"
keep="${keep:-14}"
stamp="$(date +%Y%m%dT%H%M%S)"
dest="backups/$stamp"
mkdir -p "$dest"

# --- База ------------------------------------------------------------------------
# Питон-сниппет один для обоих путей: онлайн-бэкап в /tmp контейнера, затем
# байты в stdout (так на хосте не появляются файлы, принадлежащие root).
read -r -d '' SNIPPET <<'PY' || true
import os, sqlite3, sys
src = sqlite3.connect("file:/data/hardbot.db?mode=ro", uri=True)
dst = sqlite3.connect("/tmp/backup.db")
with dst:
    src.backup(dst)
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
  compose exec -T app python -c "$SNIPPET" > "$tmp_db" 2>"$err_log" || backup_failed
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
  compose run --rm --no-deps -T app python -c "$SNIPPET" > "$tmp_db" 2>"$err_log" || backup_failed
fi

if [ ! -s "$tmp_db" ] || [ "$(head -c 15 "$tmp_db")" != "SQLite format 3" ]; then
  backup_failed
fi
mv "$tmp_db" "$dest/hardbot.db"
ok "база: $dest/hardbot.db ($(human_size "$dest/hardbot.db"))"

# --- Фотографии ------------------------------------------------------------------
if [ "$db_only" -eq 0 ]; then
  if [ -d data/photos ]; then
    mkdir -p backups/photos
    if command -v rsync >/dev/null 2>&1; then
      rsync -a data/photos/ backups/photos/
    else
      cp -au data/photos/. backups/photos/
    fi
    ok "фотографии: backups/photos/ ($(human_size backups/photos))"
  else
    ok "фотографий пока нет"
  fi
fi

# --- Ротация копий базы -----------------------------------------------------------
old="$(ls -1d backups/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9] 2>/dev/null | sort | head -n -"$keep" || true)"
if [ -n "$old" ]; then
  echo "$old" | xargs rm -rf
  ok "удалены старые копии, оставлено последних: $keep"
fi

say "Готово: $dest"
