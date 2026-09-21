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

## Fluxo
1. Identifique o arquivo enviado (name exato em `importacoes_usuario/`).
2. Chame a tool `melhorar_documento_usuario(filename[, diretrizes])`.
   - `.docx` → gera uma CÓPIA do arquivo original com as mudanças já marcadas
     no próprio documento: texto adicionado/recomposto sai em verde e texto
     removido/recomposto fica visível com tachado. Parágrafos iguais ficam
     intactos.
   - `.pdf`/`.txt`/`.md` → usa o template oficial do tipo de ato (não há
     "original" DOCX para copiar, então as marcas não se aplicam).
3. A tool devolve a nova versão e a lista `alteracoes` (tipo, item, motivo).

## Regras para a melhoria
- NÃO crie um ato novo: preserve número, ementa, objeto e assinaturas.
- Adeque fundamentação legal e numeração usando o acervo (RAG).
- Todas as mudanças precisam ser registradas em `alteracoes` para a
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