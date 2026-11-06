# ADR 0002 — LangGraph com modelo agnóstico de provedor

- **Status:** aceita

## Contexto
O sistema precisa de orquestração com estado, roteamento e retries, e de um
trace natural do que foi feito. Também deve evitar dependência de um único
provedor, tanto por acessibilidade quanto por custo.

## Decisão
Usar **Python + FastAPI + LangGraph**, e acessar modelos exclusivamente por uma
interface própria (**Model Gateway**) com adaptadores para nuvem e para modelo
local (Ollama).

## Alternativas consideradas
- **Pydantic AI:** leve e tipado, mas exigiria escrever a orquestração à mão.
- **OpenAI Agents SDK:** simples, mas focado em um ecossistema, sem abstração multi-provedor.

## Consequências
- Curva de aprendizado maior que alternativas mais simples.
- Trocar de provedor é configuração, não reescrita.
- Orçamento, redação de segredos e custo ficam centralizados no Gateway.
- Desenvolvimento pode usar modelo local para economizar tokens.
