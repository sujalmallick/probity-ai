"""Run untrusted-document parsing in a separate process with a hard wall-clock limit (and, on POSIX, a memory cap).

A few kilobytes of crafted PDF can keep pdfminer busy for minutes or make a page render allocate gigabytes, and a
thread cannot be stopped. A helper process can: on timeout it is killed, replaced, and the upload is refused.

Helpers are plain subprocesses (`python -m probity.ingestion.isolate --serve`), kept warm and reused. Unlike a
multiprocessing pool they never re-import the caller's __main__ and work inside daemonic Celery workers. Requests
and results cross the pipe as a JSON header line plus raw document bytes / a JSON result line.
"""

from __future__ import annotations

import atexit
import importlib
import json
import os
import queue
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import IO, Any

PARSE_TIMEOUT_S = 30
HELPERS = 2
MEMORY_LIMIT_BYTES = 1536 * 1024 * 1024
_TIMEOUT_MESSAGE = "the document took too long to read; re-export it as a simple PDF"


class _Helper:
    def __init__(self) -> None:
        self.proc = subprocess.Popen([sys.executable, "-m", "probity.ingestion.isolate", "--serve"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, env=os.environ.copy())

    def alive(self) -> bool:
        return self.proc.poll() is None

    def request(self, target: str, data: bytes, mime: str) -> dict:
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps({"target": target, "mime": mime, "len": len(data)}).encode() + b"\n" + data)
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise EOFError("parser helper exited")
        return json.loads(line)

    def kill(self) -> None:
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


_idle: queue.Queue[_Helper] = queue.Queue()
_slots = threading.BoundedSemaphore(HELPERS)
_waiter = ThreadPoolExecutor(max_workers=HELPERS, thread_name_prefix="parse-wait")


def _checkout() -> _Helper:
    _slots.acquire()
    try:
        while True:
            h = _idle.get_nowait()
            if h.alive():
                return h
    except queue.Empty:
        return _Helper()


def run(target: str, data: bytes, mime: str, timeout: float = PARSE_TIMEOUT_S) -> Any:
    """Call `target` ("module.function") as target(data, mime) in a helper process and return its JSON result."""
    from probity.ingestion.parse import UnsupportedDocument

    h = _checkout()
    reusable = False
    try:
        out = _waiter.submit(h.request, target, data, mime).result(timeout)
        reusable = True
    except FutureTimeout:
        raise UnsupportedDocument(_TIMEOUT_MESSAGE) from None
    except (EOFError, OSError, ValueError):
        raise UnsupportedDocument("the document could not be read") from None
    finally:
        if reusable:
            _idle.put(h)
        else:
            h.kill()  # stuck or crashed: never reuse it
        _slots.release()
    if "error" in out:
        raise UnsupportedDocument(out["error"])
    return out["result"]


@atexit.register
def _shutdown() -> None:
    while True:
        try:
            _idle.get_nowait().kill()
        except queue.Empty:
            return


# ---------------------------------------------------------------- helper side

def _limit_memory() -> None:
    try:
        import resource  # POSIX only

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    except (ImportError, ValueError, OSError):
        pass


def _serve(inp: IO[bytes], out: IO[bytes]) -> None:
    from probity.ingestion.parse import UnsupportedDocument

    while True:
        header = inp.readline()
        if not header:
            return
        req = json.loads(header)
        data = inp.read(req["len"])
        module, name = req["target"].rsplit(".", 1)
        try:
            resp = {"result": getattr(importlib.import_module(module), name)(data, req["mime"])}
        except UnsupportedDocument as e:
            resp = {"error": str(e)}
        except Exception:  # noqa: BLE001 - malformed documents must not kill the helper
            resp = {"error": "the document could not be read"}
        out.write(json.dumps(resp).encode() + b"\n")
        out.flush()


if __name__ == "__main__" and sys.argv[1:] == ["--serve"]:
    _limit_memory()
    protocol_out = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    sys.stdout = sys.stderr  # library prints must never corrupt the result stream
    _serve(sys.stdin.buffer, protocol_out)
