"""
Running the slow parts off the GUI thread.

Every long job in this app - scanning drives for games, reading a PE header, downloading a
component - is disk or network bound and takes long enough to be felt. None of it may run
on the thread that paints, so each one goes to a worker and reports back.

The reporting back is the part worth being careful about, and it is where the Tk build had
a standing hazard. There, a worker handed its result over with `after(0, ...)`, which is
only valid while a live main loop still owns the widget; closing the window with a scan in
flight landed the callback on a destroyed widget and surfaced as `RuntimeError: main
thread is not in main loop` in a traceback nobody could act on. It had to be guarded by
hand at every call site.

Here a signal connected across threads is queued onto the receiving thread's event loop,
so a result computed on a worker is always applied on the GUI thread, in order, without
locks. The one thing Qt does not decide for us is what should happen to a result whose
view has since been closed - so `run_async` checks the owner is still alive before
delivering, once, in the one place instead of at every call site.
"""

from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from shiboken6 import isValid

# Signal carriers for jobs still in flight. A QRunnable is not a QObject and cannot own
# signals, so each job gets a carrier - and something has to hold a reference to it, or
# Python collects it mid-job and the result is emitted from a freed object.
_LIVE: set = set()


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(float, str)
    retired = Signal()


class _Job(QRunnable):
    def __init__(self, work: Callable[[Callable[[float, str], None]], Any], signals: _Signals):
        super().__init__()
        self._work = work
        self._signals = signals

    def _emit(self, signal_name: str, *args) -> None:
        """Emit, unless the carrier has gone.

        Closing the window while a scan or a download is still running tears down the
        QObject tree from the GUI thread; a worker that finishes a moment later would then
        emit from a signal whose C++ side no longer exists, which surfaces as a
        `RuntimeError: Signal source has been deleted` traceback on the way out. There is
        nothing to deliver a result to at that point, so there is nothing to do about it
        but stop.
        """
        if not isValid(self._signals):
            return
        try:
            getattr(self._signals, signal_name).emit(*args)
        except RuntimeError:
            pass  # deleted between the check and the emit

    def run(self) -> None:  # on a pool thread
        try:
            result = self._work(lambda f, m: self._emit("progress", f, m))
        except Exception as exc:  # a failed job reports; it never takes the app with it
            self._emit("failed", str(exc))
        else:
            self._emit("done", result)
        finally:
            self._emit("retired")


def _guarded(owner: Optional[QObject], callback: Callable) -> Callable:
    """Drop a callback whose owner has been destroyed.

    `isValid` asks whether the C++ object behind the Python wrapper is still there, which
    is the only reliable way to tell: a Python reference to a deleted QWidget stays
    perfectly alive and raises only when it is touched.
    """
    def call(*args):
        if owner is not None and not isValid(owner):
            return
        callback(*args)
    return call


def run_async(
    owner: Optional[QObject],
    work: Callable[[Callable[[float, str], None]], Any],
    on_done: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> None:
    """Run `work` on a pool thread and deliver its result on the GUI thread.

    `work` is called with one argument, `report(fraction, message)`, which it may call as
    often as it likes from the worker thread; each call arrives on the GUI thread as
    `on_progress`. Whatever `work` returns is handed to `on_done`; an exception out of it
    is handed to `on_error` as its message, and never reaches the interpreter's top level.

    `owner` is the widget the results belong to. If it has been destroyed by the time the
    job finishes - the window was closed during a scan - the result is dropped, which is
    the whole correct response to it.
    """
    signals = _Signals()
    _LIVE.add(signals)

    if on_done is not None:
        signals.done.connect(_guarded(owner, on_done), Qt.QueuedConnection)
    if on_error is not None:
        signals.failed.connect(_guarded(owner, on_error), Qt.QueuedConnection)
    if on_progress is not None:
        signals.progress.connect(_guarded(owner, on_progress), Qt.QueuedConnection)

    # Queued, so the carrier is discarded on the GUI thread after the result it carried
    # has been delivered rather than racing it.
    signals.retired.connect(lambda: _LIVE.discard(signals), Qt.QueuedConnection)

    QThreadPool.globalInstance().start(_Job(work, signals))
