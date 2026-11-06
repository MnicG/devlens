# ADR 0006 — SQLite, jobs em processo e SSE

- **Status:** aceita

## Contexto
O projeto é de portfólio, feito por uma pessoa, e deve rodar com mínima
infraestrutura e ser fácil de reproduzir.

## Decisão
- **SQLite** para análises, traces, evidências, cache e índice.
- Análises como **tarefas assíncronas em processo**, sem fila externa.
- **SSE** (Server-Sent Events) para transmitir o trace ao frontend.

## Alternativas consideradas
- **PostgreSQL + fila (Redis/Celery):** mais robusto, mas desproporcional ao escopo.
- **WebSocket:** bidirecional sem necessidade; o fluxo do trace é unidirecional.

## Consequências
- Instalação simples via Docker Compose.
- Sem multiusuário nem escala horizontal (fora do escopo do MVP).
- Concorrência limitada e controlada por configuração.
