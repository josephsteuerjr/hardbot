set -e
cd /opt/kontur
tar -xzf kontur_engine.tgz && rm -f kontur_engine.tgz
cd /opt/kontur/engine
export DEBIAN_FRONTEND=noninteractive
python3 -m venv /opt/kontur/venv 2>/dev/null || { apt-get update -y >/dev/null 2>&1; apt-get install -y python3-venv >/dev/null 2>&1; python3 -m venv /opt/kontur/venv; }
/opt/kontur/venv/bin/pip install --upgrade pip >/dev/null
/opt/kontur/venv/bin/pip install -r requirements.txt
cat >/etc/systemd/system/kontur-engine.service <<UNIT
[Unit]
Description=Kontur Vyezdy Engine
After=network.target
[Service]
WorkingDirectory=/opt/kontur/engine
ExecStart=/opt/kontur/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8090 --workers 1
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now kontur-engine
sleep 3
curl -s http://127.0.0.1:8090/healthz > /opt/kontur/health.txt 2>&1 || true
touch /opt/kontur/DONE