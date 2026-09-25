# Oportunidades — `extrair_oportunidades`

## Função
```python
extrair_oportunidades(analise: str | None) -> list[dict]
```

Extrai do texto da **análise reestruturada** (4 seções canônicas) as oportunidades de melhoria para o planner proativo.

## Entrada: `analise` (string)
Formato esperado (saída de `reestruturar_analise`):
```
## PROBLEMAS_CONFIRMADOS
1. Texto: Art. 5º não define prazo para resposta ao interessado
   Classificacao: confirmado
   Tipo: problema

## PONTOS_DE_ATENCAO
1. Texto: Confirmacao se prazo de 60 dias e suficiente
   Classificacao: acao
   Tipo: atencao
2. Texto: Verificar se a responsabilidade esta clara
   Classificacao: acao
   Tipo: atencao

## LACUNAS_ESTRUTURAIS
1. Texto: Ausencia de procedimento para recurso administrativo
   Classificacao: lacuna
   Tipo: estrutura
```

## Saída: `list[dict]`
```python
{
  "id": "op-1",                    # estável, sequencial
  "tipo": "confirmed_issue",       # ou "actionable_attention", "structural_gap"
  "secao": "PROBLEMAS_CONFIRMADOS", # seção de origem
  "texto": "Art. 5º não define prazo para resposta ao interessado"  # truncado 400 chars
}
```

## Lógica de Classificação

### 1. `confirmed_issue` (TIPO_ISSUE_CONFIRMADO)
- **Origem**: Itens de `PROBLEMAS_CONFIRMADOS`
- **Sentido**: O próprio item É um problema confirmado. Entra também na lista de correções, mas aparece aqui para o planner avaliar camadas mais ousadas (clareza/estrutural/normativa) sobre ele.

### 2. `actionable_attention` (TIPO_ATENCAO_ACIONAVEL)
- **Origem**: `PONTOS_DE_ATENCAO` com **verbo acionável** (`_RE_OPORTUNIDADE_ACIONAVEL`)
- **Verbos detectados**: confirmar, verificar, validar, checar, revisar, ajustar, corrigir, padronizar, explicitar, definir, estabelecer, prever, incluir, adicionar, inserir, criar, instituir, disciplinar, regular, normatizar
- **Sentido**: Ponto de atenção que admite ação direta no texto (correção, clareza, adição).

### 3. `structural_gap` (TIPO_LACUNA_ESTRUTURAL)
Duas rotas:
- **Rota A**: `PONTOS_DE_ATENCAO` com **verbo acionável** + **fato gerador** (`_RE_FATO_GERADOR`)
  - Fatos geradores: comunicar, notificar, intimar, cientificar, publicar, divulgar, registrar, protocolar, cadastrar, decidir, fundamentar, motivar, justificar, prazo, recurso, revisão, reconsideração, apelação, representação, denúncia, fiscalização, sanção, penalidade, multa, advertência, suspensão, cassação, proibição, obrigação, dever, responsabilidade, competência, atribuição, procedimento, rito, tramitação, instrução, conclusão, julgamento, decisão, sentença, acórdão, voto, maioria, unanimidade, quórum, maioria absoluta, maioria simples, dois terços, três quintos
  - **Exemplo**: "Confirmar se decisão fundamentada comunica o interessado" → verbo (confirmar) + fato (comunicar) = lacuna estrutural (cabe artigo/parágrafo novo disciplinando a comunicação)
- **Rota B**: `PONTOS_DE_ATENCAO` **sem verbo acionável** mas **com fato gerador**
  - **Exemplo**: "Não disciplina a comunicação ao interessado" → fato gerador (comunicar) sem verbo = lacuna estrutural
- **Rota C**: Itens de `LACUNAS_ESTRUTURAIS` (seção dedicada) — sempre `structural_gap`

## Deduplicação
- Chave: texto normalizado (`" ".join(texto.lower().split())`)
- Mesmo texto em múltiplas seções → aparece uma vez (primeira ocorrência vence)

## IDs Estáveis
- `op-1`, `op-2`, `op-3`... atribuídos na ordem de adição à lista final
- Permitem match robusto no `reconciliar_plano` (match por ID mesmo se texto divergir)

## Exemplos

### Entrada
```
## PROBLEMAS_CONFIRMADOS
1. Texto: Art. 3º tem erro de concordância: "os servidores deve"
   Classificacao: confirmado
   Tipo: problema

## PONTOS_DE_ATENCAO
1. Texto: Confirmar se prazo de 30 dias no Art. 7º e suficiente
   Classificacao: acao
   Tipo: atencao
2. Texto: Verificar se Art. 10 prevê recurso administrativo
   Classificacao: acao
   Tipo: atencao
3. Texto: Nao disciplina a comunicacao da decisao ao interessado
   Classificacao: atencao
   Tipo: atencao

## LACUNAS_ESTRUTURAIS
1. Texto: Ausencia de procedimento para revisao de oficio
   Classificacao: lacuna
   Tipo: estrutura
```

### Saída
```python
[
  {"id": "op-1", "tipo": "confirmed_issue", "secao": "PROBLEMAS_CONFIRMADOS",
   "texto": "Art. 3º tem erro de concordância: \"os servidores deve\""},
  {"id": "op-2", "tipo": "actionable_attention", "secao": "PONTOS_DE_ATENCAO",
   "texto": "Confirmar se prazo de 30 dias no Art. 7º e suficiente"},
  {"id": "op-3", "tipo": "structural_gap", "secao": "PONTOS_DE_ATENCAO",
   "texto": "Verificar se Art. 10 prevê recurso administrativo"},
  {"id": "op-4", "tipo": "structural_gap", "secao": "PONTOS_DE_ATENCAO",
   "texto": "Nao disciplina a comunicacao da decisao ao interessado"},
  {"id": "op-5", "tipo": "structural_gap", "secao": "LACUNAS_ESTRUTURAIS",
   "texto": "Ausencia de procedimento para revisao de oficio"}
]
```

## Integração no Planner
Injetado em `_usuario_melhoria()` como bloco:
```
OPORTUNIDADES DE MELHORIA
- [op-1] (problema confirmado) Art. 3º tem erro...
- [op-2] (ponto de atenção acionavel) Confirmar se prazo...
- [op-3] (lacuna estrutural) Verificar se Art. 10...
- [op-4] (lacuna estrutural) Nao disciplina a comunicacao...
- [op-5] (lacuna estrutural) Ausencia de procedimento...
```

Planner **deve avaliar cada uma** e registrar decisão em `plano_melhoria`.

## Limitações Conhecidas (para calibração futura)
- Heurística de `structural_gap` por "verbo + fato gerador" pode classificar como lacuna estrutural itens que são apenas validação (ex.: "Confirmar se prazo de 60 dias é suficiente" → tem "confirmar" + "prazo" → structural_gap). 
- **Mitigação**: planner recebe a categoria como dica, mas decisão final é dele; plano mostra se avaliou ou não.
- Testes dedicados pendentes para calibrar regex `_RE_FATO_GERADOR` e `_RE_OPORTUNIDADE_ACIONAVEL`.