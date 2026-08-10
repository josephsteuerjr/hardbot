set -e
mkdir -p /opt/kontur/vroom /var/log/vroom
cat > /opt/kontur/vroom/config.yml <<YML
cliArgs:
  geometry: false
  planmode: false
  threads: 2
  explore: 5
  limit: '1mb'
  logdir: '/var/log/vroom'
  maxlocations: 1000
  maxvehicles: 200
  override: true
  path: ''
  port: 3000
  router: 'osrm'
  timeout: 300000
routingServers:
  osrm:
    car:
      host: '127.0.0.1'
      port: '5001'
YML
docker rm -f vroom 2>/dev/null || true
docker pull vroomvrp/vroom-docker:latest
docker run -d --name vroom --restart unless-stopped --network host -v /opt/kontur/vroom:/conf vroomvrp/vroom-docker:latest
sleep 8
curl -s -X POST http://127.0.0.1:3000 -H 'Content-Type: application/json' -d '{"vehicles":[{"id":1,"start":[69.62,40.28],"end":[69.62,40.28]},{"id":2,"start":[69.62,40.28],"end":[69.62,40.28]}],"jobs":[{"id":1,"location":[70.42,40.29]},{"id":2,"location":[69.0,39.9]},{"id":3,"location":[69.62,40.30]}]}' > /opt/kontur/vroom_test.json 2>&1 || true
touch /opt/kontur/VROOM_DONE