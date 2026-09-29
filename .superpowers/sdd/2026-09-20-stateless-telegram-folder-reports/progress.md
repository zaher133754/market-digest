# SDD ledger — plan: docs/superpowers/plans/2026-09-20-stateless-telegram-folder-reports.md

Setup: baseline `.\.venv\Scripts\python.exe -m pytest -q` → 76 passed, 1 skipped.

Ruling: workspace is not a Git repository, so a worktree, BASE commits, task-start/task-done scripts, commits, and review-package cannot operate. Work in place without initializing Git; preserve progress in this ledger and use explicit per-task RED/GREEN commands. Cost if wrong: rollback relies on copied files and the preserved server image/volumes rather than Git history.

Pre-flight Task 1 → Tasks 3/4/5/6: report model and Settings names are consistent.
Pre-flight Task 2 → Task 3: `TelegramFolderSelector.select()` and `FolderSnapshot` are consumed consistently.
Pre-flight Task 3 → Tasks 4/6: `TelegramWindowLoader.load() -> ReportInput` is consumed consistently.
Pre-flight Task 4 → Task 6: pipeline and renderer outputs are consumed consistently.
Pre-flight Task 5 → Task 6: `JsonStateStore` and runtime records are consumed consistently.
Pre-flight Task 6 → Tasks 7/8: single-process `run_app()` is the only runtime entrypoint.
Pre-flight Task 7 → Task 8: Compose service and volume names are consistent.

Task 1: Ruling: check `runtime_state_path` defaults on a directly constructed `Settings`, not the shared fixture, because the fixture deliberately redirects persistence into the workspace. Cost if wrong: a production default regression could be hidden by the test fixture.

Task 1: complete (no Git commits; tests: Task 1 → 16 passed; full suite → 87 passed, 1 skipped).

Task 2: complete (no Git commits; tests: Task 2 → 8 passed; full suite → 90 passed, 1 skipped).

Task 3: complete (no Git commits; tests: Task 3 → 4 passed; full suite → 94 passed, 1 skipped).

Task 4: complete (no Git commits; tests: Task 4 → 20 passed; full suite → 100 passed, 1 skipped).

Task 5: complete (no Git commits; tests: Task 5 → 4 passed; full suite → 104 passed, 1 skipped before obsolete DB tests were removed).

Task 6: complete (no Git commits; targeted runtime tests → 11 passed; full suite became green after the planned Task 7 CLI removal).

Task 7: complete (no Git commits; PostgreSQL/worker runtime removed; lock regenerated; tests → 93 passed; Ruff and mypy clean; Compose valid; local Docker daemon unavailable, server build succeeded).

Task 8: deployed to `/opt/market-digest` without copying secrets or touching `/root/bots`. Old image `sha256:695cf755759056d4c2e5c334c712fbfa9a68ae16f8ed897e11ab1c26434a1f56` and rollback tree `/opt/market-digest-rollback-20260920` preserved; PostgreSQL volume `market-digest_postgres_data` preserved. Final post-review tests: 94 passed; server image `sha256:76707f993e2a731668e6e66148fd2cff4f9a2c24899e418c1fda87a79c9dfeda`; Codex preflight passed; app running with 0 restarts. Owner-visible button/report acceptance remains a manual Telegram check.
