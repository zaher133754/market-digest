from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from market_digest.reports.models import ReportKind, RunTrigger
from market_digest.reports.scheduler import ReportScheduler


@dataclass
class FakeOrchestrator:
    calls: list[tuple[ReportKind, RunTrigger]] = field(default_factory=list)

    async def request(self, kind: ReportKind, trigger: RunTrigger) -> None:
        self.calls.append((kind, trigger))


class FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[tuple[Any, str, dict[str, Any]]] = []
        self.started = False
        self.shutdown_calls: list[bool] = []

    def add_job(self, func: Any, trigger: str, **kwargs: Any) -> None:
        self.jobs.append((func, trigger, kwargs))

    def start(self) -> None:
        self.started = True

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)


@pytest.mark.asyncio
async def test_scheduler_registers_two_samara_cron_jobs(settings) -> None:
    backend = FakeOrchestrator()
    scheduler_impl = FakeScheduler()
    scheduler = ReportScheduler(settings, backend, scheduler=scheduler_impl)

    await scheduler.start()

    assert scheduler_impl.started is True
    assert len(scheduler_impl.jobs) == 2
    digest_job, sentiment_job = scheduler_impl.jobs
    for _, trigger, kwargs in scheduler_impl.jobs:
        assert trigger == "cron"
        assert str(kwargs["timezone"]) == "Europe/Samara"
        assert kwargs["max_instances"] == 1
        assert kwargs["coalesce"] is True
        assert kwargs["misfire_grace_time"] == settings.scheduler_misfire_grace_seconds
    assert (digest_job[2]["hour"], digest_job[2]["minute"]) == (15, 0)
    assert (sentiment_job[2]["hour"], sentiment_job[2]["minute"]) == (16, 0)

    await digest_job[0]()
    await sentiment_job[0]()
    assert backend.calls == [
        (ReportKind.DIGEST, RunTrigger.SCHEDULED),
        (ReportKind.SENTIMENT, RunTrigger.SCHEDULED),
    ]

    scheduler.shutdown()
    assert scheduler_impl.shutdown_calls == [False]
