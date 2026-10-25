"""Background jobs on one daemon thread (strategy alert scanner, notification watchers).

Feature modules register their jobs when the app is created
(``register_jobs(scheduler, config)``); the thread starts with the app (unless
``AppConfig.background_jobs`` is off, as in the tests) and stops with it.

Jobs run one after another, each opening its own database session through the
session factory it receives. A job's interval may be a callable, so it can follow
a setting. Exceptions are caught and kept on the job (``last_error``), so one
failing job never stops the others. ``run_now`` runs a job synchronously in the
caller's thread (used by "Scan now" buttons and by tests); a per-job lock keeps
it from overlapping with the background run of the same job.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from . import i18n

log = logging.getLogger("trading_os.scheduler")

TICK_SECONDS = 1.0
JobFn = Callable[[sessionmaker[Session]], object]


@dataclass
class Job:
    name: str
    interval: float | Callable[[], float]  # seconds between runs
    fn: JobFn
    description: str = ""
    next_run: float = 0.0  # time.monotonic() of the next background run
    last_run_at: datetime | None = None
    last_duration_ms: int | None = None
    last_result: object = None
    last_error: str = ""
    runs: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def seconds(self) -> float:
        value = self.interval() if callable(self.interval) else self.interval
        return max(float(value), 1.0)


class Scheduler:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory
        self.secrets = None  # SecretBox, attached by the app so jobs can reach account credentials
        self._jobs: dict[str, Job] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------- registry

    def register(
        self,
        name: str,
        interval: float | Callable[[], float],
        fn: JobFn,
        *,
        description: str = "",
        initial_delay: float = 5.0,
    ) -> Job:
        """Add a job; its first background run happens ``initial_delay`` seconds after start."""
        job = Job(name=name, interval=interval, fn=fn, description=description)
        job.next_run = time.monotonic() + initial_delay
        self._jobs[name] = job
        return job

    def get(self, name: str) -> Job:
        return self._jobs[name]

    @property
    def jobs(self) -> list[Job]:
        return list(self._jobs.values())

    # ------------------------------------------------------------- running

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        now = time.monotonic()
        for job in self._jobs.values():
            job.next_run = max(job.next_run, now)
        self._thread = threading.Thread(target=self._loop, name="trading-os-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(TICK_SECONDS):
            for job in self.jobs:
                if self._stop.is_set():
                    return
                if time.monotonic() >= job.next_run and job.lock.acquire(blocking=False):
                    try:
                        self._execute(job)
                    finally:
                        job.lock.release()

    def _execute(self, job: Job) -> object:
        started = time.monotonic()
        job.last_run_at = datetime.now().replace(microsecond=0)
        try:
            with i18n.using(i18n.stored_language(self.session_factory)):  # notices in the user's language
                job.last_result = job.fn(self.session_factory)
            job.last_error = ""
        except Exception as exc:  # a broken job must not stop the others
            log.exception("Background job %s failed", job.name)
            job.last_error = f"{type(exc).__name__}: {exc}"[:300]
            job.last_result = None
        finally:
            job.runs += 1
            job.last_duration_ms = int((time.monotonic() - started) * 1000)
            try:
                job.next_run = time.monotonic() + job.seconds()
            except Exception:  # an interval callable that fails falls back to a minute
                job.next_run = time.monotonic() + 60.0
        return job.last_result

    def run_now(self, name: str) -> Job:
        """Run ``name`` synchronously (waits if its background run is in progress)."""
        job = self._jobs[name]
        with job.lock:
            self._execute(job)
        return job

    def status(self) -> list[dict]:
        return [
            {
                "name": job.name,
                "description": i18n.tr(job.description),  # registered with N_(), translated when shown
                "running": self.running,
                "last_run_at": job.last_run_at,
                "last_duration_ms": job.last_duration_ms,
                "last_error": job.last_error,
                "runs": job.runs,
            }
            for job in self.jobs
        ]
