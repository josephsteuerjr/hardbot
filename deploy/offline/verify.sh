#!/usr/bin/env bash
# ПРОВЕРКА ОФЛАЙН-ПАКЕТА: совпадают ли файлы с тем, что записал bundle.sh.
#
#   cd <каталог пакета> && ./verify.sh
#
# Зачем отдельным скриптом: пакет едет на флешке через проходную, и обрыв
# копирования выглядит ровно как успех — файл на месте, размер почти тот.
# Битый образ обнаружится только на docker load, посреди установки, сообщением
# про «unexpected EOF», по которому непонятно, что делать. Поэтому суммы
# сверяются ДО, отдельной командой, с внятным ответом.
#
# Скрипт живёт внутри пакета и ни от чего, кроме bash, не зависит: докера здесь
# не нужно, интернета тем более.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ $# -eq 0 ] || DIR="$(cd "$1" && pwd)"
cd "$DIR"

die() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

[ -f SHA256SUMS ] || die "в $DIR нет файла SHA256SUMS — это не каталог офлайн-пакета"
if [ -f .building ]; then
  die "пакет собран не до конца (остался файл .building) — соберите его заново на машине с интернетом"
fi

# Считалка суммы: на разных системах есть разные утилиты, берём первую живую.
if command -v sha256sum >/dev/null 2>&1;      then sum_of() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1;       then sum_of() { shasum -a 256 "$1" | cut -d' ' -f1; }
elif command -v openssl >/dev/null 2>&1;      then sum_of() { openssl dgst -sha256 "$1" | awk '{print $NF}'; }
elif command -v python3 >/dev/null 2>&1;      then sum_of() { python3 -c "import hashlib,sys;h=hashlib.sha256();f=open(sys.argv[1],'rb');[h.update(b) for b in iter(lambda:f.read(1<<20),b'')];print(h.hexdigest())" "$1"; }
else die "нечем считать SHA-256: нет ни sha256sum, ни shasum, ни openssl, ни python3"
fi

printf 'Проверка пакета: %s\n' "$DIR"
total=0; bad=0; missing=0
while IFS= read -r line; do
  [ -n "$line" ] || continue
  expected="${line%% *}"
  file="${line#* }"; file="${file# }"; file="${file#\*}"
  total=$((total + 1))
  if [ ! -f "$file" ]; then
    printf '  НЕТ ФАЙЛА  %s\n' "$file"; missing=$((missing + 1)); continue
  fi
  actual="$(sum_of "$file")"
  if [ "$actual" != "$expected" ]; then
    printf '  ИСПОРЧЕН   %s\n' "$file"; bad=$((bad + 1))
  fi
done < SHA256SUMS

if [ "$bad" -eq 0 ] && [ "$missing" -eq 0 ]; then
  printf 'Все %d файлов на месте и целы.\n' "$total"
  exit 0
fi
printf '\n'
printf 'Проверено %d, испорчено %d, не найдено %d.\n' "$total" "$bad" "$missing"
printf 'Скопируйте пакет с флешки заново ЦЕЛИКОМ. Дозаписывать по одному файлу\n'
printf 'не надо: если оборвалось одно копирование, обычно оборвалось не одно.\n'
exit 1
