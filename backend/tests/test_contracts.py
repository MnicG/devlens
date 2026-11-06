import pytest
from app.schemas.contracts import (
    AnalysisOptions,
    AnalysisRequest,
    Budget,
    Category,
    Evidence,
    EvidenceKind,
    Finding,
    InputKind,
    RepoRef,
    RepoSourceKind,
    Severity,
    TraceEvent,
    TraceEventType,
    VerifyStatus,
)
from pydantic import ValidationError


def test_analysis_request_uses_documented_defaults() -> None:
    request = AnalysisRequest(
        kind=InputKind.ISSUE,
        repo=RepoRef(source=RepoSourceKind.LOCAL, location="/tmp/repo"),
        ref="corrigir autenticação",
    )

    assert request.options == AnalysisOptions(
        budget=Budget(max_tokens=60_000, max_tool_calls=40, max_steps=12),
        local_only=False,
    )


def test_analysis_request_rejects_empty_ref() -> None:
    with pytest.raises(ValidationError):
        AnalysisRequest(
            kind=InputKind.ISSUE,
            repo=RepoRef(source=RepoSourceKind.LOCAL, location="/tmp/repo"),
            ref="",
        )


def test_evidence_uses_documented_defaults() -> None:
    evidence = Evidence(
        id="ev_example",
        repo="/tmp/repo",
        commit="abc123",
        kind=EvidenceKind.CODE,
    )

    assert evidence.excerpt == ""
    assert evidence.verify_status == VerifyStatus.PENDING


def test_finding_requires_at_least_one_evidence_id() -> None:
    with pytest.raises(ValidationError):
        Finding(
            id="finding-1",
            category=Category.CORRECTNESS,
            severity=Severity.HIGH,
            title="Falha",
            summary="Resumo",
            evidence_ids=[],
        )


def test_trace_event_rejects_summaries_longer_than_300_chars() -> None:
    with pytest.raises(ValidationError):
        TraceEvent(
            id=1,
            analysis_id="analysis-1",
            seq=1,
            ts="2026-11-06T12:00:00Z",
            type=TraceEventType.NODE_START,
            node="intake",
            name="intake",
            input_summary="x" * 301,
        )
