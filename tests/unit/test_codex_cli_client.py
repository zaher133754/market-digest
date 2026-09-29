from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from market_digest.ai.client import CodexCliClient
from market_digest.ai.schemas import PreflightResult
from market_digest.errors import LunaContractError, LunaUnavailableError


@pytest.fixture(autouse=True)
def windows_test_temp_acl(monkeypatch):
    if os.name != "nt":
        return

    @contextmanager
    def inherited_directory(*, prefix, dir):
        directory = Path(dir) / (prefix + uuid.uuid4().hex)
        directory.mkdir()
        try:
            yield str(directory)
        finally:
            shutil.rmtree(directory)

    # Exercise the same create/write/parse/cleanup lifecycle using workspace ACLs.
    monkeypatch.setattr("market_digest.ai.client.tempfile.TemporaryDirectory", inherited_directory)


class FakeProcess:
    def __init__(self, *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.stdin_payload: bytes | None = None
        self.killed = False

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.stdin_payload = input
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


class RecordingFactory:
    def __init__(self, result: dict[str, Any] | None, *, returncode: int = 0) -> None:
        self.result = result
        self.returncode = returncode
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []
        self.processes: list[FakeProcess] = []

    async def __call__(self, *args: str, **kwargs: Any) -> FakeProcess:
        self.calls.append((args, kwargs))
        stdout = b""
        if args == ("codex", "login", "status"):
            stdout = b"Logged in using ChatGPT"
        elif args == ("codex", "--version"):
            stdout = b"codex-cli 0.154.0"
        process = FakeProcess(returncode=self.returncode, stdout=stdout, stderr=b"safe failure")
        self.processes.append(process)
        if "--output-last-message" in args and self.result is not None:
            output_path = Path(args[args.index("--output-last-message") + 1])
            await asyncio.to_thread(
                output_path.write_text,
                json.dumps(self.result),
                encoding="utf-8",
            )
        return process


@pytest.mark.asyncio
async def test_codex_client_hard_codes_luna_high_and_schema(settings: Any) -> None:
    factory = RecordingFactory({"status": "ok"})
    client = CodexCliClient(settings, process_factory=factory)

    await client.parse(
        operation="preflight",
        system_prompt="Trusted instructions",
        payload="{}",
        schema=PreflightResult,
    )

    args, kwargs = factory.calls[-1]
    assert args[:2] == ("codex", "exec")
    assert args[args.index("--model") + 1] == "gpt-5.6-luna"
    assert args[args.index("--config") + 1] == 'model_reasoning_effort="high"'
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in args
    assert "--strict-config" in args
    disabled = [args[index + 1] for index, item in enumerate(args) if item == "--disable"]
    assert {"shell_tool", "unified_exec", "apps", "hooks", "multi_agent"} <= set(disabled)
    assert 'web_search="disabled"' in args
    assert "--ignore-user-config" in args
    assert "--output-schema" in args
    assert "shell" not in kwargs
    assert kwargs["env"]["CODEX_HOME"] == str(settings.codex_home)
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert "CODEX_API_KEY" not in kwargs["env"]
    assert 'forced_login_method="chatgpt"' in args
    assert 'model_provider="openai"' in args

    schema_path = Path(args[args.index("--output-schema") + 1])
    # The temporary directory is gone, proving schemas/results are cleaned up.
    assert not await asyncio.to_thread(os.path.exists, schema_path)


@pytest.mark.asyncio
async def test_codex_client_sends_telegram_payload_only_via_stdin(settings: Any) -> None:
    marker = "UNTRUSTED; rm -rf /"
    factory = RecordingFactory({"status": "ok"})
    client = CodexCliClient(settings, process_factory=factory)

    result = await client.parse(
        operation="test",
        system_prompt="Trusted instructions",
        payload=marker,
        schema=PreflightResult,
    )

    args, _ = factory.calls[0]
    assert marker not in args
    assert factory.processes[0].stdin_payload == marker.encode()
    assert result == PreflightResult(status="ok")


@pytest.mark.asyncio
async def test_codex_client_rejects_nonzero_exit(settings: Any) -> None:
    client = CodexCliClient(settings, process_factory=RecordingFactory(None, returncode=1))
    with pytest.raises(LunaUnavailableError, match="Codex CLI failed"):
        await client.parse(
            operation="test",
            system_prompt="Trusted instructions",
            payload="{}",
            schema=PreflightResult,
        )


@pytest.mark.asyncio
async def test_codex_client_rejects_missing_or_invalid_output(settings: Any) -> None:
    missing = CodexCliClient(settings, process_factory=RecordingFactory(None))
    with pytest.raises(LunaContractError, match="no structured output"):
        await missing.parse(
            operation="test",
            system_prompt="Trusted instructions",
            payload="{}",
            schema=PreflightResult,
        )

    invalid = CodexCliClient(settings, process_factory=RecordingFactory({"status": "wrong"}))
    with pytest.raises(LunaContractError, match="violated PreflightResult"):
        await invalid.parse(
            operation="test",
            system_prompt="Trusted instructions",
            payload="{}",
            schema=PreflightResult,
        )


@pytest.mark.asyncio
async def test_codex_login_status_is_checked_without_api_keys(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-leak")
    factory = RecordingFactory(None)
    client = CodexCliClient(settings, process_factory=factory)

    await client.check_login()

    args, kwargs = factory.calls[0]
    assert args == ("codex", "login", "status")
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert "CODEX_API_KEY" not in kwargs["env"]


@pytest.mark.asyncio
async def test_codex_login_status_accepts_chatgpt_message_from_stderr(settings: Any) -> None:
    class StderrLoginFactory(RecordingFactory):
        async def __call__(self, *args: str, **kwargs: Any) -> FakeProcess:
            process = await super().__call__(*args, **kwargs)
            if args == ("codex", "login", "status"):
                process.stdout = b""
                process.stderr = b"Logged in using ChatGPT"
            return process

    client = CodexCliClient(settings, process_factory=StderrLoginFactory(None))

    await client.check_login()


@pytest.mark.asyncio
async def test_codex_preflight_checks_pinned_version_and_chatgpt_login(settings: Any) -> None:
    factory = RecordingFactory({"status": "ok"})
    await CodexCliClient(settings, process_factory=factory).preflight()
    assert factory.calls[0][0] == ("codex", "--version")
    assert factory.calls[1][0] == ("codex", "login", "status")


@pytest.mark.asyncio
async def test_codex_preflight_rejects_unexpected_version(settings: Any) -> None:
    class WrongVersionFactory(RecordingFactory):
        async def __call__(self, *args: str, **kwargs: Any) -> FakeProcess:
            process = await super().__call__(*args, **kwargs)
            if args == ("codex", "--version"):
                process.stdout = b"codex-cli 0.152.0"
            return process

    with pytest.raises(LunaContractError, match="version"):
        await CodexCliClient(settings, process_factory=WrongVersionFactory(None)).preflight()


@pytest.mark.asyncio
async def test_newer_cli_and_explicit_astra_override(settings):
    class CurrentFactory(RecordingFactory):
        async def __call__(self, *args, **kwargs):
            process = await super().__call__(*args, **kwargs)
            if args == ("codex", "--version"):
                process.stdout = b"codex-cli 0.157.1"
            return process

    factory = CurrentFactory({"status": "ok"})
    await CodexCliClient(settings, model="gpt-6-astra", process_factory=factory).preflight()
    args = factory.calls[-1][0]
    assert args[args.index("--model") + 1] == "gpt-6-astra"
    assert 'model_verbosity="medium"' in args


@pytest.mark.asyncio
async def test_api_key_login_is_rejected(settings):
    class ApiFactory(RecordingFactory):
        async def __call__(self, *args, **kwargs):
            process = await super().__call__(*args, **kwargs)
            process.stdout = b"Logged in using an API key"
            return process

    with pytest.raises(LunaUnavailableError, match="ChatGPT"):
        await CodexCliClient(settings, process_factory=ApiFactory(None)).check_login()
