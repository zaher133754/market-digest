"""Single-task orchestration for manual and scheduled reports."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from market_digest.config import Settings
from market_digest.errors import LunaContractError, LunaUnavailableError
from market_digest.telegram.folders import TelegramFolderNotFound

from .analysis_models import AnalyticalResult
from .analytical_rendering import render_analytical
from .history import ResearchHistory
from .models import (
    ChannelDigestReport,
    MarketMoodReport,
    ReportKind,
    RunTrigger,
)
from .rendering import render_digest, render_sentiment
from .state import JsonStateStore, RunRecord, RunStatus, RuntimeState

Publish = Callable[[str], Awaitable[Any] | Any]
HealthProbe = Callable[[], Awaitable["HealthSnapshot"]]
logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StartDecision:
    started: bool
    message: str


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    mtproto_available: bool
    codex_available: bool
    posts_source_count: int
    chats_source_count: int
    posts_folder_available: bool = True
    chats_folder_available: bool = True


class ReportOrchestrator:
    def __init__(
        self,
        settings: Settings,
        loader: Any,
        pipeline: Any,
        state_store: JsonStateStore,
        *,
        publish: Publish,
        health_probe: HealthProbe,
    ) -> None:
        self._settings = settings
        self._loader = loader
        self._pipeline = pipeline
        self._state_store = state_store
        self._publish = publish
        self._health_probe = health_probe
        self._task_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._current_task: asyncio.Task[None] | None = None
        self._current_kind: ReportKind | None = None
        self._state: RuntimeState | None = None

    async def request(self, kind: ReportKind, trigger: RunTrigger) -> StartDecision:
        async with self._task_lock:
            if self._current_task is not None and not self._current_task.done():
                active = _kind_name(self._current_kind or kind)
                return StartDecision(
                    started=False,
                    message=f"Сейчас уже формируется {active}. Дождитесь завершения.",
                )
            requested_at = datetime.now(UTC)
            self._current_kind = kind
            self._current_task = asyncio.create_task(
                self._run(kind, trigger, requested_at),
                name=f"report-{kind.value}",
            )
        return StartDecision(
            started=True,
            message=f"{_kind_name(kind).capitalize()} запущен. Готовый отчёт придёт сюда.",
        )

    async def request_digest(self) -> str:
        return (await self.request(ReportKind.DIGEST, RunTrigger.MANUAL)).message

    async def request_sentiment(self) -> str:
        return (await self.request(ReportKind.SENTIMENT, RunTrigger.MANUAL)).message

    async def history_text(self) -> str:
        reports = await ResearchHistory(self._settings.research_history_path).recent()
        if not reports:
            return "Проверенных аналитических записок пока нет."
        return "ИСТОРИЯ АНАЛИТИКИ\n" + "\n".join(
            f"• {s.window_end.astimezone(self._settings.timezone):%d.%m %H:%M} — "
            f"{_kind_name(s.kind)} — {s.analyst_model}: {s.memo.conclusion.text}"
            for s in reports
        )

    async def sources_text(self) -> str:
        reports = await ResearchHistory(self._settings.research_history_path).recent()
        if not reports:
            return "Проверенных записок пока нет."
        report = reports[0]
        used = {ref for c in report.clusters for ref in c.message_refs}
        links = dict.fromkeys(
            f"{s.source_title}: {s.permalink or 'закрытый источник'}"
            for s in report.sources
            if s.message_ref in used
        )
        return "ИСТОЧНИКИ ПОСЛЕДНЕЙ ПРОВЕРЕННОЙ ЗАПИСКИ\n" + "\n".join(links)

    async def status_text(self) -> str:
        try:
            health = await self._health_probe()
        except Exception:
            health = HealthSnapshot(False, False, 0, 0)
        state = await self._get_state()
        current_kind = self._current_kind
        current = (
            _kind_name(current_kind) if self._is_running() and current_kind is not None else "нет"
        )
        return "\n".join(
            [
                "СТАТУС БОТА",
                f"MTProto: {_availability(health.mtproto_available)}",
                f"Codex: {_availability(health.codex_available)}",
                f"Исследование: {self._settings.research_model}",
                "Аналитик: Astra → Sol → " + self._settings.openai_model,
                _folder_status(
                    self._settings.posts_folder_name,
                    health.posts_source_count,
                    health.posts_folder_available,
                ),
                _folder_status(
                    self._settings.chats_folder_name,
                    health.chats_source_count,
                    health.chats_folder_available,
                ),
                f"Текущая задача: {current}",
                f"Последний дайджест: {_record_text(state.digest)}",
                f"Последнее настроение: {_record_text(state.sentiment)}",
                (
                    f"Расписание ({self._settings.app_timezone}): дайджест "
                    f"{self._settings.digest_hour:02d}:{self._settings.digest_minute:02d}, "
                    f"настроение {self._settings.sentiment_hour:02d}:"
                    f"{self._settings.sentiment_minute:02d}"
                ),
            ]
        )

    async def shutdown(self) -> None:
        async with self._task_lock:
            task = self._current_task
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def _run(
        self,
        kind: ReportKind,
        trigger: RunTrigger,
        requested_at: datetime,
    ) -> None:
        started_at = datetime.now(UTC)
        source_count = 0
        message_count = 0
        try:
            await self._set_record(
                kind,
                RunRecord(
                    status=RunStatus.RUNNING,
                    trigger=trigger,
                    started_at=started_at,
                ),
            )
            load = getattr(self._loader, "load_combined", self._loader.load)
            data = await load(kind, end=requested_at)
            source_count = data.source_count
            message_count = len(data.messages)
            await self._set_record(
                kind,
                RunRecord(
                    status=RunStatus.RUNNING,
                    trigger=trigger,
                    started_at=started_at,
                    source_count=source_count,
                    message_count=message_count,
                ),
            )
            if not data.messages:
                text = (
                    f"За последние 24 часа в папке «{_folder_name(kind, self._settings)}» "
                    "нет подходящих сообщений. Codex не запускался."
                )
            else:
                report = await self._pipeline.analyze(data)
                if isinstance(report, AnalyticalResult):
                    text = render_analytical(report, self._settings.app_timezone, data.warnings)
                elif report is None:
                    text = (
                        f"Изучено {message_count} сообщений за 24 часа. "
                        "Содержательных материалов для аналитической записки недостаточно. "
                        "Индекс настроения не определяется."
                    )
                elif kind is ReportKind.DIGEST and isinstance(report, ChannelDigestReport):
                    text = render_digest(
                        report,
                        data,
                        timezone_name=self._settings.app_timezone,
                    )
                elif kind is ReportKind.SENTIMENT and isinstance(report, MarketMoodReport):
                    text = render_sentiment(
                        report,
                        data,
                        timezone_name=self._settings.app_timezone,
                    )
                else:
                    raise LunaContractError("Codex returned the wrong report type")
            await _call_publish(self._publish, text)
            finished_at = datetime.now(UTC)
            await self._set_record(
                kind,
                RunRecord(
                    status=RunStatus.SUCCEEDED,
                    trigger=trigger,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=(finished_at - started_at).total_seconds(),
                    source_count=source_count,
                    message_count=message_count,
                ),
            )
        except asyncio.CancelledError:
            await self._record_failure(
                kind,
                trigger,
                started_at,
                source_count,
                message_count,
                "interrupted",
            )
            raise
        except Exception as exc:
            error_code = _safe_error_code(exc)
            logger.error(
                "report_generation_failed",
                report_kind=kind.value,
                trigger=trigger.value,
                error_code=error_code,
                error_type=type(exc).__name__,
                contract_detail=str(exc) if isinstance(exc, LunaContractError) else None,
                source_count=source_count,
                message_count=message_count,
            )
            with suppress(Exception):
                await self._record_failure(
                    kind,
                    trigger,
                    started_at,
                    source_count,
                    message_count,
                    error_code,
                )
            with suppress(Exception):
                await _call_publish(
                    self._publish,
                    f"⚠️ Не удалось сформировать {_kind_name(kind)}. Код ошибки: {error_code}.",
                )
        finally:
            async with self._task_lock:
                if self._current_task is asyncio.current_task():
                    self._current_task = None
                    self._current_kind = None

    async def _record_failure(
        self,
        kind: ReportKind,
        trigger: RunTrigger,
        started_at: datetime,
        source_count: int,
        message_count: int,
        error_code: str,
    ) -> None:
        finished_at = datetime.now(UTC)
        await self._set_record(
            kind,
            RunRecord(
                status=RunStatus.FAILED,
                trigger=trigger,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=(finished_at - started_at).total_seconds(),
                source_count=source_count,
                message_count=message_count,
                error_code=error_code,
            ),
        )

    async def _get_state(self) -> RuntimeState:
        async with self._state_lock:
            if self._state is None:
                self._state = await self._state_store.load()
            return self._state

    async def _set_record(self, kind: ReportKind, record: RunRecord) -> None:
        async with self._state_lock:
            if self._state is None:
                self._state = await self._state_store.load()
            field = "digest" if kind is ReportKind.DIGEST else "sentiment"
            self._state = self._state.model_copy(update={field: record})
            await self._state_store.save(self._state)

    def _is_running(self) -> bool:
        return self._current_task is not None and not self._current_task.done()


async def _call_publish(publish: Publish, text: str) -> None:
    result = publish(text)
    if inspect.isawaitable(result):
        await result


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, TelegramFolderNotFound):
        return "folder_not_found"
    if isinstance(exc, LunaUnavailableError):
        return "codex_unavailable"
    if isinstance(exc, LunaContractError):
        return "codex_contract"
    return "internal_error"


def _kind_name(kind: ReportKind) -> str:
    return "дайджест" if kind is ReportKind.DIGEST else "отчёт о настроении"


def _folder_name(kind: ReportKind, settings: Settings) -> str:
    return settings.posts_folder_name if kind is ReportKind.DIGEST else settings.chats_folder_name


def _availability(value: bool) -> str:
    return "доступен" if value else "недоступен"


def _folder_status(name: str, count: int, available: bool) -> str:
    return f"{name}: {count} источников" if available else f"{name}: папка не найдена"


def _record_text(record: RunRecord) -> str:
    labels = {
        RunStatus.NEVER: "ещё не запускался",
        RunStatus.RUNNING: "выполняется",
        RunStatus.SUCCEEDED: "успешно",
        RunStatus.FAILED: "ошибка",
    }
    parts = [labels[record.status]]
    if record.finished_at is not None:
        parts.append(record.finished_at.isoformat(timespec="seconds"))
    if record.duration_seconds is not None:
        parts.append(f"{record.duration_seconds:.1f} с")
    if record.status is not RunStatus.NEVER:
        parts.append(f"источников {record.source_count}, сообщений {record.message_count}")
    if record.error_code:
        parts.append(f"код {record.error_code}")
    return "; ".join(parts)
