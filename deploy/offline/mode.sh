#!/usr/bin/env bash
# ОФЛАЙН-РЕЖИМ ДЛЯ СКРИПТОВ УСТАНОВКИ. Сам по себе не запускается, его
# подключают: source "$DEPLOY_DIR/offline/mode.sh"
#
# Зачем. install.sh и update.sh вызывают `compose build`, а сборка ходит в
# интернет: за базовым образом и за пакетами pip. В закрытом контуре это не
# ошибка настройки, а тупик: собрать там нельзя в принципе. Образы туда
# привозят готовыми (deploy/offline/load.sh), и скриптам нужно всего одно —
# в этом случае не собирать.
#
# Признак офлайн-режима, любой из двух:
#   • существует файл deploy/offline/OFFLINE (его ставит load.sh);
#   • переменная окружения HARD_OFFLINE=1 (разовый запуск: HARD_OFFLINE=1 ./update.sh).
#
# Как этим пользоваться в install.sh / update.sh — вместо строки
#     compose build $services
# написать
#     compose_build_unless_offline $services
# Остальное скрипт делает сам, включая проверку, что нужные образы вообще есть.

_HARD_OFFLINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# hard_offline — истина, если собирать нельзя и не нужно.
hard_offline() {
  case "${HARD_OFFLINE:-}" in
    1|yes|y|true|да) return 0 ;;
  esac
  [ -f "$_HARD_OFFLINE_DIR/OFFLINE" ]
}

# hard_images_present — все ли образы этой единицы уже загружены.
# Список берётся у самого compose, поэтому он всегда совпадает с тем, что
# единица собирается запускать, — даже если сервисы поменяются.
hard_images_present() {
  local missing=""
  local images; images="$(docker compose config --images 2>/dev/null || true)"
  [ -n "$images" ] || return 0        # не смогли спросить — не мешаем работать
  local image
  for image in $images; do
    docker image inspect "$image" >/dev/null 2>&1 || missing="$missing $image"
  done
  [ -z "$missing" ] || {
    printf 'ОШИБКА: на машине нет образов:%s\n' "$missing" >&2
    printf '  Машина помечена как офлайн, поэтому собирать их нельзя — их привозят\n' >&2
    printf '  готовыми. Возьмите офлайн-пакет и выполните на нём ./load.sh, потом\n' >&2
    printf '  повторите установку. Если интернет здесь всё-таки есть — удалите файл\n' >&2
    printf '  %s и запустите заново.\n' "$_HARD_OFFLINE_DIR/OFFLINE" >&2
    return 1
  }
}

# compose_build_unless_offline СЕРВИСЫ… — обычная сборка, но не в закрытом контуре.
compose_build_unless_offline() {
  if hard_offline; then
    # Сначала проверяем, потом рапортуем: «сборка пропущена», а следом ошибка о
    # том, что образов нет, читается как издевательство.
    hard_images_present || exit 1
    printf '  [ок] офлайн-режим: образы привезены готовыми, сборка пропущена\n'
    return 0
  fi
  docker compose build "$@"
}
