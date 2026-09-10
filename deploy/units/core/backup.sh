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
source "$(dirname "$0")/../../_lib.sh"
source "$DEPLOY_DIR/_actions.sh"

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

if [ "$(health_of app)" != "нет" ] && [ "$(docker inspect -f '{{.State.Running}}' "$(container_id app)" 2>/dev/null)" = "true" ]; then
  compose exec -T app python -c "$SNIPPET" > "$dest/hardbot.db"
else
  # Контейнер не работает — база не занята; читаем её одноразовым контейнером
  # из того же образа (образ есть после install.sh).
  docker image inspect hard-app >/dev/null 2>&1     || die "приложение не работает, а образ hard-app не собран — снять согласованную копию нечем.
Что сделать: ./install.sh (соберёт образ и поднимет ядро) и повторить ./backup.sh."
  docker run --rm -v "$UNIT_DIR/data:/data:ro" hard-app python -c "$SNIPPET" > "$dest/hardbot.db"
fi

if [ ! -s "$dest/hardbot.db" ] || [ "$(head -c 15 "$dest/hardbot.db")" != "SQLite format 3" ]; then
  rm -f "$dest/hardbot.db"
  rmdir "$dest" 2>/dev/null || true
  die "копия базы не удалась: получился пустой или не-SQLite файл.
Что сделать: посмотрите, жив ли контейнер приложения (./doctor.sh) — копия снимается изнутри него; если он перезапускается, сначала почините его, копия сама по себе не сломана."
fi
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
