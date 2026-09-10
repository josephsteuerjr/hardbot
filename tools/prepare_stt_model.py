#!/usr/bin/env python3
"""Готовит модель распознавания речи к поставке: локальная модель → int8 → stt/model.

ЗАЧЕМ. Раньше служба речи качала модель с huggingface.co при первом старте.
Это значит: на объекте нужен интернет, нужен доступ именно к huggingface.co, и
первый запуск длится столько, сколько длится скачивание полутора гигабайт. Любой
из трёх пунктов может не выполниться — и служба просто не поднимется, а инженер
будет смотреть в журнал и гадать. Поэтому модель едет ВМЕСТЕ С ИСХОДНИКАМИ:
этот скрипт кладёт её в ``stt/model``, а ``stt/Dockerfile`` запекает её в образ.

ЧТО ИМЕННО ОН ДЕЛАЕТ.
  1. Находит исходную модель. По умолчанию — в локальном кэше huggingface
     (на машине, где модель уже качали, скачивать нечего). Если её нет —
     скачивает, но это единственный шаг, которому нужен интернет, и он
     происходит ЗДЕСЬ, а не на чужом сервере.
  2. Переводит веса из float16 в int8 средствами самого ctranslate2 — тем же
     кодом, которым пользуется штатный конвертер (``ModelSpec.optimize``).
     Это ровно то, что faster-whisper иначе делает в памяти при каждом старте:
     мы делаем это один раз и сохраняем результат. Выигрыш двойной — файл вдвое
     меньше (≈ 0,8 ГБ вместо 1,6 ГБ, а его ещё везти в чужую компанию) и старт
     службы короче, потому что квантовать на лету уже нечего.
  3. Считает SHA-256 каждого файла и пишет их в ``SHA256SUMS`` и в манифест
     ``hard-model.json``. По манифесту служба на старте понимает, какая модель
     запечена в образ, и мгновенно ловит недовезённый (обрезанный) файл.

ПОЧЕМУ КОНВЕРТЕР ЧИТАЕТ model.bin САМ. У ctranslate2 есть конвертеры «из
transformers в ctranslate2», но нет «из ctranslate2 в ctranslate2»: официальный
путь для int8 — заново конвертировать исходную модель из PyTorch, а это ещё
torch и transformers (два с лишним гигабайта колёс) и повторное скачивание.
Поэтому мы читаем уже готовый ``model.bin`` (его формат — ровно то, что пишет
``ModelSpec._serialize``: версия, имя спецификации, ревизия, переменные,
псевдонимы), раскладываем переменные обратно по настоящему ``WhisperSpec`` и
отдаём квантование и запись самому ctranslate2. Ни одной формулы квантования
здесь нет — считает библиотека, мы только подаём ей веса.

ЗАПУСК (из корня репозитория):

    python tools/prepare_stt_model.py            # подготовить stt/model
    python tools/prepare_stt_model.py --verify   # проверить контрольные суммы

Своего ctranslate2 на машине не нужно: если его нет, скрипт сам перезапускает
себя внутри образа службы речи (docker). Ключ ``--no-docker`` это отключает.

Конвертация держит все веса в памяти: на large-v3-turbo нужно ≈ 2,5 ГБ
свободной памяти и минута времени. Если работа идёт в контейнере — это память
виртуальной машины docker, а не всей системы.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Модель по умолчанию. Это уже готовая ctranslate2-сборка large-v3-turbo:
# из неё не надо ничего конвертировать из PyTorch, только переквантовать.
DEFAULT_REPO = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
DEFAULT_NAME = "large-v3-turbo"
DEFAULT_OUTPUT = REPO_ROOT / "stt" / "model"
DEFAULT_QUANTIZATION = "int8"

# Образ службы речи: в нём уже есть ctranslate2 нужной версии, отдельный
# «конвертерный» образ заводить незачем.
IMAGE = "hard-stt"
IMAGE_CONTEXT = REPO_ROOT / "stt"

MANIFEST = "hard-model.json"
SUMS = "SHA256SUMS"
# Что в каталоге поставки не наше и переписывать это нельзя: README держит сам
# каталог в git (без файла внутри пустого каталога не бывает), и без него
# `COPY model` в Dockerfile сломается ещё до разговоров про модель.
KEEP = {"README.md"}
MANIFEST_SCHEMA = "hard.stt.model.v1"

# Файлы модели faster-whisper. model.bin пишет ctranslate2, остальные копируются
# как есть — это словарь и настройки препроцессора, они от квантования не зависят.
WEIGHTS = "model.bin"

# СЛОВАРЬ БЫВАЕТ В ДВУХ ВИДАХ, И ЭТО НЕ КАПРИЗ. Старые сборки ctranslate2 писали
# словарь построчным текстом (vocabulary.txt), новые — списком в JSON
# (vocabulary.json). Ровно поэтому репозитории Systran, включая
# faster-whisper-small, отдают .txt, а mobiuslabs (large-v3-turbo) — .json.
# Скрипт требовал именно .json — и «small», которая стоит умолчанием в
# .env.example и названа в README рекомендуемой, подготовить было нельзя:
# «не хватает файлов: vocabulary.json». Читаем любой из двух, а кладём в
# поставку тот, который пишет ctranslate2 при сохранении спецификации.
VOCABULARY_JSON = "vocabulary.json"
VOCABULARY_TXT = "vocabulary.txt"
SIDECARS = ("config.json", "tokenizer.json", VOCABULARY_JSON, VOCABULARY_TXT,
            "preprocessor_config.json")
REQUIRED_SIDECARS = ("config.json", "tokenizer.json")

# Типы данных ctranslate2 по порядку из include/ctranslate2/types.h.
DTYPES = ("float32", "int8", "int16", "int32", "float16", "bfloat16")


class PrepareError(RuntimeError):
    """Понятная человеку остановка: сообщение печатается как есть, без трассировки."""


def log(message: str) -> None:
    print(message, flush=True)


# --- чтение model.bin ----------------------------------------------------------

def _read_string(handle) -> str:
    (size,) = struct.unpack("H", handle.read(2))
    return handle.read(size)[:-1].decode("utf-8")


def read_ct2_model(path: Path) -> dict:
    """Разбирает model.bin обратно в переменные — зеркало ModelSpec._serialize."""
    import numpy as np

    with path.open("rb") as handle:
        (version,) = struct.unpack("I", handle.read(4))
        if version != 6:
            raise PrepareError(
                f"{path}: версия двоичного формата {version}, а ожидалась 6. "
                "Модель собрана другой версией ctranslate2 — обновите образ службы речи."
            )
        spec_name = _read_string(handle)
        (revision,) = struct.unpack("I", handle.read(4))
        (count,) = struct.unpack("I", handle.read(4))
        variables: dict[str, "np.ndarray"] = {}
        for _ in range(count):
            name = _read_string(handle)
            (rank,) = struct.unpack("B", handle.read(1))
            shape = struct.unpack("I" * rank, handle.read(4 * rank)) if rank else ()
            (dtype_id,) = struct.unpack("B", handle.read(1))
            (num_bytes,) = struct.unpack("I", handle.read(4))
            raw = handle.read(num_bytes)
            if len(raw) != num_bytes:
                raise PrepareError(f"{path}: файл обрывается на переменной {name}")
            array = np.frombuffer(raw, dtype=np.dtype(DTYPES[dtype_id])).reshape(shape)
            # frombuffer отдаёт вид на неизменяемые байты; квантование пишет
            # в массивы на месте, поэтому копию делаем сразу.
            variables[name] = array.copy()
        (alias_count,) = struct.unpack("I", handle.read(4))
        aliases = [(_read_string(handle), _read_string(handle)) for _ in range(alias_count)]
    # Отдельная ловушка: подсунуть источником результат прошлой подготовки.
    # Квантование не идемпотентно — второй проход посчитал бы масштабы от уже
    # сжатых весов, и модель тихо превратилась бы в шум. Ловим по признаку:
    # у квантованной модели рядом с весами лежат масштабы (*_scale).
    scales = sum(1 for name in variables if name.endswith("_scale"))
    if scales:
        raise PrepareError(
            f"{path}: модель уже квантована (нашлось масштабов: {scales}). Источником "
            "нужна исходная модель в float16, а не результат прошлой подготовки — "
            "квантовать второй раз нельзя, получится не модель, а мусор.")
    return {"spec": spec_name, "revision": revision,
            "variables": variables, "aliases": aliases}


# --- сборка спецификации и квантование -----------------------------------------

def _layer_count(variables: dict, prefix: str) -> int:
    numbers = set()
    for name in variables:
        if not name.startswith(prefix):
            continue
        tail = name[len(prefix):].split("/", 1)[0]
        if tail.isdigit():
            numbers.add(int(tail))
    if not numbers or numbers != set(range(len(numbers))):
        raise PrepareError(f"не удалось сосчитать слои по префиксу {prefix}")
    return len(numbers)


def build_spec(model: dict):
    """Раскладывает прочитанные переменные обратно по настоящему WhisperSpec."""
    from ctranslate2.specs import model_spec as ms
    from ctranslate2.specs import whisper_spec

    if model["spec"] != "WhisperSpec":
        raise PrepareError(
            f"это модель {model['spec']}, а не WhisperSpec — конвертер рассчитан "
            "только на модели whisper из faster-whisper"
        )
    variables = model["variables"]
    spec = whisper_spec.WhisperSpec(
        num_encoder_layers=_layer_count(variables, "encoder/layer_"),
        num_encoder_heads=int(variables["encoder/num_heads"]),
        num_decoder_layers=_layer_count(variables, "decoder/layer_"),
        num_decoder_heads=int(variables["decoder/num_heads"]),
    )
    if spec.revision != model["revision"]:
        raise PrepareError(
            f"ревизия WhisperSpec в файле {model['revision']}, а ctranslate2 в образе "
            f"ждёт {spec.revision} — версии разошлись, конвертировать нельзя"
        )

    for name, value in variables.items():
        scope, _, key = name.rpartition("/")
        setattr(ms.index_spec(spec, scope), key, value)
    # Псевдоним — это не отдельные веса, а ссылка на чужие. Кладём тот же самый
    # объект: ctranslate2 сам заметит совпадение и снова свернёт его в псевдоним.
    for alias, target in model["aliases"]:
        scope, _, key = alias.rpartition("/")
        setattr(ms.index_spec(spec, scope), key, variables[target])
    return spec


def apply_config(spec, source_config: dict) -> None:
    """Переносит настройки декодирования (языки, запрещённые токены) в спецификацию."""
    for key in ("suppress_ids", "suppress_ids_begin", "lang_ids", "alignment_heads"):
        if key in source_config:
            setattr(spec.config, key, source_config[key])


# --- контрольные суммы ---------------------------------------------------------

def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_receipt(output: Path, name: str, repo: str, revision: str | None,
                  quantization: str, spec_name: str, ct2_version: str) -> dict:
    files = []
    for item in sorted(output.iterdir()):
        if item.name in (MANIFEST, SUMS) or not item.is_file():
            continue
        files.append({"name": item.name, "size": item.stat().st_size,
                      "sha256": sha256_of(item)})
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "name": name,
        "source": {"repo": repo, "revision": revision},
        "quantization": quantization,
        "spec": spec_name,
        "ctranslate2": ct2_version,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": files,
    }
    (output / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / SUMS).write_text(
        "".join(f"{item['sha256']}  {item['name']}\n" for item in files), encoding="ascii")
    return manifest


def verify(output: Path) -> int:
    """Проверка поставленной модели: то же, что sha256sum -c, но без sha256sum."""
    manifest_path = output / MANIFEST
    if not manifest_path.is_file():
        log(f"нет манифеста {manifest_path} — модель не подготовлена")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bad = 0
    for item in manifest.get("files", []):
        path = output / item["name"]
        if not path.is_file():
            log(f"НЕТ ФАЙЛА  {item['name']}")
            bad += 1
            continue
        size = path.stat().st_size
        if size != item["size"]:
            log(f"РАЗМЕР     {item['name']}: {size} вместо {item['size']} — файл довезён не целиком")
            bad += 1
            continue
        if sha256_of(path) != item["sha256"]:
            log(f"СУММА      {item['name']}: содержимое не совпадает с манифестом")
            bad += 1
            continue
        log(f"ок         {item['name']} ({size / 1e6:.0f} МБ)")
    if bad:
        log(f"\nповреждено файлов: {bad}. Переподготовьте модель или перевезите каталог заново.")
        return 1
    log(f"\nмодель {manifest.get('name')} ({manifest.get('quantization')}) на месте и цела")
    return 0


# --- поиск исходной модели -----------------------------------------------------

def hub_cache_root() -> Path:
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        value = os.environ.get(name)
        if value:
            return Path(value)
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def find_in_cache(repo: str) -> Path | None:
    """Ищет уже скачанный снапшот в кэше huggingface, ничего не качая."""
    folder = hub_cache_root() / ("models--" + repo.replace("/", "--"))
    snapshots = folder / "snapshots"
    if not snapshots.is_dir():
        return None
    ref = folder / "refs" / "main"
    if ref.is_file():
        candidate = snapshots / ref.read_text(encoding="utf-8").strip()
        if (candidate / WEIGHTS).exists():
            return candidate
    for candidate in sorted(snapshots.iterdir()):
        if (candidate / WEIGHTS).exists():
            return candidate
    return None


def download(repo: str) -> Path:
    log(f"модели нет в кэше — качаю {repo} (это единственный шаг, которому нужен интернет)")
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:  # pragma: no cover — в образе службы пакет есть
        raise PrepareError("нет huggingface_hub — скачать модель нечем") from exc
    try:
        path = snapshot_download(repo, allow_patterns=[WEIGHTS, *SIDECARS])
    except Exception as exc:  # noqa: BLE001 — huggingface_hub бросает свои типы
        # Голый английский трейсбек на этом шаге читается как «привезли
        # сломанное». Говорим по-русски и называем оба выхода: интернет или
        # готовый каталог модели с другой машины.
        raise PrepareError(
            f"не удалось скачать {repo}: {exc.__class__.__name__}. "
            "Этому шагу нужен интернет и доступ к huggingface.co. Если его нет — "
            "подготовьте модель на машине с интернетом и перевезите каталог, "
            "указав его через --source."
        ) from exc
    return Path(path)


def snapshot_revision(path: Path) -> str | None:
    parent = path.parent
    return path.name if parent.name == "snapshots" else None


# --- собственно подготовка -----------------------------------------------------

def publish(staging: Path, output: Path) -> None:
    """Переносит готовое из черновика в каталог поставки, манифест — последним.

    Переименовать каталог целиком нельзя (см. выше), поэтому переносим по файлу.
    Порядок важен: hard-model.json и SHA256SUMS уезжают в конце, поэтому оборванный
    посередине перенос оставляет каталог БЕЗ манифеста, а такой каталог и служба,
    и --verify считают неподготовленным. Молчаливой полумодели не бывает.
    """
    for item in output.iterdir():
        if item == staging or item.name in KEEP:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    order = {SUMS: 1, MANIFEST: 2}
    for item in sorted(staging.iterdir(), key=lambda p: order.get(p.name, 0)):
        item.replace(output / item.name)
    staging.rmdir()


def read_vocabulary(source: Path) -> list[str]:
    """Словарь модели: из vocabulary.json (список) или vocabulary.txt (по строке).

    Текстовый вид — это ровно тот же список, записанный построчно, поэтому
    единственная тонкость здесь — последний перевод строки: split оставляет
    после него пустую строку, а лишний «пустой токен» ctranslate2 поймает уже
    на сверке размера словаря с размерностью эмбеддингов и откажется работать.
    """
    json_path = source / VOCABULARY_JSON
    if json_path.is_file():
        return json.loads(json_path.read_text(encoding="utf-8"))
    text_path = source / VOCABULARY_TXT
    if text_path.is_file():
        lines = text_path.read_text(encoding="utf-8").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        return lines
    raise PrepareError(
        f"в {source} нет словаря: ни {VOCABULARY_JSON}, ни {VOCABULARY_TXT}. "
        "Это не модель faster-whisper — проверьте, тот ли репозиторий указан в --repo.")


def prepare(source: Path, output: Path, quantization: str, name: str, repo: str) -> None:
    import ctranslate2

    if not (source / WEIGHTS).is_file():
        raise PrepareError(f"в {source} нет {WEIGHTS} — это не модель faster-whisper")
    missing = [item for item in REQUIRED_SIDECARS if not (source / item).is_file()]
    if missing:
        raise PrepareError(f"в {source} не хватает файлов: {', '.join(missing)}")
    vocabulary = read_vocabulary(source)

    started = time.monotonic()
    log(f"читаю {source / WEIGHTS} ({(source / WEIGHTS).stat().st_size / 1e9:.2f} ГБ)")
    model = read_ct2_model(source / WEIGHTS)
    log(f"переменных: {len(model['variables'])}, псевдонимов: {len(model['aliases'])}")

    spec = build_spec(model)
    apply_config(spec, json.loads((source / "config.json").read_text(encoding="utf-8")))
    # Словарь в model.bin не лежит — он отдельным файлом, и ctranslate2 проверяет,
    # что его размер сходится с размерностью эмбеддингов. Заодно это проверка,
    # что словарь и model.bin из одной модели, а не из двух разных.
    spec.register_vocabulary(vocabulary)
    spec.validate()
    log(f"перевожу веса в {quantization} (считает ctranslate2 {ctranslate2.__version__})")
    spec.optimize(quantization=quantization)

    # Черновик собираем ВНУТРИ каталога назначения, а не рядом: сам каталог
    # может оказаться точкой монтирования (так и есть, когда мы работаем внутри
    # контейнера), а такую ни переименовать, ни удалить нельзя.
    output.mkdir(parents=True, exist_ok=True)
    staging = output / ".staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    spec.save(str(staging))
    # config.json и vocabulary.json ctranslate2 пишет сам, но кладём ИСХОДНЫЕ:
    # всё, что в них есть, пришло из модели, а пересобранные из спецификации
    # молча потеряют то, чего эта версия ctranslate2 про них не знает.
    #
    # Исключение — vocabulary.txt: его содержимое ctranslate2 уже сохранил в
    # vocabulary.json. Положить рядом ещё и .txt значило бы везти два словаря,
    # из которых читается один: лишний повод для вопроса «а какой настоящий?».
    for item in SIDECARS:
        if item == VOCABULARY_TXT and (staging / VOCABULARY_JSON).is_file():
            continue
        if (source / item).is_file():
            shutil.copyfile(source / item, staging / item)

    manifest = write_receipt(staging, name, repo, snapshot_revision(source),
                             quantization, model["spec"], ctranslate2.__version__)
    publish(staging, output)

    total = sum(item["size"] for item in manifest["files"])
    was = (source / WEIGHTS).stat().st_size
    log(f"готово за {time.monotonic() - started:.0f} с: {output}")
    log(f"весит {total / 1e9:.2f} ГБ (было {was / 1e9:.2f} ГБ), файлов {len(manifest['files'])}")
    log(f"контрольные суммы: {output / SUMS}")


# --- перезапуск внутри docker ---------------------------------------------------

def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                              capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_image() -> None:
    probe = subprocess.run(["docker", "image", "inspect", IMAGE],
                           capture_output=True, text=True)
    if probe.returncode == 0:
        return
    log(f"собираю образ {IMAGE} (он же нужен службе речи) — это разово")
    build = subprocess.run(["docker", "build", "-t", IMAGE, str(IMAGE_CONTEXT)])
    if build.returncode != 0:
        raise PrepareError("не удалось собрать образ службы речи — смотрите вывод docker выше")


def mount_for(source: Path) -> tuple[Path, str]:
    """Что монтировать в контейнер, чтобы ссылки внутри кэша huggingface не порвались.

    В кэше файлы снапшота — символические ссылки на ../../blobs. Смонтируешь
    только каталог снапшота — внутри контейнера будут битые ссылки, поэтому
    монтируем каталог модели целиком, а внутрь передаём путь к снапшоту.
    """
    if source.parent.name == "snapshots":
        root = source.parent.parent
        return root, f"/src/snapshots/{source.name}"
    return source, "/src"


def run_in_docker(args, source: Path | None, output: Path) -> int:
    ensure_image()
    output.mkdir(parents=True, exist_ok=True)
    command = ["docker", "run", "--rm", "--init",
               "-v", f"{REPO_ROOT / 'tools'}:/tool:ro",
               "-v", f"{output}:/out"]
    if os.name != "nt":
        command += ["-u", f"{os.getuid()}:{os.getgid()}"]  # noqa: SLF001 — только POSIX
    inner = ["python", "/tool/prepare_stt_model.py", "--no-docker",
             "--output", "/out", "--quantization", args.quantization,
             "--name", args.name, "--repo", args.repo]
    if source is not None:
        host_dir, inner_source = mount_for(source)
        command += ["-v", f"{host_dir}:/src:ro"]
        inner += ["--source", inner_source]
    else:
        # Качать будем внутри контейнера, но складывать — в обычный кэш
        # пользователя, чтобы второй запуск уже ничего не качал.
        cache = hub_cache_root().parent
        cache.mkdir(parents=True, exist_ok=True)
        # HF_HUB_OFFLINE=0 обязателен. В образе службы речи стоит HF_HUB_OFFLINE=1
        # — и это правильно: рабочая служба в интернет ходить не должна ни при
        # каких условиях. Но здесь тот же образ используется как переносной
        # ctranslate2, и это ЕДИНСТВЕННЫЙ шаг всей поставки, которому интернет
        # нужен по замыслу. Без этой строки скрипт падал английским трейсбеком
        # huggingface_hub ровно там, куда сам же и отправлял инженера.
        # Ставим только в ветке скачивания: когда модель берётся из --source или
        # из кэша, запрет остаётся в силе.
        command += ["-v", f"{cache}:/hf", "-e", "HF_HOME=/hf", "-e", "HF_HUB_OFFLINE=0"]
    command += [IMAGE, *inner]
    log("запускаю подготовку внутри контейнера (своего ctranslate2 на машине нет)")
    return subprocess.run(command).returncode


# --- разбор командной строки ----------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Подготовка модели распознавания речи к поставке без интернета",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path,
                        help="каталог с готовой моделью faster-whisper "
                             "(по умолчанию ищется в кэше huggingface)")
    parser.add_argument("--repo", default=DEFAULT_REPO,
                        help=f"откуда качать, если в кэше нет (по умолчанию {DEFAULT_REPO})")
    parser.add_argument("--name", default=DEFAULT_NAME,
                        help=f"как модель называется в STT_MODEL (по умолчанию {DEFAULT_NAME})")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"куда положить (по умолчанию {DEFAULT_OUTPUT})")
    parser.add_argument("--quantization", default=DEFAULT_QUANTIZATION,
                        choices=("int8", "int8_float32", "int8_float16", "float16", "float32"),
                        help="точность весов на диске (по умолчанию int8)")
    parser.add_argument("--verify", action="store_true",
                        help="только проверить контрольные суммы уже подготовленной модели")
    parser.add_argument("--no-docker", action="store_true",
                        help="не перезапускаться в контейнере: ctranslate2 обязан быть тут")
    args = parser.parse_args(argv)

    output = args.output.resolve()
    try:
        if args.verify:
            return verify(output)

        source = args.source.resolve() if args.source else find_in_cache(args.repo)
        if source is not None and not source.is_dir():
            raise PrepareError(f"нет каталога {source}")

        try:
            import ctranslate2  # noqa: F401
        except ImportError:
            if args.no_docker:
                raise PrepareError(
                    "нет ctranslate2. Либо поставьте его (pip install ctranslate2), "
                    "либо уберите --no-docker и дайте скрипту сделать всё в контейнере."
                ) from None
            if not docker_available():
                raise PrepareError(
                    "нет ни ctranslate2, ни работающего docker. Нужен один из двух: "
                    "запустите Docker Desktop или выполните pip install ctranslate2."
                ) from None
            return run_in_docker(args, source, output)

        if source is None:
            source = download(args.repo)
        prepare(source, output, args.quantization, args.name, args.repo)
        log("\nдальше: docker compose build stt — модель запечётся в образ")
        return 0
    except PrepareError as exc:
        log(f"\nне вышло: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
