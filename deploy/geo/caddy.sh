set -e
DOMAIN=kontur.166.88.185.90.nip.io
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -qi active; then ufw allow 80/tcp >/dev/null 2>&1; ufw allow 443/tcp >/dev/null 2>&1; fi
if ! command -v caddy >/dev/null; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gpg >/dev/null 2>&1
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y >/dev/null 2>&1
  apt-get install -y caddy >/dev/null 2>&1
fi
printf '%s {\n    reverse_proxy 127.0.0.1:8090\n}\n' "$DOMAIN" > /etc/caddy/Caddyfile
systemctl enable caddy >/dev/null 2>&1
systemctl restart caddy
sleep 10
touch /opt/kontur/CADDY_DONE