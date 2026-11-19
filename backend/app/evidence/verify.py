"""verify_evidence: verificação determinística, sem LLM (ADR 0004).

Lê o arquivo no commit fixado, confere que as linhas existem e que o HMAC do
trecho bate. Para `commit` e `issue`, confere a existência da referência.
"""

from __future__ import annotations

import hmac
import re
from typing import Protocol

from app.evidence.hashing import snippet_hmac, snippet_text, split_lines
from app.evidence.store import EvidenceStore
from app.schemas.contracts import Evidence, EvidenceKind, VerifyStatus

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REF_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")


class VerifiableSource(Protocol):
    def read_file(self, commit: str, path: str) -> str: ...

    def resolve_commit(self, ref: str | None = None) -> str: ...


def _failed(evidence: Evidence, note: str) -> Evidence:
    return evidence.model_copy(
        update={"verify_status": VerifyStatus.FAILED, "verify_note": note}
    )


def _verified(evidence: Evidence, note: str) -> Evidence:
    return evidence.model_copy(
        update={"verify_status": VerifyStatus.VERIFIED, "verify_note": note}
    )


def _verify_snippet(
    evidence: Evidence,
    source: VerifiableSource,
    hmac_key: bytes,
) -> Evidence:
    if not SHA_PATTERN.match(evidence.commit):
        return _failed(evidence, "invalid commit sha")

    if (
        evidence.path is None
        or evidence.line_start is None
        or evidence.line_end is None
    ):
        return _failed(evidence, "evidence has no path or line range")

    if evidence.snippet_hash is None:
        return _failed(evidence, "evidence has no snippet hash")

    try:
        content = source.read_file(evidence.commit, evidence.path)
    except FileNotFoundError:
        return _failed(evidence, "file does not exist at the commit")
    except PermissionError:
        return _failed(evidence, "file cannot be read")
    except ValueError:
        return _failed(evidence, "file cannot be read as text")

    lines = split_lines(content)

    if not 1 <= evidence.line_start <= evidence.line_end <= len(lines):
        return _failed(evidence, "line range is outside the file")

    actual = snippet_hmac(
        hmac_key,
        snippet_text(lines, evidence.line_start, evidence.line_end),
    )

    if not hmac.compare_digest(actual, evidence.snippet_hash):
        return _failed(evidence, "snippet hash does not match the file at the commit")

    return _verified(evidence, "file, lines and snippet hash match")


def _verify_commit(evidence: Evidence, source: VerifiableSource) -> Evidence:
    if evidence.ref is None or not REF_PATTERN.match(evidence.ref):
        return _failed(evidence, "invalid commit reference")

    try:
        resolved = source.resolve_commit(evidence.ref)
    except ValueError:
        return _failed(evidence, "commit does not exist")

    if not resolved.startswith(evidence.ref):
        return _failed(evidence, "commit reference does not match")

    return _verified(evidence, "commit exists")


def _verify_issue(evidence: Evidence, source: object) -> Evidence:
    checker = getattr(source, "issue_exists", None)

    if checker is None or evidence.ref is None:
        return _failed(evidence, "source cannot verify issues")

    if not checker(evidence.ref):
        return _failed(evidence, "issue does not exist")

    return _verified(evidence, "issue exists")


def verify_evidence(
    evidence: Evidence,
    source: VerifiableSource,
    hmac_key: bytes,
) -> Evidence:
    """Devolve uma cópia da evidência com `verify_status` e `verify_note`."""
    if evidence.kind in {EvidenceKind.CODE, EvidenceKind.TEST}:
        return _verify_snippet(evidence, source, hmac_key)

    if evidence.kind is EvidenceKind.COMMIT:
        return _verify_commit(evidence, source)

    return _verify_issue(evidence, source)


def verify_all(
    store: EvidenceStore,
    source: VerifiableSource,
    hmac_key: bytes,
) -> list[Evidence]:
    """Etapa `verify_evidence` do grafo: verifica e persiste cada evidência."""
    results = []

    for evidence in store.all():
        verified = verify_evidence(evidence, source, hmac_key)
        store.set_verification(
            verified.id,
            verified.verify_status,
            verified.verify_note,
        )
        results.append(verified)

    return results
