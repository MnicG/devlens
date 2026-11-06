# ADR 0008 — `snippet_hash` como HMAC com chave local

- **Status:** aceita

## Contexto
A evidência guarda um hash do trecho original (antes da redação de segredos)
para permitir a verificação. Um hash simples de um trecho curto com segredo de
baixa entropia poderia ser revertido por força bruta, vazando o segredo pelo banco.

## Decisão
Calcular `snippet_hash` como **HMAC** com uma chave local da instalação, sobre o
trecho original. A verificação recalcula o HMAC com a mesma chave.

## Alternativas consideradas
- **SHA-256 simples:** barato, mas exposto a ataque de dicionário em trechos curtos.
- **Não guardar hash e comparar o texto:** exigiria persistir o texto original, o que contraria a redação.

## Consequências
- Sem a chave local, o hash não permite recuperar o trecho.
- A chave fica fora do banco e do controle de versão; perdê-la invalida a verificação de análises antigas.
- Pequeno detalhe de implementação extra (gestão da chave na configuração).
