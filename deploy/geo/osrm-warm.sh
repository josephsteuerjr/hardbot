#!/bin/sh
# Прогрев графов OSRM в page cache хоста.
# osrm-routed мапит .osrm через mmap и НЕ грузит их заранее: контейнер стартует мгновенно,
# но первый запрос по «холодному» участку графа платит major page fault'ами (~0.9 c).
# Один cat подтягивает весь граф в кэш (~3 c) — дальше запросы 0.01–0.03 c.
set -u
for d in /opt/kontur/osrm /opt/kontur/osrm-uz; do
  [ -d "$d" ] || continue
  # только рабочие структуры графа; .osm.pbf (исходник, 170 МБ) не нужен
  for f in "$d"/*.osrm.*; do
    [ -f "$f" ] && cat "$f" > /dev/null 2>&1
  done
done
exit 0
