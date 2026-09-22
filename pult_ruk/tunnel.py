"""Быстрый тоннель cloudflared: пульт с ПК становится виден телефону.

Почему так, а не иначе. Руководитель смотрит сводку с телефона, но пульт живёт
на его ПК вместе с токеном «Харда». Пробрасывать порт наружу руками, ставить
VPN или поднимать своё доменное имя он не будет — а `cloudflared tunnel --url`
не требует ни аккаунта, ни настройки: подпроцесс печатает в вывод адрес вида
`https://<случайное>.trycloudflare.com`, и этого достаточно.

Тоннель живёт ровно столько, сколько живёт пульт. Погас пульт — адрес умер, и
никакой оставшийся в истории телефона ярлык уже никуда не ведёт. Поэтому
гасим и в обычном выходе, и в аварийном: `finally` в сервере плюс `atexit`
здесь. Осиротевший cloudflared — это открытая наружу дверь в портфель
направления, про которую все забыли.

Отсутствие бинарника — не исключение, а состояние: кнопка «Включить телефон»
никуда не девается, человеку пишется одной строкой, что поставить.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

# Адрес быстрого тоннеля в выводе cloudflared. Он печатает его в рамке из
# плюсов и минусов, поэтому ищем не строку целиком, а сам адрес.
URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com", re.I)

WINGET = "winget install --id Cloudflare.cloudflared"
NO_BINARY = ("Для показа на телефоне нужна программа cloudflared, а её на этом "
             "компьютере нет. Установите одной командой в терминале: "
             f"{WINGET} — и нажмите «Включить телефон» снова.")

# Куда winget и установщик кладут cloudflared, если PATH ещё не перечитан
# (после установки открытая консоль и запущенный пульт про новый PATH не знают).
WIN_GUESSES = (
    r"%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe",
    r"%ProgramFiles%\cloudflared\cloudflared.exe",
    r"%ProgramFiles(x86)%\cloudflared\cloudflared.exe",
    r"%USERPROFILE%\.cloudflared\cloudflared.exe",
)

# Живые подпроцессы: их гасит atexit, если пульт умер мимо своего finally.
_LIVE: set[subprocess.Popen] = set()


class TunnelTrouble(Exception):
    """Почему телефон не включился — сразу текстом для человека."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


def find_binary() -> str | None:
    """Путь к cloudflared или None. Сначала PATH, потом привычные места."""
    found = shutil.which("cloudflared")
    if found:
        return found
    if os.name == "nt":
        for guess in WIN_GUESSES:
            path = Path(os.path.expandvars(guess))
            if "%" not in str(path) and path.is_file():
                return str(path)
    return None


def extract_url(text: str) -> str:
    """Адрес тоннеля из строки вывода; пусто — если в ней его нет."""
    found = URL_RE.search(text or "")
    return found.group(0) if found else ""


def _kill(proc: subprocess.Popen) -> None:
    """Погасить подпроцесс, не разбираясь, жив ли он: закрытая дверь важнее
    аккуратности. Ошибки глотаем — это уже уборка за собой."""
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    except Exception:       # noqa: BLE001 — на выходе некому показывать ошибку
        pass
    _LIVE.discard(proc)


@atexit.register
def _kill_all() -> None:
    for proc in list(_LIVE):
        _kill(proc)


class Tunnel:
    """Один тоннель на пульт. Можно включать и гасить сколько угодно раз."""

    def __init__(self, port: int, binary: str | None = None,
                 argv: list[str] | None = None, wait: float = 45.0) -> None:
        self.port = port
        self.binary = binary
        self.argv = argv            # подмена команды целиком — для тестов
        self.wait = wait
        self.url = ""
        self.error = ""
        self._proc: subprocess.Popen | None = None
        self._lines: list[str] = []
        self._found = threading.Event()

    # --- состояние для экрана ---------------------------------------------
    @property
    def on(self) -> bool:
        return bool(self._proc and self._proc.poll() is None and self.url)

    @property
    def available(self) -> bool:
        """Есть ли чем поднимать тоннель. Кнопку это не прячет — только
        подписывает: пользователь должен видеть, что функция существует."""
        return bool(self.argv or self.binary or find_binary())

    def state(self) -> dict:
        return {"on": self.on, "url": self.url if self.on else "",
                "ready": self.available, "error": self.error}

    # --- запуск и остановка ------------------------------------------------
    def _command(self) -> list[str]:
        if self.argv:
            return list(self.argv)
        binary = self.binary or find_binary()
        if not binary:
            raise TunnelTrouble(NO_BINARY)
        self.binary = binary
        return [binary, "tunnel", "--url", f"http://127.0.0.1:{self.port}",
                "--no-autoupdate"]

    def _read(self, proc: subprocess.Popen) -> None:
        """Читатель вывода в отдельной нитке: cloudflared пишет адрес в stderr
        и продолжает писать туда всю жизнь. Не читать — значит однажды упереться
        в переполненный буфер трубы и подвесить тоннель."""
        stream = proc.stdout
        if stream is None:
            return
        for raw in stream:
            line = raw.rstrip()
            self._lines.append(line)
            del self._lines[:-60]        # хвост для диагностики, не журнал
            if not self.url:
                found = extract_url(line)
                if found:
                    self.url = found
                    self._found.set()
        self._found.set()                # процесс кончился — ждать больше нечего

    def _spawn(self) -> subprocess.Popen:
        flags = 0
        if os.name == "nt":
            # Пульт запускают ярлыком .pyw, без консоли. Без этого флага у
            # cloudflared на пол-экрана выскакивает чёрное окно, которое
            # руководитель закроет первым же движением — вместе с тоннелем.
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(
            self._command(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace", bufsize=1, creationflags=flags)

    async def start(self) -> str:
        """Поднять тоннель и дождаться адреса. Беда — TunnelTrouble с текстом."""
        if self.on:
            return self.url
        await self.stop()
        self.url = ""
        self.error = ""
        self._lines = []
        self._found = threading.Event()
        try:
            proc = await asyncio.to_thread(self._spawn)
        except TunnelTrouble as trouble:
            self.error = trouble.text
            raise
        except OSError as exc:
            self.error = (f"Не удалось запустить cloudflared: {exc}. "
                          f"Переустановите его: {WINGET}")
            raise TunnelTrouble(self.error)
        self._proc = proc
        _LIVE.add(proc)
        threading.Thread(target=self._read, args=(proc,), daemon=True,
                         name="cloudflared-вывод").start()
        await asyncio.to_thread(self._found.wait, self.wait)
        if self.url:
            return self.url
        tail = " ".join(self._lines[-3:])[:200]
        if proc.poll() is not None:
            self.error = ("cloudflared запустился и сразу закрылся. "
                          + (f"Последнее, что он сказал: {tail}" if tail
                             else "Проверьте, что он установлен целиком: " + WINGET))
        else:
            self.error = ("cloudflared не выдал адрес за "
                          f"{int(self.wait)} секунд — похоже, у компьютера нет "
                          "интернета. Попробуйте ещё раз.")
        await self.stop()
        raise TunnelTrouble(self.error)

    async def stop(self) -> None:
        """Погасить. Вызывается сколько угодно раз, в том числе на мёртвом."""
        proc, self._proc = self._proc, None
        self.url = ""
        if proc is not None:
            await asyncio.to_thread(_kill, proc)

    def stop_now(self) -> None:
        """То же самое, но без цикла событий — для аварийного выхода."""
        proc, self._proc = self._proc, None
        self.url = ""
        if proc is not None:
            _kill(proc)


def hint() -> str:
    """Что сказать человеку, у которого cloudflared нет."""
    return NO_BINARY if find_binary() is None else ""


if __name__ == "__main__":       # ручная проверка: python -m pult_ruk.tunnel 8000
    async def _main() -> None:
        port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
        tun = Tunnel(port)
        try:
            print(await tun.start())
            await asyncio.sleep(3600)
        finally:
            await tun.stop()

    asyncio.run(_main())
