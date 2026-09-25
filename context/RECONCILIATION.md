# Reconciliação de Plano — `reconciliar_plano`

## Propósito
Construir o **plano de melhoria oficial** que reflete **exatamente** o que o patch final faz, após todas as redes de segurança. É a "verdade ground truth" para auditoria e relatório ao usuário.

## Entradas
```python
plano_declarado: list[dict]  # do modelo (pode ter duplicatas por janela)
oportunidades: list[dict]    # de analise_formatacao.extrair_oportunidades
alteracoes: list[dict]       # patch final
remocoes: list[dict]
adicoes: list[dict]
```

## Saída
```python
list[dict]  # uma linha por achado/oportunidade avaliada
```

## Algoritmo

### 1. Deduplicação de Declarados (Janelas Sobrepostas)
Janelas com overlap fazem o mesmo achado ser declarado múltiplas vezes.
- Chave: `_chave_texto(achado_id + "|" + achado)`
- Preferência: declaração que **tem `referencia`** (apontou mudança concreta)
  - Janela 1: achado fora da janela → `decisao: "descartar"`, sem `referencia`
  - Janela 2: achado dentro → `decisao: "aplicar"`, com `referencia: "Art. 5º"`
  - **Mantém a da janela 2**

### 2. Match Declarado → Patch Efetivo
Para cada declaração dedupada:
- `_localizar_mudanca(referencia, alteracoes, remocoes, adicoes)` → `(tipo, item, rotulo)` ou `None`

**Se encontrou mudança:**
- `item.requer_decisao_juridica=True` → `status="pendente"`, `status_detalhe="proposta inserida e marcada para validação jurídica (categoria)"`
- Senão → `status="aplicado"`, `status_detalhe="aplicada (categoria)"`
- `decisao` normalizada: `"aplicar"` ou `"propor"`

**Se NÃO encontrou mudança:**
- Motivo canônico declarado (`normalizar_motivo_descarte`) → `status="nao_aplicado"`, `motivo_canonico=...`
- Senão, se `decisao=="descartar"` E motivo concreto (`_motivo_concreto`) → `status="nao_aplicado"`, `status_detalhe="descartado: " + motivo`
- Senão → `status="falhou"`, `status_detalhe="a decisão declarada não encontrou mudança efetiva no patch"`

### 3. Oportunidades Não Declaradas → "Não Avaliada"
Para cada oportunidade em `oportunidades`:
- Match por `_chave` (id+texto) **OU** por `achado_id` isolado (`_chave_id`)
  - Permite: modelo citou `achado_id="op-3"` mas parafraseou o texto
- Se não casou com nenhum declarado → linha:
  ```python
  {
    "achado_id": "op-3",
    "achado": "texto da oportunidade",
    "categoria": "",
    "risco_juridico": "",
    "decisao": "",
    "status": "falhou",
    "status_detalhe": "oportunidade 'actionable_attention' não avaliada pelo planner (sem decisão e sem mudança no patch)"
  }
  ```

### 4. Enriquecimento de Linhas
Todas as linhas recebem:
- `categoria`: `_categoria_declarada(item)` ou do declarado
- `risco_juridico`: `_risco_declarado(item)` ou do declarado
- Campos de rastreabilidade: `dispositivo_alvo`, `referencia`, `fundamento`, `relacao_com_o_achado`

## Matching Robusto (ID + Texto)
```python
# Chave composta (texto normalizado)
_chave(entrada) = _chave_texto(f"{achado_id}|{achado}")

# Chave apenas ID (para match por ID mesmo com texto divergente)
_chave_id(achado_id) = "id:" + _chave_texto(achado_id) if achado_id else ""

# Match oportunidade: _chave(oportunidade) in vistas OR _chave_id(oportunidade.id) in vistas
```

## Status Finais

| Status | Significado | Quando Ocorre |
|--------|-------------|---------------|
| `aplicado` | Mudança no patch, sem exigência jurídica | `safe_correction`/`clarity_improvement`/`structural_improvement` sem obrigação |
| `pendente` | Mudança no patch, **requer validação jurídica** | `normative_proposal` OU `cria_obrigacao=True` |
| `nao_aplicado` | Declarado mas sem mudança no patch; motivo canônico | Descartado com motivo válido |
| `falhou` | Declarado sem mudança E sem motivo válido | Planner prometeu mas não entregou |
| `nao avaliada` (status=falhou + status_detalhe específico) | Oportunidade não tocada pelo planner | Lacuna de cobertura visível |

## Exemplo de Saída
```json
[
  {
    "achado_id": "op-1",
    "achado": "Art. 5º não define prazo para resposta ao interessado",
    "categoria": "structural_improvement",
    "risco_juridico": "alto",
    "decisao": "aplicar",
    "dispositivo_alvo": "Art. 5º",
    "referencia": "Art. 5º",
    "fundamento": "Art. 5º já prevê decisão fundamentada; adicionar dever de comunicar completa o procedimento",
    "relacao_com_o_achado": "insere § com prazo e dever de comunicação",
    "status": "pendente",
    "status_detalhe": "proposta inserida e marcada para validação jurídica (structural_improvement)",
    "motivo_canonico": ""
  },
  {
    "achado_id": "op-2",
    "achado": "Confirmacao se prazo de 60 dias e suficiente",
    "categoria": "",
    "risco_juridico": "",
    "decisao": "",
    "status": "falhou",
    "status_detalhe": "oportunidade 'actionable_attention' não avaliada pelo planner (sem decisão e sem mudança no patch)"
  }
]
```

## Integração no Fluxo
Executado **duas vezes**:
1. **Dentro de `gerar_estrutura_melhoria`** — plano preliminar (patch do modelo, antes das redes de segurança)
2. **Fim de `_melhorar_e_relatar`** — plano FINAL (patch que sobreviveu a lastro, duplicação, validação DOCX)

> A segunda execução é a que vai para `_ultima_comparacao` e resposta ao usuário.