#!/usr/bin/env bash
# Восстановление ЯДРА из копии, снятой ./backup.sh (речь и маршруты
# восстанавливать не из чего — они не хранят данных, их просто ставят заново).
#
#   cd deploy/units/core
#   ./restore.sh backups/20260902T120000            # полный проверенный снимок
#   добавьте --yes, чтобы не спрашивать подтверждения
#
# Приложение останавливается на время замены базы и запускается снова.
# Текущая база перед заменой откладывается в data/hardbot.db.before-restore-<время>
# — если восстановили не то, её можно вернуть тем же скриптом.
# Файлы в data/ принадлежат root (их создаёт контейнер): запускайте от root
# или через sudo.
source "$(dirname "$0")/../../_lib.sh"
source "$DEPLOY_DIR/_actions.sh"
umask 077

src="${1:-}"
if [ -z "$src" ]; then
  say "Доступные копии:"
  ls -1d backups/[0-9]*T[0-9]* 2>/dev/null | sed 's/^/  /' || say "  копий нет — снимите: ./backup.sh"
  die "укажите, какую копию восстанавливать: ./restore.sh backups/<дата-время> [--photos] [--yes]"
fi
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
[ -f "$db_file" ] || die "в «$src» нет файла hardbot.db.
Что сделать: укажите каталог копии целиком, например ./restore.sh backups/20260902T120000 (список: ls backups/)."
[ "$(head -c 15 "$db_file")" = "SQLite format 3" ] || die "$db_file — не база SQLite"
need_cmd sha256sum "нужен coreutils для проверки полной копии"
[ -f "$src/SHA256SUMS" ] && [ ! -L "$src/SHA256SUMS" ] \
  || die "копия без SHA256SUMS неполна (старый формат не включал receipts/audit-spool); восстановление остановлено. Сделайте новый полный backup или используйте проверенный recovery_bundle в новом пустом каталоге"
[ -z "$(find "$src" -type l -print -quit)" ] || die "в копии есть символическая ссылка"
( cd "$src" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum ) \
  | cmp -s "$src/SHA256SUMS" - \
  || die "состав или SHA256 файлов копии изменился; data/ не тронут"
[ "$photos" -eq 0 ] || [ -d "$src/photos" ] \
  || die "в этой копии нет фотографий; --photos не может их восстановить"

# Симлинк на месте data/, самой базы или каталога фотографий увёл бы
# восстановление за пределы каталога единицы: том с данными пишет и контейнер, а
# он от root — подменив эти пути ссылкой на хостовый секрет (.env, ключ туннеля),
# он заставил бы restore СКОПИРОВАТЬ секрет в data/ (контейнер его прочитает) или
# ЗАПИСАТЬ базу поверх него. Такое не «чиним» — это скрыло бы подмену, — а честно
# отказываем: root на хосте не идёт по ссылке, которую подложил контейнер.
for suspect in data data/hardbot.db data/hardbot.db-wal data/hardbot.db-shm data/hardbot.db-journal data/photos data/receipts data/audit-spool.sqlite3 data/audit-spool.sqlite3-wal data/audit-spool.sqlite3-shm; do
  if [ -L "$suspect" ]; then
    die "«$suspect» — символьная ссылка, а должен быть обычный файл или каталог.
Это признак подмены: каталог data/ пишет и контейнер. Восстановление остановлено.
Что сделать: посмотрите, кто заменил «$suspect» ссылкой (ls -l data/), удалите ссылку и повторите."
  fi
done

say "Будет восстановлено:"
say "  база:        $db_file ($(human_size "$db_file"))"
[ -d "$src/photos" ] && say "  фотографии: $src/photos → data/photos"
[ -d "$src/receipts" ] && say "  чеки: $src/receipts → data/receipts"
[ -f "$src/audit-spool.sqlite3" ] && say "  audit-spool: $src/audit-spool.sqlite3"
say "  текущая база отложится в data/hardbot.db.before-restore-*"
if [ "$yes" -ne 1 ]; then
  printf 'Продолжить? [y/N] '
  read -r answer
  case "$answer" in y|Y|д|Д) ;; *) say "Отменено"; exit 0 ;; esac
fi

# Всё входящее копируем в staging ДО остановки/перемещения текущих файлов.
# На той же файловой системе публикация отдельных деревьев идёт через rename.
stage="data/.restore-stage-$$"
[ ! -e "$stage" ] || die "временный каталог $stage уже существует"
mkdir "$stage"
cp "$db_file" "$stage/hardbot.db"
for asset in photos receipts audit-spool.sqlite3; do
  [ -e "$src/$asset" ] && cp -a "$src/$asset" "$stage/$asset"
done
( cd "$stage" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) \
  | cmp -s "$src/SHA256SUMS" - \
  || { rm -rf "$stage"; die "staging не совпал с архивом; data/ не тронут"; }

say "Останавливаю приложение…"
compose stop app

stamp="$(date +%Y%m%dT%H%M%S)"
aside="backups/before-restore-$stamp-$$"
mkdir "$aside"
mkdir "$aside/raw-db"
# Before opening SQLite again preserve the exact main DB and any hot sidecars.
# The consistent rollback snapshot below is additional protection, not a
# replacement for the original WAL bytes after an interrupted restore.
for asset in hardbot.db hardbot.db-wal hardbot.db-shm hardbot.db-journal; do
  [ -e "data/$asset" ] && cp -a "data/$asset" "$aside/raw-db/$asset"
done
if [ -f data/hardbot.db ]; then
  # SIGTERM/аварийная остановка не гарантируют checkpoint. Простое cp главного
  # файла с последующим удалением -wal теряло последние подтверждённые записи.
  # Согласованный снимок создаёт SQLite, читая и основной файл, и WAL.
  read -r -d '' SNAPSHOT <<'PY' || true
import os, sqlite3, sys
source = sqlite3.connect("file:/data/hardbot.db?mode=ro", uri=True)
target = "/data/hardbot.db.before-restore-" + sys.argv[1]
temporary = target + ".part"
destination = sqlite3.connect(temporary)
try:
    source.backup(destination)
    if destination.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise RuntimeError("Rollback snapshot failed integrity check")
finally:
    destination.close()
    source.close()
os.replace(temporary, target)
PY
  compose run --rm --no-deps -T app python -c "$SNAPSHOT" "$stamp" || die "Согласованная копия текущей БД не снята. Замена отменена; исходные база и WAL сохранены."
  ok "текущая база отложена: data/hardbot.db.before-restore-$stamp"
fi
# On an ordinary write/start error, restore the complete original generation.
# A SIGKILL/power loss cannot run traps: the exact old bytes remain in aside,
# and the app must not be restarted until an operator reconciles the two sets.
publish_started=0
published=0
restore_on_error() {
  [ "$publish_started" -eq 1 ] && [ "$published" -eq 0 ] || return 0
  set +e
  compose stop app >/dev/null 2>&1
  mkdir -p "$aside/failed-publish"
  for asset in hardbot.db hardbot.db-wal hardbot.db-shm hardbot.db-journal receipts audit-spool.sqlite3 audit-spool.sqlite3-wal audit-spool.sqlite3-shm; do
    # An old member may not yet have moved when the failure happened. Never
    # mistake that still-live member for a newly published one.
    if [ -e "$aside/$asset" ] || [ -e "$src/$asset" ] || [[ "$asset" = *-wal || "$asset" = *-shm || "$asset" = *-journal ]]; then
      [ -e "data/$asset" ] && mv "data/$asset" "$aside/failed-publish/$asset"
      [ -e "$aside/$asset" ] && mv "$aside/$asset" "data/$asset"
    fi
  done
  if [ -e "$aside/photos" ] || [ -d "$src/photos" ]; then
    [ -e data/photos ] && mv data/photos "$aside/failed-publish/photos"
    [ -e "$aside/photos" ] && mv "$aside/photos" data/photos
  fi
  warn "публикация не завершилась; восстановлен прежний комплект из $aside. Проверьте его и запустите app вручную"
}
trap restore_on_error EXIT
publish_started=1
for asset in hardbot.db hardbot.db-wal hardbot.db-shm hardbot.db-journal receipts audit-spool.sqlite3 audit-spool.sqlite3-wal audit-spool.sqlite3-shm; do
  [ -e "data/$asset" ] && mv "data/$asset" "$aside/$asset"
done
# --db-only update snapshots deliberately omit photos: leave the existing tree
# untouched, never detach it while rolling back only the database.
if [ -d "$src/photos" ] && [ -e data/photos ]; then
  mv data/photos "$aside/photos"
fi
# Incomplete publish leaves prior exact DB+WAL+assets recoverable as one set
# in aside; never delete an old WAL before its matching DB is secured.
for asset in hardbot.db photos receipts audit-spool.sqlite3; do
  [ -e "$stage/$asset" ] && mv "$stage/$asset" "data/$asset"
done
rmdir "$stage"
ok "база восстановлена"

say "Запускаю приложение…"
compose start app
if wait_healthy app 180; then
  ok "app: $(health_of app)"
  published=1
else
  compose logs --tail=40 app || true
  die "приложение не поднялось после восстановления.
Что сделать: прежняя база не потеряна — она рядом, data/hardbot.db.before-restore-$stamp.
Остановите app; отложите НЕУДАЧНЫЕ data/hardbot.db и активы, верните ВМЕСТЕ файлы
из $aside (включая -wal/-shm, receipts, audit-spool), затем запустите app.
Нельзя копировать одну старую БД поверх WAL нового запуска."
fi
say "Готово"
