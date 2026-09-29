# Codex CLI server mode implementation plan

> Execute test-first. The approved specification is
> `docs/superpowers/specs/2026-09-15-codex-cli-server-bot-controls-design.md`.

**Goal:** Replace API-key semantic calls with fail-closed Codex CLI calls and
add owner-only 24-hour digest, sentiment, history, and status controls while
preserving the daily 15:00 Samara digest.

**Architecture:** One image exposes separate `app` and `worker` process modes.
The app collects Telegram data, serves aiogram, and schedules PostgreSQL jobs.
The worker alone owns the Codex login volume and executes the existing strict
four-pass Luna pipeline.

**Stack:** Python 3.12, aiogram 3, Telethon, Pydantic, SQLAlchemy async,
PostgreSQL, Alembic, APScheduler, Codex CLI, pytest, Docker Compose.

## Global constraints

- Model is exactly `gpt-5.6-luna`, effort exactly `high`, no fallback.
- Telegram content is untrusted data and never shell command text.
- All semantic work uses strict JSON Schema plus Pydantic validation.
- Both manual analyses and the scheduled digest cover exactly the trailing
  24-hour window using `start < timestamp <= end`.
- Manual requests enqueue and return quickly; AI never runs in bot handlers.
- Collection remains running during AI failures.
- No API key is required in Codex CLI mode.
- Existing credentials must not appear in example files, logs, tests, or docs.

## Task 1: Codex CLI configuration and adapter

**Files:**

- Create `tests/unit/test_codex_cli_client.py`
- Modify `tests/unit/test_config.py`
- Modify `src/market_digest/config.py`
- Replace `src/market_digest/ai/client.py`
- Modify `src/market_digest/errors.py`
- Modify `src/market_digest/application.py`
- Modify `src/market_digest/__main__.py`

**Steps:**

1. Write failing tests for immutable model/effort, subprocess arguments,
   untrusted stdin, schema generation, timeout, nonzero exit, invalid/missing
   output, status/preflight, and no API key requirement.
2. Run the new tests and confirm they fail for the expected missing adapter.
3. Introduce a transport-neutral structured client protocol and implement
   `CodexCliClient` with `asyncio.create_subprocess_exec`, no shell, private
   temporary files, output limit, timeout, and Pydantic validation.
4. Replace API settings with Codex executable/home/timeout/budget settings while
   retaining hard Luna/high validation.
5. Rename the preflight CLI command and composition functions.
6. Run focused tests, then config and AI contract tests.

## Task 2: Report kinds, durable jobs, and 24-hour windows

**Files:**

- Create `tests/unit/test_manual_jobs.py`
- Modify `tests/unit/test_windows.py`
- Modify `src/market_digest/db/enums.py`
- Modify `src/market_digest/db/models.py`
- Modify `src/market_digest/db/repositories.py`
- Modify `src/market_digest/services/windows.py`
- Modify `src/market_digest/services/jobs.py`
- Create an Alembic revision under `alembic/versions/`

**Steps:**

1. Write failing tests for channel/sentiment job kinds, exact trailing 24-hour
   windows, stable dedupe keys, and queued/running duplicate detection.
2. Add explicit report/trigger kinds and additive nullable columns required for
   manual jobs without breaking existing digest history.
3. Add a coordinator method that queues an owner-requested report and returns a
   typed acknowledgement.
4. Keep scheduled checkpoints independent from manual jobs.
5. Run focused unit tests and database integration tests.

## Task 3: Separate channel digest and chat sentiment pipeline execution

**Files:**

- Create `tests/unit/test_report_service.py`
- Modify `src/market_digest/ai/schemas.py`
- Modify `src/market_digest/ai/prompts.py`
- Modify `src/market_digest/ai/pipeline.py`
- Modify `src/market_digest/services/digest.py`
- Modify `src/market_digest/services/jobs.py`

**Steps:**

1. Write failing tests proving channel reports only load channel posts, sentiment
   reports only load chat messages, both use four Luna passes, and failed
   verification prevents delivery.
2. Add explicit report-kind input to the pipeline prompts/schemas while retaining
   complete extraction fields and lineage.
3. Generalize the digest service to an analysis service that persists both kinds
   in the existing compatible storage.
4. Ensure only scheduled success advances the scheduled checkpoint.
5. Run focused service and AI contract/eval tests.

## Task 4: Owner bot buttons, history, and status

**Files:**

- Create `tests/unit/telegram/test_bot_controls.py`
- Modify `tests/unit/telegram/test_bot_helpers.py`
- Modify `src/market_digest/telegram/models.py`
- Modify `src/market_digest/telegram/bot.py`
- Modify `src/market_digest/services/bot_backend.py`
- Modify `src/market_digest/application.py`

**Steps:**

1. Write failing tests for the four Russian buttons, owner-only callback/message
   handling, correct job mapping, immediate acknowledgement, duplicate response,
   ten-item history, and expanded status.
2. Add a persistent reply keyboard and handlers for `Дайджест сейчас`,
   `Настроение рынка`, `История`, and `Статус`.
3. Retain compatible slash commands for recovery/administration.
4. Wire job enqueue operations through the backend protocol.
5. Run all Telegram and backend tests.

## Task 5: Split process modes and Docker deployment

**Files:**

- Create `tests/unit/test_runtime_modes.py`
- Modify `src/market_digest/application.py`
- Modify `src/market_digest/__main__.py`
- Modify `Dockerfile`
- Modify `docker-compose.yml`
- Modify `.dockerignore`

**Steps:**

1. Write failing tests for CLI process-mode selection and safe settings output.
2. Split `run_app` from `run_worker`; the app owns collector/bot/scheduler and the
   worker owns Codex preflight/job processing.
3. Install a pinned Codex CLI release in the image and run as an unprivileged
   user.
4. Add separate Compose services and volumes so only the worker sees Codex auth
   and only the app sees the Telethon session.
5. Validate Compose configuration and build the image.

## Task 6: Documentation, examples, and server runbook

**Files:**

- Modify `.env.example`
- Modify `README.md`
- Modify `docs/AI_PIPELINE.md`

**Steps:**

1. Remove API-key setup and document ChatGPT-managed Codex CLI authentication.
2. Document server requirements, firewall expectations, device-code login,
   Telegram MTProto login, BotFather setup, credential rotation, launch, logs,
   backups, upgrades, status checks, and smoke tests.
3. Document the exact four-button behaviour and 24-hour windows.
4. Verify no secret-looking values remain in tracked project text.

## Task 7: Full verification and independent review

**Files:**

- Modify `tests/fixtures/ai_eval_cases.jsonl` if review finds coverage gaps
- Modify tests/docs only for discovered defects

**Steps:**

1. Incorporate the dedicated Luna reviewer findings into schemas, prompts, and
   eval fixtures.
2. Run Ruff, mypy, all unit tests, database integration tests, and coverage.
3. Run Docker build and Compose validation.
4. Perform a final code/security review focused on command injection, auth file
   exposure, model fallback, source lineage, and job idempotency.
5. Report what is locally verified and clearly separate the target-server checks
   that require the owner's credentials and server access.

