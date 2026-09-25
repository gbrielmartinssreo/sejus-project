# CHANGELOG — Melhoria Proativa (2026-09-25)

## Commit Base
- Branch: `Refac`
- Base: `fa3ebea` (`feat(analise): classifica resposta em 4 canonicas e alinha registry`)

## Arquivos Alterados

### `src/sejus_project/tools/llm_tools/analise_formatacao.py`
| Mudança | Detalhes |
|---------|----------|
| **Novas constantes** | `TIPO_ISSUE_CONFIRMADO = "confirmed_issue"`, `TIPO_ATENCAO_ACIONAVEL = "actionable_attention"`, `TIPO_LACUNA_ESTRUTURAL = "structural_gap"` |
| **Nova função** | `extrair_oportunidades(analise: str) -> list[dict]` — extrai oportunidades das 3 classes com IDs estáveis (`op-1`, `op-2`...) |
| **Export** | Adicionadas constantes + função em `__all__` |
| **Heurística** | `confirmed_issue` ← PROBLEMAS_CONFIRMADOS; `actionable_attention` ← PONTOS_DE_ATENCAO com verbo acionável; `structural_gap` ← (verbo + fato gerador) OU (fato gerador sem verbo) OU LACUNAS_ESTRUTURAIS |

### `src/sejus_project/tools/llm_tools/document_improvement.py`
| Mudança | Detalhes |
|---------|----------|
| **Taxonomia** | `CATEGORIAS_APLICAVEIS = [safe_correction, clarity_improvement, structural_improvement, normative_proposal, unsupported]` |
| **Riscos** | `RISCO_BAIXO`, `RISCO_MEDIO`, `RISCO_ALTO` |
| **Motivos descarte** | `MOTIVO_SEM_LASTRO`, `MOTIVO_JA_RESOLVIDO`, `MOTIVO_MUDANCA_DESECESSARIA`, `MOTIVO_MATERIALMENTE_SENSIVEL`, `MOTIVO_RISCO_ALTERAR_SENTIDO`, `MOTIVO_DEPENDE_DECISAO_INSTITUCIONAL` |
| **Schema `MELHORIA_DEFINITION`** | `categoria`, `cria_obrigacao`, `risco_juridico` **obrigatórios** em alteracoes/remocoes/adicoes_estruturais; `texto` **obrigatório** em adicoes_estruturais; `plano_melhoria` array no topo |
| **`_sistema_melhoria()`** | Reescrito completo: 8 perguntas internas, lacunas com fundamento próprio OU RAG, clareza permitida, múltiplas categorias/achado, plano obrigatório, origem obrigatória |
| **`_usuario_melhoria()`** | Injeta bloco `OPORTUNIDADES DE MELHORIA` com IDs; atualiza texto do RAG para dizer "não é única fonte" |
| **`aplicar_politica_categorias(alteracoes, remocoes, adicoes)`** | Política centralizada: unsupported removido; normative/obrigacao → juridico+alto; clareza+obrigacao → structural; fallback clarity |
| **`normalizar_motivo_descarte`** | Normaliza 6 motivos canônicos (aceita variações com/sem acento, underscore, espaço) |
| **`_motivo_concreto`** | Rejeita vazios, "não foi alterado", "não se aplica", <20 chars sem keyword |
| **`_descricao_motivo`** | Descrição amigável para cada motivo canônico |
| **`_categoria_declarada` / `_risco_declarado` / `_cria_obrigacao`** | Helpers de extração segura do item |
| **`reconciliar_plano`** | **NOVO**: dedupe declarados (preferência `referencia`), match por `_chave` (id+texto) **OU** `_chave_id` (id-only), oportunidades não declaradas → "não avaliada", status ground truth |
| **`gerar_estrutura_melhoria`** | Retorna tupla de 7 elementos (+ `plano_declarado`); armazena `_plano_declarado` + `_plano_melhoria` (reconciliado preliminar) na estrutura |

### `src/sejus_project/tools/llm_tools/document_generation.py`
| Mudança | Detalhes |
|---------|----------|
| **Import** | `from ... import analise_formatacao` |
| **`_melhorar_e_relatar`** | `oportunidades = analise_formatacao.extrair_oportunidades(analise_completa)` → `valores["oportunidades"]` |
| **Captura plano** | `plano_declarado = estrutura.pop("_plano_declarado")` |
| **Reconciliação FINAL** | Após TODAS as redes (lastro, coerência, duplicação, validação DOCX 5 passagens): `plano_melhoria = reconciliar_plano(plano_declarado, oportunidades, alteracoes, remocoes, adicoes)` |
| **Política aplicada late** | `aplicar_politica_categorias(alteracoes, remocoes, adicoes)` **após** validações, **antes** de montar DOCX |
| **Resposta** | `plano_melhoria` em `_ultima_comparacao` e `resposta` |

### `tests/test_minuta_melhoria.py`
| Mudança | Detalhes |
|---------|----------|
| `test_melhoria_definition_tem_remocoes_adicoes_e_lacunas` | Atualizado `required` para incluir `categoria`, `cria_obrigacao`, `risco_juridico` (alteracoes/remocoes/adicoes) e `texto` (adicoes) |

---

## Comportamento Novo — Resumo Funcional

1. **Análise gera oportunidades** — `extrair_oportunidades` lê as 4 seções canônicas e produz `op-1`..`op-N` tipados
2. **Planner recebe oportunidades** — `_usuario_melhoria` injeta bloco explícito; system prompt manda avaliar TODAS
3. **Modelo declara plano** — `plano_melhoria` no retorno da function, uma linha por achado/oportunidade
4. **Patch passa por redes** — lastro, coerência, duplicação, validação DOCX podem descartar itens
5. **Política aplicada no patch final** — categorias/risco/obrigação normalizados no que sobreviveu
6. **Plano reconciliado FINAL** — reflete **exatamente** o patch entregue; status ground truth; oportunidades não avaliadas visíveis
7. **Usuário vê plano** — no relatório/comparação: o que foi aplicado, o que ficou pendente jurídico, o que foi descartado (com motivo canônico), o que o planner ignorou

---

## Testes
- **265 passam** (183 módulos alterados + 82 outros)
- **3 falhas baseline** inalteradas (agent_robustez 2, passagens 1)
- **Ruff**: sem novos erros (6 avisos pré-existentes em document_generation)

---

## Próximos Commits Sugeridos
1. `feat(opportunities): testes unitários extrair_oportunidades + calibração regex`
2. `feat(policy): normative_proposal sempre risco alto + propagar descartes ao plano`
3. `feat(reconciliation): testes end-to-end plano final pós-validação DOCX`
4. `docs: atualizar SKILL.md com nova taxonomia e fluxo proativo`