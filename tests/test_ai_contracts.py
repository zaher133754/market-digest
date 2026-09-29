from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from market_digest.ai.schemas import (
    ClaimAudit,
    ClaimAuditBatch,
    ComparisonResult,
    DigestDraft,
    ExtractionBatch,
    SourceMessage,
)


def _assert_closed_required_objects(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            assert node.get("additionalProperties") is False
            assert set(node.get("required", [])) == set(node["properties"])
        for value in node.values():
            _assert_closed_required_objects(value)
    elif isinstance(node, list):
        for value in node:
            _assert_closed_required_objects(value)


@pytest.mark.parametrize(
    "schema_type",
    [ExtractionBatch, ComparisonResult, DigestDraft, ClaimAuditBatch],
)
def test_structured_output_schemas_are_closed_and_fully_required(schema_type: type) -> None:
    _assert_closed_required_objects(schema_type.model_json_schema())


def test_contracts_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SourceMessage.model_validate(
            {
                "message_ref": "channel_post:1",
                "record_kind": "channel_post",
                "record_id": "1",
                "message_id": 10,
                "source_id": -1001,
                "source_title": "Канал",
                "source_username": None,
                "source_kind": "channel",
                "published_at": "2026-08-27T10:00:00+04:00",
                "author_ref": "source:-1001",
                "author_display_name": "Канал",
                "text": "Сообщение",
                "permalink": None,
                "invented": True,
            }
        )


def test_supported_claim_requires_every_audit_dimension() -> None:
    with pytest.raises(ValidationError, match="every ClaimAudit quality flag"):
        ClaimAudit(
            claim_id="c1",
            supported=True,
            facts_supported=True,
            numbers_dates_tickers_correct=True,
            attribution_correct=True,
            fact_opinion_separation_correct=False,
            advertising_removed=True,
            disagreements_preserved=True,
            allowed_evidence_refs=["channel_post:1"],
            issues=["classification changed"],
            corrected_text=None,
        )
