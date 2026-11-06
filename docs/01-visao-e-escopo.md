# DevLens — 01. Visão e Escopo

> Documentação do DevLens, etapa 1 de 8. Índice em [README.md](README.md).

## 1. Resumo
DevLens é um sistema de análise de software baseado em agentes de IA. Recebe uma
Issue ou um Pull Request, investiga o repositório usando ferramentas controladas
e devolve um relatório em que cada afirmação aponta para uma evidência verificável
(arquivo, linha, commit). Todo o processo fica visível em um Execution Trace.

## 2. Problema
- Triagem de issues e revisão de PRs exigem navegar código desconhecido e
  consomem tempo.
- Ferramentas de IA existentes costumam entregar conclusões sem mostrar como
  chegaram a elas, o que dificulta a confiança.
- Análises de código grandes estouram contexto e custo de tokens se feitas
  de forma ingênua.

## 3. Objetivos
1. **Investigar com evidências:** nenhuma conclusão sem referência verificável.
2. **Transparência:** o usuário vê o plano, as ferramentas chamadas, as
   delegações e o custo de cada passo.
3. **Confiança honesta:** níveis (alta/média/baixa) justificados pela quantidade
   e qualidade das evidências, sem percentuais inventados.
4. **Economia:** uso mínimo de tokens e contexto, por design.
5. **Segurança:** tratar código analisado como dado não confiável e proteger
   segredos.
6. **Agnóstico de provedor:** funcionar com diferentes APIs e com modelos locais.
7. **Avaliável:** conjunto de casos de teste que mede a qualidade das análises.

## 4. Não-objetivos
- Não é uma plataforma de gerenciamento de projetos (sem Jira, Linear, Slack, CI/CD).
- Não corrige bugs nem altera código; é somente leitura.
- Não substitui a revisão humana; produz subsídios para ela.
- Não suporta múltiplos repositórios, multiusuário ou autenticação de equipes no MVP.
- Não executa o código analisado.

## 5. Usuários e cenários
**Usuário-alvo:** desenvolvedor individual ou mantenedor de projeto pequeno e,
no contexto do portfólio, recrutadores e avaliadores técnicos.

| Cenário | Entrada | Saída |
|---|---|---|
| Investigar bug | Issue (texto ou número) | Hipóteses ranqueadas com evidências e nível de confiança |
| Revisar PR | PR (número ou diff) | Achados por categoria (correção, segurança, testes, manutenibilidade) com evidências |

## 6. Escopo
### MVP
- Entradas: Issue e PR.
- Fonte: repositório local (git + arquivos). API do GitHub como segunda fonte,
  **se houver tempo** (primeira candidata a corte; ver [08-roadmap.md](08-roadmap.md)).
- Orchestrator em grafo híbrido: classifica a entrada, monta um plano curto,
  executa etapas determinísticas e delega a investigação aberta ao Code Analyst
  em loop com limite de passos.
- Etapa de QA dentro do Orchestrator (testes existentes e ausentes).
- Relatório com evidências validadas e níveis de confiança.
- Execution Trace no frontend, com tokens e custo por passo.
- Orçamento por análise (tokens, tool calls, passos).
- Conjunto inicial de avaliação.

### Extensões futuras (fora do MVP)
- Requirements Agent (análise de requisitos, ambiguidades, critérios de aceitação).
- Embeddings para busca semântica.
- Outros provedores Git (GitLab, Bitbucket).
- Comentários automáticos no PR via webhook.

## 7. Princípios de projeto
1. **Evidência primeiro:** afirmação sem evidência verificada é descartada ou
   marcada como baixa confiança.
2. **Agência com limites:** o LLM decide dentro de um grafo, nunca livremente.
3. **Contexto mínimo:** referências em vez de texto bruto, leituras por trecho,
   diff primeiro.
4. **Somente leitura e isolamento:** sem shell arbitrário, acesso restrito
   a repositórios permitidos.
5. **Provedor substituível:** acesso ao modelo atrás de uma interface própria.
6. **Tudo mensurável:** custo e comportamento registrados no trace.

## 8. Premissas e restrições
- Projeto de portfólio desenvolvido por uma pessoa.
- Stack base: Python, FastAPI e LangGraph.
- Custo de tokens deve permanecer baixo; desenvolvimento deve ser viável
  com modelo local.
- **Dependência de terceiros:** ao usar modelos via API em nuvem, o código
  enviado fica sujeito às políticas e à segurança do provedor. O projeto
  reduz a exposição (redação de segredos, envio de trechos mínimos, modo
  local-only), mas não elimina esse risco. A documentação e a interface devem
  informar isso claramente ao usuário.

## 9. Critérios de sucesso
- Toda afirmação do relatório tem evidência que passa na validação automática.
- Análise de uma issue ou PR em repositório **médio** (5 a 50 mil linhas de
  código, até ~500 arquivos de código-fonte; definição em
  [07-avaliacao.md](07-avaliacao.md), seção 3.2) dentro do orçamento definido,
  sem estourar contexto.
- Trace completo e legível para cada análise.
- Métricas de avaliação documentadas (ex.: taxa de acerto nos casos de teste,
  evidências inválidas rejeitadas, custo médio por análise).
- Funciona com pelo menos um provedor em nuvem e um modelo local.

## 10. Riscos iniciais
| Risco | Mitigação inicial |
|---|---|
| Alucinação de evidências | Validação automática de arquivo e linha; descarte se inválida |
| Estouro de contexto/custo | Orçamento por análise, ferramentas com saída limitada |
| Prompt injection via código/issues | Conteúdo tratado como dado; ferramentas somente leitura |
| Escopo crescer demais | Extensões fora do MVP; fases definidas no roadmap |
| Qualidade variável entre modelos | Conjunto de avaliação por modelo |

## 11. Glossário
- **Evidência:** referência verificável a um trecho de código, commit ou issue.
- **Finding:** achado do relatório, com categoria, evidências e confiança.
- **Execution Trace:** registro estruturado e visível de cada passo da análise.
- **Grafo híbrido:** fluxo com etapas fixas e pontos controlados de decisão do LLM.
- **Orçamento:** limite de tokens, chamadas de ferramenta e passos por análise.
- **Commit fixado:** commit resolvido na entrada, usado em toda a análise.
