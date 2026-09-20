#!/usr/bin/env bash
# ЗАГРУЗКА ОФЛАЙН-ПАКЕТА на машине без интернета. Одна команда:
#
#   cd <каталог пакета с флешки> && ./load.sh --deploy /opt/hard/deploy
#
# --deploy — путь к распакованным исходникам системы (каталог deploy/, внутри
# него units/core, units/stt, units/gps). Пакет обычно запускают с флешки, а
# исходники лежат на диске сервера, и связать их скрипт сам не может. Если
# каталог найдётся в обычном месте, ключ не нужен: ./load.sh хватит.
#
# Что делает по порядку: находит deploy/, сверяет контрольные суммы, заносит
# образы в docker, раскладывает модель речи, ставит отметку «эта машина офлайн»
# — после неё install.sh и update.sh не пытаются ничего собирать. Ни pip, ни
# docker pull отсюда не вызываются: если что-то не доехало, скрипт скажет об
# этом словами, а не уйдёт молча в интернет, которого нет.
#
#   ./load.sh --no-verify                  # пропустить сверку сумм (не надо так)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY=""
VERIFY=1

while [ $# -gt 0 ]; do
  case "$1" in
    --deploy) DEPLOY="${2:-}"; shift 2 ;;
    --no-verify) VERIFY=0; shift ;;
    -h|--help) sed -n '2,17p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) printf 'не понимаю аргумент «%s»\n' "$1" >&2; exit 1 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
ok()   { printf '  [ок] %s\n' "$*"; }
warn() { printf '  [!!] %s\n' "$*" >&2; }
die()  { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

# Путь из --deploy разворачиваем в полный ДО перехода в каталог пакета: иначе
# «--deploy ../hard/deploy» считался бы от флешки, а не от того каталога, где
# инженер стоял, когда набирал команду.
if [ -n "$DEPLOY" ] && [ -d "$DEPLOY" ]; then DEPLOY="$(cd "$DEPLOY" && pwd)"; fi

cd "$DIR"
[ -d images ] || die "рядом с $0 нет каталога images/ — это не каталог офлайн-пакета"
command -v docker >/dev/null 2>&1 || die "не найден docker — поставьте Docker Engine и повторите"
docker info >/dev/null 2>&1 || die "docker недоступен: демон не запущен или нет прав (нужен root или группа docker)"

say "== Хард: загрузка офлайн-пакета =="
if [ -f МАНИФЕСТ.txt ]; then sed 's/^/  /' МАНИФЕСТ.txt | head -n 6; fi

# --- 0. Куда всё это ставить ---------------------------------------------------
# Пакет с образами лежит на флешке, а исходники (deploy/units/…) распакованы
# где-то на диске. Ищем сами в обычных местах; не нашли — спрашиваем; не
# ответили — останавливаемся ДО долгой работы, а не после неё.
#
# Раньше этот поиск стоял в середине скрипта и промах был не ошибкой, а
# предупреждением: образы загружались, модель не раскладывалась, отметка
# офлайн-режима не ставилась, а в конце печаталось «Готово» и возвращался ноль.
# Дальше install.sh отметки не видел и шёл собирать образы — в закрытом контуре
# это не «медленно», а «никогда». Промах был гарантирован самой инструкцией:
# README велит копировать пакет на флешку и запускать его оттуда, то есть рядом
# с пакетом каталога deploy/ заведомо нет.
looks_like_deploy() { [ -d "$1/units" ] && [ -f "$1/units/core/install.sh" ]; }

if [ -n "$DEPLOY" ]; then
  looks_like_deploy "$DEPLOY" \
    || die "по пути «$DEPLOY» нет units/core/install.sh — это не каталог deploy/ системы «Хард»"
  DEPLOY="$(cd "$DEPLOY" && pwd)"
else
  for guess in "$DIR/.." "$DIR/../deploy" "$PWD" "$PWD/deploy" "$PWD/../deploy" \
               /opt/hard/deploy /opt/hard /srv/hard/deploy /srv/hard "${HOME:-/root}/hard/deploy"; do
    if looks_like_deploy "$guess"; then DEPLOY="$(cd "$guess" && pwd)"; break; fi
  done
fi

if [ -z "$DEPLOY" ] && [ -t 0 ]; then
  say ""
  say "Не нашёл каталог deploy/ сам. Это каталог с исходниками системы — тот,"
  say "внутри которого лежат units/core, units/stt, units/gps. Обычно /opt/hard/deploy."
  printf 'Путь к deploy/ (пусто — прервать): '
  read -r answer || answer=""
  if [ -n "$answer" ]; then
    looks_like_deploy "$answer" \
      || die "по пути «$answer» нет units/core/install.sh — это не каталог deploy/ системы «Хард»"
    DEPLOY="$(cd "$answer" && pwd)"
  fi
fi

if [ -z "$DEPLOY" ]; then
  printf 'ОШИБКА: не нашёл каталог deploy/ — тот, куда распакованы исходники системы.\n' >&2
  printf '  Укажите путь явно:\n\n' >&2
  printf '      %s --deploy /opt/hard/deploy\n\n' "$0" >&2
  printf '  Без него некуда разложить модель речи и, главное, некуда поставить\n' >&2
  printf '  отметку офлайн-режима. Без отметки установка полезет собирать образы,\n' >&2
  printf '  а сборка ходит в интернет — в закрытом контуре она не пройдёт.\n' >&2
  printf '  Ничего не сделано, повторный запуск безопасен.\n' >&2
  exit 1
fi
ok "исходники системы: $DEPLOY"

# --- 1. Суммы ------------------------------------------------------------------
say ""
if [ "$VERIFY" = 1 ]; then
  say "1/4 Сверка контрольных сумм (несколько минут на гигабайты — это нормально)"
  if [ -x ./verify.sh ]; then ./verify.sh; else bash ./verify.sh; fi
else
  warn "1/4 Сверка сумм пропущена по вашей просьбе (--no-verify)"
fi

# --- 2. Образы -----------------------------------------------------------------
say ""
say "2/4 Загрузка образов в docker"
for tar in images/*.tar; do
  [ -f "$tar" ] || die "в images/ нет ни одного .tar"
  say "  … $(basename "$tar")"
  docker load -i "$tar" | sed 's/^/      /'
done
for want in hard-app:latest hard-stt:latest; do
  if docker image inspect "$want" >/dev/null 2>&1; then
    ok "$want на месте"
  else
    warn "$want в пакете нет — эта часть системы не поднимется"
  fi
done

say ""
say "3/4 Модель речи"
# Главная модель едет ЗАПЕЧЁННОЙ в образ hard-stt, а не в этом архиве. Поэтому
# первым делом спрашиваем сам образ, что в нём лежит, и только потом говорим про
# архив. Раньше здесь безусловно советовали вписать имя из архива — и инженер,
# честно выполнив совет, получал рабочую систему на запасной модели вместо той,
# которую сам же и привёз. Ошибка была тихой: пока кэш цел, всё работает.
baked=""
if docker image inspect hard-stt:latest >/dev/null 2>&1; then
  baked="$(docker run --rm --entrypoint python hard-stt:latest -c     "import json;print(json.load(open('/opt/hard/stt-model/hard-model.json'))['name'])" 2>/dev/null || true)"
fi

if [ -n "$baked" ]; then
  ok "в образе запечена модель «$baked» — она и будет работать"
  say "      STT_MODEL в .env единицы речи МЕНЯТЬ НЕ НАДО: там уже стоит «$baked»"
fi

if [ -f model/models.tar ]; then
  model_name="$(cat model/ИМЯ-МОДЕЛИ.txt 2>/dev/null || echo '?')"
  target="$DEPLOY/units/stt/models"
  mkdir -p "$target"
  tar -C "$target" -xf model/models.tar
  if [ -n "$baked" ]; then
    ok "запасная модель «$model_name» разложена в $target (на случай осознанной замены)"
    say "      это НЕ та модель, что поднимется: работает запечённая «$baked»"
  else
    ok "модель «$model_name» разложена в $target"
    say "      в образе запечённой модели нет, поэтому впишите STT_MODEL=$model_name в .env единицы речи"
  fi
elif [ -z "$baked" ]; then
  warn "модели нет ни в образе, ни в пакете — голос работать не будет (VOICE_BACKEND=off)"
fi

# --- 4. Отметка «здесь интернета нет» ------------------------------------------
say ""
say "4/4 Отметка офлайн-режима"
mkdir -p "$DEPLOY/offline"
cat > "$DEPLOY/offline/OFFLINE" <<'MARK'
Эту отметку поставил deploy/offline/load.sh.
Пока файл существует, install.sh и update.sh НЕ СОБИРАЮТ образы: считается, что
готовые образы уже загружены из офлайн-пакета. Так и должно быть в закрытом
контуре — сборка там всё равно не пройдёт, она ходит в интернет за пакетами.
Удалять файл нужно только в одном случае: машина получила интернет и вы хотите
собирать образы на месте.
MARK
ok "$DEPLOY/offline/OFFLINE"

say ""
say "== Готово =="
say "Дальше — обычная установка, она увидит отметку и ничего собирать не станет:"
say "  cd $DEPLOY/units/core && ./install.sh"
say "  cd $DEPLOY/units/stt  && ./install.sh    # если нужен голос"
say ""
say "Если install.sh всё-таки полез собирать образы (старая версия скриптов) —"
say "запустите вместо него из каталога единицы:  docker compose up -d"
say "Образы уже есть, docker их пересобирать не станет."
