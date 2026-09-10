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
ARCHIVE_ROOT="hard-handover"
UNPACK_DIR="$UNIT_DIR/.migrate-in"

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

need_image() {
  check_docker
  docker image inspect hard-app >/dev/null 2>&1 \
    || die "нет образа hard-app. Сначала поставьте ядро: ./install.sh"
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
  docker run --rm -i -v "$UNIT_DIR/data:/data" hard-app python - "$@" < "$TOOL"
}

# То же, плюс том с распакованным архивом (только чтение).
in_app_with_archive() {
  docker run --rm -i -v "$UNIT_DIR/data:/data" -v "$UNPACK_DIR:/in:ro" \
    hard-app python - "$@" < "$TOOL"
}

# --- СНЯТИЕ -----------------------------------------------------------------

cmd_take() {
  local with_people=0 db_only=0 out="$UNIT_DIR/handover"
  while [ $# -gt 0 ]; do
    case "$1" in
      --with-people) with_people=1 ;;
      --db-only)     db_only=1 ;;
      --out)         out="${2:-}"; shift; [ -n "$out" ] || die "--out без каталога" ;;
      *) die "неизвестный параметр: $1 (см. ./migrate.sh без параметров)" ;;
    esac
    shift
  done

  need_tool; need_image
  [ -f "$UNIT_DIR/data/hardbot.db" ] || die "нет data/hardbot.db — переносить нечего"
  need_cmd sha256sum "он есть в coreutils"

  local flags=(); [ "$with_people" -eq 1 ] && flags+=(--with-people)
  [ "$db_only" -eq 1 ] && flags+=(--db-only)

  mkdir -p "$out"
  local stamp name tmp
  stamp="$(date +%Y%m%dT%H%M%S)"
  name="$ARCHIVE_ROOT-$stamp.tar"
  tmp="$out/.$name.part"

  say "Снимаю копию. Приложение останавливать не надо — и не надо, если оно"
  say "сейчас лежит: копия снимается одноразовым контейнером в любом случае."

  # Ошибку контейнера ловим сами: при set -e недописанный .part иначе
  # остался бы лежать и выглядел бы как готовый архив.
  if ! in_app take --data /data --host "$(hostname 2>/dev/null || echo сервер)" \
        "${flags[@]}" > "$tmp"; then
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
  say "Там: ./migrate.sh receive handover/$name"
}

# --- ПРИЁМ ------------------------------------------------------------------

unpack_and_verify() {
  local archive="$1"
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
  [ -d "$UNPACK_DIR/$ARCHIVE_ROOT" ] || die "в архиве нет каталога $ARCHIVE_ROOT"

  say ""
  say "ПРОВЕРКА АРХИВА"
  say "---------------"
  # Проверяем архив ДО того, как что-то трогаем в data/. Порченая база должна
  # остановить перенос здесь, а не после подмены рабочего файла.
  if ! docker run --rm -i -v "$UNPACK_DIR:/in:ro" hard-app \
        python - verify --dir "/in/$ARCHIVE_ROOT" < "$TOOL"; then
    die "архив не принят (см. список выше). data/ не тронут."
  fi
}

cmd_check() {
  need_tool; need_image
  local archive="${1:-}"
  [ -n "$archive" ] || die "укажите архив: ./migrate.sh check handover/$ARCHIVE_ROOT-*.tar"
  unpack_and_verify "$archive"
  say ""
  say "Это была только проверка. Ставить: ./migrate.sh receive $archive"
  rm -rf "$UNPACK_DIR"
}

cmd_receive() {
  local archive="" replace=0 yes=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --replace) replace=1 ;;
      --yes)     yes=1 ;;
      -*)        die "неизвестный параметр: $1" ;;
      *)         archive="$1" ;;
    esac
    shift
  done
  [ -n "$archive" ] || die "укажите архив: ./migrate.sh receive handover/$ARCHIVE_ROOT-*.tar"

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
  take)    shift; cmd_take "$@" ;;
  check)   shift; cmd_check "$@" ;;
  receive) shift; cmd_receive "$@" ;;
  help|--help|-h) usage 0 ;;
  "")      usage 1 ;;
  *)       say "Неизвестная команда: $1"; say ""; usage 1 ;;
esac
