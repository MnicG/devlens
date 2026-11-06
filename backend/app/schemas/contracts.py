"""Contratos Pydantic definidos em docs/04-contratos-de-dados.md."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

# 1. Enums


class InputKind(StrEnum):
    ISSUE = "issue"
    PR = "pr"


class RepoSourceKind(StrEnum):
    LOCAL = "local"
    GITHUB = "github"


class EvidenceKind(StrEnum):
    CODE = "code"
    COMMIT = "commit"
    ISSUE = "issue"
    TEST = "test"


class VerifyStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"


class Category(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    TESTS = "tests"
    MAINTAINABILITY = "maintainability"
    ROOT_CAUSE = "root_cause"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingStatus(StrEnum):
    KEPT = "kept"
    DOWNGRADED = "downgraded"
    DISCARDED = "discarded"


class AnalysisStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class TraceEventType(StrEnum):
    NODE_START = "node_start"
    NODE_END = "node_end"
    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"
    VERIFICATION = "verification"
    BUDGET = "budget"
    ERROR = "error"


# 2. Entrada e orçamento


class RepoRef(BaseModel):
    source: RepoSourceKind
    location: str
    commit: str | None = None


class Budget(BaseModel):
    max_tokens: int = 60_000
    max_tool_calls: int = 40
    max_steps: int = 12


class Usage(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: int = 0
    steps: int = 0
    cost_usd: float = 0.0


class AnalysisOptions(BaseModel):
    budget: Budget = Field(default_factory=Budget)
    local_only: bool = False


class AnalysisRequest(BaseModel):
    kind: InputKind
    repo: RepoRef
    ref: Annotated[str, Field(min_length=1, max_length=20_000)]
    options: AnalysisOptions = Field(default_factory=AnalysisOptions)


# 3. Evidência


class Evidence(BaseModel):
    id: str
    repo: str
    commit: str
    kind: EvidenceKind
    path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    snippet_hash: str | None = None
    excerpt: Annotated[str, Field(max_length=600)] = ""
    ref: str | None = None
    verify_status: VerifyStatus = VerifyStatus.PENDING
    verify_note: str = ""


# 4. Achados, QA e relatório


class Finding(BaseModel):
    id: str
    category: Category
    severity: Severity
    title: Annotated[str, Field(max_length=160)]
    summary: Annotated[str, Field(max_length=1200)]
    rank: int | None = None
    evidence_ids: Annotated[list[str], Field(min_length=1)]
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    confidence: Confidence | None = None
    confidence_rationale: str = ""
    status: FindingStatus = FindingStatus.KEPT


class TestScenario(BaseModel):
    description: str
    covered: bool | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class QAResult(BaseModel):
    scenarios: list[TestScenario]
    covered_count: int
    missing: list[str]


class Report(BaseModel):
    analysis_id: str
    input_summary: str
    findings: list[Finding]
    discarded_count: int
    qa: QAResult | None = None
    status: AnalysisStatus
    partial_reason: str | None = None
    usage: Usage
    notices: list[str] = Field(default_factory=list)


# 5. Trace


class TraceEvent(BaseModel):
    id: int
    analysis_id: str
    seq: int
    parent_id: int | None = None
    ts: str
    type: TraceEventType
    node: str
    name: str
    input_summary: Annotated[str, Field(max_length=300)] = ""
    output_summary: Annotated[str, Field(max_length=300)] = ""
    status: Literal["ok", "error", "skipped"] = "ok"
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    meta: dict = Field(default_factory=dict)


# 6. Envelope das ferramentas


class ToolResult(BaseModel):
    ok: bool
    data: dict | list | None = None
    truncated: bool = False
    candidates: list[Evidence] = Field(default_factory=list)
    error: str | None = None
