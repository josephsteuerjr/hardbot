#!/usr/bin/env bash
# Восстановление «Хард» из копии, снятой backup.sh.
#
#   ./restore.sh backups/20260902T120000            # только база
#   ./restore.sh backups/20260902T120000 --photos   # база + фотографии из зеркала
#   добавьте --yes, чтобы не спрашивать подтверждения
#
# Приложение останавливается на время замены базы и запускается снова.
# Текущая база перед заменой откладывается в data/hardbot.db.before-restore-<время>
# — если восстановили не то, её можно вернуть тем же скриптом.
# Файлы в data/ принадлежат root (их создаёт контейнер): запускайте от root
# или через sudo.
source "$(dirname "$0")/_lib.sh"

src="${1:-}"
[ -n "$src" ] || die "укажите каталог копии: ./restore.sh backups/<дата-время> [--photos] [--yes]"
photos=0; yes=0
for arg in "${@:2}"; do
  case "$arg" in
    --photos) photos=1 ;;
    --yes) yes=1 ;;
    *) die "неизвестный параметр: $arg" ;;
  esac
done

check_docker
db_file="$src/hardbot.db"
[ -f "$db_file" ] || db_file="$src"      # можно указать сам файл базы
[ -f "$db_file" ] || die "в $src нет hardbot.db"
[ "$(head -c 15 "$db_file")" = "SQLite format 3" ] || die "$db_file — не база SQLite"
if [ "$photos" -eq 1 ] && [ ! -d backups/photos ]; then
  die "зеркала фотографий backups/photos нет — нечего восстанавливать"
fi

say "Будет восстановлено:"
say "  база:        $db_file ($(human_size "$db_file"))"
[ "$photos" -eq 1 ] && say "  фотографии:  backups/photos → data/photos ($(human_size backups/photos))"
say "  текущая база отложится в data/hardbot.db.before-restore-*"
if [ "$yes" -ne 1 ]; then
  printf 'Продолжить? [y/N] '
  read -r answer
  case "$answer" in y|Y|д|Д) ;; *) say "Отменено"; exit 0 ;; esac
fi

say "Останавливаю приложение…"
compose stop app

stamp="$(date +%Y%m%dT%H%M%S)"
if [ -f data/hardbot.db ]; then
  cp -p data/hardbot.db "data/hardbot.db.before-restore-$stamp"
  ok "текущая база отложена: data/hardbot.db.before-restore-$stamp"
fi
rm -f data/hardbot.db-wal data/hardbot.db-shm data/hardbot.db-journal
cp "$db_file" data/hardbot.db
ok "база восстановлена"

if [ "$photos" -eq 1 ]; then
  mkdir -p data/photos
  if command -v rsync >/dev/null 2>&1; then
    rsync -a backups/photos/ data/photos/
  else
    cp -a backups/photos/. data/photos/
  fi
  ok "фотографии восстановлены"
fi

say "Запускаю приложение…"
compose start app
if wait_healthy app 180; then
  ok "app: $(health_of app)"
else
  compose logs --tail=40 app || true
  die "приложение не поднялось после восстановления"
fi
say "Готово"
