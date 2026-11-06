# ADR 0003 — Orquestrador em grafo híbrido

- **Status:** aceita

## Contexto
Um pipeline fixo é previsível, mas sem agência real. Um supervisor totalmente
dinâmico gasta mais tokens, é difícil de depurar e dá resultados instáveis.

## Decisão
O Orchestrator é um grafo com **etapas determinísticas** (obter contexto,
verificar evidência, pontuar confiança, orçamento) e **pontos controlados de
decisão do LLM** (classificar, planejar, analisar diff e investigar em loop
limitado). O QA é uma etapa do Orchestrator, não um agente separado.

## Alternativas consideradas
- **Pipeline fixo:** barato, mas descaracteriza o propósito do projeto.
- **Supervisor totalmente livre:** instável e caro.

## Consequências
- Agência concentrada onde agrega valor (investigação).
- Plano, delegações e motivos ficam claros no trace.
- Todo loop tem limite de passos, detecção de repetição e orçamento.
