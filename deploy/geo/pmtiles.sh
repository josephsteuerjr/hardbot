set -e
mkdir -p /opt/kontur/tiles && cd /opt/kontur/tiles
if ! command -v pmtiles >/dev/null; then
  v=$(curl -s https://api.github.com/repos/protomaps/go-pmtiles/releases/latest | grep -oE '"tag_name": *"v[0-9.]+"' | grep -oE '[0-9.]+' | head -1)
  curl -fsSL -o /tmp/pm.tgz "https://github.com/protomaps/go-pmtiles/releases/download/v${v}/go-pmtiles_${v}_Linux_x86_64.tar.gz"
  tar -xzf /tmp/pm.tgz -C /usr/local/bin pmtiles && chmod +x /usr/local/bin/pmtiles && echo "pmtiles $v installed"
fi
B=''; for d in $(seq 0 8); do dt=$(date -u -d "-$d day" +%Y%m%d); if curl -sfI "https://build.protomaps.com/$dt.pmtiles" >/dev/null 2>&1; then B=$dt; break; fi; done
echo "protomaps_build=$B"
# Европ. РФ + Средняя Азия (Москва/СПб/Казань/Самара + Ташкент/Худжанд/Конибодом), z0..13
pmtiles extract "https://build.protomaps.com/$B.pmtiles" /opt/kontur/tiles/basemap.pmtiles --bbox=27,36,72,60 --maxzoom=13 2>&1 | tail -6
ls -lh /opt/kontur/tiles/basemap.pmtiles
touch /opt/kontur/PMTILES_DONE