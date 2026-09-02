#!/usr/bin/env bash
# Деплой движка «Хард» на сервер-витрину — ЧИСТЫЙ ssh/scp по ключу.
# Никакого Posh-SSH (он ронял PowerShell). Несёт ТОЛЬКО КОД — реальных ПД на сервере нет.
#
#   bash engine/deploy.sh
#
# Хост/юзер — из ../_infra/secrets.env (SRV2_HOST / SRV2_USER).
# Ключ — ~/.ssh/kontur_deploy (переопределяется $KONTUR_KEY).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

# --- креды/конфиг ---
set -a; . "$ROOT/_infra/secrets.env"; set +a
RHOST="${SRV2_HOST:?SRV2_HOST missing in secrets.env}"
RUSER="${SRV2_USER:-root}"
KEY="${KONTUR_KEY:-$HOME/.ssh/kontur_deploy}"
[ -f "$KEY" ] || { echo "нет ключа $KEY — сгенерь: ssh-keygen -t ed25519 -f $KEY"; exit 1; }

SSHOPTS=(-i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o LogLevel=ERROR \
         -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20)
TGZ="${TMPDIR:-/tmp}/kontur-engine.$$.tgz"
trap 'rm -f "$TGZ"' EXIT

echo "-> упаковка engine/ (только код) ..."
tar -czf "$TGZ" -C "$ROOT" \
  --exclude='*__pycache__*' --exclude='*.pyc' \
  --exclude='engine/.venv' --exclude='*.tgz' \
  engine

echo "-> заливка на $RUSER@$RHOST:/opt/kontur ..."
ssh "${SSHOPTS[@]}" "$RUSER@$RHOST" "mkdir -p /opt/kontur"
scp "${SSHOPTS[@]}" "$TGZ" "$RUSER@$RHOST:/opt/kontur/engine.tgz"

echo "-> распаковка + зависимости + рестарт ..."
ssh "${SSHOPTS[@]}" "$RUSER@$RHOST" \
  "cd /opt/kontur && tar xzf engine.tgz && rm -f engine.tgz && bash engine/_remote_setup.sh"

echo "-> внешняя проверка ..."
curl -fsS -m 20 "https://kontur.${RHOST}.nip.io/healthz" && echo
echo "OK: деплой завершён."
