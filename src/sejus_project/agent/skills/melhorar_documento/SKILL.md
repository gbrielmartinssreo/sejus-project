---
name: melhorar_documento
description: >
  Use esta skill quando o usuário enviar um documento e pedir para melhorar,
  adequar, atualizar, revisar a redação ou comparar a versão antes/depois do
  mesmo ato. Dispara a tool melhorar_documento_usuario e explica as mudanças.
---

# Melhorar e Comparar Documento (Antes/Depois)

## Objetivo
Reescrever um documento normativo enviado pelo usuário com melhorias e
adequações jurídicas, produzindo uma nova versão em DOCX/PDF **sem mudar o
ato** (mesmo número, ementa, objeto e assinaturas), e apresentar a comparação
antes/depois.

## Como acionar
- Um botão da interface envia a mensagem "Melhore e compare o arquivo '<nome>'".
- Em linguagem natural o usuário pode pedir: "melhore o arquivo que enviei",
  "adapte esse documento", "atualiza a portaria", "revisa e compara".
- **Correção depois de uma análise**: se o documento já foi analisado nesta
  conversa e o usuário pede para corrigir/ajustar/entregar o arquivo corrigido
  (ex.: "consegue fazer a correção?", "me dê o arquivo corrigido"), esta é a
  ferramenta correta — **não** é geração de ato novo. A tool injeta
  automaticamente os apontamentos acionáveis da análise registrada para o
  mesmo documento/versão.
- **Confirmação curta depois da análise**: se o usuário responde só "sim",
  "pode", "pode gerar" (ou equivalente) depois que o agente ofereceu aplicar
  as correções, isso autoriza a melhoria do documento analisado — chame
  `melhorar_documento_usuario`. Prepare o oferecimento em termos de melhoria
  ("aplicar as correções e gerar o arquivo corrigido"), **nunca** como
  "gerar arquivo" de um ato novo.

## Apontamentos da análise (correção pós-análise)
- A análise COMPLETA é preservada como contexto, mas só os **apontamentos
  acionáveis** (com ID estável) viram instrução de alteração. Elogios,
  constatações de conformidade, promessas de análise e perguntas não geram
  mudança. Os apontamentos são **consolidados ao longo dos turnos**: um
  aprofundamento (ex.: "e o que está ruim?") que não relê o arquivo continua
  vinculado ao mesmo documento e entra na lista.
- Cada apontamento é uma **tarefa a executar** no original. A melhoria deve
  devolver, em `apontamentos_analise`, uma entrada por ID com `status`
  (`aplicado` ou `nao_aplicado`).
- Declarar `aplicado` só vale se a mudança existir de fato no patch e estiver
  ancorada no original. O sistema confere e reclassifica:
  - `pendente` — mudança inserida, mas com lastro não validado (decisão
    jurídica);
  - `falhou` — não encaminhado, referência inexistente, trecho não localizado
    ou justificativa vaga;
  - `nao_aplicado` — impedimento **concreto** informado no `motivo`.
- "Não foi alterado"/"não se aplica" **não é justificativa**: informe um
  impedimento concreto (depende de decisão jurídica, conflita com a norma X,
  cria despesa sem previsão). Aplicar outras melhorias não substitui cumprir os
  apontamentos anteriores.
- Se a análise não apontar nenhuma alteração acionável, **não invente
  correções** — devolva as listas de mudanças vazias.

## Fluxo
1. Identifique o arquivo enviado (name exato em `importacoes_usuario/`).
2. Chame a tool `melhorar_documento_usuario(filename[, diretrizes])`.
   - `.docx` → gera uma CÓPIA do arquivo original com as mudanças já marcadas
     no próprio documento: texto adicionado/recomposto sai em verde e texto
     removido/recomposto fica visível com tachado. Parágrafos iguais ficam
     intactos.
   - `.pdf`/`.txt`/`.md` → usa o template oficial do tipo de ato (não há
     "original" DOCX para copiar, então as marcas não se aplicam).
3. A tool devolve a nova versão e as listas `alteracoes` (tipo, item, motivo),
   `remocoes` (parágrafos retirados) e `adicoes_estruturais` (artigos novos).

## Como funciona (modo patch)
- O modelo NÃO reescreve o documento: devolve apenas as mudanças ancoradas ao
  texto original. O sistema copia o original e aplica o patch — parágrafos não
  citados permanecem intactos e o resultado nunca é truncado por limite de
  tokens (orçamento é limitado ao teto do modelo).
- Cada `alteracao`/`remocao` precisa de `trecho_original` copiado EXATAMENTE
  do documento recebido, para a mudança ser localizada. Itens sem âncora são
  re-tentados uma vez e, se persistirem, geram um aviso no chat.

## Regras para a melhoria
- NÃO crie um ato novo: preserve número, ementa, objeto e assinaturas.
- Adeque fundamentação legal e numeração usando o acervo (RAG).
- Todas as mudanças precisam ser registradas em `alteracoes`/`remocoes` para a
  comparação antes/depois.
- Para `.docx`, o arquivo de saída é a cópia revisada: explique ao usuário
  que o texto novo aparece em verde e o texto removido/recomposto aparece
  tachado no próprio arquivo.
- Se a melhoria falhar (arquivo não encontrado, formato inválido), explique
  o erro e pergunte se o usuário quer tentar outro arquivo.

## Apresentação da resposta
- Liste objetivamente o que foi alterado e por quê.
- Indique os downloads (original e nova versão DOCX/PDF) e a comparação
  antes/depois que aparecem na interface.
- Se nada precisou mudar, mantenha o texto e diga isso claramente.