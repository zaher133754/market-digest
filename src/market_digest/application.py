"""Composition root for the single stateless bot process."""

from __future__ import annotations

import asyncio
import signal
from contextlib import suppress
from typing import Any

import structlog

from market_digest.ai.client import CodexCliClient
from market_digest.ai.routing import ModelRoute
from market_digest.config import Settings
from market_digest.observability import configure_logging
from market_digest.reports.analytical_pipeline import AnalyticalPipeline
from market_digest.reports.history import ResearchHistory
from market_digest.reports.scheduler import ReportScheduler
from market_digest.reports.service import HealthSnapshot, ReportOrchestrator
from market_digest.reports.state import JsonStateStore
from market_digest.telegram.bot import TelegramBotConfig, TelegramDigestBot
from market_digest.telegram.folders import TelegramFolderNotFound, TelegramFolderSelector
from market_digest.telegram.models import TelegramSourceKind
from market_digest.telegram.session import MTProtoSessionConfig, MTProtoUserSession
from market_digest.telegram.window import TelegramWindowLoader

logger = structlog.get_logger(__name__)


def _session_config(settings: Settings) -> MTProtoSessionConfig:
    return MTProtoSessionConfig(
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash.get_secret_value(),
        phone=settings.telegram_phone,
        session_path=str(settings.telegram_session_path),
    )


def _bot_config(settings: Settings) -> TelegramBotConfig:
    return TelegramBotConfig(
        token=settings.bot_token.get_secret_value(),
        owner_telegram_id=settings.owner_telegram_id,
    )


async def login_telegram(settings: Settings) -> None:
    """Create the persistent MTProto user session in an interactive terminal."""

    user_session = MTProtoUserSession(_session_config(settings))
    try:
        await user_session.login_interactively()
    finally:
        await user_session.disconnect()


async def preflight_codex(settings: Settings) -> None:
    """Verify ChatGPT auth and strict output for both model routes."""

    client = CodexCliClient(settings)
    try:
        await client.check_version()
        await client.check_login()
        from market_digest.ai.schemas import PreflightResult

        for models in (
            [settings.research_model, settings.openai_model],
            [settings.analyst_model, settings.analyst_fallback_model, settings.openai_model],
        ):
            route = ModelRoute(
                [(m, CodexCliClient(settings, model=m)) for m in dict.fromkeys(models)]
            )
            await route.parse(
                operation="preflight",
                system_prompt='Return {"status":"ok"}.',
                payload="{}",
                schema=PreflightResult,
            )
            print("Codex route ready: " + route.used_models[-1] + "/high")
    finally:
        await client.close()


async def run_app(settings: Settings) -> None:
    """Run the owner bot, both schedules, MTProto and Codex in one process."""

    configure_logging(settings.log_level)
    user_session = MTProtoUserSession(_session_config(settings))
    codex = CodexCliClient(settings)
    selector = TelegramFolderSelector(user_session.client)
    loader = TelegramWindowLoader(
        user_session.client,
        selector,
        posts_folder_name=settings.posts_folder_name,
        chats_folder_name=settings.chats_folder_name,
    )
    research = ModelRoute(
        [
            (m, CodexCliClient(settings, model=m))
            for m in dict.fromkeys([settings.research_model, settings.openai_model])
        ]
    )
    analyst = ModelRoute(
        [
            (m, CodexCliClient(settings, model=m))
            for m in [
                settings.analyst_model,
                settings.analyst_fallback_model,
                settings.openai_model,
            ]
        ]
    )
    pipeline = AnalyticalPipeline(
        settings, research, analyst, ResearchHistory(settings.research_history_path)
    )
    state_store = JsonStateStore(settings.runtime_state_path)
    bot_holder: dict[str, TelegramDigestBot] = {}

    async def publish(text: str) -> None:
        await bot_holder["bot"].publish_digest(text)

    async def publish_failure(text: str) -> None:
        await bot_holder["bot"].publish_failure(text)

    async def health_probe() -> HealthSnapshot:
        mtproto_available = await _mtproto_available(user_session.client)
        codex_available = await _codex_available(codex)
        posts_count, posts_available = await _folder_count(
            selector,
            settings.posts_folder_name,
            TelegramSourceKind.CHANNEL,
        )
        chats_count, chats_available = await _folder_count(
            selector,
            settings.chats_folder_name,
            TelegramSourceKind.CHAT,
        )
        return HealthSnapshot(
            mtproto_available=mtproto_available,
            codex_available=codex_available,
            posts_source_count=posts_count,
            chats_source_count=chats_count,
            posts_folder_available=posts_available,
            chats_folder_available=chats_available,
        )

    orchestrator = ReportOrchestrator(
        settings,
        loader,
        pipeline,
        state_store,
        publish=publish,
        failure_publish=publish_failure,
        health_probe=health_probe,
    )
    bot = TelegramDigestBot(_bot_config(settings), orchestrator)
    bot_holder["bot"] = bot
    scheduler = ReportScheduler(settings, orchestrator)
    tasks: list[asyncio.Task[None]] = []
    scheduler_started = False
    try:
        await user_session.connect_authorized()
        await codex.check_version()
        await codex.check_login()
        await scheduler.start()
        scheduler_started = True
        stop_event = asyncio.Event()
        _install_signal_handlers(stop_event)
        tasks = [asyncio.create_task(bot.run(), name="owner-bot")]
        await _wait_until_stopped(tasks, stop_event)
    finally:
        if scheduler_started:
            with suppress(Exception):
                scheduler.shutdown()
        await orchestrator.shutdown()
        with suppress(RuntimeError):
            await bot.dispatcher.stop_polling()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        else:
            with suppress(Exception):
                await bot.bot.session.close()
        await user_session.disconnect()
        await codex.close()
        logger.info("market_digest_app_stopped")


async def _mtproto_available(client: Any) -> bool:
    try:
        if not client.is_connected():
            return False
        return bool(await client.is_user_authorized())
    except Exception:
        return False


async def _codex_available(client: CodexCliClient) -> bool:
    try:
        await client.check_version()
        await client.check_login()
        return True
    except Exception:
        return False


async def _folder_count(
    selector: TelegramFolderSelector,
    folder_name: str,
    source_kind: TelegramSourceKind,
) -> tuple[int, bool]:
    try:
        snapshot = await selector.select(folder_name, source_kind)
        return len(snapshot.sources), True
    except TelegramFolderNotFound:
        return 0, False
    except Exception:
        return 0, False


async def _wait_until_stopped(tasks: list[asyncio.Task[None]], stop_event: asyncio.Event) -> None:
    stop_task = asyncio.create_task(stop_event.wait(), name="shutdown-signal")
    done, _ = await asyncio.wait([*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED)
    if stop_task in done:
        return
    stop_task.cancel()
    finished = next(task for task in tasks if task in done)
    if finished.cancelled():
        raise RuntimeError(f"Runtime task {finished.get_name()} was cancelled")
    error = finished.exception()
    if error is not None:
        raise error
    raise RuntimeError(f"Runtime task {finished.get_name()} stopped unexpectedly")


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def request_shutdown() -> None:
        stop_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, request_shutdown)
        except (NotImplementedError, RuntimeError):
            continue


def safe_settings_summary(settings: Settings) -> dict[str, Any]:
    """Return a loggable configuration view with every credential omitted."""

    return {
        "telegram_api_id_configured": settings.telegram_api_id > 0,
        "telegram_phone_configured": bool(settings.telegram_phone),
        "telegram_session_path": str(settings.telegram_session_path),
        "owner_telegram_id": settings.owner_telegram_id,
        "openai_model": settings.openai_model,
        "openai_reasoning_effort": settings.openai_reasoning_effort,
        "research_model": settings.research_model,
        "analyst_route": [
            settings.analyst_model,
            settings.analyst_fallback_model,
            settings.openai_model,
        ],
        "research_history_path": str(settings.research_history_path),
        "codex_required_version": settings.codex_required_version,
        "codex_home": str(settings.codex_home),
        "timezone": settings.app_timezone,
        "digest_time": f"{settings.digest_hour:02d}:{settings.digest_minute:02d}",
        "sentiment_time": f"{settings.sentiment_hour:02d}:{settings.sentiment_minute:02d}",
        "posts_folder": settings.posts_folder_name,
        "chats_folder": settings.chats_folder_name,
        "analysis_window_hours": 24,
    }
