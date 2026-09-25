# Taxonomia — 5 Categorias + Política

## Categorias (ordem: automática → requer validação jurídica)

| Categoria | Aplicação | Risco Padrão | Requer Decisão Jurídica | Quando Usar |
|-----------|-----------|--------------|------------------------|-------------|
| `safe_correction` | **Automática** | `baixo` | Não | Correção objetiva: grafia, numeração, referência quebrada, concordância |
| `clarity_improvement` | **Automática** | `baixo` | Não | Reescrita que preserva sentido: clareza, eliminação de ambiguidade, ordenação lógica |
| `structural_improvement` | **Proposta (pendente)** | `medio` → `alto` se `cria_obrigacao` | **Sim se cria_obrigacao** | Novo parágrafo/artigo que explicita procedimento implícito, melhora rastreabilidade, adiciona controle |
| `normative_proposal` | **Proposta (pendente)** | `alto` | **Sempre** | Nova norma material: prazo novo, exigência nova, competência nova, sanção nova |
| `unsupported` | **Nunca** | — | — | Sem lastro no documento nem no acervo; inovação sem base |

## Política de Aplicação (`aplicar_politica_categorias`)

```python
# 1. unsupported → REMOVIDO do patch (motivo: mudanca_desnecessaria)
# 2. normative_proposal OU cria_obrigacao=True → requer_decisao_juridica=True, risco=alto
# 3. clarity_improvement / safe_correction COM cria_obrigacao=True → ESCALADO para structural_improvement
# 4. Categoria inválida/ausente → fallback clarity_improvement
# 5. structural_improvement sem obrigação → risco=medio, requer_decisao_juridica=False (pode ser aplicado se low-risk)
```

## Motivos Canônicos de Descarte (`MOTIVOS_DESCARTE`)

| Constante | Descrição | Quando Aplicar |
|-----------|-----------|----------------|
| `sem_lastro` | Não há base no documento nem no acervo | Inovação pura, criação de dever sem fundamento |
| `ja_resolvido_no_texto` | O texto já trata do tema adequadamente | Melhoria redundante |
| `mudanca_desnecessaria` | A mudança não agrega valor operacional/jurídico | Simplificação excessiva, preferência subjetiva |
| `materialmente_sensivel` | Altera direito/dever/sanção/prazo/competência | Qualquer mudança de mérito sem base expressa |
| `risco_de_alterar_sentido` | Reescrita pode mudar interpretação do dispositivo | Clareza que mexe em conceito jurídico sensível |
| `depende_de_decisao_institucional` | Exige ato de autoridade superior, dotação orçamentária, etc. | Nova estrutura orgânica, novo prazo legal, nova sanção |

### Normalização (`normalizar_motivo_descarte`)
Aceita variações: "sem lastro", "sem_lastro", "já resolvido no texto", "ja resolvido", "mudança desnecessária", "mudanca desnecessaria", "materialmente sensível", "risco de alterar sentido", "depende de decisão institucional".

### Validação (`_motivo_concreto`)
Rejeita: vazio, "não foi alterado", "não se aplica", "N/A", "—", motivos < 20 chars sem keyword canônica.