---
name: comparacao_retificacao
description: >
  Use esta skill quando o documento em análise for uma retificação de ato
  já publicado (título contém "Retificação", ou o corpo menciona
  "Republica-se por ter saído publicado incorretamente..."), ou quando o
  usuário perguntar "o que mudou" entre duas versões de um mesmo ato.
  Depende de estrutura_ato_normativo e da tool consultar_atos_sejus.
---

# Comparação de Retificação

## Objetivo
Quando um ato é republicado/retificado, identificar automaticamente a
versão anterior na base indexada e gerar um diff textual claro do que
mudou, evitando que o usuário (ou o próprio agente em respostas futuras)
misture dados da versão errada com a corrigida.

## Como identificar uma retificação
Sinais no documento:
- Título ou cabeçalho contém "Retificação" (ex.:
  `PORTARIA N° 20/2026/GAB-SEJUS/MT - Retificação`).
- Nota de rodapé do tipo "Republica-se por ter saído publicado
  incorretamente no Diário de [data anterior], página [X]".
- Mesmo número/ano de ato (ex. "Portaria 20/2026") aparecendo em duas
  publicações do Diário Oficial com datas diferentes.

## Fluxo recomendado

1. **Extraia os metadados da retificação** usando `estrutura_ato_normativo`
   (tipo, número, data desta publicação).

2. **Localize a versão anterior**:
   - Busque em `consultar_atos_sejus` pelo mesmo tipo + número do ato
     (ex. "Portaria 20/2026 GAB-SEJUS"), filtrando por data de publicação
     anterior à da retificação.
   - Se a nota de rodapé citar a data/página da publicação original, use
     isso como critério de busca prioritário (mais preciso que busca
     semântica genérica).

3. **Se a versão anterior não estiver indexada**: informe isso
   explicitamente ("encontrei a retificação, mas não a versão original
   publicada em [data] — não é possível gerar o diff") em vez de inventar
   o conteúdo anterior.

4. **Gere o diff campo a campo**, não como bloco de texto corrido:
   - Compare os mesmos metadados extraídos por `estrutura_ato_normativo`
     (ementa, artigos, tabelas/anexos, assinaturas).
   - Para conteúdo tabular (lotacionogramas, quantitativos de cargos),
     compare linha a linha por chave identificadora (nome do cargo/carreira),
     não por posição na tabela — colunas podem ter sido reordenadas.

5. **Apresente o resultado como uma lista de mudanças**, por exemplo:

   > **O que mudou na retificação da Portaria 20/2026/GAB-SEJUS/MT:**
   > - Coluna "Cargos Ocupados" de *Analista Administrativo*: 2 → 1
   > - Adicionado: linha "Auxiliar do Sistema Penitenciário" (antes ausente)
   > - Nenhuma mudança nas assinaturas ou na data de vigência

   Se não houver mudança detectável em algum campo, não liste — só reporte
   diferenças reais.

## Atenção
- Uma retificação **substitui** a publicação anterior para fins de
  vigência — ao responder perguntas gerais sobre o ato (fora do contexto
  de "o que mudou"), use sempre a versão retificada como fonte de verdade,
  nunca a original com erro.
- Nunca presuma o motivo da retificação se ele não estiver explícito no
  documento — reporte apenas o que de fato mudou no texto.