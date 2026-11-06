# ADR 0001 — Escopo: Issue Investigator e PR Review

- **Status:** aceita

## Contexto
A ideia inicial previa três entradas (requisito, issue e PR) e vários agentes.
Revisão de PR por agentes já é um espaço ocupado por ferramentas conhecidas, e
três entradas rasas tendem a ficar superficiais e estourar o tempo de um projeto
de portfólio feito por uma pessoa.

## Decisão
Começar com **Issue Investigator** (hipóteses ranqueadas com evidências, o menos
saturado) e **PR Review**, compartilhando a mesma base de ferramentas, evidências
e trace. O Requirements Agent e outras entradas ficam como extensões futuras.

## Alternativas consideradas
- **Só PR Review:** mais comum, compete diretamente com ferramentas conhecidas.
- **Três entradas rasas:** mostra amplitude, mas com pouca profundidade e maior risco de prazo.

## Consequências
- Diferencial vem de transparência, evidência verificável e avaliação, não de "ter vários agentes".
- O contrato `Finding` é único para hipóteses e achados de PR.
- Requirements Agent exige nova etapa de especificação quando for implementado.
