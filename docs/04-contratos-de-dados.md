# DevLens — 04. Contratos de Dados

> Documentação do DevLens, etapa 4 de 8. Índice em [README.md](README.md).

Todos os contratos são modelos Pydantic v2. Eles servem de esquema de saída dos
agentes (RNF-15), de contrato da API e de base para as tabelas do SQLite.

## 1. Enums

```python
from enum import StrEnum

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
    ROOT_CAUSE = "root_cause"   # hipóteses do fluxo Issue

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
    PARTIAL = "partial"     # orçamento estourado
    FAILED = "failed"

class TraceEventType(StrEnum):
    NODE_START = "node_start"
    NODE_END = "node_end"
    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"
    VERIFICATION = "verification"
    BUDGET = "budget"
    ERROR = "error"
```

## 2. Entrada e orçamento

```python
from typing import Annotated
from pydantic import BaseModel, Field

class RepoRef(BaseModel):
    source: RepoSourceKind
    location: str                       # caminho local ou "owner/name"
    commit: str | None = None           # None = HEAD resolvido na entrada

class Budget(BaseModel):
    max_tokens: int = 60_000
    max_tool_calls: int = 40
    max_steps: int = 12                 # passos do loop do Code Analyst

class Usage(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: int = 0
    steps: int = 0
    cost_usd: float = 0.0

class AnalysisOptions(BaseModel):
    budget: Budget = Field(default_factory=Budget)
    local_only: bool = False            # RNF-16: proíbe provedores em nuvem

class AnalysisRequest(BaseModel):
    kind: InputKind
    repo: RepoRef
    # issue: número (GitHub) ou texto livre; pr: número ou "base..head" (local)
    ref: Annotated[str, Field(min_length=1, max_length=20_000)]
    options: AnalysisOptions = Field(default_factory=AnalysisOptions)
```

Os valores padrão do orçamento são **provisórios**: serão calibrados pelo
percentil 90 dos casos corretos do conjunto de avaliação (etapa 7, seção 7).

## 3. Evidência

```python
class Evidence(BaseModel):
    id: str                              # determinístico: ev_<hash(repo,commit,kind,path,lines)>
    repo: str
    commit: str
    kind: EvidenceKind
    path: str | None = None              # None para issue
    line_start: int | None = None
    line_end: int | None = None
    snippet_hash: str | None = None      # HMAC (chave local) do trecho original, antes da redação
    excerpt: Annotated[str, Field(max_length=600)] = ""   # já redigido
    ref: str | None = None               # sha de commit ou número da issue
    verify_status: VerifyStatus = VerifyStatus.PENDING
    verify_note: str = ""
```

Regras:
- `id` determinístico: a mesma evidência encontrada duas vezes é deduplicada.
- Tools devolvem candidatos; só o Evidence Store registra e atribui o `id`.
- `excerpt` é guardado depois da redação de segredos e truncado.
- `snippet_hash` é um **HMAC** com chave local da instalação, calculado sobre o
  trecho original. Um hash simples de um segredo curto poderia ser revertido por
  força bruta e vazar o segredo pelo banco (ver ADR 0008).
- Verificação (sem LLM): arquivo existe no commit, linhas existem, HMAC confere.
  Para `commit` e `issue`, confere existência da referência.

## 4. Achados, QA e relatório

```python
class Finding(BaseModel):
    id: str
    category: Category
    severity: Severity
    title: Annotated[str, Field(max_length=160)]
    summary: Annotated[str, Field(max_length=1200)]
    rank: int | None = None              # só para hipóteses (1 = mais provável)
    evidence_ids: Annotated[list[str], Field(min_length=1)]
    contradicting_evidence_ids: list[str] = []
    confidence: Confidence | None = None          # atribuída por score_confidence, não pelo LLM
    confidence_rationale: str = ""
    status: FindingStatus = FindingStatus.KEPT

class TestScenario(BaseModel):
    description: str
    covered: bool | None = None
    evidence_ids: list[str] = []         # testes que cobrem (se covered)

class QAResult(BaseModel):
    scenarios: list[TestScenario]
    covered_count: int
    missing: list[str]                   # descrições dos cenários sem cobertura

class Report(BaseModel):
    analysis_id: str
    input_summary: str
    findings: list[Finding]              # apenas KEPT e DOWNGRADED
    discarded_count: int
    qa: QAResult | None = None
    status: AnalysisStatus
    partial_reason: str | None = None    # o que ficou de fora, se parcial
    usage: Usage
    notices: list[str] = []              # ex.: "Trechos enviados a provedor em nuvem: X"
```

Decisões:
- Hipótese (Issue) e achado de revisão (PR) usam o mesmo modelo `Finding`.
  A diferença está em `category` (`root_cause`) e `rank`.
- O LLM **propõe** achados com `evidence_ids`; `confidence`, `status` e
  `confidence_rationale` são definidos pelo código.
- `evidence_ids` é obrigatório (mínimo 1), o que implementa RF-40 no esquema.

## 5. Regra de confiança (determinística)

Evidência "forte" = `kind` em `code` ou `test`. "Indireta" = `commit` ou `issue`.
Consideram-se apenas evidências **verificadas** do achado.

| Condição | Resultado |
|---|---|
| Nenhuma evidência verificada | `status = discarded` |
| ≥2 verificadas, de ≥2 tipos distintos, ao menos 1 forte | Alta |
| 1 forte verificada, ou ≥2 verificadas do mesmo tipo | Média |
| Só evidências indiretas | Baixa |

Ajustes:
- Alguma evidência do achado falhou na verificação: rebaixa um nível e
  `status = downgraded`.
- `contradicting_evidence_ids` não vazio: no máximo Média.

`confidence_rationale` é gerada por template a partir dos números e tipos de
evidência (ex.: "2 evidências verificadas: código em `a.py:10-20` e commit
`abc123`"), sem texto livre do LLM.

## 6. Trace

```python
class TraceEvent(BaseModel):
    id: int
    analysis_id: str
    seq: int                             # ordem total na análise
    parent_id: int | None = None         # forma a árvore exibida no frontend
    ts: str                              # ISO 8601 UTC
    type: TraceEventType
    node: str                            # nó do grafo ou agente
    name: str                            # ferramenta, modelo ou etapa
    input_summary: Annotated[str, Field(max_length=300)] = ""
    output_summary: Annotated[str, Field(max_length=300)] = ""
    status: Literal["ok", "error", "skipped"] = "ok"
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    meta: dict = {}                      # ids de evidência, motivo do corte, etc.
```

Regras: resumos já redigidos e truncados (RNF-31); nenhum campo guarda prompt
ou código completo; exportável como JSON (RNF-30).

## 7. Contratos da API

| Método e rota | Corpo/Resposta |
|---|---|
| `POST /analyze/issue` | `AnalysisRequest` (`kind=issue`) → `202 {analysis_id, status, trace_url}` |
| `POST /analyze/pr` | `AnalysisRequest` (`kind=pr`) → `202 {analysis_id, status, trace_url}` |
| `GET /analyses/{id}` | `{status, usage, report?}` |
| `GET /analyses/{id}/trace` | SSE de `TraceEvent` (reabre do início se a análise terminou) |
| `GET /analyses/{id}/trace.json` | Lista completa de `TraceEvent` |
| `GET /analyses/{id}/evidences/{ev_id}` | `Evidence` |
| `DELETE /analyses/{id}` | `204`; apaga análise, trace, evidências e cache associados (RF-65) |

Erros: corpo `{code, message}`; códigos para repositório fora da allowlist,
modo local-only sem modelo local, entrada inválida, análise inexistente e
teto de custo diário atingido (RNF-06).

## 8. Envelope das ferramentas

```python
class ToolResult(BaseModel):
    ok: bool
    data: dict | list | None = None
    truncated: bool = False              # resultado cortado pelo limite
    candidates: list[Evidence] = []      # candidatos, ainda não registrados
    error: str | None = None
```

Toda ferramenta devolve `ToolResult`, com limites definidos na etapa 5.

## 9. Esquema SQLite

```sql
CREATE TABLE analyses (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  repo_source TEXT NOT NULL,
  repo_location TEXT NOT NULL,
  commit_sha TEXT NOT NULL,
  ref TEXT NOT NULL,
  options_json TEXT NOT NULL,
  status TEXT NOT NULL,
  partial_reason TEXT,
  usage_json TEXT NOT NULL DEFAULT '{}',
  report_json TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE trace_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  parent_id INTEGER REFERENCES trace_events(id),
  ts TEXT NOT NULL,
  type TEXT NOT NULL,
  node TEXT NOT NULL,
  name TEXT NOT NULL,
  input_summary TEXT NOT NULL DEFAULT '',
  output_summary TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'ok',
  duration_ms INTEGER NOT NULL DEFAULT 0,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  meta_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (analysis_id, seq)
);

CREATE TABLE evidences (
  id TEXT NOT NULL,
  analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
  repo TEXT NOT NULL,
  commit_sha TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT,
  line_start INTEGER,
  line_end INTEGER,
  snippet_hash TEXT,                   -- HMAC com chave local
  excerpt TEXT NOT NULL DEFAULT '',
  ref TEXT,
  verify_status TEXT NOT NULL DEFAULT 'pending',
  verify_note TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (analysis_id, id)
);

CREATE TABLE findings (
  id TEXT NOT NULL,
  analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
  category TEXT NOT NULL,
  severity TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  rank INTEGER,
  confidence TEXT,
  confidence_rationale TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'kept',
  PRIMARY KEY (analysis_id, id)
);

CREATE TABLE finding_evidence (
  analysis_id TEXT NOT NULL,
  finding_id TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('supports','contradicts')),
  PRIMARY KEY (analysis_id, finding_id, evidence_id),
  FOREIGN KEY (analysis_id, finding_id) REFERENCES findings(analysis_id, id) ON DELETE CASCADE,
  FOREIGN KEY (analysis_id, evidence_id) REFERENCES evidences(analysis_id, id) ON DELETE CASCADE
);

CREATE TABLE qa_scenarios (
  analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  description TEXT NOT NULL,
  covered INTEGER,                     -- 0, 1 ou NULL (indeterminado)
  evidence_ids_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (analysis_id, idx)
);

CREATE TABLE tool_cache (
  key TEXT PRIMARY KEY,                -- hash(tool, args, commit)
  commit_sha TEXT NOT NULL,
  tool TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Índice local (por commit)
CREATE TABLE index_files (
  commit_sha TEXT NOT NULL,
  path TEXT NOT NULL,
  language TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  PRIMARY KEY (commit_sha, path)
);

CREATE TABLE index_symbols (
  commit_sha TEXT NOT NULL,
  path TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,                  -- function, class, method, import
  line_start INTEGER NOT NULL,
  line_end INTEGER NOT NULL
);
CREATE INDEX idx_symbols_name ON index_symbols(commit_sha, name);

CREATE VIRTUAL TABLE index_chunks USING fts5(
  commit_sha UNINDEXED, path UNINDEXED, line_start UNINDEXED, line_end UNINDEXED,
  content
);
```

As tabelas de avaliação (`eval_cases`, `eval_runs`, `eval_results`) estão na
etapa 7.

## 10. Pontos em aberto
- Valores finais de orçamento e limites de ferramentas: calibração na etapa 7.
- Se `index_chunks` indexa por commit completo ou só por arquivos alterados
  (otimização de espaço): decidir na implementação, sem alterar os contratos.
- Retenção de análises, traces e cache: definida na etapa 6, seção 7.
