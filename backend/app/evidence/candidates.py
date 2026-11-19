"""Construção de candidatos a evidência.

As ferramentas devolvem candidatos com ``id`` vazio: só o Evidence Store
atribui o ``id`` determinístico (docs/04, seção 3).
"""

from __future__ import annotations

from app.evidence.hashing import snippet_hmac, snippet_text, split_lines
from app.gateway.redaction import redact_text
from app.schemas.contracts import Evidence, EvidenceKind

EXCERPT_MAX_CHARS = 600


def make_excerpt(text: str) -> str:
    """Redige segredos e trunca no limite do contrato."""
    return redact_text(text).text[:EXCERPT_MAX_CHARS]


def code_candidate(
    *,
    repo: str,
    commit: str,
    path: str,
    content: str,
    line_start: int,
    line_end: int,
    hmac_key: bytes,
    kind: EvidenceKind = EvidenceKind.CODE,
) -> Evidence:
    """Candidato `code` ou `test`: HMAC do trecho original, excerpt redigido."""
    lines = split_lines(content)

    if not 1 <= line_start <= line_end <= len(lines):
        raise ValueError("Line range is outside the file")

    original = snippet_text(lines, line_start, line_end)

    return Evidence(
        id="",
        repo=repo,
        commit=commit,
        kind=kind,
        path=path,
        line_start=line_start,
        line_end=line_end,
        snippet_hash=snippet_hmac(hmac_key, original),
        excerpt=make_excerpt(original),
    )


def commit_candidate(
    *,
    repo: str,
    commit: str,
    sha: str,
    summary: str,
    path: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
) -> Evidence:
    return Evidence(
        id="",
        repo=repo,
        commit=commit,
        kind=EvidenceKind.COMMIT,
        path=path,
        line_start=line_start,
        line_end=line_end,
        excerpt=make_excerpt(summary),
        ref=sha,
    )
