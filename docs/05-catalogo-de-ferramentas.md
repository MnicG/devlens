# DevLens — 05. Catálogo de Ferramentas

> Documentação do DevLens, etapa 5 de 8. Índice em [README.md](README.md).

Todas as ferramentas são somente leitura (RNF-10), devolvem `ToolResult`
(etapa 4) e passam pelo mesmo wrapper, que aplica: validação de argumentos,
contagem no orçamento, cache por commit, redação de segredos, truncamento e
registro no trace.

## 1. Convenções

- **Tipos:** `det` = chamada pelo grafo (determinístico); `agent` = exposta ao
  Code Analyst; `both` = os dois.
- **Limites padrão (provisórios, calibrados na etapa 7):**
  - saída total por chamada: 4.000 caracteres (~1.000 tokens)
  - trecho por resultado: 3 linhas ou 240 caracteres
  - `excerpt` de evidência: 600 caracteres (já definido no contrato)
- **Paginação:** resultados cortados vêm com `truncated=true` e `cursor`.
  Nunca se devolve mais para "compensar".
- **Caminhos:** sempre relativos à raiz do repositório e normalizados.
  Qualquer `..`, caminho absoluto, link simbólico para fora ou arquivo
  ignorado (`.git`, binário, acima de 1 MB) é rejeitado.
- **Commit:** toda ferramenta opera no `commit` fixado na entrada da análise,
  nunca em "HEAD vivo". É isso que torna o cache e a verificação confiáveis.

## 2. Catálogo

### 2.1 Contexto de entrada (determinísticas)

| Ferramenta | Tipo | Argumentos | Retorno | Limites |
|---|---|---|---|---|
| `get_issue` | det | `ref` (nº ou texto) | título, corpo, rótulos, comentários resumidos | corpo 3.000 caracteres; 10 comentários |
| `get_pr` | det | `ref` (nº ou `base..head`) | título, descrição, refs, estatísticas | descrição 2.000 caracteres |
| `changed_files` | det | `ref` | lista de `{path, status, additions, deletions}` | 100 arquivos; resto vira `truncated` |
| `get_diff` | both | `ref`, `path?`, `cursor?` | hunks do diff | 400 linhas por chamada, com cursor |

`get_issue` e `get_pr` com número só funcionam na fonte GitHub; no modo local,
`ref` de issue é texto livre e `ref` de PR é `base..head`.

### 2.2 Navegação no código (agent)

| Ferramenta | Argumentos | Retorno | Limites |
|---|---|---|---|
| `search_code` | `query`, `path_glob?`, `top_k=8` | lista `{path, line_start, line_end, snippet}` via BM25 (FTS5) | `top_k` máx. 20; 3 linhas por resultado |
| `read_range` | `path`, `line_start`, `line_end` | linhas numeradas | máx. 120 linhas por chamada |
| `find_symbol` | `name`, `kind?` | definições `{path, kind, line_start, line_end}` | máx. 20 |
| `list_symbols` | `path` | símbolos do arquivo (funções, classes, imports) | máx. 100 |
| `find_references` | `symbol`, `top_k=10` | onde é chamado/importado (índice de chamadas e imports) | máx. 20 |

`find_references` é aproximada (tree-sitter não resolve tipos). O resultado
sempre traz o campo `precision: "approximate"`, e o Code Analyst deve
confirmar com `read_range` antes de usar como evidência.

### 2.3 Histórico (agent)

| Ferramenta | Argumentos | Retorno | Limites |
|---|---|---|---|
| `git_log` | `path?`, `since?`, `limit=10` | `{sha, autor, data, assunto}` | máx. 20 commits |
| `git_blame` | `path`, `line_start`, `line_end` | autor/commit por linha | máx. 60 linhas |
| `git_show` | `sha`, `path?` | metadados e diff resumido do commit | 200 linhas de diff |

Mensagens de commit entram como dado não confiável (seção 4).

### 2.4 Testes e issues relacionadas

| Ferramenta | Tipo | Argumentos | Retorno | Limites |
|---|---|---|---|---|
| `find_tests` | both | `target` (arquivo ou símbolo) | testes relacionados `{path, name, line_start, line_end}` | máx. 20 |
| `check_test_scenario` | det | `description`, `target` | cobertura provável `covered: true/false/null` + testes candidatos | máx. 10 candidatos |
| `related_issues` | agent | `query`, `top_k=5` | `{number, title, state, snippet}` (só GitHub) | máx. 10 |

`find_tests` usa convenções (diretórios `tests/`, `test_*.py`, `*.test.ts`,
`__tests__`) mais busca por imports do alvo. `check_test_scenario` é heurística
(busca por termos nos nomes e corpos dos testes) e retorna `null` quando não
consegue decidir, nunca um palpite. Quem refina o cenário é a etapa `qa_check`
do grafo, com o LLM propondo cenários e a ferramenta verificando a existência.
`related_issues` depende da fonte GitHub (RF-55, prioridade M\*).

### 2.5 Controle do loop (agent)

| Ferramenta | Argumentos | Efeito |
|---|---|---|
| `finish_investigation` | `findings[]` (esquema `Finding` sem confiança nem status) | encerra o loop e entrega a saída estruturada |

Encerrar é uma ferramenta explícita: evita que o fim do loop dependa de
interpretar texto livre.

## 3. Como as ferramentas geram evidência

1. A ferramenta devolve `candidates: list[Evidence]` (path, linhas, commit,
   excerpt já redigido, `snippet_hash` como HMAC do trecho original).
2. O wrapper envia os candidatos ao **Evidence Store**, que atribui o `id`
   determinístico (deduplica) e marca `verify_status = pending`.
3. O agente recebe na resposta apenas os `ev_id` e um resumo curto, nunca o
   texto bruto repetido. Para citar, usa o `ev_id` em `evidence_ids`.
4. `verify_evidence` (etapa determinística do grafo) relê o arquivo no commit e
   confere linhas e HMAC.

| Ferramenta | Gera evidência do tipo |
|---|---|
| `search_code`, `read_range`, `find_symbol`, `find_references` | `code` |
| `git_log`, `git_blame`, `git_show` | `commit` |
| `find_tests`, `check_test_scenario` | `test` |
| `related_issues`, `get_issue` | `issue` |

`search_code` produz candidatos, mas o agente só deveria citar o que de fato
leu. Regra de validação: um `ev_id` citado em `Finding` precisa ter sido
devolvido por alguma chamada de ferramenta **nesta análise**. IDs inventados
são descartados na validação do esquema.

## 4. Segurança no wrapper

- **Somente leitura:** não existe ferramenta de escrita nem de shell. Git é
  chamado por biblioteca (sem interpolação em linha de comando), com
  argumentos validados (`sha` casa com `^[0-9a-f]{7,40}$`, `path` normalizado).
- **Conteúdo não confiável:** toda saída que contém texto do repositório
  (código, commits, issues, comentários) é entregue ao modelo dentro de
  delimitadores fixos, em campo marcado como dado, e o prompt de sistema
  instrui que nada ali é instrução.
- **Redação de segredos:** executada no wrapper, antes do texto chegar ao
  modelo e antes de persistir `excerpt`. Técnica definida na etapa 6.
- **Limites de entrada:** `query` até 200 caracteres; regex arbitrária não é
  aceita em `search_code` (consulta FTS escapada), evitando ReDoS.
- **Allowlist:** a `RepoSource` só abre repositórios da allowlist.

## 5. Erros padronizados

`ToolResult.error` usa códigos estáveis, que o agente pode tratar:

| Código | Significado | Contagem no orçamento |
|---|---|---|
| `INVALID_ARGS` | argumento fora do esquema ou do limite | conta |
| `PATH_DENIED` | path fora da raiz, ignorado ou grande demais | conta |
| `NOT_FOUND` | arquivo, símbolo, commit ou issue inexistente | conta |
| `SOURCE_UNAVAILABLE` | ferramenta indisponível para a fonte (ex.: issues no modo local) | não conta |
| `RATE_LIMITED` | limite da API do GitHub | não conta; tenta de novo com espera curta, depois falha |
| `INDEX_MISSING` | índice do commit ainda não construído | não conta; dispara indexação |
| `BUDGET_EXCEEDED` | orçamento esgotado | leva a `compose_partial` |

Falha de ferramenta nunca derruba a análise (RNF-20): vira evento `error` no
trace e o agente recebe a mensagem para tentar outro caminho.

## 6. Orçamento, cache e repetição

- Cada chamada conta 1 em `max_tool_calls`; o consumo vai para `Usage` e para o
  evento `tool_call` do trace.
- **Cache:** chave `hash(tool, args normalizados, commit)` na tabela
  `tool_cache`. Acerto de cache não conta no orçamento de chamadas, mas fica
  registrado no trace com `meta.cache_hit = true`. Ferramentas de API do
  GitHub (issues, PR) têm TTL curto, pois não são imutáveis por commit.
- **Repetição (RNF-21):** mesma ferramenta com mesmos argumentos duas vezes
  seguidas devolve o resultado em cache com um aviso ao agente
  ("resultado repetido, tente outra abordagem"); na terceira vez o loop é
  encerrado.

## 7. Índice local

- **Construção:** sob demanda, por commit, na primeira análise de um
  repositório. Arquivos Python e JS/TS entram no índice de símbolos; demais
  arquivos de texto entram só na busca textual.
- **Chunking:** janelas de ~40 linhas com sobreposição pequena, respeitando
  limites de função quando o tree-sitter permite.
- **Exclusões:** `node_modules`, `.git`, `dist`, `build`, `vendor`, binários,
  arquivos gerados e acima de 1 MB.
- **Reuso:** commits novos reindexam só arquivos cujo `content_hash` mudou.

## 8. Interface em código (resumo)

```python
class RepoSource(Protocol):
    def resolve_commit(self, ref: str | None) -> str: ...
    def read_file(self, commit: str, path: str) -> str: ...
    def list_files(self, commit: str) -> list[str]: ...
    def git_log(self, commit: str, path: str | None, limit: int) -> list[CommitInfo]: ...
    def git_blame(self, commit: str, path: str, start: int, end: int) -> list[BlameLine]: ...
    def diff(self, base: str, head: str, path: str | None) -> str: ...
    # opcional (GitHub): get_issue, get_pr, search_issues

class Tool(Protocol):
    name: str
    args_schema: type[BaseModel]
    def run(self, ctx: ToolContext, args: BaseModel) -> ToolResult: ...
```

`ToolContext` carrega repositório, commit fixado, orçamento, índice e
Evidence Store. As ferramentas dependem apenas de `RepoSource` e do índice,
nunca de disco ou rede diretamente (regra da etapa 3).

## 9. Testes das ferramentas (RNF-43)

- Repositório de fixture pequeno, com histórico conhecido, usado em todos os
  testes.
- Casos obrigatórios: path traversal, link simbólico externo, arquivo
  grande, `top_k` acima do limite, truncamento com cursor, `sha` malformado,
  cache hit, fonte sem suporte, e verificação de que o `excerpt` sai redigido.

## 10. Pontos em aberto
- Valores finais de limites e orçamento: calibração na etapa 7.
- Se `find_references` precisa de heurística extra para JS/TS (imports
  dinâmicos, re-exports): decidir na implementação.
- Técnica de redação de segredos e retenção do cache: ver etapa 6.
