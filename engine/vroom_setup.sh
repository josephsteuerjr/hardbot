#!/usr/bin/env bash
# Поднять НАСТОЯЩИЙ VROOM на сервере поверх работающего OSRM (задача #2).
# Запуск на сервере:  bash /opt/kontur/engine/vroom_setup.sh
#
# Безопасность: у сервера белый IP. vroom-express слушает 0.0.0.0:3000 ВНУТРИ контейнера,
# поэтому НЕ используем --network host. Вместо этого:
#   * общая docker-сеть kontur-net — VROOM ходит в OSRM по имени osrm-tj:5000;
#   * порт VROOM публикуем ТОЛЬКО на 127.0.0.1:3000 (движок-uvicorn на хосте достаёт его там).
# OSRM сохраняет свою публикацию 127.0.0.1:5001 для движка (OSRM_URL не меняется).
set -euo pipefail

IMAGE="vroomvrp/vroom-docker:v1.13.0"   # vroom 1.13 + vroom-express 0.11
CONF=/opt/kontur/vroom
mkdir -p "$CONF"

# config.yml монтируется в /conf; entrypoint копирует его в /vroom-express/config.yml.
cat > "$CONF/config.yml" <<'YML'
cliArgs:
  geometry: false      # геометрию маршрута добираем в движке через OSRM /route
  planmode: false
  threads: 2           # 1 vCPU — держим скромно
  explore: 5
  limit: '1mb'
  logdir: '/..'
  logsize: '100M'
  maxlocations: 1000
  maxvehicles: 200
  override: true
  path: ''
  port: 3000
  router: 'osrm'
  timeout: 300000
  baseurl: '/'
routingServers:
  osrm:
    car:
      host: 'osrm-tj'   # по имени контейнера в общей сети kontur-net
      port: '5000'      # внутренний порт OSRM (наружу он же на 127.0.0.1:5001)
YML
touch "$CONF/access.log"

# общая сеть + подключаем к ней уже работающий OSRM (идемпотентно)
docker network create kontur-net 2>/dev/null || true
docker network connect kontur-net osrm-tj 2>/dev/null || true

docker rm -f vroom 2>/dev/null || true
docker run -d --name vroom --restart unless-stopped \
  --network kontur-net -p 127.0.0.1:3000:3000 \
  -v "$CONF":/conf "$IMAGE"

echo "-> ждём старт vroom-express ..."
for i in $(seq 1 20); do
  if curl -fsS -m 3 -X POST http://127.0.0.1:3000 \
       -H 'Content-Type: application/json' \
       -d '{"vehicles":[{"id":1,"start":[69.62,40.28],"end":[69.62,40.28]}],"jobs":[{"id":1,"location":[70.43,40.29]},{"id":2,"location":[69.0,39.9]}]}' \
       >/tmp/vroom_test.json 2>/dev/null; then
    echo "-> VROOM ответил:"; head -c 400 /tmp/vroom_test.json; echo
    exit 0
  fi
  sleep 1
done
echo "!! VROOM не ответил за 20с — логи:"; docker logs --tail 40 vroom
exit 1
