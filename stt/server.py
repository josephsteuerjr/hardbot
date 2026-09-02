#!/usr/bin/env python3
"""Служба распознавания речи «Выезды».

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
TEMP_PREFIX = "vyezdy-stt-"
TEMP_SUFFIX = ".audio"

log = logging.getLogger("stt")


# --- настройки ---------------------------------------------------------------

class ConfigError(RuntimeError):
    """Служба поднята с небезопасной или неполной конфигурацией."""


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


@dataclass(frozen=True)
class Settings:
    model: str = "small"
    compute_type: str = "int8"
    device: str = "cpu"
    threads: int = 0                      # 0 = по числу ядер, но не больше 4
    beam_size: int = 5
    models_dir: Path = Path("/models")
    socket: Path = Path("/run/stt/stt.sock")
    token_file: Path = Path("/run/secrets/stt.token")
    max_bytes: int = HARD_MAX_BYTES
    rate_per_minute: int = 6
    burst: int = 2
    upload_timeout: int = 30
    temp_dir: Path = Path("/tmp/stt")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        model = (env.get("STT_MODEL") or "small").strip()
        return cls(
            model=model,
            compute_type=(env.get("STT_COMPUTE_TYPE") or "int8").strip(),
            device=(env.get("STT_DEVICE") or "cpu").strip(),
            threads=_int_env(env, "STT_THREADS", 0, 0, 64),
            beam_size=_int_env(env, "STT_BEAM_SIZE", 5, 1, 10),
            models_dir=Path((env.get("STT_MODELS_DIR") or "/models").strip()),
            socket=Path((env.get("STT_SOCKET") or "/run/stt/stt.sock").strip()),
            token_file=Path((env.get("STT_TOKEN_FILE") or "/run/secrets/stt.token").strip()),
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


def read_token(path: Path) -> str:
    """Токен: обычный файл, не читаемый всеми, 32..4096 печатных ASCII-символов."""
    try:
        info = path.lstat()
    except OSError as exc:
        raise ConfigError(f"файл токена недоступен: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ConfigError("файл токена должен быть обычным файлом, не ссылкой")
    if os.name == "posix" and info.st_mode & 0o004:
        raise ConfigError("файл токена доступен всем на чтение — нужен chmod 640")
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
        """Блокирующая загрузка (первый запуск скачивает модель в models_dir)."""
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover — в образе пакет есть всегда
            self._load_error = "faster-whisper не установлен"
            raise ConfigError(self._load_error) from exc
        settings = self.settings
        started = time.monotonic()
        log.info("модель %s (%s, %s, потоков %s): загрузка", settings.model,
                 settings.device, settings.compute_type, settings.cpu_threads())
        try:
            self._model = WhisperModel(
                settings.model,
                device=settings.device,
                compute_type=settings.compute_type,
                cpu_threads=settings.cpu_threads(),
                num_workers=1,
                download_root=str(settings.models_dir),
            )
        except Exception as exc:  # noqa: BLE001 — причина уходит в журнал без путей
            self._load_error = type(exc).__name__
            log.error("модель не загрузилась: %s", type(exc).__name__)
            raise
        log.info("модель готова за %.0f с", time.monotonic() - started)

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
    token = token if token is not None else read_token(settings.token_file)
    backend = backend if backend is not None else WhisperBackend(settings)
    service = Service(settings, backend, token)

    settings.socket.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    clear_stale_socket(settings.socket)
    removed = prepare_temp_dir(settings.temp_dir)
    if removed:
        log.info("убрано недоделанных временных файлов: %d", removed)

    loop = asyncio.get_running_loop()
    loader = None
    if isinstance(backend, WhisperBackend):
        loader = loop.run_in_executor(None, backend.load)

    runner = web.AppRunner(service.app, access_log=None)
    await runner.setup()
    site = web.UnixSite(runner, str(settings.socket))
    await site.start()
    os.chmod(settings.socket, 0o660)
    log.info("служба распознавания речи слушает %s (модель %s)", settings.socket, settings.model)

    stop = asyncio.Event()
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


async def check(settings: Settings) -> int:
    """Проверка живости через сокет с токеном: 0 — готова, 1 — нет."""
    try:
        token = read_token(settings.token_file)
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
    parser = argparse.ArgumentParser(description="Служба распознавания речи «Выезды»")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
