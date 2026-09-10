#!/usr/bin/env python3
"""Служба распознавания речи «Хард».

Отдельный контейнер: faster-whisper на CPU, слушает ТОЛЬКО Unix-сокет в общем
томе, принимает один запрос за раз, Bearer-токен читает из файла. В журнал не
попадают ни аудио, ни имена файлов, ни распознанный текст — только код ответа,
язык и длительность.

Проводной контракт (схемы ``praxis.stt.*``) сохранён ради клиента
``server/stt_client.py``: это версия формата ответа, а не имя продукта.

    GET  /healthz            → 200 {"schema":"praxis.stt.health.v1","ready":true,...}
    POST /v1/transcriptions  → 200 {"schema":"praxis.stt.v1","text":"..."}
    ошибки                   → {"schema":"praxis.stt.error.v1","error":{"code","message"}}

Запуск: ``python server.py``. Проверка живости через сокет: ``python server.py --check``.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import math
import os
import signal
import socket
import stat
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from aiohttp import ClientSession, ClientTimeout, UnixConnector, http_exceptions, web

SCHEMA = "praxis.stt.v1"
HEALTH_SCHEMA = "praxis.stt.health.v1"
ERROR_SCHEMA = "praxis.stt.error.v1"

HARD_MAX_BYTES = 15 * 1024 * 1024        # клиент режет на том же пороге
MULTIPART_OVERHEAD = 256 * 1024          # границы и заголовки multipart сверх аудио
LANGUAGES: dict[str, str | None] = {"ru": "ru", "uz": "uz", "auto": None}
TEMP_PREFIX = "hard-stt-"
TEMP_SUFFIX = ".audio"

# Манифест запечённой в образ модели — его пишет tools/prepare_stt_model.py.
MODEL_MANIFEST = "hard-model.json"

log = logging.getLogger("stt")


# --- настройки ---------------------------------------------------------------

class ConfigError(RuntimeError):
    """Служба поднята с небезопасной или неполной конфигурацией."""


class StartupFailed(RuntimeError):
    """Служба стартовала, но работать не сможет: модель не загрузилась."""


def _int_env(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}: ожидается целое число") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{name}: допустимо от {lo} до {hi}")
    return value


def _flag_env(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name}: ожидается 1 или 0")


@dataclass(frozen=True)
class Settings:
    model: str = "large-v3-turbo"
    compute_type: str = "int8"
    device: str = "cpu"
    threads: int = 0                      # 0 = по числу ядер, но не больше 4
    beam_size: int = 5
    models_dir: Path = Path("/models")
    baked_model_dir: Path = Path("/opt/hard/stt-model")
    allow_download: bool = False
    socket: Path = Path("/run/stt/stt.sock")
    token_file: Path = Path("/run/secrets/stt.token")
    token_insecure_ok: bool = False
    max_bytes: int = HARD_MAX_BYTES
    rate_per_minute: int = 6
    burst: int = 2
    upload_timeout: int = 30
    temp_dir: Path = Path("/tmp/stt")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        model = (env.get("STT_MODEL") or "large-v3-turbo").strip()
        return cls(
            model=model,
            compute_type=(env.get("STT_COMPUTE_TYPE") or "int8").strip(),
            device=(env.get("STT_DEVICE") or "cpu").strip(),
            threads=_int_env(env, "STT_THREADS", 0, 0, 64),
            beam_size=_int_env(env, "STT_BEAM_SIZE", 5, 1, 10),
            models_dir=Path((env.get("STT_MODELS_DIR") or "/models").strip()),
            baked_model_dir=Path(
                (env.get("STT_BAKED_MODEL_DIR") or "/opt/hard/stt-model").strip()),
            allow_download=_flag_env(env, "STT_ALLOW_DOWNLOAD"),
            socket=Path((env.get("STT_SOCKET") or "/run/stt/stt.sock").strip()),
            token_file=Path((env.get("STT_TOKEN_FILE") or "/run/secrets/stt.token").strip()),
            token_insecure_ok=_flag_env(env, "STT_TOKEN_INSECURE_OK"),
            max_bytes=_int_env(env, "STT_MAX_BYTES", HARD_MAX_BYTES, 1, HARD_MAX_BYTES),
            rate_per_minute=_int_env(env, "STT_RATE_PER_MINUTE", 6, 1, 120),
            burst=_int_env(env, "STT_BURST", 2, 1, 20),
            upload_timeout=_int_env(env, "STT_UPLOAD_TIMEOUT", 30, 1, 120),
            temp_dir=Path((env.get("STT_TEMP_DIR") or "/tmp/stt").strip()),
        )

    def cpu_threads(self) -> int:
        if self.threads:
            return self.threads
        return max(1, min(4, os.cpu_count() or 1))

    def resolve_model(self) -> str:
        """Что на самом деле отдать faster-whisper: путь к модели или её имя.

        STT_MODEL — это по-прежнему ИМЯ («large-v3-turbo»), а не путь: инженеру
        не надо знать, куда мы её запекли. Здесь имя превращается в путь, если
        в образе лежит ровно эта модель. Так смена модели остаётся правкой одной
        строчки в .env, а запечённая подхватывается сама и без сети.
        """
        asked = self.model
        if not asked:
            raise ConfigError("STT_MODEL пуст")
        if Path(asked).is_dir():                       # явный путь — уважаем как есть
            return asked
        manifest = baked_manifest(self.baked_model_dir)
        if manifest is None:
            return asked
        source = manifest.get("source") or {}
        known = {str(manifest.get("name", "")).lower(), str(source.get("repo", "")).lower()}
        if asked.lower() in known - {""}:
            check_baked_files(self.baked_model_dir, manifest)
            return str(self.baked_model_dir)
        return asked


def baked_manifest(directory: Path) -> dict | None:
    """Манифест запечённой модели или None, если модель в образ не попала."""
    try:
        data = json.loads((directory / MODEL_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("files"), list) else None


def check_baked_files(directory: Path, manifest: dict) -> None:
    """Сверка размеров с манифестом — дешёвая проверка «модель довезли целиком».

    Полный SHA-256 гигабайта весов на каждом старте — это лишние секунды при
    каждом перезапуске; размер ловит ровно тот случай, который в жизни и
    случается: файл скопировали по сети и копирование оборвалось. Кому нужна
    настоящая сверка сумм — ``python tools/prepare_stt_model.py --verify``.
    """
    for item in manifest.get("files", []):
        name = str(item.get("name", ""))
        try:
            size = (directory / name).stat().st_size
        except OSError:
            raise ConfigError(
                f"в запечённой модели нет файла {name} — образ собран с неполной моделью, "
                "проверьте её командой tools/prepare_stt_model.py --verify") from None
        if size != item.get("size"):
            raise ConfigError(
                f"файл модели {name}: {size} байт вместо {item.get('size')} — модель довезена "
                "не целиком; подготовьте её заново (tools/prepare_stt_model.py)")


def _tighten_token(path: Path, *, announce: bool) -> None:
    """Файл токена читается всеми — разобраться, правда ли это, и сузить права.

    ПОЧЕМУ ЗДЕСЬ ЦЕЛАЯ ФУНКЦИЯ, А НЕ ОДНА СТРОЧКА. Проверка «токен не должен
    читаться всеми» на Linux осмысленна и остаётся. Но не всякая файловая
    система вообще хранит права: bind-mount с Windows (drvfs/9p, gRPC-FUSE)
    показывает у любого файла 0777 просто потому, что взять права ему неоткуда.
    Прежний код видел это число, считал его решением администратора и отказывался
    стартовать — служба уходила в вечный цикл перезапусков, а в журнале стояло
    «нужен chmod 640», который ничего не менял. Проверка была верной по букве и
    бессмысленной по сути: она читала цифру, которой никто не устанавливал.

    Отличить настоящие права от выдуманных чтением нельзя — только действием.
    Поэтому мы не спрашиваем «какой режим», а говорим «поставь 0640» и смотрим,
    что вышло. Ответов ровно три, и каждый — это разная ситуация:

      * получилось — права были настоящие, теперь файл закрыт; цель проверки
        достигнута лучше, чем отказом: секрет защищён, и служба работает;
      * chmod не сработал (только чтение, чужой владелец) — права настоящие, а
        починить их мы не можем: отказываемся, как и раньше;
      * chmod «удался», но режим не изменился — файловая система прав не хранит,
        и число 0777 не значит ничего. Отказываться по нему — та же фикция;
        пишем в журнал предупреждение и работаем дальше.

    Аварийный выключатель STT_TOKEN_INSECURE_OK=1 остаётся на четвёртый,
    непредусмотренный случай и кричит об этом в журнал при каждом старте.
    """
    try:
        os.chmod(path, 0o640)
    except OSError as exc:
        raise ConfigError(
            "файл токена доступен всем на чтение, и сузить права не вышло "
            f"({type(exc).__name__}) — нужен chmod 640 на файле {path}") from exc
    if not path.lstat().st_mode & 0o004:
        if announce:
            log.warning("файл токена был доступен всем на чтение; права сужены до 0640")
        return
    if announce:
        log.warning("файловая система не хранит права доступа (chmod ничего не меняет): "
                    "режим 0%o у файла токена ничего не означает и не проверяется. "
                    "Так бывает у bind-mount с Windows. Секрет здесь защищён только тем, "
                    "кто вообще может добраться до этого каталога.",
                    stat.S_IMODE(path.lstat().st_mode))


def read_token(path: Path, *, insecure_ok: bool = False, announce: bool = True) -> str:
    """Токен: обычный файл, не читаемый всеми, 32..4096 печатных ASCII-символов."""
    try:
        info = path.lstat()
    except OSError as exc:
        raise ConfigError(f"файл токена недоступен: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ConfigError("файл токена должен быть обычным файлом, не ссылкой")
    if os.name == "posix" and info.st_mode & 0o004:
        if insecure_ok:
            if announce:
                log.error("ВНИМАНИЕ: STT_TOKEN_INSECURE_OK=1 — проверка прав на файле токена "
                          "выключена вручную, файл читается всеми. Это временная мера.")
        else:
            _tighten_token(path, announce=announce)
    if not 32 <= info.st_size <= 4096:
        raise ConfigError("длина токена вне допустимого диапазона (32..4096 байт)")
    try:
        value = path.read_bytes().decode("ascii").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError("токен не читается как ASCII") from exc
    if len(value) < 32 or any(not 33 <= ord(ch) <= 126 for ch in value):
        raise ConfigError("токен содержит недопустимые символы или слишком короткий")
    return value


# --- ограничитель частоты ------------------------------------------------------

class TokenBucket:
    """rate запросов в минуту с запасом burst; отказ возвращает секунды ожидания."""

    def __init__(self, rate_per_minute: int, burst: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._rate = rate_per_minute / 60.0
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._clock = clock
        self._stamp = clock()

    def take(self) -> int:
        """0 — пропущен; иначе через сколько секунд появится следующий слот."""
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + max(0.0, now - self._stamp) * self._rate)
        self._stamp = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0
        return max(1, math.ceil((1.0 - self._tokens) / self._rate))


# --- распознаватель ------------------------------------------------------------

class BackendBusy(RuntimeError):
    """Единственный рабочий поток занят другим запросом."""


class BackendNotReady(RuntimeError):
    """Модель ещё грузится (или не загрузилась)."""


class BackendFailed(RuntimeError):
    """Аудио не удалось декодировать или распознать."""


class Backend(Protocol):
    @property
    def ready(self) -> bool: ...

    @property
    def busy(self) -> bool: ...

    def transcribe(self, path: Path, language: str | None) -> str: ...


class WhisperBackend:
    """faster-whisper с одним рабочим потоком; модель грузится один раз при старте."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self._load_error: str | None = None
        self._run_lock = threading.Lock()

    def load(self) -> None:
        """Блокирующая загрузка модели: из образа, а при STT_ALLOW_DOWNLOAD=1 — из сети."""
        settings = self.settings
        source = settings.resolve_model()
        # Запрет на сеть выставляем ДО импорта: huggingface_hub читает эти
        # переменные один раз, когда его импортируют, и позже менять их поздно.
        # Двойная страховка (переменная окружения + local_files_only в вызове)
        # тут не паранойя: обе выключают разные слои — сам hub и обёртку
        # faster-whisper, и любой из них поодиночке может однажды передумать.
        os.environ["HF_HUB_OFFLINE"] = "0" if settings.allow_download else "1"
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover — в образе пакет есть всегда
            self._load_error = "faster-whisper не установлен"
            raise ConfigError(self._load_error) from exc
        started = time.monotonic()
        log.info("модель %s (%s, %s, потоков %s, %s): загрузка", settings.model,
                 settings.device, settings.compute_type, settings.cpu_threads(),
                 "из образа" if source != settings.model else "из кэша")
        try:
            self._model = self._open(WhisperModel, source)
        except Exception as exc:  # noqa: BLE001 — причина уходит в журнал без путей
            log.error("модель не загрузилась: %s", type(exc).__name__)
            spare = self._spare_model(source)
            if spare is None:
                self._load_error = type(exc).__name__
                self._explain_failure(source)
                raise
            log.warning("беру модель, запечённую в образ, вместо «%s»: её не нашлось ни в "
                        "образе, ни в кэше, а качать запрещено. Служба речи будет работать, "
                        "но моделью «%s». Хотите именно «%s» — либо подготовьте её "
                        "(tools/prepare_stt_model.py --name %s) и пересоберите образ, либо "
                        "разрешите скачивание: STT_ALLOW_DOWNLOAD=1.",
                        settings.model, spare[1], settings.model, settings.model)
            try:
                self._model = self._open(WhisperModel, spare[0])
            except Exception as second:  # noqa: BLE001
                self._load_error = type(second).__name__
                log.error("запечённая модель тоже не загрузилась: %s", type(second).__name__)
                raise
        log.info("модель готова за %.0f с", time.monotonic() - started)

    def _open(self, whisper_model, source: str):
        settings = self.settings
        return whisper_model(
            source,
            device=settings.device,
            compute_type=settings.compute_type,
            cpu_threads=settings.cpu_threads(),
            num_workers=1,
            download_root=str(settings.models_dir),
            local_files_only=not settings.allow_download,
        )

    def _spare_model(self, source: str) -> tuple[str, str] | None:
        """Запечённая модель как замена ненайденной — (путь, имя) или None.

        ЗАЧЕМ ПОДМЕНА, А НЕ ЧЕСТНЫЙ ОТКАЗ. Имя модели живёт в .env единицы речи,
        а модель — в образе; разъехаться им проще простого (например, в .env
        остался старый STT_MODEL от прошлой сборки). Отказ означает «голоса нет
        совсем» и разбирательство на объекте; подмена означает «голос работает,
        но не той моделью» и строчку в журнале, которая прямо говорит, что
        произошло и как это поправить. Для системы, которую разворачивает чужая
        ИТ-служба, второе лучше.

        Порядок важен: сюда попадают только ПОСЛЕ того, как faster-whisper не
        нашёл запрошенную модель. Значит подмена не перебивает ни явный путь, ни
        модель, заранее положенную в кэш /models, — она только заполняет пустоту.
        """
        settings = self.settings
        if settings.allow_download or source == str(settings.baked_model_dir):
            return None
        manifest = baked_manifest(settings.baked_model_dir)
        if manifest is None:
            return None
        try:
            check_baked_files(settings.baked_model_dir, manifest)
        except ConfigError as exc:
            log.error("запечённая модель тоже не годится: %s", exc)
            return None
        return str(settings.baked_model_dir), str(manifest.get("name", "?"))

    def _explain_failure(self, source: str) -> None:
        """Заменить нечем: сказать словами, что делать дальше."""
        settings = self.settings
        if settings.allow_download:
            log.error("скачивание разрешено (STT_ALLOW_DOWNLOAD=1), но модель не пришла — "
                      "проверьте, доступен ли с этой машины huggingface.co.")
            return
        if source != str(settings.baked_model_dir):
            log.error("в образе модели нет, а скачивание выключено. Подготовьте модель "
                      "командой `python tools/prepare_stt_model.py` и пересоберите образ "
                      "службы речи (docker compose build stt).")
        else:
            log.error("модель в образе есть, но открыть её не удалось. Сверьте файлы: "
                      "`python tools/prepare_stt_model.py --verify` — и, если ругается, "
                      "подготовьте модель заново и пересоберите образ.")

    @property
    def ready(self) -> bool:
        return self._model is not None

    @property
    def busy(self) -> bool:
        return self._run_lock.locked()

    def transcribe(self, path: Path, language: str | None) -> str:
        model = self._model
        if model is None:
            raise BackendNotReady(self._load_error or "модель не готова")
        if not self._run_lock.acquire(blocking=False):
            raise BackendBusy("занято")
        try:
            segments, _info = model.transcribe(
                str(path),
                language=language,
                beam_size=self.settings.beam_size,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            parts = [str(seg.text).strip() for seg in segments if str(getattr(seg, "text", "")).strip()]
        except Exception as exc:  # noqa: BLE001
            raise BackendFailed(type(exc).__name__) from exc
        finally:
            self._run_lock.release()
        return " ".join(parts)


# --- HTTP-служба ---------------------------------------------------------------

class _Reject(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.retry_after = status, code, message, retry_after


class Service:
    """Приложение aiohttp: два маршрута, один запрос в работе, токен из файла."""

    def __init__(self, settings: Settings, backend: Backend, token: str,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self.backend = backend
        self._token = token
        self._bucket = TokenBucket(settings.rate_per_minute, settings.burst, clock)
        self._inflight = False
        self.app = web.Application(client_max_size=settings.max_bytes + MULTIPART_OVERHEAD)
        self.app.router.add_get("/healthz", self.health)
        self.app.router.add_post("/v1/transcriptions", self.transcribe)

    # -- служебное --
    def _authorized(self, request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        presented = header[len("Bearer "):]
        return bool(presented) and hmac.compare_digest(presented, self._token)

    @staticmethod
    def _error(request_id: str, status: int, code: str, message: str,
               retry_after: int | None = None) -> web.Response:
        headers: dict[str, str] = {}
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        if status == 401:
            headers["WWW-Authenticate"] = "Bearer"
        return web.json_response(
            {"schema": ERROR_SCHEMA, "request_id": request_id,
             "error": {"code": code, "message": message}},
            status=status, headers=headers)

    # -- маршруты --
    async def health(self, request: web.Request) -> web.Response:
        request_id = uuid.uuid4().hex
        if not self._authorized(request):
            return self._error(request_id, 401, "unauthorized", "valid bearer token required")
        ready = bool(self.backend.ready)
        busy = bool(self._inflight or self.backend.busy)
        return web.json_response(
            {"schema": HEALTH_SCHEMA, "status": "ready" if ready else "warming",
             "ready": ready, "busy": busy},
            status=200 if ready else 503)

    async def transcribe(self, request: web.Request) -> web.Response:
        request_id = uuid.uuid4().hex
        if not self._authorized(request):
            return self._error(request_id, 401, "unauthorized", "valid bearer token required")
        if not self.backend.ready:
            return self._error(request_id, 503, "warming", "speech model is not ready", 2)
        if self._inflight or self.backend.busy:
            return self._error(request_id, 429, "busy", "speech worker is busy", 2)
        wait = self._bucket.take()
        if wait:
            return self._error(request_id, 429, "rate_limited", "speech request rate exceeded", wait)

        self._inflight = True
        started = time.monotonic()
        status = 500
        language = "unknown"
        temp: Path | None = None
        try:
            try:
                temp, language = await asyncio.wait_for(
                    self._receive(request), timeout=self.settings.upload_timeout)
            except TimeoutError:
                raise _Reject(408, "upload_timeout", "audio upload timed out") from None
            job = asyncio.create_task(asyncio.to_thread(
                self.backend.transcribe, temp, LANGUAGES[language]))
            try:
                text = await asyncio.shield(job)
            except asyncio.CancelledError:
                # Поток не отменить: дождаться, чтобы не удалить файл у него из-под ног.
                try:
                    await job
                except Exception:  # noqa: BLE001
                    pass
                raise
            status = 200
            return web.json_response(
                {"schema": SCHEMA, "request_id": request_id, "text": text, "language": language})
        except _Reject as exc:
            status = exc.status
            return self._error(request_id, exc.status, exc.code, exc.message, exc.retry_after)
        except BackendNotReady:
            status = 503
            return self._error(request_id, 503, "warming", "speech model is not ready", 2)
        except BackendBusy:
            status = 429
            return self._error(request_id, 429, "busy", "speech worker is busy", 2)
        except BackendFailed:
            status = 422
            return self._error(request_id, 422, "transcription_failed",
                               "audio could not be transcribed")
        except asyncio.CancelledError:
            status = 499
            raise
        except Exception as exc:  # noqa: BLE001
            status = 500
            log.warning("запрос %s: сбой %s", request_id, type(exc).__name__)
            return self._error(request_id, 500, "internal_error", "speech service failed")
        finally:
            if temp is not None:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    log.warning("запрос %s: временный файл не удалён", request_id)
            self._inflight = False
            log.info("запрос %s: status=%d language=%s duration_ms=%d", request_id, status,
                     language, int((time.monotonic() - started) * 1000))

    # -- приём тела --
    async def _receive(self, request: web.Request) -> tuple[Path, str]:
        if request.content_type != "multipart/form-data":
            raise _Reject(415, "unsupported_media_type", "multipart/form-data required")
        limit = self.settings.max_bytes
        if request.content_length is not None and request.content_length > limit + MULTIPART_OVERHEAD:
            raise _Reject(413, "audio_too_large", "audio exceeds size limit")
        try:
            reader = await request.multipart()
        except (ValueError, web.HTTPBadRequest, http_exceptions.HttpProcessingError) as exc:
            raise _Reject(400, "invalid_multipart", "invalid multipart body") from exc

        temp: Path | None = None
        language: str | None = None
        try:
            try:
                while (part := await reader.next()) is not None:
                    if part.name == "audio":
                        if temp is not None:
                            raise _Reject(400, "duplicate_audio",
                                          "exactly one audio field required")
                        temp = await self._spool(part, limit)
                    elif part.name == "language":
                        if language is not None:
                            raise _Reject(400, "duplicate_language",
                                          "exactly one language field required")
                        language = (await self._small(part, 16)).decode("ascii").strip().lower()
                    else:
                        raise _Reject(400, "unexpected_field",
                                      "only audio and language fields are accepted")
            except UnicodeError as exc:
                raise _Reject(400, "invalid_language",
                              "language must be ru, uz, or auto") from exc
            except (ValueError, web.HTTPBadRequest, http_exceptions.HttpProcessingError) as exc:
                raise _Reject(400, "invalid_multipart", "invalid multipart body") from exc
            if temp is None:
                raise _Reject(400, "missing_audio", "audio field is required")
            if language is None:
                raise _Reject(400, "missing_language", "language field is required")
            if language not in LANGUAGES:
                raise _Reject(400, "invalid_language", "language must be ru, uz, or auto")
            return temp, language
        except BaseException:
            if temp is not None:
                temp.unlink(missing_ok=True)
            raise

    async def _spool(self, part, limit: int) -> Path:
        """Аудио — во временный файл 0600 в temp_dir (tmpfs), не дальше limit байт."""
        fd, raw = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX,
                                   dir=str(self.settings.temp_dir))
        path = Path(raw)
        total = 0
        try:
            os.chmod(path, 0o600)
            with os.fdopen(fd, "wb") as target:
                while chunk := await part.read_chunk(size=64 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise _Reject(413, "audio_too_large", "audio exceeds size limit")
                    target.write(chunk)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        if total == 0:
            path.unlink(missing_ok=True)
            raise _Reject(400, "empty_audio", "audio field is empty")
        return path

    @staticmethod
    async def _small(part, maximum: int) -> bytes:
        value = bytearray()
        while chunk := await part.read_chunk(size=64 * 1024):
            value.extend(chunk)
            if len(value) > maximum:
                raise _Reject(400, "field_too_large", "multipart field is too large")
        return bytes(value)


# --- сокет и жизненный цикл ----------------------------------------------------

def _is_socket(path: Path) -> bool:
    try:
        return stat.S_ISSOCK(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def clear_stale_socket(path: Path) -> None:
    """Файл сокета от прошлого процесса убираем; живой сокет — повод остановиться."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode):
        raise ConfigError(f"по пути сокета лежит не сокет: {path}")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.2)
    try:
        probe.connect(str(path))
    except (ConnectionRefusedError, FileNotFoundError):
        path.unlink(missing_ok=True)
        return
    except OSError as exc:
        raise ConfigError("не удалось проверить существующий сокет") from exc
    finally:
        probe.close()
    raise ConfigError("сокет уже обслуживается другим процессом")


def prepare_temp_dir(path: Path) -> int:
    """Каталог 0700; чужие файлы не трогаем, свои недоделанные — убираем."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)      # tmpfs из compose приходит с 1777
    except OSError:
        pass
    removed = 0
    for candidate in path.iterdir():
        if candidate.name.startswith(TEMP_PREFIX) and candidate.name.endswith(TEMP_SUFFIX) \
                and candidate.is_file() and not candidate.is_symlink():
            candidate.unlink(missing_ok=True)
            removed += 1
    return removed


async def serve(settings: Settings, backend: Backend | None = None,
                token: str | None = None) -> None:
    if token is None:
        token = read_token(settings.token_file, insecure_ok=settings.token_insecure_ok)
    if backend is None:
        # Разбираемся с моделью ДО того, как открыли сокет: обрезанный файл или
        # имя, которого нет ни в образе, ни в кэше, — это ошибка конфигурации.
        # Пусть контейнер выйдет с внятным сообщением, а не встанет «прогревающимся»
        # навсегда, заставляя инженера ждать и гадать.
        settings.resolve_model()
        backend = WhisperBackend(settings)
    service = Service(settings, backend, token)

    settings.socket.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    clear_stale_socket(settings.socket)
    removed = prepare_temp_dir(settings.temp_dir)
    if removed:
        log.info("убрано недоделанных временных файлов: %d", removed)

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    failure: list[BaseException] = []
    loader = None
    if isinstance(backend, WhisperBackend):
        loader = loop.run_in_executor(None, backend.load)

        def _loaded(task) -> None:
            """Модель не загрузилась — служба останавливается, а не «греется» вечно.

            Раньше сокет продолжал отвечать «warming», и снаружи это выглядело
            одинаково и когда модель ещё грузится, и когда её нет и не будет:
            healthcheck молчал весь start-period, инженер ждал. Работать без
            модели служба всё равно не может, а ядро отсутствие речи переживает
            (сотрудник вводит текст руками). Значит честнее упасть сразу — тогда
            и compose, и install.sh покажут причину в первую же минуту.
            """
            if not task.cancelled() and task.exception() is not None:
                failure.append(task.exception())
                loop.call_soon_threadsafe(stop.set)

        loader.add_done_callback(_loaded)

    runner = web.AppRunner(service.app, access_log=None)
    await runner.setup()
    site = web.UnixSite(runner, str(settings.socket))
    await site.start()
    os.chmod(settings.socket, 0o660)
    log.info("служба распознавания речи слушает %s (модель %s)", settings.socket, settings.model)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # pragma: no cover — не POSIX
            pass
    try:
        await stop.wait()
    finally:
        log.info("остановка")
        await runner.cleanup()
        if _is_socket(settings.socket):
            settings.socket.unlink(missing_ok=True)
        if loader is not None and not loader.done():
            loader.cancel()
    if failure:
        raise StartupFailed(
            f"служба остановлена: модель не загрузилась ({type(failure[0]).__name__}), "
            "причина строкой выше")


async def check(settings: Settings) -> int:
    """Проверка живости через сокет с токеном: 0 — готова, 1 — нет."""
    try:
        # announce=False: проверка живости бежит каждые 30 секунд, и предупреждение
        # о правах, уместное один раз при старте, в журнале превратилось бы в шум.
        token = read_token(settings.token_file, insecure_ok=settings.token_insecure_ok,
                           announce=False)
    except ConfigError as exc:
        print(f"токен: {exc}")
        return 1
    try:
        async with ClientSession(connector=UnixConnector(path=str(settings.socket)),
                                 timeout=ClientTimeout(total=8),
                                 headers={"Authorization": f"Bearer {token}"}) as session:
            async with session.get("http://stt/healthz") as response:
                payload = await response.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        print(f"недоступна: {type(exc).__name__}")
        return 1
    if response.status == 200 and payload.get("ready") is True:
        print("готова")
        return 0
    print(f"не готова: {payload.get('status', response.status)}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Служба распознавания речи «Хард»")
    parser.add_argument("--check", action="store_true",
                        help="проверить готовность через сокет и выйти")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = Settings.from_env()
        if args.check:
            return asyncio.run(check(settings))
        asyncio.run(serve(settings))
    except ConfigError as exc:
        log.error("конфигурация: %s", exc)
        return 2
    except StartupFailed as exc:
        log.error("%s", exc)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
