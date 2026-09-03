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
Ao encontrar uma inconsistência, reporte no formato:

> ⚠️ **[Categoria]** — Art. X / Anexo Y: [descrição objetiva do problema].
> Sugestão: [correção proposta, se houver].

Não reescreva o ato inteiro automaticamente — aponte os pontos e peça
confirmação antes de gerar uma versão corrigida, pois alterações em atos
normativos têm efeito jurídico.