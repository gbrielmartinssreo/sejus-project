---
name: extracao_tabela_anexo
description: >
  Use esta skill ao processar atos normativos com anexos tabulares densos
  (quadros de cargos DGA-1 a DGA-10, lotacionogramas, tabelas de tributos/
  NCM, quantitativos por unidade) — tanto na ingestão para o RAG
  (convert_pdf_md.py / ingestion.py / chunking.py) quanto ao responder
  perguntas pontuais sobre uma linha específica dessas tabelas.
---

# Extração de Tabela em Anexo

## Objetivo
Evitar que tabelas densas virem "texto solto" ruim para embeddings/RAG, e
garantir que perguntas pontuais sobre uma linha específica sejam
respondidas com precisão, sem confundir colunas ou hierarquia.

## Por que isso é um problema aqui
PDFs como o Decreto nº 1.933/2026 (Anexos I, II e III) têm tabelas
hierárquicas de várias colunas (Unidade → Cargo → Símbolo Remuneratório →
Quantidade Cargo/Função) que, quando extraídas como texto corrido de PDF,
perdem o alinhamento coluna-linha. Isso é visível no próprio texto bruto,
onde células adjacentes colam sem separador claro (ex. "DGA-6 - 1" pode
significar símbolo, cargo ou função dependendo da linha).

## Na ingestão (`convert_pdf_md.py` / `chunking.py`)

1. **Não deixe a tabela virar parágrafo de texto corrido.** Converta para
   tabela markdown (`| Unidade | Cargo | Símbolo | Cargo | Função |`) antes
   de gerar embeddings.
2. **Preserve a hierarquia de seções** como contexto de cada linha — ex.
   linhas do Anexo I devem carregar consigo o cabeçalho de seção mais
   próximo ("1.5 Gabinete do Secretário Adjunto de Inteligência") mesmo
   quando isoladas em um chunk, senão a linha "Assistente Técnico II |
   DGA-9 | - | 13" perde todo o significado fora de contexto.
3. **Considere chunking por tabela inteira** (ou por bloco de seção, ex.
   "NÍVEL DE DIREÇÃO SUPERIOR" completo) em vez de chunking por tamanho
   fixo de caracteres — cortar uma tabela no meio de uma unidade
   organizacional é o erro mais comum e mais custoso aqui.
4. **Gere uma versão "resumo textual" da tabela** como chunk adicional
   (ex. "O cargo de Secretário-Adjunto de Administração Penitenciária tem
   símbolo DGA-2, 1 cargo criado e nenhuma função") para melhorar recall
   em buscas semânticas por linguagem natural, mantendo a tabela markdown
   como fonte de verdade para valores exatos.

## Ao responder perguntas sobre uma linha específica

1. **Prefira busca por palavra-chave exata** (nome do cargo, símbolo DGA,
   nome da unidade) em vez de busca puramente vetorial — tabelas têm baixa
   densidade semântica e alta densidade factual, então keyword match tende
   a performar melhor que similarity search aqui.
2. **Sempre devolva a linha inteira com seus rótulos de coluna**, nunca só
   o número. Errado: "é 13". Certo: "Assistente Técnico II (DGA-9) no
   Gabinete do Secretário Adjunto de Inteligência: 0 cargos, 13 funções".
3. **Verifique o total/subtotal quando disponível** (ex. Anexo II do
   Decreto 1.933 tem linha `TOTAL | 277`) para validar consistência antes
   de apresentar números somados por você mesmo — se a soma que você
   calculou não bate com o total declarado no documento, informe a
   divergência em vez de corrigir silenciosamente.
4. **Não interpole/estime valores ausentes.** Onde a tabela tem "-"
   (célula vazia, ex. "Cargos Vagos: -"), isso normalmente significa zero
   ou não aplicável — não confundir com dado ausente/erro de extração sem
   checar o PDF original.

## Casos especiais observados nos documentos SEJUS
- **Tabelas com nomenclatura semelhante mas símbolos diferentes**: ex.
  "Diretor de Penitenciária I" (DGA-3) vs. "Diretor de Penitenciária II"
  (DGA-4) — atenção ao algarismo romano, é comum confundir ao responder.
- **Colunas "Cargo" vs "Função"**: no Decreto 1.933, cargos em comissão e
  funções de confiança são contados em colunas separadas na mesma linha —
  nunca some as duas sem deixar explícito que está somando categorias
  diferentes.