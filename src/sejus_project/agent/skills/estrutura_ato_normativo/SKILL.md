---
name: estrutura_ato_normativo
description: >
  Use esta skill sempre que precisar interpretar, classificar ou extrair
  metadados de um ato normativo do Diário Oficial de MT (Decreto, Portaria,
  Instrução Normativa, Resolução, Ata) antes de responder ao usuário ou
  indexar o documento. É a skill-base: as demais (revisao_documento,
  consulta_normativa, comparacao_retificacao) partem do que ela extrai.
---

# Estrutura de Ato Normativo

## Objetivo
Extrair de forma padronizada os metadados e a estrutura de qualquer ato
publicado no Diário Oficial (SEJUS/MT), para permitir buscas, comparações
e resumos confiáveis.

## Quando usar
- Antes de resumir, revisar ou comparar qualquer Decreto/Portaria/IN.
- Ao ingerir um novo documento no RAG (`ingestion.py`), como etapa de
  pré-processamento para gerar metadados de chunk.
- Quando o usuário perguntar "o que é esse documento" ou "do que trata".

## Estrutura típica de um ato normativo (MT/SEJUS)

Todo ato segue aproximadamente esta ordem — procure por esses marcadores:

1. **Cabeçalho do Diário Oficial**: data, número da edição, página.
2. **Tipo e número do ato**: ex. `DECRETO Nº 1.933, DE 10 DE MARÇO DE 2026`,
   `PORTARIA Nº 44/2025/GAB-SEJUS/MT`, `INSTRUÇÃO NORMATIVA Nº 05/2026/GAB-SEJUS/MT`.
3. **Ementa**: frase logo abaixo do título, geralmente em itálico/negrito,
   resumindo o objeto ("Dispõe sobre...", "Designa...", "Cria...").
4. **Autoridade emissora + fundamento de competência**:
   "O GOVERNADOR..." / "O SECRETÁRIO DE ESTADO DE JUSTIÇA, no uso das
   atribuições que lhe confere o art. X...".
5. **Bloco de CONSIDERANDO** (opcional, comum em Portarias e INs): cada
   "CONSIDERANDO" é uma justificativa/base legal — extrair todas.
6. **Verbo de deliberação**: `DECRETA:`, `RESOLVE:`, `RESOLVEM:`.
7. **Corpo**: Artigos (Art. 1º, 2º...), incisos (I, II...), parágrafos
   (§1º, parágrafo único), alíneas (a, b, c).
8. **Disposições finais**: vigência ("entra em vigor..."), revogações
   ("Revogam-se as disposições em contrário" / "Revoga-se o Decreto nº...").
9. **Local, data, assinaturas** (cargo + nome).
10. **Protocolo** (código do IOMAT, ex: `Protocolo 1792395`) — identificador
    único do documento no Diário Oficial, útil como chave de deduplicação.

## Metadados a extrair sempre que possível

| Campo | Exemplo |
|---|---|
| tipo_ato | Decreto / Portaria / Instrução Normativa / Ata / Extrato de Contrato |
| numero | 1.933/2026, 44/2025 |
| orgao_emissor | GAB-SEJUS, COR/SEJUS, SESP |
| data_assinatura | 10/03/2026 |
| data_publicacao | 10/03/2026 (data do Diário) |
| data_vigencia | pode divergir da publicação — ex. Art.19 do Decreto 1.933 diz "entra em vigor em 03 de março de 2026", retroativo à publicação de 10/03 |
| ementa | texto da ementa |
| atos_revogados | ex. "Revoga-se o Decreto nº 1.213, de 03/01/2025" |
| atos_referenciados | leis/decretos citados nos considerandos e fundamentos |
| protocolo_iomat | identificador único |
| e_retificacao | true/false — ver skill comparacao_retificacao |

## Atenção: datas divergentes
É comum um ato ter **data de assinatura**, **data de publicação no Diário**
e **data de vigência** diferentes entre si (ex.: Decreto nº 1.933 foi
publicado em 10/03/2026 mas seu Art. 19 fixa vigência em 03/03/2026).
Nunca assuma que são a mesma data — sempre cite qual data está sendo usada.

## Saída recomendada (para uso por outras skills/RAG)
Ao final da extração, produza um bloco de metadados compacto, por exemplo:

```json
{
  "tipo_ato": "Decreto",
  "numero": "1.933/2026",
  "data_publicacao": "2026-03-10",
  "data_vigencia": "2026-03-03",
  "ementa": "Dispõe sobre a Estrutura Organizacional da SEJUS...",
  "atos_revogados": ["Decreto nº 1.213/2025"],
  "protocolo_iomat": "1792402"
}
```

Isso facilita tanto o `comparacao_retificacao` (identificar o que é "o
mesmo ato" em versões diferentes) quanto o `consulta_normativa` (citar a
fonte corretamente).