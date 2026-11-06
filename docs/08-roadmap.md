# DevLens — 08. Roadmap

> Documentação do DevLens, etapa 8 de 8. Índice em [README.md](README.md).

Princípio: **cada fase termina em algo demonstrável**. Se o projeto parar em
qualquer marco, o que existe já é apresentável no portfólio.

## 1. Premissas das estimativas

- Uma pessoa, desenvolvimento em paralelo a outras atividades.
- Estimativas em horas de trabalho efetivo, com **~30% de margem** embutida
  para imprevistos (tool calling de modelo local, depuração de agentes e
  integração costumam estourar).
- Conversão para calendário depende da disponibilidade: ~10 h/semana
  equivale a ~4 a 5 meses; ~20 h/semana, a ~2 a 2,5 meses.

## 2. Visão geral

| Fase | Foco | Horas | Marco |
|---|---|---|---|
| 0 | Fundação do repositório | ~10 | Projeto roda, CI verde |
| 1 | Núcleo determinístico (sem LLM) | ~35 | Ferramentas e verificação testadas |
| 2 | Agente e fluxo Issue (CLI) | ~35 | **v0.1**: issue → relatório com evidências |
| 3 | Fluxo PR, QA e confiança | ~25 | Relatório completo dos dois fluxos |
| 4 | API, trace ao vivo e frontend | ~35 | **v0.2**: demo visual |
| 5 | Avaliação e fonte GitHub | ~35 | Números publicáveis |
| 6 | Polimento e publicação | ~15 | **v1.0** |
| | **Total** | **~190** | |

## 3. Fases

### Fase 0: Fundação (~10 h)
- Estrutura de pastas da etapa 3, `pyproject`, lint/format/tipos (ruff, mypy),
  pytest, pre-commit com varredura de segredos.
- CI no GitHub Actions: testes, lint, `pip-audit`, `gitleaks`.
- Docker Compose mínimo; `.env.example`; modelos Pydantic da etapa 4.
- Documentação das etapas 1 a 8 em `docs/`, e decisões em `docs/adr/`.

**Pronto quando:** `docker compose up` sobe o backend e o CI passa.

### Fase 1: Núcleo determinístico (~35 h)
Tudo que não precisa de LLM, e que sustenta o diferencial.
- `LocalRepoSource` (git por biblioteca, sem shell, hooks desligados).
- Índice: tree-sitter (Python e JS/TS) + FTS5, por commit.
- Repo Tools da etapa 5 com limites, cache e erros padronizados.
- Evidence Store, `verify_evidence`, regra de confiança.
- Redação de segredos com testes por regra.
- Repositório de fixture e testes de segurança (traversal, symlink, `.env`).

**Pronto quando:** dado um repositório, as ferramentas respondem dentro dos
limites, evidências são registradas e verificadas, e os testes de segurança
passam, tudo sem chamar nenhum modelo.

### Fase 2: Agente e fluxo Issue (~35 h)
- **Spike inicial (primeiras horas):** testar tool calling com o modelo local
  e com um modelo de nuvem econômico em 2 ou 3 ferramentas. É o maior risco
  técnico; se o local for fraco demais, o desenvolvimento usa nuvem barata e o
  local vira "suportado com limitações".
- Model Gateway (papéis, orçamento, contagem de custo, redação, local-only).
- Grafo LangGraph: `intake → classify → plan → fetch_context → investigate →
  verify → score → compose_report` e `compose_partial`.
- Code Analyst com loop limitado, `finish_investigation` e detecção de repetição.
- Trace persistido (SQLite) e CLI: `devlens issue ...` imprimindo relatório.
- **Início da avaliação:** esquema de casos, primeiro repositório sintético e
  ~5 casos de issue.

**Pronto quando (v0.1):** uma issue em repositório local gera hipóteses
ranqueadas, com evidências verificadas e trace completo.

### Fase 3: Fluxo PR, QA e confiança (~25 h)
- `analyze_diff` por categoria, com aresta condicional para `investigate`.
- `qa_check` com `find_tests` e `check_test_scenario`.
- Relatório final unificado (`Report`), notices de envio a terceiros.
- Casos de PR no conjunto de avaliação (defeituosos e limpos).

**Pronto quando:** os dois fluxos produzem relatório completo e os casos de PR
do dev set rodam de ponta a ponta.

### Fase 4: API, trace ao vivo e frontend (~35 h)
- FastAPI: rotas da etapa 4 (incluindo `DELETE`), jobs em background, SSE do
  trace, chave de API e CORS restrito, teto de custo diário e retenção.
- Frontend React + TS: formulário de análise, **trace em árvore ao vivo**
  (com tokens e custo por passo), relatório com links para evidências,
  aviso de provedor, renderização segura.
- Persistência e limpeza por retenção.

**Pronto quando (v0.2):** é possível abrir a interface, submeter uma issue ou
PR e ver o trace se formando e o relatório final. Gravar o primeiro GIF de demo.

### Fase 5: Avaliação e fonte GitHub (~35 h)
- Completar os ~30 casos (sintéticos e reais), divisão dev/test.
- Runner com record/replay, baselines e ablações da etapa 7.
- Calibrar orçamento (percentil 90) e limiares de redação.
- Rodar modelos (local, nuvem econômica, mista) e gerar o relatório.
- `GitHubRepoSource` (leitura, token mínimo) e `related_issues` (RF-04 e
  RF-55, prioridade M\*).

**Pronto quando:** existe um relatório de avaliação reproduzível, com tabela
baseline × completo × modelo, e o DevLens analisa um repositório do GitHub.

### Fase 6: Polimento e publicação (~15 h)
- README (seção 5), `SECURITY.md`, diagrama de arquitetura, GIF/vídeo curto.
- Exemplos de relatório real, seção "Falhas conhecidas", licença.
- Revisão final de segredos no histórico git, tag `v1.0`.

## 4. Linha de corte (o que sacrificar se o tempo apertar)

Em ordem, do primeiro a cortar ao último:

1. Frontend refinado: manter apenas a visualização do trace e do relatório,
   sem recursos extras.
2. Segundo repositório sintético e casos reais além de 3.
3. Ablações (manter só baseline 1 e completo).
4. `GitHubRepoSource` (sai do MVP e vira "próxima versão", com o fluxo local
   completo). RF-04 e RF-55 já estão marcados como M\* na etapa 2.
5. Modelo de nuvem forte nos papéis (manter um provedor na nuvem e o local).

**Não cortar:** verificação de evidências, orçamento, redação de segredos,
trace e avaliação com baseline. São o diferencial do projeto e a parte
que demonstra seriedade.

## 5. Plano do repositório e README

Estrutura do README:
1. Resumo de uma frase e GIF da demo no topo.
2. Problema e o que torna o DevLens diferente (evidência verificável, trace,
   avaliação, economia de contexto).
3. Início rápido com Docker, em modo local-only e em modo nuvem.
4. Arquitetura (diagrama do grafo) e links para `docs/`.
5. **Resultados da avaliação** (tabela e limitações).
6. Segurança e privacidade (resumo, aviso de terceiros, link ao `SECURITY.md`).
7. Limitações conhecidas e roadmap futuro (Requirements Agent, embeddings,
   GitLab, webhooks).

Práticas: commits pequenos e descritivos, issues e milestones no GitHub
espelhando as fases, ADRs curtos para as decisões da etapa 3, releases com
notas.

## 6. Riscos de cronograma

| Risco | Prob. | Mitigação |
|---|---|---|
| Modelo local com tool calling fraco | Alta | Spike no início da fase 2; plano B com nuvem barata |
| Agente instável/difícil de depurar | Média | Loop curto, esquemas estritos, record/replay e trace desde a fase 2 |
| Avaliação consome mais que o previsto | Média | Começar pequena na fase 2 e crescer por fases |
| Custo de API na avaliação | Média | Replay no CI, teto de custo, modelos baratos no dev |
| Frontend engolir o tempo | Média | Escopo mínimo definido; linha de corte item 1 |
| Perder motivação com projeto longo | Média | Marcos demonstráveis (v0.1, v0.2) a cada fase |

## 7. Definição de pronto do MVP

- Fluxos Issue e PR funcionam em repositório local (e no GitHub, se não cortado).
- Nenhuma evidência inválida chega ao relatório.
- Orçamento respeitado, com relatório parcial no estouro.
- Trace visível no frontend, com custo por passo.
- Modo local-only comprovado por teste.
- Relatório de avaliação publicado com baseline e limitações.
- README, `SECURITY.md` e documentação em `docs/` completos.

## 8. Mapeamento fase × requisitos (resumo)

| Fase | Requisitos principais |
|---|---|
| 1 | RF-03, RF-41/42, RF-43, RF-50 a 54, RNF-02, 04, 10, 12, 13 |
| 2 | RF-10 a 15, RF-20 a 22, RF-40, RF-60, RF-70 a 72, RNF-01, 05, 15, 21 |
| 3 | RF-13, RF-30 a 32 |
| 4 | RF-61 a 65, RNF-06, 14, 16, 17, 18, 19 |
| 5 | RF-04, RF-55, RF-80, RF-81 |
| 6 | RNF-42, documentação |
