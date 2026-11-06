# ADR 0004 — Verificação de evidência sem LLM e confiança por regras

- **Status:** aceita

## Contexto
Um percentual de confiança gerado por LLM não tem calibração real, e modelos
podem inventar referências de arquivo e linha.

## Decisão
- Toda evidência é **verificada por código** no commit fixado (arquivo existe, linhas existem, HMAC do trecho confere).
- O LLM **propõe** achados; **confiança** (alta/média/baixa), **status** e justificativa são definidos por regras e templates, nunca pelo modelo.
- Achados sem evidência verificada são descartados; evidência falha rebaixa o achado.

## Alternativas consideradas
- **Confiança numérica pelo LLM:** parece precisa, mas não é calibrada.
- **Verificação por outro LLM:** custa tokens e herda os mesmos erros.

## Consequências
- Custo zero de tokens na verificação e comportamento determinístico.
- Meta fixa: nenhuma evidência inválida chega ao usuário.
- A regra de confiança pode ser auditada e testada como código comum.
