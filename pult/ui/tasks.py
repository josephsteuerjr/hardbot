"""Фоновые задачи: `fn` — в пуле потоков, `done(result)` / `fail(exc)` — в
UI-потоке. Замена Tk-паре «threading.Thread + after(0, …)»."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Worker(QRunnable):
    def __init__(self, fn, signals: _Signals) -> None:
        super().__init__()
        self.fn = fn
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:                       # noqa: BLE001 — показываем пользователю
            self.signals.failed.emit(exc)
            return
        self.signals.done.emit(result)


class TaskRunner(QObject):
    """Держит сигналы живыми, пока задача не вернулась в UI-поток."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = QThreadPool.globalInstance()
        self._live: set[_Signals] = set()

    def run(self, fn, done=None, fail=None) -> None:
        signals = _Signals(self)
        self._live.add(signals)

        def _finish(callback, payload):
            self._live.discard(signals)
            signals.deleteLater()
            if callback:
                callback(payload)

        signals.done.connect(lambda res: _finish(done, res))
        signals.failed.connect(lambda exc: _finish(fail, exc))
        self.pool.start(_Worker(fn, signals))

    @Slot()
    def wait(self, msecs: int = 30000) -> bool:
        return self.pool.waitForDone(msecs)
