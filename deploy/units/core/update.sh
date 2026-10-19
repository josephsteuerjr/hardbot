#!/usr/bin/env bash
# ОБНОВЛЕНИЕ ЯДРА и применение правок .env:
#
#   cd deploy/units/core && ./update.sh
#
# До пересборки сохраняем полный снимок данных, прежний образ по его Docker ID
# и закрытую квитанцию конфигурации. Самостоятельного отката данных здесь нет:
# после запуска новая версия могла записать факты, которые нельзя выкинуть.
source "$(dirname "$0")/../../_lib.sh"
source "$DEPLOY_DIR/_actions.sh"
umask 077

banner "обновление"
check_docker
require_env
require_sources server
check_profiles

# install.sh отвечает за первый старт. Без прежнего живого контейнера нельзя
# обещать, что rollback-tag действительно соответствует последней рабочей версии.
old_container="$(container_id app)"
[ -n "$old_container" ] && [ "$(health_of app)" = healthy ] \
  || die "нет healthy app с известным прежним образом; для первичной установки используйте ./install.sh, а для аварийной ситуации сначала ./doctor.sh. Обновление не начато."
old_image="$(docker inspect -f '{{.Image}}' "$old_container" 2>/dev/null)" \
  || die "не удалось получить image ID работающего app; обновление не начато"
[[ "$old_image" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || die "неожиданный image ID работающего app; обновление не начато"
docker image inspect "$old_image" >/dev/null 2>&1 \
  || die "прежний образ $old_image не найден; обновление не начато"

step "1/4 Полная резервная копия"
# --db-only не является полной границей восстановления: чеки, фото и audit-spool
# составляют единый набор фактов. Если backup отказывает, до сборки не доходим.
[ -f data/hardbot.db ] || die "нет рабочей data/hardbot.db; обновление не начато"
backup_log="$(mktemp -t hard-core-update-backup-XXXXXX)"
if ./backup.sh >"$backup_log" 2>&1; then
  cat "$backup_log"
else
  cat "$backup_log" >&2
  rm -f "$backup_log"
  die "полная копия не завершилась; прежний контейнер и образ не менялись"
fi
backup_dir="$(sed -n 's/^Готово: \(backups\/[^[:space:]]*\)$/\1/p' "$backup_log" | tail -n 1)"
rm -f "$backup_log"
[[ "$backup_dir" =~ ^backups/[0-9]{8}T[0-9]{6}([-_][A-Za-z0-9]+)?$ ]] \
  && [ -f "$backup_dir/SHA256SUMS" ] && [ ! -L "$backup_dir" ] \
  || die "backup не выдал проверяемый путь полной копии; обновление не начато"
# Backup может длиться минуты. Если за это время другой оператор заменил app,
# исходный image ID уже не описывает состояние непосредственно перед сборкой.
[ "$(container_id app)" = "$old_container" ] \
  && [ "$(health_of app)" = healthy ] \
  && [ "$(docker inspect -f '{{.Image}}' "$old_container" 2>/dev/null)" = "$old_image" ] \
  || die "app изменился во время копирования; копия сохранена в $backup_dir, пересборка не начата"

step "2/4 Сохранение прежнего образа и конфигурации, сборка"
receipt="backups/update-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir "$receipt" || die "не удалось создать отдельную квитанцию $receipt; обновление не начато"
# Штатная ротация удаляет backups/<stamp> после следующих копий. Переносим
# именно готовый снимок в отдельную закрытую квитанцию (rename на том же диске)
# ДО пересборки: дата из маркера была нужна, чтобы выбрать не чужой архив.
mv "$backup_dir" "$receipt/pre-update-data" \
  || die "не удалось закрепить полную копию вне ротации; обновление не начато"
backup_dir="$receipt/pre-update-data"
rollback_tag="hard-app:rollback-$(date -u +%Y%m%dt%H%M%Sz)-$$"
# Сначала частная квитанция: docker inspect содержит секреты среды. candidate.env
# — уже НОВАЯ .env, а не реконструкция прежней .env работающего контейнера.
cp .env "$receipt/candidate.env"
compose config > "$receipt/candidate-compose.yml" \
  || die "не удалось сохранить эффективную Compose-конфигурацию; обновление не начато"
docker inspect "$old_container" > "$receipt/prior-container.json" \
  || die "не удалось сохранить прежнюю конфигурацию контейнера; обновление не начато"
printf '%s\n' "$old_image" > "$receipt/prior-image-id"
printf '%s\n' "$rollback_tag" > "$receipt/prior-image-tag"
printf '%s\n' "$backup_dir" > "$receipt/pre-update-backup"
docker image tag "$old_image" "$rollback_tag" \
  || die "не удалось закрепить прежний образ; обновление не начато"
[ "$(docker image inspect -f '{{.Id}}' "$rollback_tag" 2>/dev/null)" = "$old_image" ] \
  || die "закреплённый образ не совпал с прежним ID; обновление не начато"
cat > "$receipt/RECOVERY.txt" <<EOF
ПРИВАТНАЯ КВИТАНЦИЯ ОБНОВЛЕНИЯ (содержит конфигурацию/секреты): $receipt
Полный согласованный backup: $backup_dir
Прежний image ID: $old_image
Закреплённый тег: $rollback_tag
Прежний контейнер: $old_container

При сбое НЕ восстанавливайте одну БД поверх нового WAL и НЕ перезапускайте
автоматически: новая версия могла уже принять новые рабочие факты. Сохраните
текущее состояние, определите по журналу, какие записи после backup нужно
перенести/согласовать вручную; без этого возврат к старым данным теряет их.

Порядок ручного возврата после согласования потери/переноса новых фактов:
  1. В этом каталоге проверьте prior-image-id и prior-container.json (.Config.Env,
     .HostConfig.PortBindings, .Mounts), сравните с candidate.env и
     candidate-compose.yml. candidate.env содержит ВХОДЯЩУЮ настройку, а не
     прежний .env; верните прежние значения и исходные Compose-файлы из
     доверенного снимка, если они менялись. Без доказанного старого конфига
     остановитесь: образ сам по себе НЕ восстанавливает конфигурацию.
  2. cd '$UNIT_DIR'; docker compose stop app
  3. docker image inspect '$rollback_tag' и сравнить .Id с '$old_image'.
     Только после сверки: docker image tag '$rollback_tag' hard-app
  4. docker compose create --no-build --force-recreate --no-deps app
     (создаётся ОСТАНОВЛЕННЫЙ контейнер из прежнего образа).
  5. ./restore.sh '$backup_dir' --yes
     (штатный restore сохраняет текущее DB+WAL+активы в aside; перед
     применением проверяет SHA256SUMS; стартует уже старый контейнер).
  6. ./doctor.sh; проверьте рабочие пути и внешний HTTPS с настоящего клиента.
     Локальный http://127.0.0.1 не доказывает внешний TLS/маршрут.

НЕ запускайте docker image prune до отдельного решения об удалении этого тега.
EOF
# Отказ сборки/build_images выходит через die. Даже при отказе квитанция и
# закреплённый образ остаются, а сообщение не советует опасный DB-only restore.
update_completed=0
on_update_exit() {
  if [ "$update_completed" -ne 1 ]; then
    warn "обновление не завершено; прежний образ сохранён: $rollback_tag ($old_image)"
    warn "полная копия: $backup_dir; ручной порядок и прежняя конфигурация: $receipt/RECOVERY.txt"
    warn "не восстанавливайте БД автоматически: после запуска могли появиться новые факты"
  fi
}
trap on_update_exit EXIT
build_images

step "3/4 Перезапуск"
start_unit

step "4/4 Проверка"
if ! wait_unit; then
  die "приложение не поднялось после обновления. Сохраните журналы: docker compose logs --tail=100 app; затем следуйте $receipt/RECOVERY.txt (образ и полный backup закреплены, автоматического отката нет)."
fi

port="$(app_port)"
if command -v curl >/dev/null 2>&1; then
  # Локальный HTTP — дополнительный пробник, НЕ проверка внешнего HTTPS.
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:${port}/" 2>/dev/null)" || true
  case "${code:-000}" in
    000)
      warn "контейнер здоров, но http://127.0.0.1:${port}/ не отвечает — проверьте APP_PORT в .env" ;;
    2??|3??)
      ok "локальный http://127.0.0.1:${port}/ отвечает (HTTP ${code}); внешний HTTPS НЕ проверен" ;;
    *)
      warn "локальный http://127.0.0.1:${port}/ отвечает кодом ${code}; смотрите docker compose logs --tail=50" ;;
  esac
fi

# Не prune: старый образ нужен для восстановления и после успешного localhost
# пробника. Оператор снимает retained tag только после отдельной приёмки.
update_completed=1
say ""
say "==== Ядро обновлено локально; внешний HTTPS требует отдельной проверки ===="
say "Откат сохранён: $receipt/RECOVERY.txt"
compose ps | sed 's/^/  /'
