---
name: revisao_documento
description: >
  Use esta skill ao revisar uma minuta de ato normativo (Decreto, Portaria,
  Instrução Normativa) antes da publicação, ou ao auditar um ato já
  publicado em busca de inconsistências formais, jurídicas ou de
  numeração. Depende da skill estrutura_ato_normativo para extrair a
  estrutura primeiro.
---

# Revisão de Documento (Checklist de Conformidade)

## Objetivo
Verificar se um ato normativo (minuta ou publicado) está formalmente
consistente, antes de ser usado como fonte de verdade ou publicado.

## Pré-requisito
Sempre rode mentalmente a skill `estrutura_ato_normativo` primeiro para
ter tipo, número, ementa, considerandos, artigos e revogações extraídos.

## Estrutura padrão da resposta de análise

A análise de documento deve sair, **por padrão**, centrada nos problemas e
lacunas — mas **completa e densa**, não enxuta nem um panorama genérico.
Ordem fixa:

1. **Pontos fortes (bloco curto no início).** 3–5 bullets de **uma linha
   cada** sobre o que está realmente sólido (ex.: estrutura em capítulos,
   fundamentação legal na LEP, cobertura temática dos capítulos). Só isso é
   breve. Serve de contexto — não é o corpo da análise.

2. **Pontos fracos / apontamentos de correção (corpo principal).** Braço
   principal da resposta, **desenvolvido por item**. Cada bullet tem:
   - **Um problema por bullet**, anunciado já na abertura com verbo direto
     (ex.: *"Falta cláusula de vigência..."*, *"Não há previsão de recurso
     administrativo..."*, *"Terminologia inconsistente: ora 'DGA-5', ora
     'DGA 5' no Anexo II."*, *"Ausente previsão de monitoramento/prestação
     de contas..."*). NÃO comente com tom vago ou suave (ex.: *"poderia ser
     incluído...", "seria interessante..."*).
   - **Por que é ruim**: consequência/risco concreto do problema (ex.: gera
     subjetividade, cria retificação futura, fragiliza o controle, risco de
     questionamento jurídico).
   - **Referência** sempre que possível (Art. X / Anexo Y / considerando).
   - **Sugestão de correção** objetiva ao final.
   Cada item pode ter **várias frases** — a exigência é objetividade e um
   único problema por bullet, não brevidade. Como cada bullet alimenta um
   apontamento acionável da correção (`analysis_registry`), mantenha-o
   autossuficiente (não depender de contexto que só aparece em outro bullet).

   Exemplo:
   > ⚠️ **[Vigência]** — Ausência de cláusula de vigência no final do ato.
   > Sem ela fica indefinido quando a norma passa a produzir efeitos, risco
   > comum de retificação posterior. Sugestão: acrescentar "Esta Portaria
   > entra em vigor na data de sua publicação".

3. **Cobertura obrigatória do checklist.** A análise completa deve percorrer
   **todas** as seções do checklist abaixo (numeração/formatação, ementa↔corpo,
   fundamentação legal, revogações/vigência, assinaturas/competência,
   consistência de nomes/matrículas), além de lacunas de conteúdo que afetem
   a segurança jurídica do ato (prazo de validade, recurso administrativo,
   monitoramento/prestação de contas, terminologia). Se uma seção não tem
   problema, diga em uma linha (ex.: "Numeração sequencial: ok") — não a
   omita. Se a minuta não tiver problemas, afirme isso em uma linha e encerre.

Este formato vale quando o usuário pede análise/revisão de um documento
enviado. Não se aplica a pergunta geral sobre normas nem a pedido de
geração de minuta nova — nesses casos responda normalmente.

## Checklist de revisão

### 1. Numeração e formatação
- [ ] Artigos numerados sequencialmente sem pular (Art. 1º, 2º, 3º...).
- [ ] Incisos em romano maiúsculo (I, II, III), parágrafos com § ou
      "Parágrafo único", alíneas em letra minúscula (a, b, c).
- [ ] Símbolos e siglas usados de forma consistente (ex.: sempre "DGA-5",
      nunca alternar "DGA 5" e "DGA-5" no mesmo documento sem motivo —
      isso ocorre no Anexo II do Decreto 1.933; verificar se é erro de
      formatação do PDF ou do próprio ato).

### 2. Coerência ementa ↔ corpo
- [ ] A ementa descreve fielmente o que o corpo do ato faz.
- [ ] Se o ato "dispõe sobre estrutura organizacional e redistribuição de
      cargos", confirme que ambos os temas aparecem no corpo.

### 3. Fundamentação legal
- [ ] Toda competência invocada ("no uso das atribuições que lhe confere
      o art. X") corresponde a um artigo real da Constituição Estadual,
      Lei Complementar ou Decreto citado — sinalize se não puder verificar.
- [ ] Cada "CONSIDERANDO" tem relação lógica com o RESOLVE/DECRETA que
      segue.
- [ ] Leis e decretos citados existem e a numeração está plausível
      (ex.: "Lei Complementar nº 612, de 28 de janeiro de 2019").

### 4. Revogações e vigência
- [ ] Se o ato revoga outro, o número e data do ato revogado estão corretos
      e não há revogação "fantasma" (revogar algo já revogado antes).
- [ ] Cláusula de vigência está presente ("Esta Portaria/Decreto entra em
      vigor..."). Falta disso é uma não-conformidade comum.
- [ ] Se há vigência retroativa ou diferida (efeitos a partir de data
      distinta da publicação), isso está explícito e sem ambiguidade.

### 5. Assinaturas e competência de quem assina
- [ ] Cargo de quem assina é compatível com a competência exercida no ato
      (ex.: Secretário Adjunto Corregedor-Geral assinando algo que é
      atribuição exclusiva do Secretário titular é uma bandeira vermelha).
- [ ] Todos os órgãos/pessoas mencionados como coautores no preâmbulo
      ("O SECRETÁRIO... e o SECRETÁRIO ADJUNTO...") também assinam ao final.

### 6. Consistência interna de nomes/matrículas (Portarias de nomeação)
- [ ] Nomes e matrículas aparecem de forma idêntica em todo o documento
      (evita erro típico de digitação que gera necessidade de retificação
      futura, como visto na Portaria 20/2026).
- [ ] Não há duplicidade de titular/suplente para o mesmo cargo.

## Como reportar problemas

A resposta **começa pelo bloco curto de pontos fortes e segue com os
apontamentos de correção** (ver "Estrutura padrão da resposta de análise"),
nunca por panorama geral raso. Cada inconsistência encontrada vira **um
bullet, um problema por item, desenvolvido** no formato:

> ⚠️ **[Categoria]** — Art. X / Anexo Y: [descrição objetiva da lacuna].
> [Por que é ruim: consequência/risco.] Sugestão: [correção proposta].

Não reescreva o ato inteiro automaticamente — aponte os pontos e peça
confirmação antes de gerar uma versão corrigida, pois alterações em atos
normativos têm efeito jurídico. Esses bullets são exatamente o que alimenta
a lista de apontamentos acionáveis usada na correção (`analysis_registry`):
portanto, frase direta desde a abertura, um problema por item e item
autossuficiente, para que nenhum apontamento fique vago nem seja descartado
como mera constatação de conformidade.

Quando o usuário confirmar e pedir o **arquivo corrigido** (ou responder já
com uma confirmação curta, como "sim", "pode", "pode gerar"), encaminhe para a
ferramenta `melhorar_documento_usuario` (fluxo "melhorar e comparar"): ela
aplica os apontamentos desta análise ao documento original como um patch
(mesmo ato — número, ementa, objeto e assinaturas preservados), marca as
mudanças no próprio arquivo e apresenta a comparação antes/depois. Nunca
trate essa entrega como "gerar o arquivo" de um ato novo via
`gerar_documento_normativo`. A análise
produzida permanece como contexto; não a substitua por uma revisão
independente nem invente correções que ela não apontou.