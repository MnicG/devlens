"""Regra de confiança determinística (docs/04, seção 5; ADR 0004).

Confiança, status e justificativa vêm só de código e de template: nenhum texto
livre do LLM entra aqui.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from app.schemas.contracts import (
    Confidence,
    Evidence,
    EvidenceKind,
    Finding,
    FindingStatus,
    VerifyStatus,
)

STRONG_KINDS = {EvidenceKind.CODE, EvidenceKind.TEST}

_KIND_LABEL = {
    EvidenceKind.CODE: "código",
    EvidenceKind.TEST: "teste",
    EvidenceKind.COMMIT: "commit",
    EvidenceKind.ISSUE: "issue",
}

_DOWNGRADE = {
    Confidence.HIGH: Confidence.MEDIUM,
    Confidence.MEDIUM: Confidence.LOW,
    Confidence.LOW: Confidence.LOW,
}


def _describe(evidence: Evidence) -> str:
    label = _KIND_LABEL[evidence.kind]

    if evidence.kind in STRONG_KINDS:
        if evidence.line_start == evidence.line_end:
            where = f"{evidence.path}:{evidence.line_start}"
        else:
            where = f"{evidence.path}:{evidence.line_start}-{evidence.line_end}"
        return f"{label} em `{where}`"

    if evidence.kind is EvidenceKind.COMMIT:
        return f"{label} `{(evidence.ref or '')[:7]}`"

    return f"{label} `#{evidence.ref}`"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " e " + items[-1]


def _base_confidence(verified: list[Evidence]) -> Confidence:
    kinds = Counter(item.kind for item in verified)
    has_strong = any(kind in STRONG_KINDS for kind in kinds)

    if len(verified) >= 2 and len(kinds) >= 2 and has_strong:
        return Confidence.HIGH

    if has_strong or any(count >= 2 for count in kinds.values()):
        return Confidence.MEDIUM

    return Confidence.LOW


def score_finding(
    finding: Finding,
    evidences: Mapping[str, Evidence],
) -> Finding:
    """Atribui `confidence`, `confidence_rationale` e `status` ao achado.

    Ids desconhecidos ou ainda não verificados contam como evidência que não
    passou na verificação; por isso o `verify_evidence` roda antes.
    """
    supporting = [evidences.get(item) for item in finding.evidence_ids]
    verified = [
        item
        for item in supporting
        if item is not None and item.verify_status is VerifyStatus.VERIFIED
    ]
    failed_count = len(supporting) - len(verified)

    if not verified:
        return finding.model_copy(
            update={
                "confidence": None,
                "confidence_rationale": "Nenhuma evidência verificada.",
                "status": FindingStatus.DISCARDED,
            }
        )

    confidence = _base_confidence(verified)
    status = FindingStatus.KEPT
    notes: list[str] = []

    if failed_count:
        confidence = _DOWNGRADE[confidence]
        status = FindingStatus.DOWNGRADED
        notes.append(
            f"{failed_count} evidência(s) não passaram na verificação"
            " e rebaixaram a confiança"
        )

    if finding.contradicting_evidence_ids:
        if confidence is Confidence.HIGH:
            confidence = Confidence.MEDIUM
        notes.append("há evidências contraditórias, o que limita a confiança a média")

    plural = "evidência verificada" if len(verified) == 1 else "evidências verificadas"
    rationale = f"{len(verified)} {plural}: {_join([_describe(i) for i in verified])}"

    if notes:
        rationale += "; " + "; ".join(notes)

    return finding.model_copy(
        update={
            "confidence": confidence,
            "confidence_rationale": rationale + ".",
            "status": status,
        }
    )


def score_findings(
    findings: Iterable[Finding],
    evidences: Mapping[str, Evidence],
) -> list[Finding]:
    return [score_finding(finding, evidences) for finding in findings]
