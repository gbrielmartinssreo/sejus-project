# Salvaguardas — Redes de Segurança (Mantidas Inalteradas)

## 1. Âncoras Exatas (`trecho_original`)
- Modelo DEVE copiar **fielmente** do trecho recebido (incluindo rótulo)
- Match case-insensitive, normalizado (`_chave_texto` remove acentos, espaços extras)
- Falha de âncora → item descartado, cobertura rebaixada para `falhou`

## 2. Proteção de Subitens (`_absorver_subitens`)
- Alteração de um artigo/pai **engole** incisos/parágrafos filhos
- Subitens absorvidos marcados como removidos internamente
- Evita duplicação: incisos reformulados não aparecem como "novos" + "alterados"

## 3. Anti-Renumeração (`_RE_AP_RENUMERACAO`, regra LC 95/1998)
- Artigos novos **no meio** → sufixo letra (Art. 6º-A, 6º-B...)
- Numeração contínua (Art. 34, 35...) **apenas após último artigo**
- Renumeração de capítulos existentes → `nao_aplicado` (impedimento concreto)

## 4. Revogação Específica
- Revogação só aplicada se cita **norma específica** (número, data, órgão)
- "Revogam-se as disposições em contrário" → descartado (vago)

## 5. Lastro Temático (`_validar_lastros`, `_checar_coerencia_lastros`)
- Cada mudança declara `lastro` (referência a ato base)
- `_validar_lastros`: anota documento do RAG correspondente, marca `lastro_fantasma` se não encontrado
- `_checar_coerencia_lastros`: **tema do lastro ≠ tema da mudança** → `requer_decisao_juridica=True`
  - Ex.: lastro sobre "EPI" usado para mudar "prazo de recurso" → marcação jurídica

## 6. Filtro de Patch (`_problemas_do_patch`, `_filtrar_patch_valido`)
Verificações por item:
- `trecho_original` encontrado no original
- `novo_texto` diferente do original
- `rotulo` presente
- Categoria válida (5 permitidas)
- `requer_decisao_juridica` coerente com categoria/obrigação
- Sem duplicação de `rotulo`/`trecho_original` dentro do patch

Retry: até 2 tentativas por janela com feedback dos problemas.

## 7. Cobertura de Apontamentos (`_validar_cobertura`)
- Cada apontamento da análise → deve ter entrada em `apontamentos_analise` com `status`
- Status: `aplicado` | `pendente` | `nao_aplicado` | `falhou`
- `nao_aplicado` exige `motivo` **concreto** (não "não foi alterado")
- Apontamento sem declaração → `falhou`

## 8. Duplicação Cruzada (`_duplicacoes_do_patch`)
- Após consolidação de todas as janelas: verifica se patch insere texto já existente
- Se houver duplicação → **patch inteiro descartado**, fallback cópia intacta

## 9. Validação Pós-Geração DOCX (`docx_validacao.validar_docx_gerado` — 5 passagens)
1. **Estrutura**: número, ementa, assinaturas preservados
2. **Conteúdo**: alterações/remoções/adições refletidas no arquivo
3. **Formatação**: estilos, numeração, hierarquia mantidos
4. **Rastreabilidade**: comentários/controle de alterações no Word
5. **Integridade**: nenhum parágrafo órfão, subitens preservados

### Descarte Responsável
- Falha em passagem → identifica `rotulos` responsáveis
- Remove apenas itens responsáveis → remonta DOCX
- Até 3 tentativas; sem responsáveis ou patch vazio → fallback cópia intacta
- **NUNCA entrega arquivo com alterações não confirmadas**

## 10. Reconciliação de Plano (NOVA — executa por último)
- `reconciliar_plano` reflete **apenas** o patch que sobreviveu a TODAS as redes acima
- Status `aplicado`/`pendente`/`nao_aplicado`/`falhou` são **verdade ground truth**
- Oportunidades não avaliadas aparecem explicitamente

---

## Ordem de Execução no Código (`_melhorar_e_relatar`)

```python
# 1. Geração do patch (janelas + filtro + cobertura)
estrutura, alteracoes, remocoes, adicoes, lacunas = gerar_estrutura_melhoria(...)
plano_declarado = estrutura.pop("_plano_declarado")

# 2. Validação de lastro
for grupo in (alteracoes, remocoes, adicoes):
    _validar_lastros(grupo, contexto)
_checar_coerencia_lastros(alteracoes, remocoes, adicoes, contexto)

# 3. Duplicação
if _duplicacoes_do_patch(...):
    alteracoes, remocoes, adicoes = [], [], []
    cobertura = _validar_cobertura(..., patch_descartado=True)

# 4. Política de categorias (APÓS validações, ANTES do DOCX)
aplicar_politica_categorias(alteracoes, remocoes, adicoes)

# 5. Montagem DOCX + validação 5 passagens (com descarte responsável)
output_path = montar_docx_revisado(...)
passagens = validar_docx_gerado(...)
# ... loop de descarte/remonta ...

# 6. RECONCILIAÇÃO FINAL DO PLANO (reflete patch final)
plano_melhoria = reconciliar_plano(plano_declarado, oportunidades, alteracoes, remocoes, adicoes)

# 7. Resposta
return {"plano_melhoria": plano_melhoria, ...}
```

---

## Garantias de Não-Regressão
- Todas as redes 1-9 existiam antes e **não foram modificadas**
- Nova rede 10 (reconciliação final) **só adiciona visibilidade**, não bloqueia
- Testes de `test_correcao_pos_analise.py` (55) e `test_document_generation.py` (59) passam
- Validação pós-geração continua sendo a última barreira antes da entrega