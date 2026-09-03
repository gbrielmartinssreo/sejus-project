---
name: consulta_normativa
description: >
  Use esta skill sempre que o usuário fizer uma pergunta cujo fundamento
  deva vir da base RAG de atos da SEJUS (Qdrant/sejus_atos), em vez de
  memória do modelo — ex. "o que diz o Art. X do Decreto Y", "quais
  portarias tratam de tornozeleira eletrônica", "essa nomeação ainda está
  vigente". Trabalha em conjunto com a tool consultar_atos_sejus.
---

# Consulta Normativa (RAG)

## Objetivo
Responder perguntas sobre atos da SEJUS com precisão factual, citando a
fonte exata (tipo, número, artigo, protocolo), evitando alucinar números
de lei ou conteúdo de artigos.

## Regra de ouro
**Nunca responda de memória** o conteúdo específico de um artigo, valor
monetário, nome de servidor ou data de vigência. Sempre acione
`consultar_atos_sejus` (retrieval.py) antes de afirmar algo factual sobre
um ato. Se a busca não retornar o trecho necessário, diga isso
explicitamente em vez de completar com suposição.

## Fluxo recomendado

1. **Reformule a pergunta em termos de busca semântica** — extraia
   entidades-chave: tipo de ato, número, tema, órgão, servidor, data.
   Ex.: "reajuste da tornozeleira eletrônica" → buscar por
   "tornozeleira eletrônica valor ressarcimento monitoramento".

2. **Chame `consultar_atos_sejus`** com a query reformulada.

3. **Valide a granularidade do resultado**:
   - Se a pergunta pede um artigo específico (ex. "Art. 5º"), confira se o
     chunk retornado contém esse artigo inteiro ou apenas um trecho — se
     picotado, busque novamente ou combine chunks adjacentes.
   - Para dados de tabela (anexos de cargos, lotacionograma), veja a skill
     `extracao_tabela_anexo` — busca vetorial pura tende a recuperar mal
     linhas específicas de tabela.

4. **Verifique se há retificação ou revogação posterior** do ato
   encontrado antes de apresentar como resposta final — busque também por
   "retificação [número do ato]" e "revoga [número do ato]". Se o ato for
   uma retificação, use a skill `comparacao_retificacao` para checar o que
   mudou em relação à versão original.

5. **Cite a fonte na resposta**: tipo, número, data, e se possível o
   artigo/protocolo exato. Formato sugerido:
   > Conforme a Portaria nº 38/2025/GAB-SEJUS/MT (Art. 1º, inciso I), o
   > valor diário pelo uso do equipamento é de R$ 7,20.

## Casos especiais

- **Pergunta sobre estrutura organizacional atual**: buscar sempre a
  versão mais recente do Decreto de estrutura (verificar data — decretos
  anteriores costumam ser revogados expressamente, ex. Art. 18 do Decreto
  1.933/2026 revoga o Decreto 1.213/2025).
- **Pergunta sobre pessoa/servidor**: buscar por matrícula funcional além
  do nome, já que nomes podem se repetir ou ter grafias variadas entre
  documentos.
- **Pergunta sobre valores monetários**: sempre confirmar se há portaria
  de reajuste mais recente antes de citar um valor como atual.
- **Sem resultado relevante**: responda "não encontrei esse ato/trecho na
  base indexada" — não tente reconstruir o conteúdo a partir de contexto
  genérico sobre legislação penitenciária.