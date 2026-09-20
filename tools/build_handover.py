#!/usr/bin/env python
"""Комплект развёртывания «Хард» — то, чем систему ставит человек без инженерного опыта.

Это НЕ пакет исходников (его собирает build_it_package.py и предназначен он
ИТ-службе). Здесь только то, что нужно, чтобы поднять систему на новой машине и
жить с ней дальше: проверка машины, образы, сама система, ресурсы, четыре
документа. Тестов, пульта и кода обработки реестров в комплекте нет — к
развёртыванию они отношения не имеют.

    python tools/build_handover.py
    python tools/build_handover.py --resources /путь/к/карте-и-графам
    python tools/build_handover.py --no-bundle          # без образов, только система

Состав на выходе:

    0-проверка/     годится ли машина; там же эталон вывода
    1-образы/       офлайн-пакет: load.sh и образы, из которых всё поднимается
    2-система/      deploy + server + stt — то, что кладётся в /opt/hard
    3-ресурсы/      карта, дорожные графы, справочник адресов
    НАЧНИ ОТСЮДА.md, ЕСЛИ НЕ РАБОТАЕТ.md, ТРЕБОВАНИЯ-ИТ.md, ВЕРСИЯ, SHA256SUMS
"""
from __future__ import annotations

import argparse
import datetime
import shutil
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_it_package import (  # noqa: E402
    ROOT,
    BuildStop,
    normalized_bytes,
    resolve_version,
    scan_for_leaks,
    select_files,
    sha256_of,
)

# Что из исходников едет в комплект. Всё остальное — не про развёртывание.
SYSTEM_PARTS = ("deploy", "server", "stt")

# Веса модели в комплект не кладём: они уже запечены в образ речи, а второй
# экземпляр — это лишние 0,76 ГБ на флешке. Остальное из stt/model оставляем,
# чтобы было видно, какая именно модель внутри образа.
SKIP_FILES = {"stt/model/model.bin"}

# Эти четыре лежат в корне комплекта, читаются первыми и не должны дублироваться
# внутри 2-система/deploy.
ROOT_DOCS = {
    "deploy/НАЧНИ ОТСЮДА.md": "НАЧНИ ОТСЮДА.md",
    "deploy/ЕСЛИ НЕ РАБОТАЕТ.md": "ЕСЛИ НЕ РАБОТАЕТ.md",
    "deploy/ТРЕБОВАНИЯ-ИТ.md": "ТРЕБОВАНИЯ-ИТ.md",
}

RESOURCES_NOTE = """В этом каталоге должны лежать ресурсы: карта, дорожные графы и справочник
адресов. Сейчас их здесь нет — положите перед тем, как везти комплект.

  map/basemap.pmtiles   подложка карты, около 3 ГБ. Без неё система поставится
                        и все проверки пройдёт, а карта молча уйдёт на
                        интернет-подложку
  osrm/<страна>/        дорожные графы для порядка объезда, единица gps
  geonames/             справочник адресов

Собирать их на месте нельзя: несколько часов машинного времени и около 10 ГБ
диска. Их берут готовыми с работающей машины.

Рядом должен оказаться файл SHA256SUMS — проверка после перевоза:

    sha256sum -c SHA256SUMS
"""


def newest_bundle(root: Path) -> Path | None:
    out = root / "deploy" / "offline" / "out"
    if not out.is_dir():
        return None
    ready = [p for p in out.iterdir() if p.is_dir() and not (p / ".building").exists()]
    return max(ready, key=lambda p: p.stat().st_mtime, default=None)


def copy_sources(root: Path, kit: Path) -> tuple[int, list[str]]:
    """Исходники берём тем же списком, что и пакет: из git, с той же проверкой утечек."""
    files = select_files(root)
    scan_for_leaks(root, files)

    system = kit / "2-система"
    copied = 0
    docs: list[str] = []
    for relative in files:
        posix = relative.as_posix()
        if posix in SKIP_FILES:
            continue
        if posix in ROOT_DOCS:
            target = kit / ROOT_DOCS[posix]
            docs.append(ROOT_DOCS[posix])
        else:
            if posix.split("/", 1)[0] not in SYSTEM_PARTS:
                continue
            target = system / Path(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(normalized_bytes(root, relative))
        copied += 1
    return copied, docs


def copy_tree(src: Path, dst: Path) -> int:
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return sum(1 for p in dst.rglob("*") if p.is_file())


def write_sums(kit: Path) -> int:
    lines = []
    for path in sorted(p for p in kit.rglob("*") if p.is_file() and p.name != "SHA256SUMS"):
        lines.append(f"{sha256_of(path)}  {path.relative_to(kit).as_posix()}\n")
    (kit / "SHA256SUMS").write_text("".join(lines), encoding="utf-8", newline="\n")
    return len(lines)


def build(root: Path, out_dir: Path, bundle: Path | None, resources: Path | None) -> Path:
    version = resolve_version(root, None)
    day = f"{datetime.date.today():%Y-%m-%d}"
    kit = out_dir / f"hard-kit-{day}"
    if kit.exists():
        shutil.rmtree(kit)
    kit.mkdir(parents=True)

    print(f"== Хард: комплект развёртывания ==\n  версия: {version}\n  каталог: {kit}\n")

    print("1/5 Система: deploy, server, stt")
    copied, docs = copy_sources(root, kit)
    print(f"  [ок] файлов: {copied}; в корень вынесено документов: {len(docs)}")
    if not docs:
        raise BuildStop("в корень комплекта не попал ни один документ — проверьте ROOT_DOCS")

    print("2/5 Проверка машины")
    check = kit / "0-проверка"
    check.mkdir()
    (check / "preflight.sh").write_bytes(normalized_bytes(root, PurePosixPath("deploy/preflight.sh")))
    ref = root / "deploy" / "preflight-эталон.txt"
    if ref.exists():
        shutil.copy2(ref, check / "эталон.txt")
        print("  [ок] preflight.sh и эталон вывода")
    else:
        print("  [!!] эталона вывода нет — сравнивать будет не с чем")

    print("3/5 Образы")
    if bundle is None:
        print("  [--] без образов: на той стороне понадобится интернет и сборка")
    else:
        n = copy_tree(bundle, kit / "1-образы")
        size = sum(p.stat().st_size for p in (kit / "1-образы").rglob("*") if p.is_file())
        print(f"  [ок] {bundle.name}: файлов {n}, {size / 2**30:.1f} ГБ")

    print("4/5 Ресурсы: карта, дороги, адреса")
    res = kit / "3-ресурсы"
    res.mkdir()
    if resources is None:
        (res / "ЧТО-ЭТО.txt").write_text(RESOURCES_NOTE, encoding="utf-8", newline="\n")
        print("  [--] ресурсов нет, положена записка: чего не хватает и откуда взять")
    else:
        n = copy_tree(resources, res)
        size = sum(p.stat().st_size for p in res.rglob("*") if p.is_file())
        print(f"  [ок] файлов {n}, {size / 2**30:.1f} ГБ")

    (kit / "ВЕРСИЯ").write_text(
        f"{version}\n{day}\nкомплект развёртывания\n", encoding="utf-8", newline="\n")

    print("5/5 Контрольные суммы")
    total = write_sums(kit)
    print(f"  [ок] SHA256SUMS: {total} файлов")

    whole = sum(p.stat().st_size for p in kit.rglob("*") if p.is_file())
    print(f"\n== Готово ==\n  {kit}\n  размер: {whole / 2**30:.1f} ГБ")
    print("\nДальше:\n  1. Скопировать каталог целиком на флешку.\n"
          "  2. На новой машине открыть «НАЧНИ ОТСЮДА.md» и идти сверху вниз.")
    return kit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Комплект развёртывания «Хард»")
    parser.add_argument("--out", default=str(ROOT / "dist"), help="куда положить комплект (dist/)")
    parser.add_argument("--bundle", help="каталог офлайн-пакета образов (по умолчанию — свежий из deploy/offline/out)")
    parser.add_argument("--no-bundle", action="store_true", help="собрать без образов")
    parser.add_argument("--resources", help="каталог с картой, графами и адресами")
    args = parser.parse_args(argv)

    bundle = None
    if not args.no_bundle:
        bundle = Path(args.bundle) if args.bundle else newest_bundle(ROOT)
        if bundle is not None and not bundle.is_dir():
            print(f"ОШИБКА: нет каталога образов: {bundle}", file=sys.stderr)
            return 2

    resources = Path(args.resources) if args.resources else None
    if resources is not None and not resources.is_dir():
        print(f"ОШИБКА: нет каталога ресурсов: {resources}", file=sys.stderr)
        return 2

    try:
        build(ROOT, Path(args.out), bundle, resources)
    except BuildStop as stop:
        print(stop, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
