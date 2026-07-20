"""Stable, dotted-path job functions -- APScheduler persists jobs by reference (module +
qualname), never by pickling a closure, so a fresh process can rehydrate and re-fire them after
a restart (build plan: "job functions as stable module-level dotted paths, never closures").

The actual business logic lives wherever `set_context()` points at process startup (the
facade, in practice, per build plan phase 12). These functions are deliberately thin dispatchers
so the *dotted path* (shouldertap.engine.scheduler.jobs.fire_escalation etc.) never changes even
as what it delegates to evolves -- and eligibility re-derivation ("should I actually act now,
given stored timestamps vs. the clock") lives in the context implementation, not here, since it
needs store/clock access this module intentionally doesn't have.
"""

from __future__ import annotations

from typing import Protocol


class JobContext(Protocol):
    def handle_escalation_timer(self, request_id: str) -> None: ...

    def handle_give_up_timer(self, request_id: str) -> None: ...

    def run_quiet_hours_sweep(self) -> None: ...


_context: JobContext | None = None


def set_context(context: JobContext) -> None:
    global _context
    _context = context


def _require_context() -> JobContext:
    if _context is None:
        raise RuntimeError(
            "scheduler job context not set -- call jobs.set_context(...) during app startup "
            "before the scheduler can be started"
        )
    return _context


def fire_escalation(request_id: str) -> None:
    _require_context().handle_escalation_timer(request_id)


def fire_give_up(request_id: str) -> None:
    _require_context().handle_give_up_timer(request_id)


def run_quiet_hours_sweep() -> None:
    _require_context().run_quiet_hours_sweep()
