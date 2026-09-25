# Testes — Status e Cobertura

## Resumo Atual (2026-09-25)

| Módulo | Testes | Status | Observações |
|--------|--------|--------|-------------|
| `test_analise_formatacao.py` | 37 | ✅ PASS | Reformulação 4 classes + `extrair_oportunidades` |
| `test_correcao_pos_analise.py` | 55 | ✅ PASS | Fluxo análise → correção → DOCX completo |
| `test_document_generation.py` | 59 | ✅ PASS | Geração, montagem, validação, modelos |
| `test_minuta_melhoria.py` | 32 | ✅ PASS | Schema, prompts, janelas, dedupe, lastro |
| **Total (excl. baseline)** | **183** | ✅ **PASS** | |
| `test_agent_robustez.py` | 2 falhas | ⚠️ BASELINE | Encoding preexistente, não relacionado |
| `test_passagens_analise_e_conferencia.py` | 1 falha | ⚠️ BASELINE | Preexistente, não relacionado |
| **Grand Total** | **186 + 3 falhas** | | 3 falhas = baseline conhecido |

---

## Testes que Validam a Nova Funcionalidade

### `test_minuta_melhoria.py`
- `test_melhoria_definition_tem_remocoes_adicoes_e_lacunas` — **atualizado** para novos required fields (categoria, cria_obrigacao, risco_juridico, texto, plano_melhoria)
- `test_schema_estende_lastro_e_requer_decisao_juridica` — valida campos de lastro e decisão jurídica
- `test_gerar_estrutura_melhoria_define_padrao_de_requer_decisao` — default de requer_decisao_juridica
- `test_gerar_estrutura_melhoria_identifica_lastro_no_contexto` / `test_gerar_estrutura_melhoria_sinaliza_lastro_fantasma` — lastro
- `test_validar_lastros_tambem_anota_alteracoes_e_remocoes` — lastro em todos grupos
- `test_checar_coerencia_lastros_marca_requer_sem_sobreposicao_tematica` / `..._nao_marca_quando_temas_concordam` — coerência temática
- `test_problemas_do_patch_aceita_patch_saudavel` / `detecta_ancora_invalida` / `detecta_campos_ausentes` — filtro de patch
- `test_melhoria_patch_problematico_retenta_com_mensagem_de_retry` / `..._após_retry_descarta_mudanca_invalida` — retry
- `test_janelas_conteudo_cobre_documento_inteiro` / `..._pequeno_devolve_uma_janela` — janelas
- `test_dedupe_mudancas_remove_repeticao_entre_janelas` — dedupe cross-janela
- `test_usuario_melhoria_inclui_documento_maior_que_60k` — prompt com oportunidades
- `test_gerar_estrutura_melhoria_processa_documento_grande_em_janelas` — integração janelas

### `test_correcao_pos_analise.py`
- `test_fluxo_completo_analise_aprofundamento_correcao_docx` — **end-to-end** com apontamentos, cobertura, DOCX
- `test_fallback_nenhuma_correcao_entrega_copia_intacta` / `..._parcial_entrega_documento_parcialmente_corrigido` / `..._todas_correcoes_entregam_documento_corrigido` — fallbacks
- `test_renumeracao_do_modelo_que_quebraria_a_sequencia_e_descartada` / `test_renumeracao_canonica_corrige_capitulo_duplicado` — anti-renumeração
- `test_passagens_de_validacao_registradas_na_resposta` / `..._no_fallback_intacto` — validação pós-geração
- `test_considerando_ambiguo_descartado_preserva_os_originais` — preservação
- `test_melhoria_retenta_sem_cobertura_e_entrega_com_cobertura` / `..._rebaixa_cobertura_de_patch_sem_ancora` — cobertura
- `test_melhoria_injeta_apontamentos_da_analise_registrada` / `..._nao_injeta_analise_de_versao_diferente` — registry
- `test_extrair_apontamentos_renumerar_capitulos` / `..._exclui_promessas_ofertas_e_perguntas` — extração
- `test_aprofundamento_consolida_apontamentos_no_mesmo_documento` / `..._envia_todos_os_apontamentos` — aprofundamento
- `test_correcao_pos_analise_vai_para_melhoria` / `..._sem_analise_*` — roteamento
- `test_pedido_de_correcao_nao_confunde_ajuste_generico` — disambiguação
- `test_aplicar_patch_absorve_incisos_reformulados` / `test_montar_docx_revisado_tacha_incisos_reformulados` — subitem protection
- `test_cobertura_aplicada_quando_mudanca_ancorada` / `..._rebaixa_quando_ancora_nao_encontrada` / `..._pendente_quando_lastro_invalido` — cobertura + lastro
- `test_marcar_origem_vincula_item_ao_apontamento_aprovado` / `..._nao_aceita_rotulo_declarado_sem_vinculo` — origem
- `test_cobertura_aprovado_com_lastro_ausente_nao_vira_pendente` / `..._iniciativa_modelo_sem_lastro_continua_pendente` — origem + lastro
- `test_validar_lastros_aprovado_registra_origem_em_vez_de_inventada` / `..._iniciativa_modelo_mantem_aviso_de_inventada` — lastro + origem
- `test_checar_coerencia_exclui_item_aprovado` / `..._mantem_marca_para_iniciativa_modelo` — coerência
- `test_cobertura_apontamento_sem_declaracao_falhou` / `..._nao_aplicado_com_impedimento_concreto` / `..._motivo_vago_vira_falha` / `..._problemas_cobertura_ausente_e_sem_motivo` — cobertura edge cases

### `test_document_generation.py`
- `test_melhoria_docx_preserva_layout_e_gera_comparacao` — layout + comparação
- `test_melhoria_identifica_documento_do_lastro` / `..._lastro_fantasma_sinaliza_aviso_sem_bloquear` — lastro
- `test_melhoria_sem_nome_usa_importacao_mais_recente` / `..._repetida_nao_regenera_arquivo` / `..._com_diretrizes_novas_regenera` — cache/regeneração
- `test_obter_textos_comparacao_devolve_dados_da_ultima_melhoria` / `..._sem_melhoria` — comparação
- `test_merge_de_adicoes_reflete_na_comparacao_do_turno` — merge
- `test_montar_docx_revisado_marca_diferencas_no_original` / `..._adicao_sai_como_revisao_de_insercao` — DOCX revisado
- `test_montagem_adicao_sai_como_revisao_de_insercao` — adições estruturais
- `test_montagem_com_docx_de_usuario_preserva_formatacao` — preservação formatação
- `test_modelo_referencia_repassado_ao_llm` — modelo no prompt
- `test_filtra_lacunas_sem_precedente` — lacunas
- `test_contexto_melhoria_dedupa_fontes_e_etiqueta_tema` — contexto RAG
- `test_precedente_exige_score_e_palavra_chave` / `..._sem_score_minimo_nao_passa` — RAG thresholds
- `test_tema_por_nome_aceita_variacoes` — temas
- `test_guarda_nao_bloqueia_pedido_normativo` — não bloqueio
- `test_confirmacao_faca_isso_gera_pendente` — confirmação

### `test_analise_formatacao.py`
- 37 testes da reformulação das 4 classes canônicas
- Cobertura: infinitivos corretivos, exclusão de particípios/adverbiais, lista solta, idempotência, registry

---

## Testes Pendentes (Próximos Passos)

### Unitários Específicos da Nova Funcionalidade
- [ ] `test_extrair_oportunidades_classifica_corretamente` — 3 tipos, dedupe, IDs
- [ ] `test_aplicar_politica_categorias_escalada_obrigacao` — clarity+obrigacao → structural
- [ ] `test_aplicar_politica_categorias_normative_sempre_alto` — normative_proposal → risco alto
- [ ] `test_aplicar_politica_categorias_unsupported_removido` — unsupported sumiu do patch
- [ ] `test_normalizar_motivo_descarte_variacoes` — 6 motivos + variações
- [ ] `test_motivo_concreto_rejeita_vagos` — "não foi alterado", curtos, etc.
- [ ] `test_reconciliar_plano_dedupe_janelas` — mesmo achado em 2 janelas
- [ ] `test_reconciliar_plano_match_por_id` — ID match sem texto idêntico
- [ ] `test_reconciliar_plano_nao_avaliada_visivel` — oportunidade não declarada aparece
- [ ] `test_reconciliar_plano_status_pendente_quando_requer_juridica` — pendente correto
- [ ] `test_reconciliar_plano_final_apos_validacao_docx` — reflete descarte pós-geração

### Integração / End-to-End
- [ ] Documento longo (>60k chars) com oportunidades em múltiplas janelas
- [ ] Oportunidade `structural_gap` → adição estrutural com fundamento no próprio documento
- [ ] Oportunidade `actionable_attention` → clarity_improvement aplicada automaticamente
- [ ] Oportunidade `confirmed_issue` → múltiplas categorias no mesmo achado
- [ ] Descarte por `sem_lastro` aparece no plano com motivo canônico
- [ ] Validação DOCX descarta item → plano reflete `nao_aplicado`
- [ ] Relatório final expõe `plano_melhoria` completo

### Regressão de Salvaguardas
- [ ] Âncora inexata → item descartado, cobertura falhou
- [ ] Subitem protection → incisos absorvidos não duplicados
- [ ] Anti-renumeração → Art. 6º-A não renumera Art. 7º
- [ ] Revogação vaga → descartada
- [ ] Lastro temático divergente → requer_decisao_juridica
- [ ] Duplicação cross-janela → patch descartado, fallback intacto
- [ ] Validação 5 passagens → descarte responsável, nunca arquivo corrupto

---

## Como Rodar

```bash
# Suite completa (exclui baseline conhecido)
$env:PYTHONDONTWRITEBYTECODE=1; uv run --no-sync pytest tests/ -q --tb=no --ignore=tests/test_agent_robustez.py --ignore=tests/test_passagens_analise_e_conferencia.py

# Módulo específico
$env:PYTHONDONTWRITEBYTECODE=1; uv run --no-sync pytest tests/test_minuta_melhoria.py -v
$env:PYTHONDONTWRITEBYTECODE=1; uv run --no-sync pytest tests/test_correcao_pos_analise.py -v
$env:PYTHONDONTWRITEBYTECODE=1; uv run --no-sync pytest tests/test_document_generation.py -v
$env:PYTHONDONTWRITEBYTECODE=1; uv run --no-sync pytest tests/test_analise_formatacao.py -v

# Lint
uv run --no-sync ruff check --output-format=concise src/sejus_project/tools/llm_tools/
```

---

## Baseline Falhas Conhecidas (Não Relacionadas)

| Teste | Erro | Origem |
|-------|------|--------|
| `test_analise_com_nome_errado_cai_para_upload_da_sessao` | Encoding/Unicode | `test_agent_robustez.py` |
| `test_apos_confirmacao_analisa_arquivo_no_proximo_turno` | Encoding/Unicode | `test_agent_robustez.py` |
| `test_conferencia_descarta_contradicoes_do_feedback` | Assertion | `test_passagens_analise_e_conferencia.py` |

Estas existiam **antes** da nova funcionalidade (commit fa3ebea) e não foram afetadas pelas mudanças.