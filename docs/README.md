# DevLens — Documentação

DevLens é um sistema de análise de software baseado em agentes de IA: recebe uma
Issue ou um Pull Request, investiga o repositório com ferramentas somente
leitura e entrega um relatório em que toda afirmação aponta para uma evidência
verificável, com o processo inteiro visível em um Execution Trace.

## Como ler

Os documentos seguem a ordem em que o projeto foi pensado. Quem tem pouco tempo
pode ler 01, 03 e 08.

| # | Documento | Responde a |
|---|---|---|
| 01 | [Visão e escopo](01-visao-e-escopo.md) | O que é, para quem e o que fica de fora |
| 02 | [Requisitos](02-requisitos.md) | O que o sistema precisa fazer e com que qualidade |
| 03 | [Arquitetura](03-arquitetura.md) | Como as peças se encaixam (camadas, grafo, componentes) |
| 04 | [Contratos de dados](04-contratos-de-dados.md) | Formatos de evidência, achado, trace, API e banco |
| 05 | [Catálogo de ferramentas](05-catalogo-de-ferramentas.md) | O que os agentes podem fazer, com quais limites |
| 06 | [Segurança e threat model](06-seguranca-e-threat-model.md) | Ameaças, controles, privacidade e retenção |
| 07 | [Avaliação](07-avaliacao.md) | Como medir se o sistema acerta e quanto custa |
| 08 | [Roadmap](08-roadmap.md) | Fases, estimativas, marcos e linha de corte |

## Decisões de arquitetura (ADRs)

| ADR | Decisão |
|---|---|
| [0001](adr/0001-escopo-issue-e-pr.md) | Escopo A+C: Issue Investigator e PR Review sobre a mesma base |
| [0002](adr/0002-langgraph-e-modelo-agnostico.md) | LangGraph, com modelo atrás de interface própria |
| [0003](adr/0003-orquestrador-hibrido.md) | Orquestrador em grafo híbrido |
| [0004](adr/0004-verificacao-de-evidencia-e-confianca.md) | Verificação de evidência sem LLM e confiança por regras |
| [0005](adr/0005-indice-local-sem-embeddings.md) | Índice local (tree-sitter + FTS5) em vez de embeddings |
| [0006](adr/0006-sqlite-jobs-em-processo-e-sse.md) | SQLite, jobs em processo e SSE |
| [0007](adr/0007-local-first-e-modo-local-only.md) | Local-first e modo local-only |
| [0008](adr/0008-snippet-hash-como-hmac.md) | `snippet_hash` como HMAC com chave local |

## Convenções

- Requisitos: **RF** (funcional) e **RNF** (não funcional); prioridade **M**
  (MVP), **M\*** (MVP se houver tempo) e **E** (extensão).
- Valores numéricos (orçamento, limites de ferramentas, retenção) são
  **provisórios** até a calibração descrita em [07-avaliacao.md](07-avaliacao.md).
- Premissa declarada: ao usar modelos em nuvem, o código enviado fica sujeito às
  políticas do provedor. Ver [06](06-seguranca-e-threat-model.md), seção 6.
