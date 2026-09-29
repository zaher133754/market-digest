"""Two daily APScheduler jobs for the stateless reports."""

from __future__ import annotations

from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from market_digest.config import Settings

from .models import ReportKind, RunTrigger


class ReportScheduler:
    def __init__(self, settings: Settings, orchestrator: Any, *, scheduler: Any = None) -> None:
        self._settings = settings
        self._orchestrator = orchestrator
        self._scheduler = scheduler or AsyncIOScheduler(timezone=settings.timezone)

    async def start(self) -> None:
        common = {
            "timezone": self._settings.timezone,
            "max_instances": 1,
            "coalesce": True,
            "misfire_grace_time": self._settings.scheduler_misfire_grace_seconds,
            "replace_existing": True,
        }
        self._scheduler.add_job(
            self._run_digest,
            "cron",
            id="daily-channel-digest",
            hour=self._settings.digest_hour,
            minute=self._settings.digest_minute,
            **common,
        )
        self._scheduler.add_job(
            self._run_sentiment,
            "cron",
            id="daily-market-mood",
            hour=self._settings.sentiment_hour,
            minute=self._settings.sentiment_minute,
            **common,
        )
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    async def _run_digest(self) -> None:
        await self._orchestrator.request(ReportKind.DIGEST, RunTrigger.SCHEDULED)

    async def _run_sentiment(self) -> None:
        await self._orchestrator.request(ReportKind.SENTIMENT, RunTrigger.SCHEDULED)
