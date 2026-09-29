from __future__ import annotations

import pytest

from market_digest.__main__ import _parser


def test_cli_has_no_database_worker_modes() -> None:
    parser = _parser()
    assert parser.parse_args([]).command == "app"
    assert parser.parse_args(["app"]).command == "app"
    assert parser.parse_args(["codex-preflight"]).command == "codex-preflight"
    for removed in ("worker", "run"):
        with pytest.raises(SystemExit):
            parser.parse_args([removed])
