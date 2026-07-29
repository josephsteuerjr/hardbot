"""Клиент общего Whisper-контура Praxis через authenticated Unix socket.

Hardbot не загружает модель и не имеет автоматического local fallback. В запрос
не передаются telegram_id, ФИО, КД, имя исходного файла или текст формы: только
байты аудио и язык ``ru|uz|auto``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import aiohttp


MAX_AUDIO_BYTES = 15 * 1024 * 1024
LANGUAGES = {"ru", "uz", "auto"}
DEFAULT_BASE_URL = "http://praxis-stt"


@dataclass(slots=True)
class STTError(RuntimeError):
    """Проверяемая ошибка удалённого STT без утечки ответа/секрета в текст."""

    message: str
    status: int | None = None
    retryable: bool = False
    retry_after: int | None = None

    def __str__(self) -> str:
        return self.message


class STTClient:
    def __init__(
        self,
        *,
        token_file: str | Path,
        unix_socket: str | Path | None,
        timeout_seconds: float = 60.0,
        connect_timeout_seconds: float = 1.0,
        base_url: str = DEFAULT_BASE_URL,
        connector_factory: Callable[[], aiohttp.BaseConnector] | None = None,
    ):
        self.token_file = Path(token_file)
        self.unix_socket = Path(unix_socket) if unix_socket is not None else None
        self.timeout_seconds = float(timeout_seconds)
        self.connect_timeout_seconds = float(connect_timeout_seconds)
        self.base_url = base_url.rstrip("/")
        self._connector_factory = connector_factory

    def _token(self) -> str:
        try:
            raw = self.token_file.read_bytes()
        except OSError as exc:
            raise STTError(
                "Praxis STT token недоступен",
                retryable=True,
            ) from exc
        try:
            token = raw.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise STTError("Praxis STT token имеет неверный формат") from exc
        if (
            len(token) < 32
            or any(ord(char) < 33 or ord(char) > 126 for char in token)
        ):
            raise STTError("Praxis STT token имеет неверный формат")
        return token

    def _connector(self) -> aiohttp.BaseConnector:
        if self._connector_factory is not None:
            return self._connector_factory()
        if self.unix_socket is None:
            return aiohttp.TCPConnector()
        return aiohttp.UnixConnector(path=str(self.unix_socket))

    def _timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(
            total=self.timeout_seconds,
            connect=self.connect_timeout_seconds,
            sock_connect=self.connect_timeout_seconds,
            sock_read=self.timeout_seconds,
        )

    @staticmethod
    async def _payload(response: aiohttp.ClientResponse) -> dict:
        try:
            payload = await response.json(content_type=None)
        except (aiohttp.ClientError, json.JSONDecodeError, ValueError) as exc:
            raise STTError(
                "Praxis STT вернул некорректный ответ",
                status=response.status,
                retryable=response.status >= 500,
            ) from exc
        if not isinstance(payload, dict):
            raise STTError(
                "Praxis STT вернул некорректный ответ",
                status=response.status,
                retryable=response.status >= 500,
            )
        return payload

    @staticmethod
    def _retry_after(response: aiohttp.ClientResponse) -> int | None:
        try:
            return max(1, min(int(response.headers.get("Retry-After", "")), 300))
        except (TypeError, ValueError):
            return None

    async def health(self) -> dict:
        """Authenticated readiness, пригодна для deploy-canary."""
        headers = {"Authorization": f"Bearer {self._token()}"}
        try:
            async with aiohttp.ClientSession(
                connector=self._connector(),
                connector_owner=True,
                timeout=self._timeout(),
                headers=headers,
            ) as session:
                async with session.get(self.base_url + "/healthz") as response:
                    payload = await self._payload(response)
                    if (
                        response.status == 200
                        and payload.get("schema") == "praxis.stt.health.v1"
                        and payload.get("ready") is True
                    ):
                        return payload
                    raise STTError(
                        "Praxis STT ещё не готов",
                        status=response.status,
                        retryable=response.status in {429, 503},
                        retry_after=self._retry_after(response),
                    )
        except STTError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise STTError(
                "Praxis STT недоступен",
                retryable=True,
            ) from exc

    async def transcribe(self, audio_path: str | Path, language: str) -> str:
        language = str(language or "auto").strip().casefold()
        if language not in LANGUAGES:
            raise STTError("Недопустимый язык STT")
        path = Path(audio_path)
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise STTError("Аудиофайл недоступен") from exc
        if size <= 0:
            raise STTError("Аудиофайл пуст")
        if size > MAX_AUDIO_BYTES:
            raise STTError("Аудиофайл превышает 15 MiB", status=413)

        headers = {"Authorization": f"Bearer {self._token()}"}
        form = aiohttp.FormData()
        # Имя намеренно константное: временный путь hardbot и исходное имя
        # браузерного файла не должны пересекать процессную границу Praxis.
        stream = path.open("rb")
        form.add_field(
            "audio",
            stream,
            filename="audio.bin",
            content_type="application/octet-stream",
        )
        form.add_field("language", language)
        try:
            async with aiohttp.ClientSession(
                connector=self._connector(),
                connector_owner=True,
                timeout=self._timeout(),
                headers=headers,
            ) as session:
                async with session.post(
                    self.base_url + "/v1/transcriptions",
                    data=form,
                ) as response:
                    payload = await self._payload(response)
                    if (
                        response.status == 200
                        and payload.get("schema") == "praxis.stt.v1"
                        and isinstance(payload.get("text"), str)
                    ):
                        return payload["text"].strip()
                    retryable = response.status in {408, 429, 503} or (
                        response.status >= 500
                    )
                    raise STTError(
                        "Praxis STT не распознал аудио"
                        if response.status == 422
                        else "Praxis STT временно недоступен"
                        if retryable
                        else "Praxis STT отклонил аудио",
                        status=response.status,
                        retryable=retryable,
                        retry_after=self._retry_after(response),
                    )
        except STTError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise STTError(
                "Praxis STT недоступен",
                retryable=True,
            ) from exc
        finally:
            stream.close()
