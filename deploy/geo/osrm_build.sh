set -e
mkdir -p /opt/kontur/osrm && cd /opt/kontur/osrm
[ -f tajikistan.osm.pbf ] || curl -sL -o tajikistan.osm.pbf https://download.geofabrik.de/asia/tajikistan-latest.osm.pbf
IMG=ghcr.io/project-osrm/osrm-backend
docker pull $IMG
docker run --rm -v /opt/kontur/osrm:/data $IMG osrm-extract -p /opt/car.lua /data/tajikistan.osm.pbf
docker run --rm -v /opt/kontur/osrm:/data $IMG osrm-partition /data/tajikistan.osrm
docker run --rm -v /opt/kontur/osrm:/data $IMG osrm-customize /data/tajikistan.osrm
docker rm -f osrm-tj 2>/dev/null || true
docker run -d --name osrm-tj --restart unless-stopped -p 127.0.0.1:5001:5000 -v /opt/kontur/osrm:/data $IMG osrm-routed --algorithm mld /data/tajikistan.osrm
sleep 4
curl -s "http://127.0.0.1:5001/route/v1/driving/69.62,40.28;70.42,40.29?overview=false" | head -c 160 > /opt/kontur/osrm_test.txt 2>&1 || true
touch /opt/kontur/OSRM_DONE