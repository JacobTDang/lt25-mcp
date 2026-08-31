"""A live view of a convergence run.

Watching a number in a terminal scroll past does not show whether a tone is
getting closer. This serves a page that updates as each iteration lands: the
distance trend, which bands are still off and in which direction, where the
knobs are, and the target spectrum with the amp's own laid over it.

Server-sent events rather than polling, so an iteration appears the moment it
is recorded. No framework and no CDN: it is a local page reading local state.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PAGE = Path(__file__).parent / "index.html"
DEFAULT_PORT = 8765


@dataclass
class Stage:
    """One step of the pipeline, as it happens."""

    name: str
    status: str = "pending"
    """pending | running | done | failed | skipped"""
    detail: str = ""
    seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "seconds": self.seconds,
        }


@dataclass
class LiveState:
    """Everything the page draws. Mutated by the run, read by the server."""

    status: str = "waiting"
    stages: list[Stage] = field(default_factory=list)
    active_stage: str = ""
    target_name: str = ""
    preset_name: str = ""
    amp_label: str = ""
    distance: float | None = None
    converged: bool = False
    iterations: list[dict[str, Any]] = field(default_factory=list)
    band_gaps: dict[str, float] = field(default_factory=dict)
    knobs: dict[str, float] = field(default_factory=dict)
    chain: list[dict[str, Any]] = field(default_factory=list)
    moves: list[dict[str, Any]] = field(default_factory=list)
    spectrum_hz: list[float] = field(default_factory=list)
    spectrum_target: list[float] = field(default_factory=list)
    spectrum_current: list[float] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stages": [s.to_dict() for s in self.stages],
            "active_stage": self.active_stage,
            "target_name": self.target_name,
            "preset_name": self.preset_name,
            "amp_label": self.amp_label,
            "distance": self.distance,
            "converged": self.converged,
            "iterations": self.iterations,
            "band_gaps": self.band_gaps,
            "knobs": self.knobs,
            "chain": self.chain,
            "moves": self.moves,
            "spectrum": {
                "hz": self.spectrum_hz,
                "target": self.spectrum_target,
                "current": self.spectrum_current,
            },
            "log": self.log[-40:],
        }


class Broadcaster:
    """Fans state snapshots out to every connected page."""

    def __init__(self, state: LiveState) -> None:
        self.state = state
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=32)
        with self._lock:
            self._clients.append(q)
        q.put(self.state.snapshot())
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def publish(self) -> None:
        snapshot = self.state.snapshot()
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(snapshot)
            except queue.Full:
                # A page that has stopped reading is dropped rather than
                # allowed to block the run.
                self.unsubscribe(q)

    def note(self, message: str) -> None:
        self.state.log.append(message)
        self.publish()

    def set_stages(self, names: list[str]) -> None:
        self.state.stages = [Stage(name) for name in names]
        self.state.active_stage = ""
        self.publish()

    def _stage(self, name: str) -> Stage:
        for stage in self.state.stages:
            if stage.name == name:
                return stage
        stage = Stage(name)
        self.state.stages.append(stage)
        return stage

    @contextmanager
    def stage(self, name: str, detail: str = ""):
        """Mark a pipeline step running, then done - or failed, loudly."""
        stage = self._stage(name)
        stage.status, stage.detail = "running", detail
        self.state.active_stage = name
        self.publish()
        started = time.monotonic()
        try:
            yield stage
        except BaseException as exc:
            stage.status = "failed"
            stage.detail = f"{type(exc).__name__}: {exc}"[:200]
            stage.seconds = time.monotonic() - started
            self.state.active_stage = ""
            self.note(f"{name}: FAILED - {stage.detail}")
            raise
        else:
            stage.status = "done"
            stage.seconds = time.monotonic() - started
            self.state.active_stage = ""
            self.publish()

    def skip(self, name: str, why: str = "") -> None:
        stage = self._stage(name)
        stage.status, stage.detail = "skipped", why
        self.publish()


def _handler_for(broadcaster: Broadcaster):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # keep the console for the run
            pass

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                body = PAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q = broadcaster.subscribe()
                try:
                    while True:
                        try:
                            snapshot = q.get(timeout=15)
                            payload = json.dumps(snapshot)
                            self.wfile.write(f"data: {payload}\n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    broadcaster.unsubscribe(q)
                return

            self.send_error(404)

    return Handler


class Dashboard:
    """Runs the page in a background thread for the duration of a run."""

    def __init__(self, state: LiveState, port: int = DEFAULT_PORT) -> None:
        self.state = state
        self.broadcaster = Broadcaster(state)
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self, open_browser: bool = True) -> str:
        self._server = ThreadingHTTPServer(
            ("127.0.0.1", self.port), _handler_for(self.broadcaster)
        )
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        if open_browser:
            webbrowser.open(self.url)
        return self.url

    def publish(self) -> None:
        self.broadcaster.publish()

    def note(self, message: str) -> None:
        self.broadcaster.note(message)

    def set_stages(self, names: list[str]) -> None:
        self.broadcaster.set_stages(names)

    def stage(self, name: str, detail: str = ""):
        return self.broadcaster.stage(name, detail)

    def skip(self, name: str, why: str = "") -> None:
        self.broadcaster.skip(name, why)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> Dashboard:
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
