#!/usr/bin/env bash
# ЗАПАСНОЙ ПУТЬ: собрать образы на закрытой машине из колёс pip.
#
#   cd <каталог пакета> && ./build-from-wheels.sh --root /opt/hard
#
# Основной путь — готовые образы (./load.sh), и трогать этот скрипт нужно только
# если ИТ-служба принципиально не берёт чужие собранные образы и собирает всё у
# себя. Тогда в пакете должны быть колёса (bundle.sh --with-wheels): в нём лежат
# и сами пакеты, и базовый образ python, потому что за ним сборка тоже ходит в
# интернет.
#
# Почему это именно запасной путь: здесь всё зависит от совпадения платформы
# (колёса под linux/amd64 не встанут на arm), версии python в базовом образе и
# наличия компилятора для пакетов без колёс. Ни одно из этих совпадений не видно
# заранее — они выясняются посреди сборки.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) printf 'не понимаю аргумент «%s»\n' "$1" >&2; exit 1 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
ok()   { printf '  [ок] %s\n' "$*"; }
warn() { printf '  [!!] %s\n' "$*" >&2; }
die()  { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

# Корень исходников: там, где лежат каталоги server/ и stt/.
if [ -z "$ROOT" ]; then
  for guess in "$DIR/../.." "$PWD"; do
    if [ -d "$guess/server" ] && [ -d "$guess/stt" ]; then ROOT="$(cd "$guess" && pwd)"; break; fi
  done
fi
[ -n "$ROOT" ] || die "не нашёл исходники — укажите путь: --root /путь/к/распакованным/исходникам"
[ -d "$ROOT/server" ] || die "в $ROOT нет каталога server/"
[ -d "$DIR/wheels" ] || die "в пакете нет каталога wheels/ — он собирается командой ./bundle.sh --with-wheels"

command -v docker >/dev/null 2>&1 || die "не найден docker"
docker info >/dev/null 2>&1 || die "docker недоступен: демон не запущен или нет прав"

base="$(sed -n 's/^FROM \([^ ]*\).*/\1/p' "$ROOT/server/Dockerfile" | head -n 1)"
docker image inspect "$base" >/dev/null 2>&1 \
  || die "нет базового образа $base. Он лежит в этом же пакете: сначала ./load.sh"
ok "базовый образ $base на месте"

# Сборка одной части. Колёса кладутся в <часть>/wheelhouse — Dockerfile забирает
# их шаблоном «wheelhous[e]», а pip получает --no-index и в сеть не ходит вообще.
build_part() {   # build_part server hard-app
  local part="$1" image="$2"
  local dockerfile="$ROOT/$part/Dockerfile"
  if [ ! -d "$DIR/wheels/$part" ]; then
    warn "колёс для $part/ в пакете нет — пропускаю"
    return 0
  fi
  if ! grep -q 'wheelhous\[e\]' "$dockerfile"; then
    warn "$part/Dockerfile не умеет собираться из колёс — пропускаю"
    say  "      нужна строка COPY requirements.txt wheelhous[e]/ /build/ и"
    say  "      RUN pip install --no-cache-dir \${PIP_FLAGS} -r /build/requirements.txt"
    say  "      (как сделано в server/Dockerfile). Пока этого нет — берите готовый образ."
    return 0
  fi
  say ""
  say "Сборка $image из колёс"
  rm -rf "$ROOT/$part/wheelhouse"
  mkdir -p "$ROOT/$part/wheelhouse"
  cp "$DIR/wheels/$part"/*.whl "$DIR/wheels/$part"/*.tar.gz "$ROOT/$part/wheelhouse/" 2>/dev/null || true
  local count; count="$(ls -1 "$ROOT/$part/wheelhouse" | wc -l | tr -d ' ')"
  [ "$count" != "0" ] || { rm -rf "$ROOT/$part/wheelhouse"; die "в wheels/$part нет ни одного пакета"; }
  # --network none: у сборки нет сети ВООБЩЕ. Это не перестраховка, а способ
  # получить честный ответ здесь и сейчас: если чего-то не хватает, сборка
  # упадёт на этой машине с понятной строкой, а не «почти соберётся» за счёт
  # случайно доступного зеркала и не развалится потом в закрытом контуре.
  if docker build --network none --build-arg PIP_FLAGS="--no-index --find-links=/build" \
       -t "$image:latest" "$ROOT/$part"; then
    rm -rf "$ROOT/$part/wheelhouse"
    ok "$image:latest собран из $count пакетов, без единого обращения в сеть"
    say "      образ примерно на размер колёс больше обычного: их копия остаётся"
    say "      внутри. На работу это не влияет, дисковое место — единственная плата"
  else
    rm -rf "$ROOT/$part/wheelhouse"
    die "сборка $image не прошла. Смотрите последние строки выше: «No matching distribution»
       означает, что нужного колеса нет в пакете — обычно это другая платформа
       (собирали на amd64, ставите на arm) или другая версия python в базовом образе"
  fi
}

build_part server hard-app
build_part stt hard-stt

say ""
say "== Готово =="
docker images --filter "label=ru.hard.service" --format '  {{.Repository}}:{{.Tag}}  {{.Size}}'
say ""
say "Дальше — обычная установка: cd $ROOT/deploy/units/core && ./install.sh"
