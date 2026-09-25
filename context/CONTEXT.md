# CONTEXT — sejus-project (branch Refac, commit fa3ebea + novos edits)

## Estado Atual (2026-09-25)
- Branch: `Refac` (origin synced em fa3ebea)
- Último commit: `fa3ebea` — `feat(analise): classifica resposta em 4 canonicas e alinha registry`
- Working tree: alterações não commitadas nos arquivos de melhoria proativa
- Testes: 265 passed; 3 falhas baseline conhecidas (agent_robustez 2, passagens 1) — **não relacionadas**
- Ruff: limpo nos arquivos alterados (6 avisos pré-existentes em document_generation)

---

## Objetivo da Nova Funcionalidade
Tornar **“Revisar e gerar DOCX”** proativo:
- Identificar e propor melhorias proporcionais a **todos** achados acionáveis
- Cinco categorias com política explícita
- Oportunidades derivadas da análise (confirmed_issue, actionable_attention, structural_gap)
- Motivos canônicos de descarte
- Plano rastreável por achado/oportunidade
- Manter salvaguardas: âncoras, subitem-protection, anti-renumeração, revogação específica, lastro, validação DOCX 5 passagens

---

## Arquitetura — Fluxo Principal
```
agent.py::_tratar_revisao_direta
  → _revisar_gerar
    → document_generation.analise_para_correcao
      → melhorar_documento_usuario
        → _melhorar_e_relatar
          → document_improvement.gerar_estrutura_melhoria
            → _gerar_patch_janela (por janela, 60k chars, overlap 2k)
            → _filtrar_patch_valido / _problemas_do_patch / _validar_cobertura
          → montar_docx_revisado
          → validação pós-geração (docx_validacao)
          → resposta + _ultima_comparacao (inclui plano_melhoria)
```

---

## Mudanças Implementadas

### 1. analise_formatacao.py
- **Constantes**: `TIPO_ISSUE_CONFIRMADO`, `TIPO_ATENCAO_ACIONAVEL`, `TIPO_LACUNA_ESTRUTURAL`
- **`extrair_oportunidades(analise)`** → list[dict]{id, tipo, secao, texto}
  - `confirmed_issue`: itens de PROBLEMAS_CONFIRMADOS
  - `actionable_attention`: PONTOS_DE_ATENCAO com verbo acionável
  - `structural_gap`: PONTOS_DE_ATENCAO com fato gerador OU lacunas sem verbo mas com fato
- Exportado em `__all__`

### 2. document_improvement.py
#### Taxonomia e Política
- `CATEGORIAS = [safe_correction, clarity_improvement, structural_improvement, normative_proposal, unsupported]`
- `RISCOS = [baixo, medio, alto]`
- `MOTIVOS_DESCARTE = [sem_lastro, ja_resolvido_no_texto, mudanca_desnecessaria, materialmente_sensivel, risco_de_alterar_sentido, depende_de_decisao_institucional]`
- **Schema**: `categoria`, `cria_obrigacao`, `risco_juridico` obrigatórios em alteracoes/remocoes/adicoes_estruturais; `texto` obrigatório em adicoes_estruturais; `plano_melhoria` array no topo
- **`aplicar_politica_categorias(alteracoes, remocoes, adicoes)`**
  - `unsupported` → removido (motivo `mudanca_desnecessaria`)
  - `normative_proposal` / cria_obrigacao → `requer_decisao_juridica=True`, risco `alto`
  - `clarity_improvement`/`safe_correction` com `cria_obrigacao=True` → escalado para `structural_improvement`
  - Fallback: categoria inválida → `clarity_improvement`
- **`reconciliar_plano(plano_declarado, oportunidades, alteracoes, remocoes, adicoes)`**
  - Dedup declarados por `achado_id|achado` (preferência: quem tem `referencia`)
  - Match oportunidades por `_chave` (id+texto) **OU** por `achado_id` isolado (id-only)
  - Status: `aplicado` | `pendente` (requer_decisao_juridica) | `nao_aplicado` (motivo canônico) | `falhou` (sem mudança + motivo vago)
  - Oportunidades não declaradas → `nao avaliada` (visível)
- **`_sistema_melhoria()`** reescrito: busca oportunidade em TODO documento, permite clareza/estrutural/normativa, base interna OU RAG, exige plano
- **`_usuario_melhoria()`** agora injeta bloco `OPORTUNIDADES DE MELHORIA` com IDs estáveis

#### Helpers
- `normalizar_motivo_descarte`, `_motivo_concreto`, `_descricao_motivo`, `_categoria_declarada`, `_risco_declarado`, `_cria_obrigacao`

### 3. document_generation.py
- Import `analise_formatacao`
- Em `_melhorar_e_relatar`:
  - `oportunidades = analise_formatacao.extrair_oportunidades(analise_completa)` → `valores["oportunidades"]`
  - Captura `plano_declarado` da estrutura (`_plano_declarado`)
  - **Reconciliação FINAL** após TODAS as redes de segurança:
    - `_duplicacoes_do_patch`, `_validar_lastros`, `_checar_coerencia_lastros`, validação DOCX 5 passagens
    - `plano_melhoria = reconciliar_plano(plano_declarado, oportunidades, alteracoes, remocoes, adicoes)`
  - `aplicar_politica_categorias(alteracoes, remocoes, adicoes)` **após** validações, antes de montar DOCX
  - `plano_melhoria` exposto em `_ultima_comparacao` e `resposta`

---

## Salvaguardas Mantidas (inalteradas)
- Âncoras exatas (`trecho_original` copiado fielmente)
- Subitem protection (engolhidos na alteração do pai)
- Anti-renumeração: artigos novos só com sufixo (Art. 6º-A)
- Revogação só com norma específica
- Lastro temático: `_checar_coerencia_lastros` marca `requer_decisao_juridica`
- Validação pós-geração: 5 passagens, descarte responsável, fallback cópia intacta

---

## Testes Atualizados
- `tests/test_minuta_melhoria.py::test_melhoria_definition_tem_remocoes_adicoes_e_lacunas` — required fields novos
- Todos os 265 testes passam (exceto 3 baseline)

---

## Próximos Passos (pendentes)
1. Validar heurística `extrair_oportunidades` em corpus real (atualmente: prazo + confirmação → structural_gap)
2. Forçar `normative_proposal` → risco `alto` mesmo sem `cria_obrigacao` (atualmente `medio`)
3. Propagar descartes de `aplicar_politica_categorias` para `plano_melhoria` (hoje itens unsupported somem do plano)
4. Testes dedicados: taxonomia, escalada, motivos canônicos, plano completo, documento longo, salvaguardas pós-geração