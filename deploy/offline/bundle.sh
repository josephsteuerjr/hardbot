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
#   ./bundle.sh --no-model               # без запасной копии модели (голос
#                                        остаётся: рабочая модель — в образе речи)
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

# Путь в форме, которую понимает docker НА ЭТОЙ машине.
#
# ЗАЧЕМ. В Git Bash под Windows `pwd` печатает /c/Users/…, а всё, что похоже на
# список путей через двоеточие, MSYS по дороге к чужой программе переписывает
# сам: аргумент «-v /c/…/.model-tmp:/models» превращается в
# «C:\…\.model-tmp;C:\Program Files\Git\models». Докер видит одну странную
# ссылку, создаёт рядом каталог-огрызок «.model-tmp;C», монтирует пустоту, и
# контейнер честно отчитывается об успехе — а наружу не выходит ничего.
# Отсюда и брались «скачанные» модели по 12 КБ. cygpath -m даёт «C:/…»: докер
# такую форму принимает, а MSYS её уже не трогает.
hostpath() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}

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
# ГЛАВНОЕ ПРО ГОЛОС: модель, на которой работает служба, ЗАПЕЧЕНА В ОБРАЗ
# hard-stt (см. stt/Dockerfile). Раз образ уезжает в пакете — уезжает и она,
# качать на закрытой машине нечего. Поэтому первым делом проверяем именно
# образ: пакет, в котором модели нет, увозить бессмысленно, а узнать об этом
# надо здесь, у интернета, а не там, где интернета уже не будет.
#
# Отдельная копия модели в model/models.tar — это ЗАПАСНОЙ путь для тех, кто на
# месте захочет другую (например, более лёгкую) модель, не пересобирая образ.
# Голос без неё работает.
say ""
say "4/5 Модель речи"

# 4.1. Что лежит в образе. Читаем манифест запечённой модели тем же способом,
#      каким его читает сама служба, и сверяем размеры файлов.
BAKED="$(docker run --rm --entrypoint python "hard-stt:$VERSION" -c "
import json, pathlib
d = pathlib.Path('/opt/hard/stt-model')
try:
    m = json.loads((d / 'hard-model.json').read_text('utf-8'))
    files = [f for f in m.get('files', []) if isinstance(f, dict)]
except Exception:
    print('|0|0|манифеста нет'); raise SystemExit(0)
bad, size = [], 0
for f in files:
    p = d / str(f.get('name', ''))
    try:
        got = p.stat().st_size
    except OSError:
        bad.append(p.name + ' — нет'); continue
    size += got
    if got != f.get('size'):
        bad.append('%s — %d байт вместо %d' % (p.name, got, f.get('size')))
print('%s|%d|%d|%s' % (m.get('name', '?'), len(files), size, '; '.join(bad)))
" 2>/dev/null || true)"
baked_name="${BAKED%%|*}"; rest="${BAKED#*|}"
baked_files="${rest%%|*}"; rest="${rest#*|}"
baked_bytes="${rest%%|*}"; baked_bad="${rest#*|}"
case "$baked_files" in ''|*[!0-9]*) baked_files=0 ;; esac
case "$baked_bytes" in ''|*[!0-9]*) baked_bytes=0 ;; esac

if [ "$baked_files" -gt 0 ] && [ -z "$baked_bad" ]; then
  ok "$(printf 'в образе hard-stt запечена модель «%s»: %s файлов, %s ГБ — голос в закрытом контуре заработает без интернета' \
        "$baked_name" "$baked_files" "$(awk -v b="$baked_bytes" 'BEGIN{printf "%.2f", b/1073741824}')")"
elif [ -n "$baked_bad" ] && [ "$baked_files" -gt 0 ]; then
  die "модель в образе hard-stt довезена не целиком: $baked_bad
     Подготовьте её заново (python tools/prepare_stt_model.py) и пересоберите образ."
elif [ -n "$MODEL" ]; then
  warn "в образе hard-stt модели НЕТ (нет /opt/hard/stt-model/hard-model.json)"
  say  "      голос на закрытой машине заработает только из копии ниже, и то если в"
  say  "      .env единицы речи будет STT_MODEL=$MODEL"
else
  die "в образе hard-stt модели нет, и отдельную класть не велено (--no-model): голоса в пакете не будет вовсе.
     Подготовьте модель (python tools/prepare_stt_model.py) и пересоберите образ — либо соберите пакет без --no-model."
fi

# 4.2. Запасная копия модели отдельным файлом (по умолчанию «small»).
if [ -n "$MODEL" ]; then
  say ""
  say "  запасная копия модели «$MODEL» (качается один раз, small ≈ 460 МБ, large-v3-turbo ≈ 1,5 ГБ)"
  MODEL_DIR="$BUNDLE/.model-tmp"
  rm -rf "$MODEL_DIR"; mkdir -p "$MODEL_DIR"
  # HF_HUB_OFFLINE=0 обязателен: в образе речи стоит запрет на исходящий
  # трафик (и правильно — рабочая служба в интернет не ходит), а здесь тот же
  # образ используется как загрузчик. Без этой строки сборка офлайн-пакета с
  # голосом падала английским трейсбеком huggingface_hub на любой машине.
  docker run --rm -v "$(hostpath "$MODEL_DIR"):/models" -e HF_HOME=/models -e HF_HUB_OFFLINE=0 --entrypoint python \
    "hard-stt:$VERSION" -c "
from faster_whisper import WhisperModel
WhisperModel('${MODEL}', device='cpu', compute_type='int8', download_root='/models')
print('  модель скачана и проверена загрузкой')" || die "не скачалась модель «$MODEL» (проверьте имя и интернет)"

  # ПРОВЕРКА ПОСЛЕ СКАЧИВАНИЯ. «Контейнер отработал» и «файлы лежат на диске» —
  # это два разных факта, и они расходятся: том мог не примонтироваться, диск
  # мог кончиться, каталог мог оказаться не тем. Раньше расхождения никто не
  # ловил, и в пакет уезжал tar на 12 КБ с одним пустым каталогом внутри —
  # с рапортом об успехе и правильной контрольной суммой пустоты.
  # Считаем ВСЁ, что не каталог: кэш huggingface наполовину состоит из ссылок
  # (snapshots/… → blobs/…), и в tar они тоже попадают как отдельные записи.
  got_files="$(find "$MODEL_DIR" ! -type d | wc -l | tr -d ' ')"
  got_kb="$(du -sk "$MODEL_DIR" | cut -f1)"
  [ "$got_files" -gt 0 ] || die "контейнер отчитался об успехе, а в $MODEL_DIR пусто.
     Значит каталог не вышел наружу: докер писал не туда, куда мы смотрим.
     Проверьте, что путь к пакету доступен докеру (в Docker Desktop — File Sharing),
     и что в пути нет ничего экзотического. Пустую «модель» в пакет не кладём."
  [ -n "$(find "$MODEL_DIR" -type f -size +10M -print -quit)" ] || \
    die "скачалось $got_files файлов на $got_kb КБ — весов модели среди них нет.
     Самая лёгкая модель whisper весит десятки мегабайт; то, что лежит в
     $MODEL_DIR, моделью не является. В пакет это не поедет."

  mkdir -p "$BUNDLE/model"
  tar -C "$MODEL_DIR" -cf "$BUNDLE/model/models.tar" .

  # ПРОВЕРКА ПОСЛЕ УПАКОВКИ — по самому tar, а не по каталогу, из которого его
  # делали: на закрытой машине распаковывать будут именно его.
  tar_files="$(tar -tf "$BUNDLE/model/models.tar" | grep -cv '/$' || true)"
  tar_kb=$(( $(wc -c < "$BUNDLE/model/models.tar") / 1024 ))
  [ "$tar_files" = "$got_files" ] || die "в model/models.tar $tar_files записей, а скачано $got_files — упаковалось не всё."
  [ "$tar_kb" -ge $(( got_kb / 2 )) ] || die "model/models.tar весит $tar_kb КБ при $got_kb КБ скачанного — упаковалось не всё."

  rm -rf "$MODEL_DIR" 2>/dev/null || \
    docker run --rm -v "$(hostpath "$BUNDLE"):/b" --entrypoint sh "hard-stt:$VERSION" \
      -c 'rm -rf /b/.model-tmp' >/dev/null 2>&1 || true
  printf '%s\n' "$MODEL" > "$BUNDLE/model/ИМЯ-МОДЕЛИ.txt"
  ok "model/models.tar — $(du -h "$BUNDLE/model/models.tar" | cut -f1), записей внутри $tar_files (модель «$MODEL», скачано $got_kb КБ)"
  say "      это ЗАПАСНАЯ модель: она нужна только тому, кто на месте захочет"
  say "      работать не запечённой «$baked_name», а другой. Тогда и только тогда"
  say "      её распаковывают и ставят STT_MODEL=$MODEL. Не нужна — ./bundle.sh --no-model,"
  say "      пакет похудеет, а голос останется."
else
  say "  отдельную копию модели не кладём (--no-model): голос работает на запечённой «$baked_name»"
fi

# --- 5. Колёса (запасной путь) -------------------------------------------------
if [ "$WITH_WHEELS" = 1 ]; then
  say ""
  say "5/5 Колёса pip для сборки из исходников"
  base="$(sed -n 's/^FROM \([^ ]*\).*/\1/p' "$ROOT/server/Dockerfile" | head -n 1)"
  for part in server stt; do
    mkdir -p "$BUNDLE/wheels/$part"
    docker run --rm \
      -v "$(hostpath "$ROOT/$part/requirements.txt"):/req.txt:ro" \
      -v "$(hostpath "$BUNDLE/wheels/$part"):/wheels" \
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
  printf 'модель речи: %s (запечена в образ hard-stt)\n' "${baked_name:-?}"
  printf 'её копия   : %s (отдельным файлом, запасной путь)\n' "${MODEL:-нет}"
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
say "  2. Там: cd <каталог пакета> && ./load.sh --deploy /путь/к/deploy"
say "     (--deploy — каталог с исходниками системы на диске сервера; если он"
say "      лежит в обычном месте вроде /opt/hard/deploy, ключ можно не писать)"
say "  3. Затем обычная установка (deploy/units/<единица>/install.sh) — она увидит"
say "     загруженные образы и ничего собирать не станет."
