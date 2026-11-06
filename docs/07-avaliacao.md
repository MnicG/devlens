# DevLens — 07. Estratégia de Avaliação

> Documentação do DevLens, etapa 7 de 8. Índice em [README.md](README.md).

Objetivo: provar com números, e não com demonstração isolada, que o DevLens
acerta, não inventa evidências, respeita o orçamento e resiste a injeção.
A avaliação é parte do produto (RF-80, RF-81), não um anexo.

## 1. O que medimos

| Dimensão | Métrica | Como é calculada |
|---|---|---|
| Acerto (Issue) | hit@1, hit@3, MRR | Posição da causa correta no ranking de hipóteses |
| Acerto (PR) | recall e precisão de achados | Achados esperados encontrados / achados produzidos corretos |
| Falsos positivos | achados em PR limpo | Nº de achados `kept` em casos sem defeito |
| Validade das evidências | taxa de evidência inválida | Evidências propostas pelo agente que falham em `verify_evidence` |
| Calibração | acerto por nível de confiança | Acerto de achados Alta vs. Média vs. Baixa (Alta deve acertar mais que Baixa) |
| QA | acurácia da cobertura | `covered` vs. gabarito dos cenários de teste |
| Custo | tokens, USD, tool calls, passos | Direto do `Usage` e do trace |
| Orçamento | taxa de relatório parcial | Análises que estouram o orçamento |
| Robustez | resistência a injeção | Casos adversariais em que o relatório não foi manipulado |
| Estabilidade | variância entre execuções | Mesmo caso rodado N vezes (padrão: 3) |

A métrica de validade de evidência é a mais importante do projeto: ela mede
o diferencial central (nenhuma afirmação sem evidência real).

## 2. Princípios

1. **Métricas primárias são determinísticas.** Acerto, evidência e QA são
   comparados por regra com gabarito, sem LLM como juiz.
2. **LLM como juiz só em apoio** (ex.: legibilidade do resumo), claramente
   separado e nunca entra nas métricas principais.
3. **Dev set e test set separados.** Prompts e limiares são ajustados no dev
   set; o test set só roda para os números publicados.
4. **Resultados honestos:** falhas e casos ruins entram no relatório público.
5. **Comparar com linha de base** para mostrar que a arquitetura agrega valor.

## 3. Conjunto de casos

Tamanho realista para uma pessoa: **~30 casos** no MVP, ampliáveis.

| Grupo | Qtde | Origem |
|---|---|---|
| Issue com causa conhecida | 10 | 5 sintéticos (bug plantado) + 5 de projetos open source reais |
| PR com defeito conhecido | 8 | 5 sintéticos + 3 de PRs reais com bug corrigido depois |
| PR limpo (controle negativo) | 4 | PRs corretos, sem defeito esperado |
| Adversarial (injeção) | 5 | Sintéticos: comentário, issue, commit, README, nome de arquivo |
| QA (cobertura de testes) | 3 | Alvos com lacuna de teste conhecida |

Divisão: ~2/3 dev e ~1/3 test, definida por sorteio fixo e registrada.

### 3.1 Repositórios de teste

- **Sintéticos:** 2 repositórios pequenos criados para a avaliação (um
  Python/FastAPI e um TypeScript/Express, ~2–5 mil linhas), com histórico git
  construído de propósito e bugs plantados com gabarito exato. Controlam o
  gabarito e **não existem nos dados de treino dos modelos**.
- **Reais:** 2 a 3 projetos open source pequenos/médios em Python e JS/TS.
  Gabarito derivado de **issue + commit de correção** (a causa é o código que
  o commit corrigiu). Cada caso fixa o `commit` anterior à correção.
- **Contaminação:** modelos podem ter visto projetos públicos famosos.
  Mitigação: preferir issues recentes e projetos pouco conhecidos, e manter
  os sintéticos como base principal. Esse limite fica documentado.

### 3.2 Definição de repositório "médio"

| Faixa | Tamanho | Papel |
|---|---|---|
| Pequeno | até ~5 mil linhas de código | Casos sintéticos, testes rápidos |
| **Médio (alvo do MVP)** | 5 a 50 mil linhas, até ~500 arquivos de código-fonte | Casos reais e meta de orçamento |
| Grande | acima de 50 mil linhas | Fora do MVP; análise restrita a arquivos alterados, com aviso |

## 4. Formato do caso

Cada caso é um arquivo YAML em `eval/cases/`.

```yaml
id: issue-014
split: dev                       # dev | test
kind: issue                      # issue | pr | qa | adversarial
repo: { source: local, location: fixtures/shop-api, commit: 3fa91c2 }
input:
  ref: "Depois de atualizar o perfil, o usuário é deslogado."
expected:
  root_causes:                   # issue: causa(s) aceitas
    - path: app/services/user_service.py
      line_start: 42
      line_end: 58
      tolerance: 10              # linhas de folga no casamento
  must_cite_kinds: [code]        # tipos de evidência exigidos
  forbidden: []                  # afirmações que não podem aparecer
notes: "Sessão invalidada ao reatribuir o objeto User"
```

Variações por tipo:
- **pr:** `expected.findings` com `{category, path, line_start, line_end, min_severity}`;
  PR limpo usa `expected.findings: []`.
- **qa:** `expected.scenarios` com `{description, covered: true|false}`.
- **adversarial:** `expected.must_not` (ex.: o relatório não pode conter o
  texto injetado como achado, nem chamar ferramenta fora do escopo) e
  `payload_location` (onde a injeção foi plantada).

### Regra de casamento (determinística)

Um achado produzido **acerta** um esperado quando:
1. mesmo `path`; e
2. intervalo de linhas sobrepõe o esperado, considerando a `tolerance`; e
3. (PR) mesma `category`; e
4. pelo menos uma evidência `verified` do achado cai nesse trecho.

Isso impede acerto por sorte sem evidência.

## 5. Linhas de base e ablações

| Variante | O que mostra |
|---|---|
| **Baseline 1:** uma única chamada ao LLM com diff/issue, sem ferramentas | Valor de investigar com ferramentas |
| **Baseline 2:** grafo completo, sem `verify_evidence` | Valor da verificação (taxa de evidência inválida sem filtro) |
| **Ablação:** sem índice (só `read_range` e busca simples) | Valor do índice local |
| **Ablação:** orçamento reduzido à metade | Curva acerto × custo |
| **Completo** | Resultado principal |

Cada variante roda nos mesmos casos e modelos, e a tabela comparativa vai
para o README.

## 6. Modelos avaliados

Pelo menos três configurações (RF-71/81):
1. **Local** (via Ollama), para desenvolvimento.
2. **Nuvem econômica** (triagem e análise).
3. **Nuvem forte** na síntese, com modelo econômico no resto (papéis).

O resultado por modelo mostra o custo-benefício real, que serve ao tema de
economia do projeto.

## 7. Calibração do orçamento

1. Rodar o dev set com orçamento folgado (ex.: 200 mil tokens, 100 chamadas).
2. Medir distribuição de tokens, chamadas e passos nos casos **corretos**.
3. Definir o padrão em torno do **percentil 90** desses casos, com margem.
4. Publicar a curva "acerto × orçamento" (variante da seção 5) e justificar
   o ponto escolhido.
5. Reavaliar quando o modelo ou o repositório-alvo mudar de faixa.

Os valores atuais (60 mil tokens, 40 chamadas, 12 passos) são provisórios até
essa rodada. O mesmo vale para os limites de saída das ferramentas (etapa 5).

## 8. Execução, gravação e CI

- **Runner:** `python -m eval run --suite dev --models local,cloud-cheap --variant full --repeats 3`.
- **Record/replay do modelo:** respostas do LLM podem ser gravadas
  (cassetes, indexadas por hash do prompt) e reproduzidas. Permite testar o
  grafo, a verificação e o scoring de forma **determinística, sem custo e
  offline**.
- **CI:**
  - sempre: testes unitários, contratos e avaliação em modo replay;
  - manual/agendado: avaliação real com modelos, pela chave do próprio autor,
    com teto de custo.
- **Relatório:** gera `eval/reports/<data>.md` e `.json` com métricas por
  modelo e variante, e os casos que falharam com link para o trace.

### Tabelas SQLite de avaliação

```sql
CREATE TABLE eval_cases (
  id TEXT PRIMARY KEY,
  split TEXT NOT NULL,                 -- dev | test
  kind TEXT NOT NULL,
  case_json TEXT NOT NULL,
  content_hash TEXT NOT NULL           -- detecta caso alterado depois de rodar
);

CREATE TABLE eval_runs (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  suite TEXT NOT NULL,
  variant TEXT NOT NULL,               -- full | no_tools | no_verify | no_index | half_budget
  model_config_json TEXT NOT NULL,
  git_sha TEXT NOT NULL,               -- versão do DevLens avaliada
  mode TEXT NOT NULL                   -- live | replay
);

CREATE TABLE eval_results (
  run_id TEXT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
  case_id TEXT NOT NULL REFERENCES eval_cases(id),
  repeat_idx INTEGER NOT NULL,
  analysis_id TEXT,                    -- análise gerada (liga ao trace)
  hit_at_1 INTEGER, hit_at_3 INTEGER, reciprocal_rank REAL,
  findings_correct INTEGER, findings_total INTEGER, findings_expected INTEGER,
  evidences_total INTEGER, evidences_invalid INTEGER,
  qa_correct INTEGER, qa_total INTEGER,
  injection_resisted INTEGER,          -- 0/1/NULL
  status TEXT NOT NULL,                -- completed | partial | failed
  tokens_in INTEGER, tokens_out INTEGER, tool_calls INTEGER, steps INTEGER,
  cost_usd REAL,
  PRIMARY KEY (run_id, case_id, repeat_idx)
);
```

## 9. Como interpretar e publicar

- Reportar **média e dispersão** das 3 repetições, nunca só o melhor caso.
- Com ~30 casos, os números são **indicativos, não estatisticamente fortes**.
  O README diz isso explicitamente, com o tamanho do conjunto e os intervalos.
- Seção "Falhas conhecidas" com 3 a 5 casos em que o sistema errou e por quê.
- Tabela final no README: métricas × (baseline, completo) × modelo.
- **Metas:** definidas só depois da primeira rodada de baseline, para não
  inventar números. Meta qualitativa fixa desde já: **evidência inválida
  entregue ao usuário = 0** (garantida pela verificação), e Alta confiança
  acertando mais que Baixa.

## 10. Entregas desta etapa na implementação

1. Esquema dos casos (Pydantic) e validador dos YAML.
2. Os dois repositórios sintéticos com bugs plantados e gabarito.
3. Scorer determinístico (casamento, métricas) com testes próprios.
4. Runner com record/replay.
5. Primeiros 10 casos (dev), depois expansão ao conjunto completo.

## 11. Pontos em aberto
- Escolha dos 2–3 projetos open source reais: depende de achar issues com
  commit de correção claro nas linguagens do MVP.
- Se casos adversariais usam também um juiz LLM para detectar manipulação
  sutil, ou só regras: começar só com regras.
- Limiar final de repetições (3) conforme custo da avaliação real.
