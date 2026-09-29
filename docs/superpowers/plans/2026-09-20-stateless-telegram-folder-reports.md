# Stateless Telegram Folder Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить PostgreSQL-архитектуру одним лёгким контейнером, который по расписанию и кнопкам формирует два отчёта за 24 часа из Telegram-папок `Посты` и `Чаты`.

**Architecture:** Один `app` совмещает owner-only aiogram-бота, APScheduler, on-demand Telethon-загрузку и изолированные вызовы Codex. Сырые сообщения существуют только в памяти/`tmpfs`; постоянный JSON хранит лишь операционные метаданные. Общая `asyncio.Lock` не допускает параллельных тяжёлых анализов.

**Tech Stack:** Python 3.12, aiogram 3, Telethon 1.44, APScheduler 3, Pydantic 2, Codex CLI 0.154.0, `gpt-5.6-luna/high`, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-20-stateless-telegram-folder-reports-design.md`

## Global Constraints

- Модель строго `gpt-5.6-luna`, reasoning effort строго `high`, Codex CLI строго `0.154.0`; fallback запрещён.
- Часовой пояс `Europe/Samara`; дайджест запускается в 15:00, настроение — в 16:00.
- Источники выбираются только из нативных Telegram-папок `Посты` и `Чаты`.
- Ручные действия разрешены только `OWNER_TELEGRAM_ID` и используют тот же путь, что расписание.
- Окно каждого отчёта — ровно последние 24 elapsed hours, `(start, end]`.
- Сырые сообщения, ответы Codex, токены и данные авторизации постоянно не сохраняются.
- Одновременно выполняется не более одного анализа.
- `/root/bots` не читается и не изменяется.
- PostgreSQL volume не удаляется при первом развёртывании; `docker compose down -v` запрещён.
- В текущем workspace нет `.git`. Не инициализировать репозиторий без разрешения владельца. Приведённые commit-шаги выполняются только после появления Git; иначе контрольная точка фиксируется успешными тестами и перечнем изменённых файлов.

## Review Focus

- Telegram может вернуть заголовок папки как строку или объект с `.text`, а peers могут повторяться между pinned/include; Task 2 фиксирует нормализацию и дедупликацию тестом.
- Сообщения ровно на нижней границе исключаются, ровно на верхней включаются, timezone всегда aware; Task 3 фиксирует это тестом.
- Большая выборка не должна переполнить контекст или запустить параллельные Codex-вызовы; Task 4 проверяет chunk/reduce и последовательность.
- Двойной клик и совпадение расписания с ручным запуском не должны создавать второй анализ; Task 6 проверяет одну общую блокировку.
- Повреждённый JSON или остановка во время atomic replace не должны мешать запуску и не должны восстанавливать ложный `running`; Task 5 проверяет fail-safe восстановление.

---

### Task 1: Stateless configuration and report domain

**Files:**
- Create: `src/market_digest/reports/__init__.py`
- Create: `src/market_digest/reports/models.py`
- Modify: `src/market_digest/config.py`
- Modify: `.env.example`
- Modify: `tests/conftest.py`
- Modify: `tests/unit/test_config.py`
- Test: `tests/unit/test_report_models.py`

**Interfaces:**
- Produces: `ReportKind`, `RunTrigger`, `ReportMessage`, `ReportInput`, `ReportPoint`, `ChannelDigestReport`, `MarketMoodReport`, `mood_label()`.
- Produces settings: `posts_folder_name`, `chats_folder_name`, `sentiment_hour`, `sentiment_minute`, `runtime_state_path`.

- [ ] **Step 1: Write failing configuration tests**

```python
def test_stateless_defaults(settings: Settings) -> None:
    assert settings.posts_folder_name == "Посты"
    assert settings.chats_folder_name == "Чаты"
    assert (settings.digest_hour, settings.digest_minute) == (15, 0)
    assert (settings.sentiment_hour, settings.sentiment_minute) == (16, 0)
    assert settings.runtime_state_path == Path("/data/state/runtime.json")
    assert not hasattr(settings, "database_url")
```

- [ ] **Step 2: Run the configuration test and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_config.py::test_stateless_defaults`  
Expected: FAIL because the new settings do not exist and `database_url` still exists.

- [ ] **Step 3: Write failing domain-contract tests**

```python
def test_market_mood_index_is_bounded() -> None:
    with pytest.raises(ValidationError):
        MarketMoodReport(index=11, summary="x", positive=[], negative=[], themes=[],
                         expectations=[], disagreements=[], extremes=[], conclusion="x")

@pytest.mark.parametrize(
    ("score", "label"),
    [(1, "паника"), (3, "негатив"), (5, "нейтральное"),
     (6, "умеренный оптимизм"), (8, "сильная эйфория"), (10, "предельная эйфория")],
)
def test_mood_label_is_deterministic(score: int, label: str) -> None:
    assert mood_label(score) == label
```

- [ ] **Step 4: Implement strict report models**

`reports/models.py` must define immutable/strict Pydantic models with these public shapes:

```python
class ReportKind(StrEnum):
    DIGEST = "digest"
    SENTIMENT = "sentiment"

class RunTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"

class ReportMessage(StrictModel):
    message_ref: str
    source_title: str
    source_username: str | None
    published_at: datetime
    author_display_name: str
    text: str
    permalink: str | None

class ReportInput(StrictModel):
    kind: ReportKind
    window_start: datetime
    window_end: datetime
    source_count: int = Field(ge=0)
    messages: list[ReportMessage]
    warnings: list[str]

class ReportPoint(StrictModel):
    text: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)

class ChannelDigestReport(StrictModel):
    overview: list[ReportPoint]
    key_events: list[ReportPoint]
    author_views: list[ReportPoint]
    instruments_and_macro: list[ReportPoint]
    declared_ideas: list[ReportPoint]
    disagreements: list[ReportPoint]
    watchlist: list[ReportPoint]
    conclusion: str

class MarketMoodReport(StrictModel):
    index: int = Field(ge=1, le=10)
    summary: str
    positive: list[ReportPoint]
    negative: list[ReportPoint]
    themes: list[ReportPoint]
    expectations: list[ReportPoint]
    disagreements: list[ReportPoint]
    extremes: list[ReportPoint]
    conclusion: str
```

`mood_label()` derives the Russian category from the approved scale; the model does not choose its own label.

- [ ] **Step 5: Replace database/source-filter settings with stateless settings**

Remove `database_url`, retention and include/exclude fields from `Settings`. Add:

```python
posts_folder_name: str = Field(default="Посты", min_length=1)
chats_folder_name: str = Field(default="Чаты", min_length=1)
sentiment_hour: int = Field(default=16, ge=0, le=23)
sentiment_minute: int = Field(default=0, ge=0, le=59)
runtime_state_path: Path = Path("/data/state/runtime.json")
```

Update `.env.example` accordingly and remove database/retention/source-filter variables.

- [ ] **Step 6: Run Task 1 tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_config.py tests/unit/test_report_models.py`  
Expected: PASS.

- [ ] **Step 7: Commit the domain checkpoint when Git is available**

```bash
git add .env.example src/market_digest/config.py src/market_digest/reports tests/conftest.py tests/unit/test_config.py tests/unit/test_report_models.py
git commit -m "refactor: define stateless report domain"
```

---

### Task 2: Telegram folder resolution

**Files:**
- Create: `src/market_digest/telegram/folders.py`
- Modify: `src/market_digest/telegram/sources.py`
- Test: `tests/unit/telegram/test_folders.py`

**Interfaces:**
- Consumes: `TelegramSourceKind`, `SourceHandle`, `source_from_entity()`.
- Produces: `normalize_folder_title(value: object) -> str`, `FolderSnapshot`, `TelegramFolderSelector.select(folder_name, expected_kind)`.

- [ ] **Step 1: Write failing folder-selection tests**

Use a fake MTProto client whose raw request returns filters containing pinned,
included and excluded peers. Cover:

```python
assert normalize_folder_title("  ПОСТЫ ") == "посты"
assert normalize_folder_title(SimpleNamespace(text=" Чаты ")) == "чаты"

snapshot = await selector.select("Посты", TelegramSourceKind.CHANNEL)
assert [item.source.title for item in snapshot.sources] == ["Канал A", "Канал B"]
assert snapshot.ignored_titles == ("Группа не того типа",)
```

The fixture must place the same channel in pinned/include and place another peer
in exclude; expected output contains the duplicate once and omits the excluded peer.

- [ ] **Step 2: Run the folder tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/unit/telegram/test_folders.py`  
Expected: FAIL because `TelegramFolderSelector` does not exist.

- [ ] **Step 3: Implement folder lookup through MTProto dialog filters**

```python
@dataclass(frozen=True, slots=True)
class FolderSnapshot:
    requested_name: str
    resolved_title: str
    sources: tuple[SourceHandle, ...]
    ignored_titles: tuple[str, ...]

class TelegramFolderSelector:
    def __init__(self, client: Any) -> None: ...

    async def select(
        self, folder_name: str, expected_kind: TelegramSourceKind
    ) -> FolderSnapshot: ...
```

Call `functions.messages.GetDialogFiltersRequest()`, accept both a direct list
and an object exposing `.filters`, normalize title objects, combine pinned and
included peers, subtract excluded peers, deduplicate by marked peer id, resolve
entities through `client.get_entity()`, and retain only the expected source kind.
Raise a dedicated `TelegramFolderNotFound` with the requested title; return an
empty snapshot for an existing empty folder.

- [ ] **Step 4: Add wrong-type, absent-folder and malformed-filter tests**

Assert absent folder raises `TelegramFolderNotFound`; a user/private dialog is
ignored; malformed filters do not leak peer objects into error text.

- [ ] **Step 5: Run Task 2 tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/unit/telegram/test_sources.py tests/unit/telegram/test_folders.py`  
Expected: PASS.

- [ ] **Step 6: Commit the folder checkpoint when Git is available**

```bash
git add src/market_digest/telegram/folders.py src/market_digest/telegram/sources.py tests/unit/telegram/test_folders.py
git commit -m "feat: select report sources from telegram folders"
```

---

### Task 3: On-demand exact 24-hour loader

**Files:**
- Create: `src/market_digest/telegram/window.py`
- Modify: `src/market_digest/services/windows.py`
- Test: `tests/unit/telegram/test_window.py`
- Modify: `tests/unit/test_windows.py`

**Interfaces:**
- Consumes: `ReportKind`, `ReportInput`, `ReportMessage`, `TelegramFolderSelector`, `message_to_dto()`, `exact_24_hour_window()`.
- Produces: `TelegramWindowLoader.load(kind, end) -> ReportInput`.

- [ ] **Step 1: Write the failing boundary test**

```python
result = await loader.load(ReportKind.DIGEST, end)
assert [m.message_ref for m in result.messages] == ["telegram:-1001:2", "telegram:-1001:3"]
assert result.window_start == end - timedelta(hours=24)
assert result.window_end == end
```

The fake source returns messages at `start`, `start + 1 second`, `end`, and
`end + 1 second`; only the middle two must remain.

- [ ] **Step 2: Run the loader test and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/unit/telegram/test_window.py`  
Expected: FAIL because the loader does not exist.

- [ ] **Step 3: Implement sequential on-demand loading**

```python
class TelegramWindowLoader:
    def __init__(
        self,
        client: Any,
        selector: TelegramFolderSelector,
        *,
        posts_folder_name: str,
        chats_folder_name: str,
    ) -> None: ...

    async def load(self, kind: ReportKind, end: datetime) -> ReportInput: ...
```

For digest select channels from `Посты`; for sentiment select chats from
`Чаты`. Iterate each source sequentially with newest messages first, skip values
newer than `end`, stop at `published_at <= start`, map text/captions via
`message_to_dto()`, and produce stable refs. Sort the final list by
`(published_at, message_ref)` so tests and prompts are deterministic.

- [ ] **Step 4: Add empty/wrong-timezone/content tests**

Assert an empty folder and an existing folder with no 24-hour messages return an
empty `messages` list without error; naive `end` raises `ValueError`; captions
are retained; service messages and whitespace-only messages are excluded.

- [ ] **Step 5: Run Task 3 tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_windows.py tests/unit/telegram/test_messages.py tests/unit/telegram/test_window.py`  
Expected: PASS.

- [ ] **Step 6: Commit the loader checkpoint when Git is available**

```bash
git add src/market_digest/telegram/window.py src/market_digest/services/windows.py tests/unit/telegram/test_window.py tests/unit/test_windows.py
git commit -m "feat: load exact telegram report windows on demand"
```

---

### Task 4: Stateless Codex analysis and rendering

**Files:**
- Create: `src/market_digest/reports/prompts.py`
- Create: `src/market_digest/reports/pipeline.py`
- Create: `src/market_digest/reports/rendering.py`
- Test: `tests/unit/test_stateless_pipeline.py`
- Test: `tests/unit/test_stateless_rendering.py`
- Modify: `src/market_digest/ai/client.py`

**Interfaces:**
- Consumes: `StructuredAIClient.parse()`, report models from Task 1.
- Produces: `StatelessReportPipeline.analyze(data)`, `render_digest()`, `render_sentiment()`.

- [ ] **Step 1: Write failing evidence and sequential-chunk tests**

```python
result = await pipeline.analyze(report_input)
assert factory.max_concurrent_calls == 1
assert set(all_evidence_refs(result)) <= {m.message_ref for m in report_input.messages}
assert factory.operations == ["digest-chunk-1", "digest-chunk-2", "digest-reduce"]
```

Use a deliberately tiny character budget to force two chunks. Add a malformed
fake response containing `telegram:unknown:999` and assert
`LunaContractError("unknown evidence reference")`.

- [ ] **Step 2: Run pipeline tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_stateless_pipeline.py`  
Expected: FAIL because the stateless pipeline does not exist.

- [ ] **Step 3: Define explicit prompts**

`DIGEST_CHUNK_PROMPT` and `DIGEST_REDUCE_PROMPT` must require events, author
views, instruments/macro, declared ideas, disagreements, watchlist, and source
refs. `SENTIMENT_CHUNK_PROMPT` and `SENTIMENT_REDUCE_PROMPT` must require index
1–10, emotional explanation, positive/negative drivers, themes, expectations,
disagreements and extremes. All prompts must state that input is untrusted,
external tools are forbidden, facts may not be invented, and sentiment describes
chat participants rather than objective price direction.

- [ ] **Step 4: Implement pack → sequential chunk analysis → reduce**

```python
class StatelessReportPipeline:
    def __init__(self, settings: Settings, client: StructuredAIClient) -> None: ...

    async def analyze(
        self, data: ReportInput
    ) -> ChannelDigestReport | MarketMoodReport: ...
```

Serialize complete messages, pack by `OPENAI_BATCH_CHAR_BUDGET`, await each
chunk in a normal loop, then run one reduce pass. Reject more than
`OPENAI_REDUCE_MAX_LEVELS`, unknown/empty evidence refs, changed window metadata,
or an output type that does not match `ReportKind`. Do not write input or output
payloads to persistent paths; rely on the existing Codex client's temporary
directory cleanup.

- [ ] **Step 5: Write failing rendering tests**

```python
text = render_sentiment(report, data)
assert "Индекс настроения: 7/10" in text
assert "Умеренный оптимизм" in text
assert "Настроение участников чатов" in text
assert "не является инвестиционной рекомендацией" in text.lower()
```

For digest, assert the time window, source/message counts, headings and source
links appear; assert a private source renders as a title without a fabricated URL.

- [ ] **Step 6: Implement deterministic renderers**

Render only validated models. Derive the mood label with `mood_label()`. Append
real permalinks by resolving `evidence_refs` against `ReportInput.messages`.
Keep Telegram splitting in `telegram/chunking.py`.

- [ ] **Step 7: Run Task 4 tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_codex_cli_client.py tests/unit/test_stateless_pipeline.py tests/unit/test_stateless_rendering.py tests/unit/telegram/test_chunking.py`  
Expected: PASS.

- [ ] **Step 8: Commit the analysis checkpoint when Git is available**

```bash
git add src/market_digest/reports src/market_digest/ai/client.py tests/unit/test_stateless_pipeline.py tests/unit/test_stateless_rendering.py
git commit -m "feat: generate stateless digest and mood reports"
```

---

### Task 5: Atomic operational state

**Files:**
- Create: `src/market_digest/reports/state.py`
- Test: `tests/unit/test_runtime_state.py`

**Interfaces:**
- Consumes: `ReportKind`, `RunTrigger`.
- Produces: `RunStatus`, `RunRecord`, `RuntimeState`, `JsonStateStore.load()`, `JsonStateStore.save()`.

- [ ] **Step 1: Write failing atomic-state tests**

```python
await store.save(state)
assert await store.load() == state
assert not state_path.with_suffix(".tmp").exists()

state_path.write_text("{broken", encoding="utf-8")
recovered = await store.load()
assert recovered.digest.status == RunStatus.NEVER
assert recovered.sentiment.status == RunStatus.NEVER
```

Add a stored `running` record representing an interrupted process and assert
load converts it to `failed` with safe error code `interrupted`.

- [ ] **Step 2: Run state tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_runtime_state.py`  
Expected: FAIL because the state store does not exist.

- [ ] **Step 3: Implement metadata-only state**

```python
class RunStatus(StrEnum):
    NEVER = "never"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

class RunRecord(StrictModel):
    status: RunStatus = RunStatus.NEVER
    trigger: RunTrigger | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    source_count: int = Field(default=0, ge=0)
    message_count: int = Field(default=0, ge=0)
    error_code: str | None = None
```

`JsonStateStore.save()` must create the parent directory, write JSON to a sibling
temporary file with mode `0600`, `flush()` plus `os.fsync()`, then `os.replace()`.
Never include message text, prompts, tokens, phone number or exception strings.

- [ ] **Step 4: Run Task 5 tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_runtime_state.py`  
Expected: PASS.

- [ ] **Step 5: Commit the state checkpoint when Git is available**

```bash
git add src/market_digest/reports/state.py tests/unit/test_runtime_state.py
git commit -m "feat: persist safe atomic runtime status"
```

---

### Task 6: Report orchestration, buttons and schedules

**Files:**
- Create: `src/market_digest/reports/service.py`
- Create: `src/market_digest/reports/scheduler.py`
- Modify: `src/market_digest/telegram/models.py`
- Modify: `src/market_digest/telegram/bot.py`
- Rewrite: `src/market_digest/application.py`
- Modify: `tests/unit/telegram/test_bot_controls.py`
- Create: `tests/unit/test_report_orchestrator.py`
- Create: `tests/unit/test_stateless_scheduler.py`

**Interfaces:**
- Consumes: loader, pipeline, renderers, state store and `send_in_chunks()`.
- Produces: `ReportOrchestrator.request()`, `ReportOrchestrator.status_text()`, `ReportScheduler.start()/shutdown()`.

- [ ] **Step 1: Write failing no-overlap and immediate-ack tests**

```python
first = await orchestrator.request(ReportKind.DIGEST, RunTrigger.MANUAL)
second = await orchestrator.request(ReportKind.SENTIMENT, RunTrigger.SCHEDULED)
assert first.started is True
assert second.started is False
assert "Дайджест" in second.message
assert loader.calls == [ReportKind.DIGEST]
```

Hold the fake pipeline on an event so the second request occurs while the first
is active. Assert the first call returns an acknowledgement before releasing the
pipeline event.

- [ ] **Step 2: Run orchestrator tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_report_orchestrator.py`  
Expected: FAIL because the orchestrator does not exist.

- [ ] **Step 3: Implement one shared background-task orchestrator**

```python
@dataclass(frozen=True, slots=True)
class StartDecision:
    started: bool
    message: str

class ReportOrchestrator:
    async def request(self, kind: ReportKind, trigger: RunTrigger) -> StartDecision: ...
    async def status_text(self) -> str: ...
    async def shutdown(self) -> None: ...
```

Guard task creation with a short `asyncio.Lock`, keep one strong task reference,
update state to running before work, load/analyze/render/send in `_run()`, store
only safe error codes, notify the owner on failures, and clear current-task state
in `finally`. Empty input sends a no-data message without calling Codex.

- [ ] **Step 4: Write status and failure tests**

Assert status contains MTProto/Codex availability, both folder counts, current
task, last outcomes and schedules. Assert raw exception text and raw messages do
not appear. Assert a pipeline failure publishes no partial report and records a
safe error code.

- [ ] **Step 5: Replace the bot backend contract and buttons**

`DigestBotBackend` becomes:

```python
class DigestBotBackend(Protocol):
    async def status_text(self) -> str: ...
    async def request_digest(self) -> str: ...
    async def request_sentiment(self) -> str: ...
```

The exact persistent keyboard is:

```text
[ Дайджест сейчас ] [ Настроение сейчас ]
[ Статус ]
```

Remove history/latest/sources commands and handlers. Keep owner-only middleware,
safe error replies and chunked delivery.

- [ ] **Step 6: Write and implement scheduler tests**

Assert APScheduler receives two cron jobs in `Europe/Samara`: digest at 15:00
and sentiment at 16:00, both calling `orchestrator.request()` with
`RunTrigger.SCHEDULED`, `max_instances=1`, `coalesce=True`, and configured
misfire grace.

- [ ] **Step 7: Rewrite application composition**

`run_app(settings)` must create one MTProto session, connect authorized, create
folder selector, window loader, Codex client, pipeline, state store,
orchestrator, bot and scheduler. Run bot polling until signal or task failure.
On shutdown stop scheduler, cancel/wait report task, disconnect Telethon, close
Codex and bot sessions, and leave state consistent.

- [ ] **Step 8: Run Task 6 tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_report_orchestrator.py tests/unit/test_stateless_scheduler.py tests/unit/telegram/test_bot_controls.py tests/unit/telegram/test_bot_helpers.py`  
Expected: PASS.

- [ ] **Step 9: Commit the runtime checkpoint when Git is available**

```bash
git add src/market_digest/application.py src/market_digest/reports src/market_digest/telegram tests/unit/test_report_orchestrator.py tests/unit/test_stateless_scheduler.py tests/unit/telegram
git commit -m "feat: run scheduled and manual reports in one service"
```

---

### Task 7: Remove PostgreSQL runtime and simplify the container

**Files:**
- Modify: `src/market_digest/__main__.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `Dockerfile`
- Rewrite: `docker-compose.yml`
- Delete: `alembic.ini`
- Delete: `alembic/`
- Delete: `src/market_digest/db/`
- Delete: `src/market_digest/services/bot_backend.py`
- Delete: `src/market_digest/services/digest.py`
- Delete: `src/market_digest/services/jobs.py`
- Delete: obsolete DB/job tests under `tests/integration/`, `tests/unit/test_bot_backend.py`, `tests/unit/test_manual_jobs.py`, `tests/unit/test_report_service.py`, and old DB renderer tests replaced by Task 4.
- Modify: `tests/unit/test_runtime_modes.py`

**Interfaces:**
- Consumes: the single-process `run_app()` from Task 6.
- Produces: Compose services `app` and profile-only `codex-login`; volumes `telegram_session`, `codex_auth`, `runtime_state`.

- [ ] **Step 1: Write the failing CLI/runtime test**

```python
def test_cli_has_no_database_worker_modes() -> None:
    parser = _parser()
    assert parser.parse_args([]).command == "app"
    for removed in ("worker", "run"):
        with pytest.raises(SystemExit):
            parser.parse_args([removed])
```

- [ ] **Step 2: Run the runtime test and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_runtime_modes.py`  
Expected: FAIL because `worker` and `run` are still accepted.

- [ ] **Step 3: Remove legacy runtime imports and dependencies**

Keep CLI commands `app`, `login`, `check-config`, `codex-preflight`. Remove
Alembic, asyncpg and SQLAlchemy dependencies and the PostgreSQL integration
marker. Delete only modules/tests made obsolete by the approved design; keep
generic Telegram, Codex, chunking and window utilities.

- [ ] **Step 4: Regenerate the lock file**

Run: `uv lock`  
Expected: success; `uv.lock` no longer contains direct project dependencies on
Alembic, asyncpg or SQLAlchemy.

- [ ] **Step 5: Rewrite Docker image and Compose**

Dockerfile must create and own `/data/telegram`, `/data/codex`, `/data/state`
and `/tmp/market-digest`, and must stop copying Alembic files.

Compose target shape:

```yaml
services:
  app:
    image: market-digest:mvp
    command: ["python", "-m", "market_digest", "app"]
    env_file: [.env]
    init: true
    restart: unless-stopped
    stop_grace_period: 60s
    read_only: true
    tmpfs:
      - /tmp/market-digest:uid=10001,gid=10001,mode=0700
    volumes:
      - telegram_session:/data/telegram
      - codex_auth:/data/codex
      - runtime_state:/data/state
  codex-login:
    image: market-digest:mvp
    command: ["codex", "login", "--device-auth"]
    profiles: ["setup"]
    stdin_open: true
    tty: true
    volumes:
      - codex_auth:/data/codex
volumes:
  telegram_session:
  codex_auth:
  runtime_state:
```

- [ ] **Step 6: Run all static and unit checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
docker compose config --quiet
docker build -t market-digest:mvp .
```

Expected: all commands exit 0; no DB integration test is skipped because it has
been removed.

- [ ] **Step 7: Commit the runtime-removal checkpoint when Git is available**

```bash
git add -A
git commit -m "refactor: remove postgres runtime and worker"
```

---

### Task 8: Documentation and safe server rollout

**Files:**
- Rewrite: `README.md`
- Modify: `.dockerignore` only if new local runtime artifacts require exclusion.
- No access: `/root/bots` on the server.

**Interfaces:**
- Documents the final operator workflow and rollback boundary.

- [ ] **Step 1: Rewrite the runbook around the approved architecture**

Document `.env`, build, MTProto login, Codex login/preflight, Telegram folder
names, buttons, schedules, status fields, logs, restart, backup volumes and
explicit prohibition on `docker compose down -v` during migration.

- [ ] **Step 2: Verify documentation commands against Compose**

Run each read-only command from the new README locally:

```bash
docker compose config --quiet
docker compose config --services
```

Expected services: `app` and `codex-login`; no `postgres`, `migrate`, or `worker`.

- [ ] **Step 3: Upload code without overwriting server secrets**

Copy project sources, Dockerfile and Compose to `/opt/market-digest`, excluding
`.env`, session files, auth files and all volumes. Confirm `.env` remains mode
`600`.

- [ ] **Step 4: Build and preflight before stopping the old deployment**

On the server:

```bash
cd /opt/market-digest
docker build -t market-digest:mvp .
docker compose config --quiet
docker compose run -T --rm app python -m market_digest check-config
docker compose run -T --rm app python -m market_digest codex-preflight
```

Expected: config and Codex preflight pass using preserved credentials.

- [ ] **Step 5: Cut over without deleting volumes**

Using the old Compose definition saved before upload, stop only the old project
containers; then use the new Compose file:

```bash
docker stop market-digest-app-1 market-digest-worker-1 market-digest-postgres-1
cd /opt/market-digest
docker compose up -d --remove-orphans app
```

Do not run `down -v`. Confirm `market-digest_postgres_data` still appears in
`docker volume ls` as the rollback backup.

- [ ] **Step 6: Verify the live service**

```bash
docker compose ps
docker inspect --format='{{.Name}} {{.State.Status}} restarts={{.RestartCount}}' market-digest-app-1
docker compose logs --no-color --tail=200 app
systemctl is-active moexbot.service zhan-klod-bot.service caddy.service
```

Expected: only the new market-digest `app` runs, restart count is 0, folders are
discovered, no error-level log appears, and all pre-existing services remain
`active`.

- [ ] **Step 7: Perform owner-visible acceptance checks**

Ask the owner to press `Статус`, then `Дайджест сейчас`, wait for completion,
then `Настроение сейчас`. Verify the status shows folder/source counts and the
two schedules; the digest includes only `Посты`; the mood report includes only
`Чаты`, has an index 1–10 and detailed explanation; no second run starts during
an active task.

- [ ] **Step 8: Record rollback point and defer deletion**

Record the old image id and preserved volume name in the deployment report.
Leave `market-digest_postgres_data` intact until the owner separately approves
deletion after a stable observation period.

- [ ] **Step 9: Commit documentation when Git is available**

```bash
git add README.md .dockerignore
git commit -m "docs: document stateless telegram report deployment"
```

---

## Final Verification

- [ ] Run `.\.venv\Scripts\python.exe -m pytest -q` and confirm zero failures/skips.
- [ ] Run `.\.venv\Scripts\ruff.exe check src tests` and confirm zero findings.
- [ ] Run `.\.venv\Scripts\mypy.exe src` and confirm success.
- [ ] Run `docker compose config --quiet` and confirm success.
- [ ] Build `market-digest:mvp` from a clean Docker layer and confirm exit 0.
- [ ] Run server Codex preflight and confirm `gpt-5.6-luna/high Structured Outputs preflight passed`.
- [ ] Confirm live `app` is running with zero restarts and logs contain no errors.
- [ ] Confirm the old PostgreSQL volume still exists but no PostgreSQL container is running.
- [ ] Confirm `/root/bots` was not accessed or modified and existing services are active.
- [ ] Confirm both owner-visible reports and `Статус` meet the acceptance criteria.
