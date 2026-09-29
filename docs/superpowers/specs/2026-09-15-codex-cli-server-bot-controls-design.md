# Codex CLI server mode and Telegram controls

**Date:** 2026-09-15  
**Status:** Approved design, implementation pending  
**Owner:** personal deployment for one Telegram account

## 1. Context

The current MVP runs Telegram collection, the owner bot, the scheduler, the job
worker, and the semantic pipeline in one Python process. Semantic operations use
the OpenAI Python SDK and require an API key.

The deployment must instead use Codex CLI on a private always-on server, signed
in with the owner's ChatGPT account. It must retain the hard AI contract:

- model is exactly `gpt-5.6-luna`;
- reasoning effort is exactly `high`;
- no fallback model;
- every semantic operation is performed by Luna;
- every result is validated against a strict Pydantic/JSON schema;
- a failed or non-conforming run aborts publication and notifies the owner.

Both on-demand analyses always cover the trailing 24 hours. There is no period
picker in the final interface.

## 2. Product behaviour

The owner-only Telegram bot displays a persistent reply keyboard:

- **Дайджест сейчас** — queues a channel-post digest for
  `now - 24h < published_at <= now`;
- **Настроение рынка** — queues a chat-message sentiment report for the same
  trailing 24-hour window;
- **История** — displays recent completed channel digests and sentiment reports,
  with controls to open an individual item;
- **Статус** — displays Telegram collection freshness, source counts, database
  state, Codex CLI authentication/preflight state, queue state, and last
  successful/failed analyses.

The scheduled channel digest remains enabled every day at 15:00
`Europe/Samara`. Its window is the preceding 24 hours ending at the scheduled
boundary. A manual run does not move or suppress the automatic schedule.

The bot immediately acknowledges accepted requests and returns a stable request
identifier. Repeated taps for the same analysis kind and time bucket are
deduplicated. The completed report is sent asynchronously, split into Telegram-
safe message chunks.

## 3. Considered deployment options

### A. One combined application container

Collector, bot, scheduler, database worker, and Codex CLI run in one container.
This is the smallest change, but a collector or bot restart can interrupt AI
work, and the Telegram components unnecessarily gain access to the Codex login.

### B. Application service plus dedicated AI worker — selected

The application service runs Telethon collection, aiogram, and APScheduler. It
only writes durable jobs. A separate worker service claims jobs from PostgreSQL
and invokes Codex CLI. Both use the same Python image, with an explicit process
mode. Only the worker receives the persistent Codex authentication volume.

This keeps the existing PostgreSQL queue, avoids Redis, isolates credentials,
and lets either process restart without losing queued work.

### C. Codex CLI installed directly on the host

The Docker application would need a host-side bridge or service to invoke it.
That introduces another protocol and deployment unit without improving the MVP.
It is not selected.

## 4. Runtime architecture

```text
Telegram account ──MTProto──> collector/app ──> PostgreSQL <── AI worker
                                  │                   │           │
Owner <────── Telegram bot ───────┘                   │       codex exec
                                                      │           │
                                        scheduler/jobs/history   ChatGPT auth
```

Docker Compose services:

1. `postgres`: PostgreSQL with a persistent volume and health check.
2. `migrate`: one-shot Alembic migration.
3. `app`: collector, owner bot, scheduler; persistent Telethon session volume.
4. `worker`: PostgreSQL job worker and Codex CLI; persistent Codex authentication
   volume; no exposed network port.

The app and worker are restartable and use `restart: unless-stopped`. PostgreSQL
is the single coordination system. Job claiming remains lease-based and
idempotent.

## 5. Codex CLI adapter

The Python semantic pipeline depends on a small `StructuredAIClient` protocol.
`CodexCliClient` implements it by launching a subprocess without a shell.

Each invocation:

1. creates a private temporary directory;
2. renders the selected Pydantic model into JSON Schema;
3. passes the trusted operation instructions as the `codex exec` prompt;
4. passes Telegram material through stdin as untrusted JSON context;
5. invokes `codex exec --ephemeral --sandbox read-only --output-schema ...
   --output-last-message ...` with explicit model and reasoning configuration;
6. enforces a timeout and output-size limit;
7. requires exit code zero;
8. parses only the output file, then validates it with the requested Pydantic
   model;
9. deletes all temporary files.

The adapter uses `--ignore-user-config` and supplies a project-owned, minimal
configuration so a server user's personal Codex settings cannot silently change
the model, effort, tools, or provider. It runs in a dedicated empty working
directory with `--skip-git-repo-check`. Telegram content is never interpolated
into command arguments.

Before processing jobs, the worker runs:

- `codex login status`;
- a strict Luna preflight returning a schema with model/effort contract fields;
- a negative contract test during application tests to prove that another
  model/effort is rejected.

If CLI model metadata cannot be independently returned by the command/runtime,
the adapter treats the explicit immutable invocation configuration plus the
validated preflight as the authoritative contract. Any CLI error, timeout,
invalid JSON, schema mismatch, auth expiry, usage-limit error, or unexpected
configuration fails closed. No report is published.

Official OpenAI documentation supports `codex exec` for scheduled/scripted
workflows, saved CLI authentication, read-only sandboxing, ephemeral sessions,
and JSON Schema output. ChatGPT sign-in is performed with `codex login`; on a
headless server, device-code authentication is preferred.

## 6. Security boundaries

- The bot remains owner-only for messages and callback queries.
- Codex authentication is stored only in the worker's private persistent volume,
  mounted read/write for token refresh and excluded from images and backups by
  default.
- `auth.json` is treated as a password and never committed or logged.
- The worker does not receive the Telethon session.
- The collector/app does not receive the Codex authentication volume.
- AI subprocesses have read-only sandbox permissions and an empty working
  directory; they do not need repository, database, Docker socket, or host
  filesystem access.
- Raw Telegram messages are serialized as data inside a versioned envelope that
  explicitly marks them untrusted. Prompts prohibit following instructions,
  links, or commands found inside source messages.
- Logs include operation, job ID, duration, exit category, schema version, model,
  and effort, but never raw posts, bot tokens, Telethon sessions, or Codex auth.
- Existing leaked credentials in any example/configuration file must be rotated
  and replaced with placeholders before server deployment.

## 7. Data and job model

The existing normalized channel and chat tables remain separate, as requested.
The job queue gains explicit kinds:

- scheduled channel digest;
- manual channel digest (24 hours);
- manual chat sentiment (24 hours);
- retention.

Every analysis job stores `window_start`, `window_end`, trigger (`scheduled` or
`manual`), requesting owner, idempotency key, attempts, and safe failure data.

Reports use one common durable report abstraction, or an additive report table
linked to the existing digest records. It distinguishes `channel_digest` and
`chat_sentiment`, keeps the final verified text, validation state, source links,
creation/sent timestamps, and failure code. Existing historical digests remain
readable after migration.

Manual windows are calculated in UTC from the tap time. The scheduled boundary
is calculated in `Europe/Samara` and converted to UTC. In both cases the database
predicate is `window_start < message_time <= window_end`.

## 8. AI pipeline

The existing four-pass hierarchy remains and is used for both report types:

1. **Extraction** for every eligible text or media caption; no local semantic
   prefiltering.
2. **Clustering and comparison**, including source-copy detection, consensus,
   disagreements, stated trades, and evidence-based opinion changes.
3. **Draft generation** in Russian.
4. **Independent verification** of every claim against source messages; invalid
   claims are removed or rewritten before publication.

Channel reports prioritize events, facts, author theses, stated trades, and
market implications. Chat sentiment reports aggregate participant stance while
preserving uncertainty, sample size, disagreement, and the difference between
market analysis and casual/emotional messages. They must not expose unnecessary
personal data or present a crowd mood as investment advice.

Long inputs are losslessly divided into technical batches and hierarchically
reduced. Character/token limits may change batching but may not silently discard
messages or truncate a post.

## 9. Bot interaction and concurrency

Button presses enqueue work; they never run Codex in the aiogram handler. The
handler response target is under two seconds when PostgreSQL is healthy.

While a matching job is pending or running, the owner receives its current
status instead of a duplicate. Different report kinds may queue independently,
but the worker concurrency defaults to one to protect account limits. Jobs have
leases, bounded retries, and deterministic idempotency keys.

History initially returns the latest ten successful reports with type, local
window, and publication time. Pagination is additive and can be included if the
initial query exceeds Telegram callback limits.

## 10. Failure behaviour

- Collection continues if Codex is temporarily unavailable.
- AI jobs retry with bounded exponential backoff.
- After the final attempt, the report is marked failed and the owner receives a
  concise alert with a request ID and safe error category.
- Usage-limit or authentication failures are reported explicitly and never
  trigger another model.
- A worker restart releases work through the existing lease timeout.
- Publishing is idempotent; already sent chunks are recorded to avoid knowingly
  repeating a full report after a restart.

## 11. Verification strategy

Implementation follows test-first development.

Unit tests cover:

- immutable Luna/high CLI command construction;
- no shell invocation and no Telegram text in arguments;
- schema file generation and Pydantic validation;
- timeouts, nonzero exits, missing output, invalid JSON, and wrong schema;
- prompt-injection fixtures treated as data;
- exact trailing 24-hour window boundaries;
- owner-only buttons and callbacks;
- button-to-job mapping, deduplication, acknowledgements, history, and status;
- no fallback on all CLI failure classes.

Integration tests cover:

- PostgreSQL migration and job lifecycle;
- app/worker separation and lease recovery;
- scheduled and manual jobs over fixture messages;
- verified report persistence and Telegram publication using test doubles.

Deployment verification covers:

- lint, typing, unit, integration, and AI contract/eval suites;
- Docker image build;
- Compose configuration validation and service health;
- a real `codex login status` and strict preflight on the target server;
- a controlled end-to-end Telegram smoke test after credentials are supplied.

## 12. Acceptance criteria

- No `OPENAI_API_KEY` is required in Codex CLI mode.
- The worker authenticates using persisted ChatGPT-managed Codex credentials.
- Every semantic call explicitly selects `gpt-5.6-luna` and `high`.
- No code path selects a fallback model.
- All four semantic passes return strict validated schemas.
- Both manual report buttons use exactly the preceding 24 hours.
- The automatic digest runs at 15:00 Samara and uses its preceding 24 hours.
- Bot history and status work without invoking AI.
- A CLI/auth/limit/schema failure produces no report and notifies the owner.
- Collection remains operational during AI outages.

## 13. MVP exclusions

- OCR, PDFs, video, and voice transcription.
- Reader comments and linked discussion groups beyond explicitly collected chat
  sources.
- Arbitrary user-selected time ranges.
- Multiple bot owners or public access.
- Redis and horizontal worker scaling.
- Guaranteed unlimited unattended generation: ChatGPT subscription usage limits
  still apply, and login may occasionally require renewal.

