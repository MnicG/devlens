# ADR 0007 — Local-first e modo local-only

- **Status:** aceita

## Contexto
Código analisado pode ser privado. Usar modelos por API em nuvem sujeita o que
for enviado às políticas e à segurança do provedor, risco que o projeto não
elimina, apenas reduz.

## Decisão
- Operar primeiro sobre **repositório local**; a fonte GitHub é opcional (M\*).
- Enviar ao provedor apenas **trechos mínimos e já redigidos**, com aviso explícito na interface e no relatório.
- Oferecer **modo local-only** com dupla barreira: bloqueio no Model Gateway e, no perfil Docker, rede sem rota de saída.

## Alternativas consideradas
- **Só nuvem:** mais simples, mas inadequado para código privado.
- **Só bloqueio em código:** a garantia dependeria de ausência de bugs.

## Consequências
- Privacidade e transparência são requisitos desde o dia 1.
- Modelos locais pequenos podem ter tool calling mais fraco (risco tratado no roadmap).
- É preciso testar que o modo local-only não faz chamadas de rede de saída.
