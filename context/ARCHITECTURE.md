# Arquitetura — Fluxo de Melhoria Proativa

## Cadeia Principal
```
agent.py::_tratar_revisao_direta
  → _revisar_gerar
    → document_generation.analise_para_correcao (retorna apontamentos + analise_completa)
      → melhorar_documento_usuario
        → _melhorar_e_relatar
          → document_improvement.gerar_estrutura_melhoria
            → _janelas_conteudo (60k chars, overlap 2k)
            → _gerar_patch_janela (por janela)
            → _filtrar_patch_valido / _problemas_do_patch / _validar_cobertura
            → _construir_estrutura + _plano_melhoria + _plano_declarado
          → montar_docx_revisado
          → docx_validacao.validar_docx_gerado (5 passagens)
          → resposta + _ultima_comparacao
```

## Módulos Principais

### agent.py
- Entry point: `_tratar_revisao_direta` → `_revisar_gerar`
- Gerencia sessão, registry de análise, loop de correção

### document_generation.py
- `analise_para_correcao`: extrai apontamentos da análise registrada
- `melhorar_documento_usuario`: orquestra melhoria
- `_melhorar_e_relatar`: **coração da nova funcionalidade**
  - Injeta `oportunidades` (via `analise_formatacao.extrair_oportunidades`)
  - Chama `gerar_estrutura_melhoria`
  - **Reconcilia plano FINAL** após todas as redes de segurança
  - Aplica `aplicar_politica_categorias` antes de montar DOCX
  - Expõe `plano_melhoria` na resposta

### document_improvement.py
- `gerar_estrutura_melhoria`: processa em janelas, acumula patch + plano declarado
- `_gerar_patch_janela`: uma chamada LLM por janela, retorna (dados, alts, rems, adds, lacs, declarada, plano_janela)
- `_filtrar_patch_valido`, `_problemas_do_patch`, `_validar_cobertura`: redes de segurança do patch
- `aplicar_politica_categorias`: política de 5 categorias + risco + obrigatoriedade jurídica
- `reconciliar_plano`: plano reconciliado contra patch efetivo
- `_sistema_melhoria` / `_usuario_melhoria`: prompts do planner

### analise_formatacao.py
- `extrair_oportunidades(analise)`: deriva oportunidades das 3 classes canônicas
- `reestruturar_analise`: normaliza análise bruta → 4 seções canônicas

---

## Dados que Fluem

### analise_completa (string)
Texto da análise reestruturada com 4 seções:
- PROBLEMAS_CONFIRMADOS
- PONTOS_DE_ATENCAO
- PONTOS_FORTES
- CHECKLIST

### oportunidades (list[dict])
```python
{"id": "op-1", "tipo": "confirmed_issue|actionable_attention|structural_gap", "secao": "...", "texto": "..."}
```

### plano_declarado (list[dict]) — do modelo
```python
{
  "achado_id": "op-1",
  "achado": "texto do achado",
  "decisao": "aplicar|propor|descartar",
  "referencia": "Art. 5º",           # rotulo da mudança no patch
  "categoria": "structural_improvement",
  "cria_obrigacao": True,
  "risco_juridico": "alto",
  "dispositivo_alvo": "Art. 5º",
  "fundamento": "base legal",
  "relacao_com_o_achado": "resolve lacuna de comunicação"
}
```

### plano_melhoria (list[dict]) — reconciliado FINAL
```python
{
  "achado_id": "op-1",
  "achado": "...",
  "categoria": "structural_improvement",
  "risco_juridico": "alto",
  "decisao": "aplicar",
  "dispositivo_alvo": "Art. 5º",
  "referencia": "Art. 5º",
  "fundamento": "...",
  "relacao_com_o_achado": "...",
  "status": "aplicado|pendente|nao_aplicado|falhou",
  "status_detalhe": "aplicada (structural_improvement)" | "proposta inserida e marcada para validação jurídica" | "descartado: sem_lastro" | "não avaliada",
  "motivo_canonico": "sem_lastro"  # quando nao_aplicado
}
```

### resposta (dict) — retornada ao agente
```python
{
  "status": "improved",
  "alteracoes": [...],
  "remocoes": [...],
  "adicoes_estruturais": [...],
  "lacunas": [...],
  "plano_melhoria": [...],      # NOVO
  "apontamentos_analise": [...],
  "textos": {"antes": "...", "depois": "..."},
  ...
}
```

---

## Redes de Segurança (ordem de execução)
1. `_filtrar_patch_valido` / `_problemas_do_patch` — dentro de `_gerar_patch_janela` (por janela)
2. `_validar_cobertura` — consolida apontamentos da análise
3. `_duplicacoes_do_patch` — descarta patch inteiro se houver duplicação
4. `_validar_lastros` — anota lastro por item
5. `_checar_coerencia_lastros` — marca `requer_decisao_juridica` por tema divergente
6. **`aplicar_politica_categorias`** — aplica taxonomia + risco + obrigatoriedade
7. `docx_validacao.validar_docx_gerado` — 5 passagens, descarte responsável, fallback
8. **Reconciliação FINAL do plano** — reflete patch que SOBREVIVEU a tudo acima