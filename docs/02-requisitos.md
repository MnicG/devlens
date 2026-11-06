# DevLens — 02. Requisitos

> Documentação do DevLens, etapa 2 de 8. Índice em [README.md](README.md).

Convenções: **RF** = requisito funcional, **RNF** = não funcional.
Prioridade: **M** = MVP, **M\*** = MVP se houver tempo (primeira candidata a
corte, ver roadmap), **E** = extensão futura.

## 1. Requisitos funcionais

### 1.1 Entrada e fontes
| ID | Requisito | Pri |
|---|---|---|
| RF-01 | Aceitar uma Issue como entrada (texto livre ou número, quando houver fonte GitHub) | M |
| RF-02 | Aceitar um PR como entrada (número ou diff local entre duas refs) | M |
| RF-03 | Operar sobre repositório local (git + arquivos) | M |
| RF-04 | Operar sobre repositório do GitHub via API, somente leitura | M\* |
| RF-05 | Aceitar requisito em texto como entrada (Requirements Agent) | E |

### 1.2 Orchestrator
| ID | Requisito | Pri |
|---|---|---|
| RF-10 | Classificar a entrada (issue ou PR) e montar um plano curto de etapas | M |
| RF-11 | Executar etapas determinísticas (obter issue/PR, extrair diff, listar arquivos alterados, localizar testes) | M |
| RF-12 | Delegar a investigação aberta ao Code Analyst em loop com limite de passos | M |
| RF-13 | Executar a etapa de QA: identificar cenários de teste necessários e verificar se existem no repositório | M |
| RF-14 | Consolidar achados dos agentes em um relatório único | M |
| RF-15 | Interromper a análise ao atingir o orçamento e entregar relatório parcial indicando o que ficou de fora | M |

### 1.3 Code Analyst (fluxo Issue)
| ID | Requisito | Pri |
|---|---|---|
| RF-20 | Gerar hipóteses ranqueadas para a causa de uma issue | M |
| RF-21 | Cada hipótese deve ter evidências (código, commits, issues relacionadas) | M |
| RF-22 | Usar histórico git (commits recentes e blame) como fonte de evidência | M |

### 1.4 Revisão de PR
| ID | Requisito | Pri |
|---|---|---|
| RF-30 | Analisar o diff por categoria: correção, segurança, testes, manutenibilidade | M |
| RF-31 | Expandir contexto além do diff apenas sob demanda | M |
| RF-32 | Cada achado tem severidade e categoria | M |

### 1.5 Evidências e confiança
| ID | Requisito | Pri |
|---|---|---|
| RF-40 | Toda afirmação do relatório referencia pelo menos uma evidência | M |
| RF-41 | Validar automaticamente cada evidência (arquivo existe, linha existe, trecho confere com o commit) | M |
| RF-42 | Descartar ou rebaixar achados cujas evidências falhem na validação | M |
| RF-43 | Atribuir nível de confiança (alta/média/baixa) com justificativa baseada nas evidências | M |

### 1.6 Ferramentas de repositório
| ID | Requisito | Pri |
|---|---|---|
| RF-50 | Buscar código por texto/padrão com resultados limitados (top-k, trechos curtos) | M |
| RF-51 | Ler arquivo por intervalo de linhas | M |
| RF-52 | Consultar símbolos, imports e chamadas via índice local | M |
| RF-53 | Consultar histórico (commits, blame, diff entre refs) | M |
| RF-54 | Listar e localizar testes relacionados | M |
| RF-55 | Buscar issues relacionadas (somente fonte GitHub) | M\* |

### 1.7 Trace, custo e interface
| ID | Requisito | Pri |
|---|---|---|
| RF-60 | Registrar cada passo (agente, ferramenta, entrada resumida, saída resumida, duração, tokens, custo) | M |
| RF-61 | Exibir o trace em árvore no frontend, durante e após a análise | M |
| RF-62 | Expor API: `POST /analyze/issue`, `POST /analyze/pr`, `GET /analyses/{id}` | M |
| RF-63 | Exibir o relatório final com links para as evidências | M |
| RF-64 | Persistir análises e traces para consulta posterior | M |
| RF-65 | Apagar uma análise e todos os dados associados (`DELETE /analyses/{id}`) | M |

### 1.8 Provedores de modelo
| ID | Requisito | Pri |
|---|---|---|
| RF-70 | Acessar modelos por interface própria, trocável por configuração | M |
| RF-71 | Suportar ao menos um provedor em nuvem e um modelo local | M |
| RF-72 | Permitir escolher modelo por papel (triagem/extração vs. síntese) | M |

### 1.9 Avaliação
| ID | Requisito | Pri |
|---|---|---|
| RF-80 | Executar um conjunto de casos de teste com resultado esperado | M |
| RF-81 | Gerar métricas por modelo (acerto, evidências inválidas, custo, passos) | M |

## 2. Requisitos não funcionais

### 2.1 Economia de contexto e custo
| ID | Requisito |
|---|---|
| RNF-01 | Orçamento configurável por análise (tokens, tool calls, passos) |
| RNF-02 | Ferramentas nunca devolvem arquivo inteiro; saídas têm limite de tamanho |
| RNF-03 | Agentes trocam referências a evidências, não texto bruto |
| RNF-04 | Cache de resultados de ferramentas por hash de commit |
| RNF-05 | Cada sub-agente roda com contexto isolado |
| RNF-06 | Teto de custo diário configurável; ao atingir, novas análises são recusadas com erro claro |

### 2.2 Segurança
| ID | Requisito |
|---|---|
| RNF-10 | Todas as ferramentas são somente leitura; sem execução de shell arbitrário nem do código analisado |
| RNF-11 | Conteúdo de código, issues e PRs tratado como dado não confiável (mitigação de prompt injection) |
| RNF-12 | Acesso restrito a repositórios em allowlist; proteção contra path traversal |
| RNF-13 | Redação de segredos antes de enviar qualquer trecho a um LLM |
| RNF-14 | Token do GitHub com escopo mínimo, lido de variável de ambiente, nunca registrado em log ou trace |
| RNF-15 | Saídas dos agentes validadas por esquema (Pydantic) |
| RNF-16 | Modo local-only que impede qualquer envio a provedores em nuvem |
| RNF-17 | Interface e documentação informam que modelos em nuvem sujeitam o código enviado às políticas do provedor |
| RNF-18 | Retenção de dados configurável (análises, traces, cache, índice, logs) com limpeza periódica |
| RNF-19 | API com bind local por padrão, chave de API obrigatória ao expor em outra interface e CORS restrito |

### 2.3 Confiabilidade e determinismo
| ID | Requisito |
|---|---|
| RNF-20 | Falha de ferramenta ou de modelo não derruba a análise; é registrada no trace e tratada |
| RNF-21 | Loop do Code Analyst com limite máximo de passos e detecção de repetição |
| RNF-22 | Mesma entrada e mesmo commit devem produzir trace comparável (temperatura baixa, ferramentas determinísticas) |

### 2.4 Observabilidade
| ID | Requisito |
|---|---|
| RNF-30 | Trace estruturado exportável (JSON) |
| RNF-31 | Logs sem segredos nem trechos de código completos |

### 2.5 Manutenibilidade e portabilidade
| ID | Requisito |
|---|---|
| RNF-40 | Stack base: Python, FastAPI, LangGraph |
| RNF-41 | Provedor de modelo, fonte Git e ferramentas atrás de interfaces substituíveis |
| RNF-42 | Execução reproduzível via Docker Compose |
| RNF-43 | Testes automatizados para ferramentas, validação de evidências e esquemas |

## 3. Restrições
- Desenvolvimento por uma pessoa; sem dependência de infraestrutura paga obrigatória.
- Desenvolvimento viável com modelo local.
- Sem multiusuário nem autenticação de equipes no MVP.

## 4. Decisões que fecharam os pontos em aberto
1. **Linguagens do MVP:** Python e JavaScript/TypeScript (impacta índice e tree-sitter).
2. **Tamanho de repositório-alvo:** 5 a 50 mil linhas de código (definição na etapa 7).
3. **Persistência:** SQLite.
4. **Frontend:** React + TypeScript, com escopo mínimo: trace em árvore e relatório (detalhe no roadmap).

Observações:
- A validação de evidências (RF-41/42) é requisito do MVP, não extra: é o coração do diferencial.
- A busca por issues relacionadas (RF-55) só funciona com fonte GitHub; no modo local puro fica indisponível.
