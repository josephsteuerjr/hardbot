#!/usr/bin/env bash
# ДИАГНОСТИКА ВСЕЙ СИСТЕМЫ — первое, что стоит запустить, когда «что-то не
# работает»:
#
#   cd deploy && ./doctor.sh
#
# Скрипт ничего не меняет. Он обходит все три единицы (core, stt, gps),
# запускает диагностику каждой установленной и в конце говорит, с чего начать.
# Диагностику одной единицы можно запустить и отдельно:
#   cd deploy/units/core && ./doctor.sh
#
# Не установленная единица — это НЕ поломка: система задумана так, что речь и
# маршруты могут отсутствовать, а ядро при этом работает полностью.
source "$(dirname "$0")/_lib.sh"
source "$DEPLOY_DIR/_actions.sh"

say "==================================================================="
say " Хард · диагностика всей системы · $(date '+%Y-%m-%d %H:%M')"
say "==================================================================="

# --- Докер --------------------------------------------------------------------
# Отдельно и первым делом: если сломан он, все остальные проверки будут врать.
if ! command -v docker >/dev/null 2>&1; then
  die "на сервере нет докера — без него не работает ничего.
Что сделать: установите Docker Engine по инструкции https://docs.docker.com/engine/install/ и повторите ./doctor.sh"
fi
if ! docker info >/dev/null 2>&1; then
  die "докер установлен, но не отвечает.
Что сделать — по порядку:
  1) запустить: systemctl start docker  (и systemctl enable docker, чтобы поднимался сам);
  2) если пишет «permission denied» — запустите от root: sudo ./doctor.sh"
fi
if ! docker compose version >/dev/null 2>&1; then
  die "нет Docker Compose v2 (нужна команда «docker compose», старая «docker-compose» не подходит).
Что сделать: apt install docker-compose-plugin (или поставьте свежий Docker Engine, плагин входит в него)."
fi
ok "докер и compose v2 работают"

# --- Общее хозяйство ----------------------------------------------------------
step "Общие сеть и том (по ним единицы находят друг друга)"
if docker network inspect "$SHARED_NET" >/dev/null 2>&1; then
  ok "сеть $SHARED_NET есть"
else
  warn "нет общей сети $SHARED_NET — ядро не увидит маршрутизаторы"
  hint "её создаёт install.sh любой из единиц; вручную: docker network create $SHARED_NET"
fi
if docker volume inspect "$SHARED_STT_VOLUME" >/dev/null 2>&1; then
  ok "том $SHARED_STT_VOLUME есть"
else
  warn "нет общего тома $SHARED_STT_VOLUME — ядро и служба речи не свяжутся"
  hint "его создаёт install.sh любой из единиц; вручную: docker volume create $SHARED_STT_VOLUME"
fi

step "Что сейчас запущено"
for u in core stt gps; do
  running="$(project_services_running "$(project_of "$u")" | tr '\n' ' ')"
  if [ -n "$running" ]; then
    printf '  %-5s работает: %s\n' "$u" "$running"
  elif [ -f "$DEPLOY_DIR/units/$u/.env" ]; then
    printf '  %-5s установлена, но НЕ работает\n' "$u"
  else
    printf '  %-5s не установлена\n' "$u"
  fi
done

# --- По единицам --------------------------------------------------------------
installed=0
troubled=""
for u in core stt gps; do
  say ""
  say "-------------------------------------------------------------------"
  if [ ! -f "$DEPLOY_DIR/units/$u/.env" ]; then
    say "==== единица «$u» — не установлена ===="
    say "  Если она не нужна — так и должно быть."
    say "  Поставить: cd $DEPLOY_DIR/units/$u && ./install.sh"
    continue
  fi
  installed=$((installed + 1))
  if ! bash "$DEPLOY_DIR/units/$u/doctor.sh"; then
    troubled="$troubled $u"
  fi
done

# --- Итог ---------------------------------------------------------------------
say ""
say "==================================================================="
if [ "$installed" -eq 0 ]; then
  say " Не установлена ни одна единица."
  say " Начните с ядра: cd $DEPLOY_DIR/units/core && ./install.sh"
elif [ -z "$troubled" ]; then
  say " Замечаний нет: всё установленное работает."
else
  say " Замечания есть в единицах:$troubled"
  say ""
  say " С чего начинать: с ядра. Речь и маршруты — надстройки, без них система"
  say " работает, а без ядра не работает ничего."
  say " Каждое замечание выше помечено [!!], и под ним строкой → написано,"
  say " что сделать. Если ничего не понятно — пришлите разработчику весь вывод"
  say " этой команды целиком, его достаточно. Сохранить его в файл:"
  say "     ./doctor.sh > отчёт.txt 2>&1"
  say " (2>&1 обязательно: замечания печатаются в поток ошибок и без него"
  say "  в файл не попадут)."
fi
say "==================================================================="
[ -z "$troubled" ] || exit 1
