#!/usr/bin/env bash
# ПЕРЕЕЗД ЖИВОЙ БАЗЫ «ХАРД» С ОДНОГО СЕРВЕРА НА ДРУГОЙ.
#
# Не путать с backup.sh/restore.sh: те делают копию ВНУТРИ одной установки
# (для отката). Здесь другое — снять работающую систему целиком и поднять её
# на чужом сервере: база, фотографии, контрольные суммы и опись, по которой
# принимающая сторона сверяет, что доехало.
#
#   НА СТАРОМ СЕРВЕРЕ:
#     ./migrate.sh take                  # снять всё: базу и фотографии
#     ./migrate.sh take --db-only        # только базу (фото повезут отдельно)
#     ./migrate.sh take --with-people    # вместе с людьми (см. ниже и MIGRATION.md)
#
#   ПЕРЕНОС ПОРТФЕЛЕЙ НЕСКОЛЬКИХ СОТРУДНИКОВ (не всей системы). Отдельная
#   операция: с рабочего сервера снимается ЧАСТЬ, а на принимающей машине она
#   ДОЛИВАЕТСЯ в то, что там уже есть, — ничего не заменяя и ничего не стирая.
#
#     ./migrate.sh employees                                  # чьи ФИО в реестре
#     ./migrate.sh take --only-employees "Фамилия Имя;Фамилия Имя"
#     ./migrate.sh compat  handover/hard-subset-*.tar         # сверить схемы
#     ./migrate.sh receive handover/hard-subset-*.tar --merge  # долить
#
#   Сочетается с --from-container и --db-only. ФИО, которого нет в базе, — это
#   отказ со списком похожих, а не пустой архив: опечатка в фамилии иначе
#   обнаружилась бы уже на другой машине.
#
#   ЕСЛИ СТАРЫЙ СЕРВЕР — НЕ ТАКАЯ ЖЕ УСТАНОВКА. Данные бывают в контуре, который
#   ставили до этих скриптов: свой каталог, свой образ, имена другие. Снимать
#   оттуда — та же операция, надо только показать, ОТКУДА брать:
#
#     ./migrate.sh take --from-container hardbot2-bot-1
#
#   Скрипт спросит у самого контейнера, какой у него образ и какой каталог хоста
#   подключён как /data, — то есть не даст перепутать каталог и снять копию
#   чужой или пустой базы. Контейнер может быть и остановлен: `docker inspect`
#   отвечает и про такой. Если контейнера нет вовсе, путь и образ задаются
#   руками: --data КАТАЛОГ --image ОБРАЗ.
#
#   НА НОВОМ СЕРВЕРЕ:
#     ./migrate.sh check   handover/hard-handover-*.tar   # только проверить
#     ./migrate.sh receive handover/hard-handover-*.tar   # проверить и поставить
#
# ЧТО НЕ ПЕРЕЕЗЖАЕТ И ПОЧЕМУ. Всё, что на новом сервере станет ложью или
# лишней дверью: токен бота (он лежит в базе, а не только в .env), беседа для
# сводок, устройства пульта, токены и ссылки входа в мини-приложение,
# привязки «телеграм-аккаунт → сотрудник». Последнее — не список людей, а
# белый список доступа к персональным данным должников: переехавшая строка
# открыла бы карточки человеку, которому новый владелец ничего не выдавал.
# Полный разбор с обоснованием каждой таблицы — в deploy/MIGRATION.md.
#
# Флаг --with-people переносит людей, но обезвреженными: привязки приезжают
# заявками (их надо одобрить в пульте), беседы — выключенными.
#
# Файлы .env НЕ ПЕРЕЕЗЖАЮТ НИКОГДА. На новом сервере свой домен, свой ключ
# пульта, свой токен бота; .env заполняется там заново по .env.example.
source "$(dirname "$0")/../../_lib.sh"

TOOL="$DEPLOY_DIR/../tools/export_for_handover.py"
# Перенос портфелей живёт в server/, а не в tools/, по одной причине: его
# запускают ВНУТРИ образа приложения, и там он оказывается сам собой — даже
# если каталог tools/ на эту машину не привозили.
SUBSET_TOOL="$DEPLOY_DIR/../server/subset_handover.py"
ARCHIVE_ROOT="hard-handover"
SUBSET_ROOT="hard-subset"
UNPACK_DIR="$UNIT_DIR/.migrate-in"

# Откуда берём данные и чем их читаем. По умолчанию — эта самая установка;
# снятие с чужого контура меняет обе строки (--from-container / --data --image).
# Приём всегда идёт в СВОЮ единицу: принимать «куда-нибудь» нельзя.
APP_IMAGE="hard-app"
DATA_DIR="$UNIT_DIR/data"

# Подсказка — это шапка самого файла: одно место вместо двух, которые
# расходятся. Печатаем комментарий со второй строки до первой строки кода,
# поэтому правка шапки не требует правки номеров строк.
usage() {
  awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' "$0"
  exit "${1:-1}"
}

need_tool() {
  [ -f "$TOOL" ] || die "не найден $TOOL — рядом с deploy/ должен лежать каталог tools/ из того же пакета"
  need_cmd tar "он есть в любом дистрибутиве: apt install tar"
}

need_subset_tool() {
  [ -f "$SUBSET_TOOL" ] \
    || die "не найден $SUBSET_TOOL — рядом с deploy/ должен лежать каталог server/ из того же пакета"
  need_cmd tar "он есть в любом дистрибутиве: apt install tar"
}

need_image() {
  check_docker
  docker image inspect "$APP_IMAGE" >/dev/null 2>&1 \
    || if [ "$APP_IMAGE" = "hard-app" ]; then
         die "нет образа hard-app. Сначала поставьте ядро: ./install.sh"
       else
         die "нет образа $APP_IMAGE — проверьте: docker image ls"
       fi
}

# Спросить у контейнера, где его данные и каким образом их читать. Ошибиться
# каталогом здесь дороже всего: копия снимется с ЧУЖОЙ или пустой базы и будет
# выглядеть совершенно нормально, пока её не откроют на новом сервере.
from_container() {
  local name="$1"
  check_docker
  docker inspect "$name" >/dev/null 2>&1 \
    || die "нет контейнера $name — посмотрите список: docker ps -a"
  APP_IMAGE="$(docker inspect "$name" --format '{{.Config.Image}}' 2>/dev/null)"
  [ -n "$APP_IMAGE" ] || die "у контейнера $name не читается образ"
  # Приложение держит данные в /data при любой версии установки (APP_DATA=/data
  # в образе), поэтому ищем ровно эту точку монтирования.
  DATA_DIR="$(docker inspect "$name" \
    --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' 2>/dev/null)"
  [ -n "$DATA_DIR" ] \
    || die "у контейнера $name нет каталога, подключённого как /data.
Значит данные лежат где-то ещё — укажите путь руками: --data КАТАЛОГ --image ОБРАЗ"
  say "Источник: контейнер $name"
  say "  данные: $DATA_DIR"
  say "  образ:  $APP_IMAGE"
}

# in_app КОМАНДА… — выполнить инструмент переноса ОДНОРАЗОВЫМ контейнером из
# образа приложения.
#
# Почему не `docker compose exec` в работающий контейнер. Приложение бывает в
# трёх состояниях: работает, остановлено и падает по кругу. Третье — не
# редкость, а как раз частый повод переезжать, и exec в такой контейнер
# отвечает «container is restarting», то есть перенос спотыкается ровно там,
# где он нужнее всего. Одноразовый контейнер одинаково работает во всех трёх.
#
# Том с данными монтируется НА ЗАПИСЬ, хотя мы только читаем: база живёт в
# режиме WAL, и SQLite нужен доступ на запись к служебному файлу -shm — без
# него он базу вообще не откроет и не доберёт хвост из -wal, где лежат
# последние визиты. Двум процессам работать с одной базой SQLite можно —
# ради этого WAL и сделан; приложение останавливать не надо.
#
# Сам инструмент подаётся в контейнер через stdin: на сервере может не быть
# питона, а образ приложения есть всегда.
in_app() {
  docker run --rm -i -v "$DATA_DIR:/data" "$APP_IMAGE" python - "$@" < "$TOOL"
}

# То же, плюс том с распакованным архивом (только чтение).
in_app_with_archive() {
  docker run --rm -i -v "$DATA_DIR:/data" -v "$UNPACK_DIR:/in:ro" \
    "$APP_IMAGE" python - "$@" < "$TOOL"
}

# Те же две функции для переноса портфелей. Отдельные, а не «ещё один параметр»:
# инструмент подаётся в контейнер через stdin, и подменить файл на лету нечем.
# Важно, что это работает и со СТАРЫМ образом чужого контура: в него ничего не
# добавляли, python в нём есть, а сам инструмент приезжает потоком.
in_subset() {
  docker run --rm -i -v "$DATA_DIR:/data" "$APP_IMAGE" python - "$@" < "$SUBSET_TOOL"
}

in_subset_with_archive() {
  docker run --rm -i -v "$DATA_DIR:/data" -v "$UNPACK_DIR:/in:ro" \
    "$APP_IMAGE" python - "$@" < "$SUBSET_TOOL"
}

# --- СНЯТИЕ -----------------------------------------------------------------

cmd_take() {
  local with_people=0 db_only=0 out="$UNIT_DIR/handover" container="" only=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --with-people)   with_people=1 ;;
      --db-only)       db_only=1 ;;
      --only-employees) only="${2:-}"; shift; [ -n "$only" ] || die "--only-employees без ФИО" ;;
      --out)           out="${2:-}"; shift; [ -n "$out" ] || die "--out без каталога" ;;
      --from-container) container="${2:-}"; shift; [ -n "$container" ] || die "--from-container без имени" ;;
      --data)          DATA_DIR="${2:-}"; shift; [ -n "$DATA_DIR" ] || die "--data без каталога" ;;
      --image)         APP_IMAGE="${2:-}"; shift; [ -n "$APP_IMAGE" ] || die "--image без образа" ;;
      *) die "неизвестный параметр: $1 (см. ./migrate.sh без параметров)" ;;
    esac
    shift
  done
  [ -n "$container" ] && from_container "$container"

  if [ -n "$only" ] && [ "$with_people" -eq 1 ]; then
    die "--with-people и --only-employees вместе не имеют смысла: портфель без
своего человека бесполезен, поэтому привязки выбранных сотрудников едут всегда."
  fi

  need_image
  [ -f "$DATA_DIR/hardbot.db" ] || die "нет $DATA_DIR/hardbot.db — переносить нечего"
  need_cmd sha256sum "он есть в coreutils"

  local flags=() root="$ARCHIVE_ROOT"
  if [ -n "$only" ]; then
    need_subset_tool
    root="$SUBSET_ROOT"
    flags+=(--only-employees "$only")
  else
    need_tool
    [ "$with_people" -eq 1 ] && flags+=(--with-people)
  fi
  [ "$db_only" -eq 1 ] && flags+=(--db-only)

  mkdir -p "$out"
  local stamp name tmp
  stamp="$(date +%Y%m%dT%H%M%S)"
  name="$root-$stamp.tar"
  tmp="$out/.$name.part"

  say "Снимаю копию. Приложение останавливать не надо — и не надо, если оно"
  say "сейчас лежит: копия снимается одноразовым контейнером в любом случае."

  # Ошибку контейнера ловим сами: при set -e недописанный .part иначе
  # остался бы лежать и выглядел бы как готовый архив.
  local rc=0
  if [ -n "$only" ]; then
    in_subset take --data /data --host "$(hostname 2>/dev/null || echo сервер)" \
      "${flags[@]}" > "$tmp" || rc=$?
  else
    in_app take --data /data --host "$(hostname 2>/dev/null || echo сервер)" \
      "${flags[@]}" > "$tmp" || rc=$?
  fi
  if [ "$rc" -ne 0 ]; then
    rm -f "$tmp"
    die "снять копию не удалось (текст ошибки выше)"
  fi

  # Проверяем то, что получилось на ХОСТЕ, двумя дешёвыми проверками.
  # Дёшево здесь принципиально: архив с фотографиями бывает на десятки
  # гигабайт, и `tar -tf` пришлось бы прочитать его целиком — ради двух
  # ответов, которые видны в первых и последних байтах.
  #
  #   начало: в байтах 257..262 любого tar стоит подпись «ustar». Если docker
  #           подпортил двоичный поток (такое бывает на экзотических сборках),
  #           подпись не сойдётся. Имя первого файла НЕ проверяем: там может
  #           стоять служебная запись @PaxHeader — русские имена внутри архива
  #           хранятся именно так;
  #   конец:  правильный tar заканчивается двумя пустыми блоками по 512 байт.
  #           Оборванная запись (кончилось место на диске) их не содержит.
  local magic tail_junk
  magic="$(head -c 263 "$tmp" | tail -c 6 | tr -d '\000 ')"
  if [ "$magic" != "ustar" ]; then
    rm -f "$tmp"
    die "получился не tar (подпись «$magic» вместо «ustar») — двоичный поток испорчен"
  fi
  tail_junk="$(tail -c 1024 "$tmp" | tr -d '\000' | wc -c)"
  if [ "$tail_junk" -ne 0 ]; then
    rm -f "$tmp"
    die "архив дописан не до конца (нет завершающих блоков) — скорее всего кончилось место на диске"
  fi

  mv "$tmp" "$out/$name"
  ( cd "$out" && sha256sum "$name" > "$name.sha256" )
  ok "архив:      $out/$name ($(human_size "$out/$name"))"
  ok "сумма:      $out/$name.sha256"
  say ""
  say "Везите на новый сервер ОБА файла (архив и .sha256), например:"
  say "  scp $out/$name $out/$name.sha256 root@НОВЫЙ:/opt/hard/deploy/units/core/handover/"
  if [ -n "$only" ]; then
    say "Там: ./migrate.sh compat  handover/$name   # сверить схемы"
    say "     ./migrate.sh receive handover/$name --merge"
  else
    say "Там: ./migrate.sh receive handover/$name"
  fi
}

# --- КТО ЕСТЬ В РЕЕСТРЕ -----------------------------------------------------
# Отдельная команда, потому что весь перенос портфелей держится на точном
# написании ФИО, а пишут их руками — и в реестре, и в заявке на перенос.
cmd_employees() {
  local container=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --from-container) container="${2:-}"; shift; [ -n "$container" ] || die "--from-container без имени" ;;
      --data)  DATA_DIR="${2:-}"; shift; [ -n "$DATA_DIR" ] || die "--data без каталога" ;;
      --image) APP_IMAGE="${2:-}"; shift; [ -n "$APP_IMAGE" ] || die "--image без образа" ;;
      *) die "неизвестный параметр: $1" ;;
    esac
    shift
  done
  [ -n "$container" ] && from_container "$container"
  need_subset_tool; need_image
  [ -f "$DATA_DIR/hardbot.db" ] || die "нет $DATA_DIR/hardbot.db"
  in_subset employees --data /data
}

# --- ПРИЁМ ------------------------------------------------------------------

unpack_and_verify() {
  # $2 — вид архива: "full" (вся система) или "subset" (портфели сотрудников).
  # Их нельзя перепутать: внутри разные каталоги и разные имена базы, а
  # инструмент проверки у каждого свой.
  local archive="$1" kind="${2:-full}" root tool
  if [ "$kind" = "subset" ]; then root="$SUBSET_ROOT"; tool="$SUBSET_TOOL"
  else                            root="$ARCHIVE_ROOT"; tool="$TOOL"; fi
  [ -f "$archive" ] || die "нет файла $archive"

  if [ -f "$archive.sha256" ]; then
    if command -v sha256sum >/dev/null 2>&1; then
      say "Сверяю контрольную сумму архива…"
      ( cd "$(dirname "$archive")" && sha256sum -c "$(basename "$archive").sha256" ) \
        || die "сумма архива не сошлась — файл побился по дороге, везите заново"
      ok "сумма архива сошлась"
    else
      warn "нет sha256sum — сумму архива не проверяю"
    fi
  else
    warn "рядом нет $archive.sha256 — проверю только то, что внутри архива"
  fi

  rm -rf "$UNPACK_DIR"
  mkdir -p "$UNPACK_DIR"
  say "Распаковываю…"
  tar -xf "$archive" -C "$UNPACK_DIR" || die "архив не распаковался"
  if [ ! -d "$UNPACK_DIR/$root" ]; then
    if [ "$kind" = "subset" ] && [ -d "$UNPACK_DIR/$ARCHIVE_ROOT" ]; then
      die "это архив ПОЛНОГО переезда, а не портфелей: принимать его надо без --merge"
    fi
    if [ "$kind" = "full" ] && [ -d "$UNPACK_DIR/$SUBSET_ROOT" ]; then
      die "это архив ПОРТФЕЛЕЙ сотрудников: принимать его надо с --merge,
иначе он заменил бы всю базу вместо доливки"
    fi
    die "в архиве нет каталога $root"
  fi

  say ""
  say "ПРОВЕРКА АРХИВА"
  say "---------------"
  # Проверяем архив ДО того, как что-то трогаем в data/. Порченая база должна
  # остановить перенос здесь, а не после подмены рабочего файла.
  if ! docker run --rm -i -v "$UNPACK_DIR:/in:ro" "$APP_IMAGE" \
        python - verify --dir "/in/$root" < "$tool"; then
    die "архив не принят (см. список выше). data/ не тронут."
  fi
}

cmd_check() {
  # Проверять архив полезно и на СТАРОМ сервере, сразу после снятия: там нет
  # образа hard-app, поэтому читать архив нечем — кроме образа того самого
  # приложения, которым его сняли.
  local archive=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --image)          APP_IMAGE="${2:-}"; shift; [ -n "$APP_IMAGE" ] || die "--image без образа" ;;
      --from-container) from_container "${2:-}"; shift ;;
      -*)               die "неизвестный параметр: $1" ;;
      *)                archive="$1" ;;
    esac
    shift
  done
  need_tool; need_image
  [ -n "$archive" ] || die "укажите архив: ./migrate.sh check handover/$ARCHIVE_ROOT-*.tar"
  unpack_and_verify "$archive"
  say ""
  say "Это была только проверка. Ставить: ./migrate.sh receive $archive"
  rm -rf "$UNPACK_DIR"
}

# --- СВЕРКА СХЕМ ------------------------------------------------------------
# Отдельная команда, потому что её задают ДО переноса и на чужой машине:
# «влезет ли это туда вообще». Ответ бывает нужен раньше, чем архив привезут.
cmd_compat() {
  local archive=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -*) die "неизвестный параметр: $1" ;;
      *)  archive="$1" ;;
    esac
    shift
  done
  [ -n "$archive" ] || die "укажите архив: ./migrate.sh compat handover/$SUBSET_ROOT-*.tar"
  need_subset_tool; need_image; check_docker
  [ -f "$UNIT_DIR/data/hardbot.db" ] \
    || die "на этой машине ещё нет базы — сначала ./install.sh и один запуск: схему заводит приложение"
  unpack_and_verify "$archive" subset
  say ""
  in_subset_with_archive compat --data /data --archive "/in/$SUBSET_ROOT"
  rm -rf "$UNPACK_DIR"
  say ""
  say "Это была только сверка. Долить: ./migrate.sh receive $archive --merge"
}

# --- ДОЛИВКА ПОРТФЕЛЕЙ ------------------------------------------------------
# Отличается от приёма целой системы ровно тем, ради чего и сделана: ничего не
# заменяется. Прежняя база не отодвигается в сторону, а дополняется, поэтому
# копию перед доливкой снимаем сами — откатываться иначе будет не на что.
receive_merge() {
  local archive="$1" yes="$2"
  need_subset_tool; need_image; check_docker
  [ -f "$UNIT_DIR/data/hardbot.db" ] \
    || die "на этой машине ещё нет базы — сначала ./install.sh и один запуск: схему заводит приложение"

  unpack_and_verify "$archive" subset

  say ""
  say "СВЕРКА СХЕМ"
  say "-----------"
  in_subset_with_archive compat --data /data --archive "/in/$SUBSET_ROOT" \
    || { rm -rf "$UNPACK_DIR"; die "схемы несовместимы — доливка не начата, data/ не тронут"; }

  # Кто именно приезжает — ДО вопроса «продолжить?». Числа проверка уже
  # показала, а вот фамилии видно только здесь, и перепутанный архив узнаётся
  # по ним за секунду. Печатаем голову описи, до раздела «ФАЙЛЫ».
  say ""
  say "КТО ПРИЕЗЖАЕТ"
  say "-------------"
  awk '/^ФАЙЛЫ:/ { exit } { print "  " $0 }' \
    "$UNPACK_DIR/$SUBSET_ROOT/ОПИСЬ.txt" 2>/dev/null || true

  local mstamp aside; mstamp="$(date +%Y%m%dT%H%M%S)"
  aside="$UNIT_DIR/backups/before-merge-$mstamp"
  say ""
  say "БУДЕТ СДЕЛАНО"
  say "-------------"
  say "  • приложение остановится"
  say "  • копия нынешней базы уедет в ${aside#$UNIT_DIR/} (ничего не удаляется)"
  say "  • портфели из архива ДОЛЬЮТСЯ в базу: что уже есть — останется"
  say "  • фотографии из архива добавятся к тем, что лежат в data/photos"
  say "  • приложение запустится, и будет напечатано «было → стало»"
  if [ "$yes" -ne 1 ]; then
    printf 'Продолжить? [y/N] '
    read -r answer
    case "$answer" in y|Y|д|Д) ;; *) rm -rf "$UNPACK_DIR"; say "Отменено"; exit 0 ;; esac
  fi

  say ""
  say "Останавливаю приложение…"
  # Приложение останавливаем не из-за SQLite (WAL допускает двух писателей), а
  # ради простоты разбора: если что-то пойдёт не так, состояние базы будет
  # определяться одной нашей транзакцией, а не гонкой с живыми запросами.
  compose stop app >/dev/null 2>&1 || true

  mkdir -p "$aside"
  cp -a "$UNIT_DIR/data/hardbot.db" "$aside/hardbot.db"
  for spare in "$UNIT_DIR"/data/hardbot.db-wal "$UNIT_DIR"/data/hardbot.db-shm; do
    [ -f "$spare" ] && cp -a "$spare" "$aside/"
  done
  ok "копия базы отложена: ${aside#$UNIT_DIR/}/hardbot.db"

  say ""
  local rc=0
  in_subset_with_archive merge --data /data --archive "/in/$SUBSET_ROOT" || rc=$?

  say ""
  say "Запускаю приложение…"
  compose up -d >/dev/null
  if ! wait_healthy app "$UNIT_WAIT_SECONDS"; then
    compose logs --tail=40 app || true
    die "приложение не поднялось после доливки. Копия базы до неё: ${aside#$UNIT_DIR/}"
  fi
  ok "приложение: $(health_of app)"

  rm -rf "$UNPACK_DIR"
  if [ "$rc" -ne 0 ]; then
    die "доливка не выполнена (текст ошибки выше). База осталась прежней:
вся доливка идёт одной транзакцией, поэтому «наполовину» не бывает."
  fi
  say ""
  say "ЧТО СДЕЛАТЬ ДАЛЬШЕ:"
  say "  • пульт, раздел «Вход»: выдать ссылки приехавшим сотрудникам;"
  say "  • сверить числа выше с ОПИСЬ.txt из архива;"
  say "  • копию базы до доливки (${aside#$UNIT_DIR/}) удалить, когда всё сойдётся."
  say ""
  say "Готово."
}

cmd_receive() {
  local archive="" replace=0 yes=0 merge=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --replace) replace=1 ;;
      --merge)   merge=1 ;;
      --yes)     yes=1 ;;
      -*)        die "неизвестный параметр: $1" ;;
      *)         archive="$1" ;;
    esac
    shift
  done
  [ -n "$archive" ] || die "укажите архив: ./migrate.sh receive handover/$ARCHIVE_ROOT-*.tar"
  if [ "$merge" -eq 1 ]; then
    [ "$replace" -eq 0 ] \
      || die "--merge и --replace исключают друг друга: доливка НЕ заменяет базу"
    receive_merge "$archive" "$yes"
    return 0
  fi

  need_tool; need_image; check_docker
  mkdir -p "$UNIT_DIR/data"
  unpack_and_verify "$archive"

  local incoming_photos=0
  [ -d "$UNPACK_DIR/$ARCHIVE_ROOT/photos" ] && incoming_photos=1

  # --- Что уже стоит на этом сервере ---------------------------------------
  local state
  state="$(in_app state --data /data | tr -d '\r')"
  say ""
  say "СОСТОЯНИЕ ЭТОГО СЕРВЕРА"
  say "-----------------------"
  say "  $state"

  case "$state" in
    занято*)
      if [ "$replace" -ne 1 ]; then
        rm -rf "$UNPACK_DIR"
        say ""
        die "на этом сервере УЖЕ ЕСТЬ данные ($state).
Затирать их молча нельзя: сливать две базы «Хард» автоматически невозможно —
номера договоров совпадут, а визиты и фотографии за ними разные.

Выберите одно:
  1) Поставить сюда, отложив то, что есть (старое НЕ удаляется, а уезжает
     в backups/before-migration-<время>/ и возвращается обратно вручную):
         ./migrate.sh receive $archive --replace
  2) Ставить на чистую установку — на другом сервере или после
     удаления каталога data/ этой единицы.
Ничего не изменено."
      fi
      ;;
  esac

  # --- Подтверждение --------------------------------------------------------
  local mstamp; mstamp="$(date +%Y%m%dT%H%M%S)"
  local aside="$UNIT_DIR/backups/before-migration-$mstamp"
  say ""
  say "БУДЕТ СДЕЛАНО"
  say "-------------"
  say "  • приложение остановится"
  if [ "$replace" -eq 1 ]; then
    if [ "$incoming_photos" -eq 1 ]; then
      say "  • текущие база и фотографии уедут в ${aside#$UNIT_DIR/} (не удалятся)"
    else
      say "  • текущая база уедет в ${aside#$UNIT_DIR/} (не удалится)"
      say "  • фотографии останутся как есть: в архиве их нет (снимали с --db-only)"
    fi
  fi
  if [ "$incoming_photos" -eq 1 ]; then
    say "  • в data/ встанут база и фотографии из архива"
  else
    say "  • в data/ встанет база из архива"
  fi
  say "  • приложение запустится и опись сверится с тем, что реально в базе"
  if [ "$yes" -ne 1 ]; then
    printf 'Продолжить? [y/N] '
    read -r answer
    case "$answer" in y|Y|д|Д) ;; *) rm -rf "$UNPACK_DIR"; say "Отменено"; exit 0 ;; esac
  fi

  say ""
  say "Останавливаю приложение…"
  compose stop app >/dev/null 2>&1 || true

  # --- Отложить то, что было -----------------------------------------------
  # Именно ОТЛОЖИТЬ, а не удалить: перенос — операция, которую делают один раз
  # и под давлением, и «а верните как было» звучит чаще, чем хотелось бы.
  if [ "$replace" -eq 1 ]; then
    mkdir -p "$aside"
    if [ -f "$UNIT_DIR/data/hardbot.db" ]; then
      mv "$UNIT_DIR/data/hardbot.db" "$aside/hardbot.db"
      # Спутники WAL без своей базы бессмысленны и опасны — уносим вместе с ней.
      for spare in "$UNIT_DIR"/data/hardbot.db-wal "$UNIT_DIR"/data/hardbot.db-shm; do
        [ -f "$spare" ] && mv "$spare" "$aside/"
      done
      ok "прежняя база отложена: ${aside#$UNIT_DIR/}/hardbot.db"
    fi
    if [ "$incoming_photos" -eq 1 ] && [ -d "$UNIT_DIR/data/photos" ]; then
      mv "$UNIT_DIR/data/photos" "$aside/photos"
      ok "прежние фотографии отложены: ${aside#$UNIT_DIR/}/photos"
    fi
  else
    rm -f "$UNIT_DIR"/data/hardbot.db-wal "$UNIT_DIR"/data/hardbot.db-shm
  fi

  # --- Подставить новое -----------------------------------------------------
  cp "$UNPACK_DIR/$ARCHIVE_ROOT/hardbot.db" "$UNIT_DIR/data/hardbot.db"
  ok "база подставлена"
  if [ "$incoming_photos" -eq 1 ]; then
    mkdir -p "$UNIT_DIR/data/photos"
    cp -a "$UNPACK_DIR/$ARCHIVE_ROOT/photos/." "$UNIT_DIR/data/photos/"
    ok "фотографии подставлены"
  else
    say "  в архиве фотографий нет (снимали с --db-only) — каталог photos не тронут"
  fi

  say ""
  say "Запускаю приложение…"
  compose up -d >/dev/null
  if ! wait_healthy app "$UNIT_WAIT_SECONDS"; then
    compose logs --tail=40 app || true
    die "приложение не поднялось после переноса. Архив распакован в ${UNPACK_DIR#$UNIT_DIR/} — ничего не потеряно."
  fi
  ok "приложение: $(health_of app)"

  # --- Отчёт ----------------------------------------------------------------
  # Считаем по ЖИВОЙ базе, которую только что открыло приложение, и сверяем с
  # описью из архива. Это и есть ответ на вопрос «а точно всё доехало?»:
  # сверять две бумажки друг с другом смысла нет, сверять надо с фактом.
  say ""
  say "ПЕРЕНЕСЕНО"
  say "----------"
  local rc=0
  in_app_with_archive inventory --data /data --compare "/in/$ARCHIVE_ROOT/manifest.json" || rc=$?
  say ""
  if [ "$rc" -ne 0 ]; then
    warn "опись сошлась не полностью — расхождения помечены выше."
    warn "Данные на месте, но прежде чем отдавать систему в работу, разберитесь с ними."
  else
    ok "опись сошлась полностью: в базе ровно то, что было заявлено при снятии"
  fi

  rm -rf "$UNPACK_DIR"
  say ""
  say "ЧТО СДЕЛАТЬ ДАЛЬШЕ (это НЕ переезжает и настраивается здесь):"
  say "  • .env: свой MINIAPP_URL, свой BOT_TOKEN, свой ADMIN_ID, свой ADMIN_API_KEY;"
  say "  • пульт: подключить заново (мастер-ключ из .env, устройства не переезжают);"
  say "  • сотрудники: пишут боту /start и получают доступ после одобрения владельцем;"
  say "  • беседы для отчётов: добавить бота в группу заново."
  [ "$replace" -eq 1 ] && say "  • прежние данные лежат в ${aside#$UNIT_DIR/} — удалите, когда убедитесь, что всё в порядке."
  say ""
  say "Готово."
}

# ----------------------------------------------------------------------------

case "${1:-}" in
  take)      shift; cmd_take "$@" ;;
  check)     shift; cmd_check "$@" ;;
  compat)    shift; cmd_compat "$@" ;;
  employees) shift; cmd_employees "$@" ;;
  receive)   shift; cmd_receive "$@" ;;
  help|--help|-h) usage 0 ;;
  "")      usage 1 ;;
  *)       say "Неизвестная команда: $1"; say ""; usage 1 ;;
esac
