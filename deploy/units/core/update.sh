#!/usr/bin/env bash
# ОБНОВЛЕНИЕ ЯДРА и применение правок .env:
#
#   cd deploy/units/core && ./update.sh
#
# Порядок: копия базы → пересборка образа из соседнего каталога server/ →
# перезапуск → ожидание готовности. Данные в data/ не трогаются.
#
# Этим же скриптом применяется ЛЮБАЯ правка .env: вписали BOT_TOKEN, поменяли
# APP_PORT, включили голос — ./update.sh, и настройки в деле.
source "$(dirname "$0")/../../_lib.sh"
source "$DEPLOY_DIR/_actions.sh"

banner "обновление"
check_docker
require_env
require_sources server
check_profiles

step "1/4 Резервная копия базы"
# Копия — первым делом и всегда: обновление может принести новую схему базы,
# и откатываться будет уже не с чего. --db-only снимает только базу, секунды.
if [ -f data/hardbot.db ]; then
  ./backup.sh --db-only
else
  say "  базы ещё нет (приложение ни разу не запускалось) — копировать нечего"
fi

step "2/4 Сборка образа"
build_images

step "3/4 Перезапуск"
start_unit

step "4/4 Проверка"
if ! wait_unit; then
  say ""
  die "приложение не поднялось после обновления.
Что сделать по порядку:
  1) причина почти всегда в строках журнала выше (частое — опечатка в .env);
  2) вернуть прежнюю базу: ./restore.sh backups/<последняя копия> --yes
     (список копий: ls backups/);
  3) полная картина: $DEPLOY_DIR/doctor.sh"
fi

port="$(app_port)"
if command -v curl >/dev/null 2>&1; then
  # Тот же разбор, что и в install.sh: приговор выносится по коду ответа.
  # Ненулевой код возврата curl сам по себе ничего не значит — он бывает и при
  # неудачной записи тела («curl: (23) ...») уже после ответа 200.
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:${port}/" 2>/dev/null)" || true
  case "${code:-000}" in
    000)
      warn "контейнер здоров, но http://127.0.0.1:${port}/ не отвечает — проверьте APP_PORT в .env" ;;
    2??|3??)
      ok "http://127.0.0.1:${port}/ отвечает (HTTP ${code})" ;;
    *)
      warn "http://127.0.0.1:${port}/ отвечает, но кодом ${code} — это не похоже на рабочее приложение"
      warn "последние строки журнала: docker compose logs --tail=50" ;;
  esac
fi

# Старые слои нашего образа после пересборки висят как <none>. Чистим ТОЛЬКО
# свои (по метке): на сервере рядом живут чужие проекты, общий prune их снесёт.
docker image prune -f --filter "label=ru.hard.service" >/dev/null 2>&1 || true

say ""
say "==== Ядро обновлено ===="
compose ps | sed 's/^/  /'
