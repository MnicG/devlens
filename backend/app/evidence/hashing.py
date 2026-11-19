"""HMAC do trecho e identificadores determinísticos (ADR 0008, docs/04 seção 3)."""

from __future__ import annotations

import hashlib
import hmac
import os

HMAC_KEY_ENV = "DEVLENS_HMAC_KEY"
MIN_KEY_BYTES = 16


def load_hmac_key(environ: dict[str, str] | None = None) -> bytes:
    """Lê a chave local da instalação; ela nunca vai para banco nem para o git."""
    env = os.environ if environ is None else environ
    value = env.get(HMAC_KEY_ENV, "")
    key = value.encode("utf-8")

    if len(key) < MIN_KEY_BYTES:
        raise RuntimeError(
            f"{HMAC_KEY_ENV} must be set to at least {MIN_KEY_BYTES} characters"
        )

    return key


def split_lines(content: str) -> list[str]:
    """Divisão de linhas única, usada na criação e na verificação."""
    return content.splitlines()


def snippet_text(lines: list[str], line_start: int, line_end: int) -> str:
    return "\n".join(lines[line_start - 1 : line_end])


def snippet_hmac(key: bytes, text: str) -> str:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def evidence_id(
    repo: str,
    commit: str,
    kind: str,
    path: str | None,
    line_start: int | None,
    line_end: int | None,
    ref: str | None = None,
) -> str:
    parts = [repo, commit, kind, path or "", str(line_start), str(line_end), ref or ""]
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"ev_{digest[:16]}"
