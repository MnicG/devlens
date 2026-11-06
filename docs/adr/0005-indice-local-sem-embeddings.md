# ADR 0005 — Índice local em vez de embeddings no MVP

- **Status:** aceita

## Contexto
A análise precisa localizar código relevante gastando pouco contexto e poucos
tokens, sem depender de serviços externos.

## Decisão
Indexar cada commit com **tree-sitter** (símbolos, imports e chamadas para
Python e JS/TS) e **busca textual BM25 via SQLite FTS5**. Embeddings ficam como
extensão futura.

## Alternativas consideradas
- **Embeddings desde o início:** busca semântica melhor, porém mais custo, infraestrutura e não determinismo.

## Consequências
- Busca e navegação funcionam sem LLM, offline e de forma reproduzível.
- `find_references` é aproximada (sem resolução de tipos) e marcada como tal.
- Consultas puramente semânticas podem ter recall menor; será medido na avaliação.
