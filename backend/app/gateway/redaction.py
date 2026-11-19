"""Redação de segredos (docs/06, seção 5).

Técnica em camadas, melhor esforço (não é garantia):
1. regras por padrão conhecido (chaves de provedores, tokens, PEM, JWT, URLs);
2. contexto por nome de variável (password, secret, token, ...);
3. entropia em literais longos.

O valor vira ``[REDACTED:<regra>]`` e as linhas são preservadas, para que a
numeração do código continue coerente. Nenhum valor secreto é guardado nos
resultados da redação, apenas a regra e a linha.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

DEFAULT_ENTROPY_THRESHOLD = 4.5
ENTROPY_MIN_LENGTH = 24


def _marker(rule: str) -> str:
    return f"[REDACTED:{rule}]"


@dataclass(frozen=True)
class RedactionFinding:
    rule: str
    line: int


@dataclass(frozen=True)
class RedactionResult:
    text: str
    findings: list[RedactionFinding] = field(default_factory=list)

    @property
    def redacted(self) -> bool:
        return bool(self.findings)


# 1. Regras por padrão conhecido (cada uma cobre o token inteiro)

TOKEN_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b"),
    ),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("stripe_key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{24,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    (
        "llm_api_key",
        re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b"),
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ),
]

PEM_BLOCK = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
    r".*?"
    r"(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|\Z)",
    re.DOTALL,
)

# scheme://user:password@host -> mantém esquema e usuário, redige a senha
URL_CREDENTIALS = re.compile(
    r"(?P<prefix>\b[A-Za-z][A-Za-z0-9+.-]*://[^\s:/@\"'<>]+:)"
    r"(?P<secret>[^\s@/\"'<>]+)"
    r"(?=@)"
)

# 2. Contexto por nome de variável

SECRET_NAME = (
    r"[A-Za-z0-9_.-]*"
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|private[_-]?key"
    r"|access[_-]?key|auth[_-]?key|credential)"
    r"[A-Za-z0-9_.-]*"
)

QUOTED_ASSIGNMENT = re.compile(
    rf"(?P<head>(?<![A-Za-z0-9_]){SECRET_NAME}[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"'])(?P<value>[^\"'\n]{3,})(?P=quote)",
    re.IGNORECASE,
)

# KEY=value ou key: value (env, YAML, ini, shell), uma atribuição por linha
UNQUOTED_ASSIGNMENT = re.compile(
    rf"^(?P<head>[ \t]*(?:export[ \t]+)?-?[ \t]*(?P<name>{SECRET_NAME})"
    r"[ \t]*[:=][ \t]*)"
    r"(?P<value>[^\s\"'()\[\]{}<>$,;#]{4,})"
    r"(?P<tail>[ \t]*(?:#.*)?)$",
    re.IGNORECASE | re.MULTILINE,
)

NOT_A_SECRET_VALUE = re.compile(
    r"^(?:none|null|nil|true|false|undefined|changeme|example|string|str"
    r"|[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)$",
    re.IGNORECASE,
)

PLACEHOLDER_VALUE = re.compile(r"^(?:\$\{.*\}|\{\{.*\}\}|<.*>|%\(.*\)s)$")

# 3. Entropia

HIGH_ENTROPY_LITERAL = re.compile(
    rf"(?P<quote>[\"'`])(?P<value>[A-Za-z0-9+/=_-]{{{ENTROPY_MIN_LENGTH},}})"
    r"(?P=quote)"
)


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0

    counts = Counter(value)
    total = len(value)

    return -sum((count / total) * math.log2(count / total) for count in counts.values())


class SecretRedactor:
    def __init__(self, entropy_threshold: float = DEFAULT_ENTROPY_THRESHOLD) -> None:
        self.entropy_threshold = entropy_threshold

    def redact(self, text: str) -> RedactionResult:
        findings: list[RedactionFinding] = []

        def record(rule: str, source: str, offset: int) -> None:
            findings.append(RedactionFinding(rule, source.count("\n", 0, offset) + 1))

        # Cada etapa roda sobre o texto já transformado pela anterior; a linha
        # é calculada no texto da etapa, e como nenhuma etapa remove quebras
        # de linha, a numeração é a mesma do texto original.
        text = self._apply(text, PEM_BLOCK, "private_key", record, keep_lines=True)

        for rule, pattern in TOKEN_RULES:
            text = self._apply(text, pattern, rule, record)

        text = self._redact_url_credentials(text, record)
        text = self._redact_named_secrets(text, record)
        text = self._redact_entropy(text, record)

        return RedactionResult(text=text, findings=findings)

    @staticmethod
    def _apply(
        text: str,
        pattern: re.Pattern[str],
        rule: str,
        record: Callable[[str, str, int], None],
        keep_lines: bool = False,
    ) -> str:
        def replace(match: re.Match[str]) -> str:
            record(rule, text, match.start())
            replacement = _marker(rule)
            if keep_lines:
                replacement += "\n" * match.group(0).count("\n")
            return replacement

        return pattern.sub(replace, text)

    @staticmethod
    def _redact_url_credentials(
        text: str,
        record: Callable[[str, str, int], None],
    ) -> str:
        def replace(match: re.Match[str]) -> str:
            if match.group("secret").startswith("[REDACTED:"):
                return match.group(0)
            record("url_credentials", text, match.start())
            return match.group("prefix") + _marker("url_credentials")

        return URL_CREDENTIALS.sub(replace, text)

    @staticmethod
    def _is_secret_value(value: str) -> bool:
        if value.startswith("[REDACTED:"):
            return False
        if PLACEHOLDER_VALUE.match(value) or NOT_A_SECRET_VALUE.match(value):
            return False
        return True

    def _redact_named_secrets(
        self,
        text: str,
        record: Callable[[str, str, int], None],
    ) -> str:
        def replace_quoted(match: re.Match[str]) -> str:
            if not self._is_secret_value(match.group("value")):
                return match.group(0)
            record("named_secret", text, match.start())
            quote = match.group("quote")
            return f"{match.group('head')}{quote}{_marker('named_secret')}{quote}"

        text_after_quoted = QUOTED_ASSIGNMENT.sub(replace_quoted, text)

        def replace_unquoted(match: re.Match[str]) -> str:
            if not self._is_secret_value(match.group("value")):
                return match.group(0)
            record("named_secret", text_after_quoted, match.start())
            return (
                f"{match.group('head')}{_marker('named_secret')}{match.group('tail')}"
            )

        return UNQUOTED_ASSIGNMENT.sub(replace_unquoted, text_after_quoted)

    def _redact_entropy(
        self,
        text: str,
        record: Callable[[str, str, int], None],
    ) -> str:
        def replace(match: re.Match[str]) -> str:
            if shannon_entropy(match.group("value")) < self.entropy_threshold:
                return match.group(0)
            record("high_entropy", text, match.start())
            quote = match.group("quote")
            return f"{quote}{_marker('high_entropy')}{quote}"

        return HIGH_ENTROPY_LITERAL.sub(replace, text)


_DEFAULT_REDACTOR = SecretRedactor()


def redact_text(text: str) -> RedactionResult:
    return _DEFAULT_REDACTOR.redact(text)
