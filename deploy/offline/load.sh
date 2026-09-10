#!/usr/bin/env bash
# ЗАГРУЗКА ОФЛАЙН-ПАКЕТА на машине без интернета. Одна команда:
#
#   cd <каталог пакета с флешки> && ./load.sh
#
# Что делает по порядку: сверяет контрольные суммы, заносит образы в docker,
# раскладывает модель речи, ставит отметку «эта машина офлайн» — после неё
# install.sh и update.sh не пытаются ничего собирать. Ни pip, ни docker pull
# отсюда не вызываются: если что-то не доехало, скрипт скажет об этом словами,
# а не уйдёт молча в интернет, которого нет.
#
#   ./load.sh --deploy /opt/hard/deploy    # если каталог deploy/ не рядом
#   ./load.sh --no-verify                  # пропустить сверку сумм (не надо так)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY=""
VERIFY=1

while [ $# -gt 0 ]; do
  case "$1" in
    --deploy) DEPLOY="${2:-}"; shift 2 ;;
    --no-verify) VERIFY=0; shift ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) printf 'не понимаю аргумент «%s»\n' "$1" >&2; exit 1 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
ok()   { printf '  [ок] %s\n' "$*"; }
warn() { printf '  [!!] %s\n' "$*" >&2; }
die()  { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

cd "$DIR"
[ -d images ] || die "рядом с $0 нет каталога images/ — это не каталог офлайн-пакета"
command -v docker >/dev/null 2>&1 || die "не найден docker — поставьте Docker Engine и повторите"
docker info >/dev/null 2>&1 || die "docker недоступен: демон не запущен или нет прав (нужен root или группа docker)"

say "== Хард: загрузка офлайн-пакета =="
if [ -f МАНИФЕСТ.txt ]; then sed 's/^/  /' МАНИФЕСТ.txt | head -n 6; fi

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

# --- 3. Куда всё это ставить ---------------------------------------------------
# Пакет с образами лежит на флешке, а исходники (deploy/units/…) распакованы
# где-то на диске. Пытаемся найти их сами: чаще всего один из трёх вариантов.
if [ -z "$DEPLOY" ]; then
  for guess in "$DIR/.." "$PWD/deploy" "$PWD/../deploy" "$PWD"; do
    if [ -d "$guess/units" ]; then DEPLOY="$(cd "$guess" && pwd)"; break; fi
  done
fi

say ""
say "3/4 Модель речи"
if [ -f model/models.tar ]; then
  model_name="$(cat model/ИМЯ-МОДЕЛИ.txt 2>/dev/null || echo '?')"
  if [ -n "$DEPLOY" ]; then
    target="$DEPLOY/units/stt/models"
    mkdir -p "$target"
    tar -C "$target" -xf model/models.tar
    ok "модель «$model_name» разложена в $target"
    say "      в .env единицы речи должно стоять STT_MODEL=$model_name — иначе она полезет качать другую"
  else
    warn "не нашёл каталог deploy/ — модель не разложена"
    say "      сделайте это руками (замените путь на свой):"
    say "        mkdir -p /путь/к/deploy/units/stt/models"
    say "        tar -C /путь/к/deploy/units/stt/models -xf $DIR/model/models.tar"
  fi
else
  warn "модели в пакете нет — голос работать не будет (VOICE_BACKEND=off)"
fi

# --- 4. Отметка «здесь интернета нет» ------------------------------------------
say ""
say "4/4 Отметка офлайн-режима"
if [ -n "$DEPLOY" ]; then
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
else
  warn "каталог deploy/ не найден — отметку поставить некуда"
  say "      создайте пустой файл deploy/offline/OFFLINE руками, иначе установка полезет собирать образы"
fi

say ""
say "== Готово =="
say "Дальше — обычная установка, она увидит отметку и ничего собирать не станет:"
say "  cd ${DEPLOY:-/путь/к/deploy}/units/core && ./install.sh"
say "  cd ${DEPLOY:-/путь/к/deploy}/units/stt  && ./install.sh    # если нужен голос"
say ""
say "Если install.sh всё-таки полез собирать образы (старая версия скриптов) —"
say "запустите вместо него из каталога единицы:  docker compose up -d"
say "Образы уже есть, docker их пересобирать не станет."
