#!/usr/bin/env bash
# Серверный bootstrap движка «Контур · Выезды» (вызывается deploy.sh / deploy.ps1
# после распаковки engine.tgz в /opt/kontur). Идемпотентен. ПД здесь нет — только код.
set -euo pipefail

cd /opt/kontur/engine

command -v python3 >/dev/null || { apt-get update -qq && apt-get install -y -qq python3 python3-venv; }
[ -d /opt/kontur/venv ] || python3 -m venv /opt/kontur/venv
/opt/kontur/venv/bin/pip -q install --upgrade pip >/dev/null
/opt/kontur/venv/bin/pip -q install -r requirements.txt

# systemd-юнит создаём только если его нет; OSRM_URL/VROOM_URL живут в drop-in osrm.conf,
# чтобы деплой кода не перетирал окружение.
UNIT=/etc/systemd/system/kontur-engine.service
if [ ! -f "$UNIT" ]; then
  cat >"$UNIT" <<'UNITEOF'
[Unit]
Description=Kontur Vyezdy Engine
After=network.target
[Service]
WorkingDirectory=/opt/kontur/engine
ExecStart=/opt/kontur/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8090 --workers 1
Restart=always
[Install]
WantedBy=multi-user.target
UNITEOF
  systemctl daemon-reload
  systemctl enable kontur-engine >/dev/null 2>&1 || true
fi

systemctl restart kontur-engine
sleep 2
curl -fsS http://127.0.0.1:8090/healthz && echo "  <- healthz OK"
