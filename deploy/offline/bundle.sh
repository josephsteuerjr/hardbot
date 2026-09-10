#!/usr/bin/env bash
# СБОРКА ОФЛАЙН-ПАКЕТА. Запускается ОДИН РАЗ на машине, где есть интернет.
# Результат — каталог, который целиком копируется на флешку и увозится в
# закрытый контур. Там ничего не собирается и ничего не качается: только
# ./load.sh, и образы на месте.
#
#   cd deploy/offline
#   ./bundle.sh                          # ядро + речь, модель small
#   ./bundle.sh --with gps --with proxy  # добавить маршруты и Caddy
#   ./bundle.sh --model large-v3-turbo   # другая модель речи
#   ./bundle.sh --no-model               # без модели (голос будет выключен)
#   ./bundle.sh --with-wheels            # + колёса pip (запасной путь, см. README)
#   ./bundle.sh --out /media/flash       # сразу на флешку
#
# ПОЧЕМУ ОБРАЗЫ, А НЕ ИСХОДНИКИ С КОЛЁСАМИ. Собранный образ — это один файл и
# одна команда на той стороне. Сборка из колёс — это pip, компиляторы, версия
# базового образа, совпадение платформы и десяток мест, где всё это может
# разойтись; каждое такое место — вечер чужого инженера. Колёса в пакет тоже
# кладутся (--with-wheels), но как запасной путь для тех, кто хочет собирать сам.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  [ок] %s\n' "$*"; }
warn() { printf '  [!!] %s\n' "$*" >&2; }
die()  { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

# --- разбор аргументов ---------------------------------------------------------
VERSION=""
OUT_BASE="$HERE/out"
MODEL="small"
WITH_GPS=0
WITH_PROXY=0
WITH_WHEELS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:-}"; shift 2 ;;
    --out)     OUT_BASE="${2:-}"; shift 2 ;;
    --model)   MODEL="${2:-}"; shift 2 ;;
    --no-model) MODEL=""; shift ;;
    --with-wheels) WITH_WHEELS=1; shift ;;
    --with)
      case "${2:-}" in
        gps)   WITH_GPS=1 ;;
        proxy) WITH_PROXY=1 ;;
        *) die "--with принимает gps или proxy, а не «${2:-}»" ;;
      esac
      shift 2 ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "не понимаю аргумент «$1» (см. ./bundle.sh --help)" ;;
  esac
done

command -v docker >/dev/null 2>&1 || die "не найден docker"
docker info >/dev/null 2>&1 || die "docker недоступен: демон не запущен или нет прав"
docker compose version >/dev/null 2>&1 || die "нужен Docker Compose v2 (команда «docker compose»)"

if [ -z "$VERSION" ]; then
  VERSION="$(git -C "$ROOT" describe --tags --always --dirty 2>/dev/null || true)"
  VERSION="${VERSION#v}"
  [ -n "$VERSION" ] || VERSION="$(date +%Y%m%d)"
fi
case "$VERSION" in *[!A-Za-z0-9._-]*) die "недопустимая версия «$VERSION»: буквы, цифры, точка, дефис" ;; esac

DAY="$(date +%Y-%m-%d)"
BUNDLE="$OUT_BASE/hard-offline-$VERSION-$DAY"
say "== Хард: сборка офлайн-пакета =="
say "  версия: $VERSION"
say "  пакет:  $BUNDLE"
mkdir -p "$BUNDLE/images"
: > "$BUNDLE/.building"

# --- 1. Наши образы ------------------------------------------------------------
say ""
say "1/5 Сборка наших образов (несколько минут при первом запуске)"
docker build -t "hard-app:latest" -t "hard-app:$VERSION" "$ROOT/server"
ok "hard-app:latest и hard-app:$VERSION"
docker build -t "hard-stt:latest" -t "hard-stt:$VERSION" "$ROOT/stt"
ok "hard-stt:latest и hard-stt:$VERSION"

# --- 2. Чужие образы -----------------------------------------------------------
# Тянутся из интернета КАК ЕСТЬ. Их версии — часть пакета: точный слепок
# записывается в МАНИФЕСТ.txt, чтобы через год было видно, что именно увезли.
# ВНИМАНИЕ на форму записи: везде if, а не «условие && действие». При set -e
# несработавшее «&&» само по себе считается ошибкой и обрывает скрипт — на этом
# сгорело больше установок, чем на любой опечатке.
THIRD=()
if [ "$WITH_PROXY" = 1 ]; then THIRD+=("caddy:2"); fi
if [ "$WITH_GPS" = 1 ]; then
  THIRD+=("ghcr.io/project-osrm/osrm-backend:latest" "vroomvrp/vroom-docker:v1.13.0")
fi
if [ "$WITH_WHEELS" = 1 ]; then
  # Для сборки из колёс на закрытой машине нужен и базовый образ Python:
  # без него docker build пойдёт за ним в интернет и встанет.
  THIRD+=("$(sed -n 's/^FROM \([^ ]*\).*/\1/p' "$ROOT/server/Dockerfile" | head -n 1)")
fi
if [ ${#THIRD[@]} -gt 0 ]; then
  say ""
  say "2/5 Загрузка чужих образов: ${THIRD[*]}"
  for ref in "${THIRD[@]}"; do
    docker pull "$ref" >/dev/null || die "не скачивается образ $ref"
    ok "$ref"
  done
else
  say ""
  say "2/5 Чужих образов не требуется (ни --with gps, ни --with proxy, ни --with-wheels)"
fi

# --- 3. Выгрузка в файлы -------------------------------------------------------
say ""
say "3/5 Выгрузка образов в файлы (docker save)"
# Имя файла — из ссылки на образ: «/» и «:» в именах файлов не годятся.
file_for() { printf '%s' "$1" | tr '/:' '__'; }

save_image() {   # save_image ССЫЛКА [ДОП.ССЫЛКА…] — все ссылки в один файл
  local primary="$1" file
  file="images/$(file_for "$primary").tar"
  docker save -o "$BUNDLE/$file" "$@"
  ok "$(printf '%-46s %s' "$*" "$(du -h "$BUNDLE/$file" | cut -f1)")"
}
save_image "hard-app:latest" "hard-app:$VERSION"
save_image "hard-stt:latest" "hard-stt:$VERSION"
for ref in ${THIRD[@]+"${THIRD[@]}"}; do save_image "$ref"; done

# --- 4. Модель речи ------------------------------------------------------------
# Без неё контейнер речи при первом старте пойдёт на huggingface.co и в закрытом
# контуре будет вечно перезапускаться. Качаем тем же кодом, которым её грузит
# служба, — значит на месте она точно найдётся и второй раз качаться не будет.
say ""
if [ -n "$MODEL" ]; then
  say "4/5 Модель речи «$MODEL» (качается один раз, small ≈ 460 МБ, large-v3-turbo ≈ 1,5 ГБ)"
  MODEL_DIR="$BUNDLE/.model-tmp"
  rm -rf "$MODEL_DIR"; mkdir -p "$MODEL_DIR"
  # HF_HUB_OFFLINE=0 обязателен: в образе речи стоит запрет на исходящий
  # трафик (и правильно — рабочая служба в интернет не ходит), а здесь тот же
  # образ используется как загрузчик. Без этой строки сборка офлайн-пакета с
  # голосом падала английским трейсбеком huggingface_hub на любой машине.
  docker run --rm -v "$MODEL_DIR:/models" -e HF_HOME=/models -e HF_HUB_OFFLINE=0 --entrypoint python \
    "hard-stt:$VERSION" -c "
from faster_whisper import WhisperModel
WhisperModel('${MODEL}', device='cpu', compute_type='int8', download_root='/models')
print('модель скачана и проверена загрузкой')" || die "не скачалась модель «$MODEL» (проверьте имя и интернет)"
  mkdir -p "$BUNDLE/model"
  tar -C "$MODEL_DIR" -cf "$BUNDLE/model/models.tar" .
  rm -rf "$MODEL_DIR"
  printf '%s\n' "$MODEL" > "$BUNDLE/model/ИМЯ-МОДЕЛИ.txt"
  ok "model/models.tar — $(du -h "$BUNDLE/model/models.tar" | cut -f1) (модель «$MODEL»)"
else
  say "4/5 Модель речи не кладём (--no-model): голос в закрытом контуре работать не будет"
fi

# --- 5. Колёса (запасной путь) -------------------------------------------------
if [ "$WITH_WHEELS" = 1 ]; then
  say ""
  say "5/5 Колёса pip для сборки из исходников"
  base="$(sed -n 's/^FROM \([^ ]*\).*/\1/p' "$ROOT/server/Dockerfile" | head -n 1)"
  for part in server stt; do
    mkdir -p "$BUNDLE/wheels/$part"
    docker run --rm \
      -v "$ROOT/$part/requirements.txt:/req.txt:ro" \
      -v "$BUNDLE/wheels/$part:/wheels" \
      --entrypoint python "$base" -m pip download -q -r /req.txt -d /wheels \
      || die "не скачались колёса для $part/"
    ok "wheels/$part: $(ls "$BUNDLE/wheels/$part" | wc -l | tr -d ' ') файлов, $(du -sh "$BUNDLE/wheels/$part" | cut -f1)"
    cp "$ROOT/$part/requirements.txt" "$BUNDLE/wheels/$part/requirements.txt"
  done
else
  say ""
  say "5/5 Колёса не кладём (нужны только тем, кто хочет собирать образы сам: --with-wheels)"
fi

# --- Манифест, суммы, скрипты --------------------------------------------------
{
  printf 'Офлайн-пакет «Хард»\n'
  printf 'версия     : %s\n' "$VERSION"
  printf 'собран     : %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
  printf 'модель речи: %s\n' "${MODEL:-нет}"
  printf 'колёса pip : %s\n' "$([ "$WITH_WHEELS" = 1 ] && echo да || echo нет)"
  printf '\nОбразы внутри (слепок на момент сборки):\n'
  for ref in "hard-app:latest" "hard-stt:latest" ${THIRD[@]+"${THIRD[@]}"}; do
    printf '  %-46s %s\n' "$ref" "$(docker image inspect -f '{{.Id}}' "$ref" 2>/dev/null || echo '?')"
  done
  printf '\nЧто с этим делать: ./load.sh на закрытой машине. Подробности — README.md.\n'
} > "$BUNDLE/МАНИФЕСТ.txt"

cp "$HERE/load.sh" "$HERE/verify.sh" "$BUNDLE/"
if [ "$WITH_WHEELS" = 1 ]; then cp "$HERE/build-from-wheels.sh" "$BUNDLE/"; fi
cp "$HERE/README.md" "$BUNDLE/README.md"
chmod +x "$BUNDLE"/*.sh 2>/dev/null || true

# Контрольные суммы — по всему, кроме самого файла сумм. Считаются последними.
say ""
say "Контрольные суммы"
( cd "$BUNDLE" && rm -f .building SHA256SUMS \
  && find . -type f ! -name SHA256SUMS -print0 | sort -z \
     | xargs -0 sha256sum > SHA256SUMS )
ok "SHA256SUMS: $(wc -l < "$BUNDLE/SHA256SUMS" | tr -d ' ') файлов"

say ""
say "== Готово =="
say "  $BUNDLE"
say "  размер: $(du -sh "$BUNDLE" | cut -f1)"
say ""
say "Дальше:"
say "  1. Скопировать ЭТОТ КАТАЛОГ ЦЕЛИКОМ на флешку и унести в закрытый контур."
say "  2. Там: cd <каталог пакета> && ./load.sh"
say "  3. Затем обычная установка (deploy/units/<единица>/install.sh) — она увидит"
say "     загруженные образы и ничего собирать не станет."
