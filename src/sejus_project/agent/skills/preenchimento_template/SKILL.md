---
name: preenchimento_template
description: Use ao gerar um DOCX de ato normativo da SEJUS (Decreto, Portaria, Portaria Conjunta, Instrução Normativa ou Retificação) a partir de um pedido, template em docs/templates e contexto do RAG. Orienta seleção do template, preenchimento dos marcadores reais, confirmação de dados e validação do arquivo em outputs.
---

# Preenchimento de Template da SEJUS

Esta skill se aplica somente aos templates DOCX do projeto. Os arquivos fonte
ficam em `docs/templates/` e os documentos gerados ficam em `outputs/`.

Templates disponíveis:

- `Template_Decreto.docx`
- `Template_Instrucao_Normativa.docx`
- `Template_Portaria.docx`
- `Template_Portaria_Conjunta.docx`
- `Template_Retificacao_Portaria.docx`

Os templates atuais usam marcadores entre colchetes, como `[XX]`, `[ANO]`,
`[NOME DO SIGNATÁRIO]` e `[TEXTO DO ARTIGO 1º ...]`. Não substitua esses
marcadores por nomes inventados como `{numero}` sem antes verificar o DOCX.

## Regra anti-loop (leia antes de tudo)

O erro mais comum desta skill é ficar perguntando a mesma coisa em vários
turnos, sem avançar. Para evitar isso:

1. **Mantenha um estado acumulado de `values` durante toda a conversa.**
   Toda resposta que o usuário já deu (em qualquer turno anterior) conta como
   preenchida. Nunca volte a perguntar um campo que já foi respondido, mesmo
   que a resposta tenha vindo junto com outra coisa ou em uma mensagem curta.
2. **Peça os dados pendentes reais uma única vez, em uma lista consolidada.**
   Não faça rodadas sucessivas de "ainda faltam alguns dados" indo campo por
   campo. Se após a inspeção do template restarem N marcadores sem valor,
   pergunte os N de uma vez.
3. **No máximo uma rodada de perguntas.** Depois que o usuário responder
   (mesmo que parcialmente) a essa lista consolidada, não pergunte de novo.
   Para qualquer marcador que ainda faltar, siga para "Preenchimento
   automático" abaixo em vez de abrir uma nova rodada.
4. **Frases de autorização finalizam a minuta imediatamente.** Trate como
   autorização para preencher o restante com RAG/dados plausíveis e gerar o
   arquivo já nesse turno — sem novas perguntas — qualquer variação de:
   "o resto pega da base de dados", "pode inventar", "pode gerar", "gera o
   arquivo", "manda ver", "da seu pulo" / "dá seu pulo", "segue com o que
   tiver" ou equivalentes. Não interprete essas frases como pedido de mais
   detalhes.

## Marcadores reais vs. instruções estruturais do template

Nem todo texto entre colchetes é um campo a ser respondido pelo usuário.
Antes de listar pendências, classifique cada marcador:

- **Campo de dado real** (precisa de valor específico): `[XX]`, `[ANO]`,
  `[NOME DO SIGNATÁRIO]`, `[CARGO]`, `[EMENTA]`, `[TEXTO DO ARTIGO 1º —
  OBJETO PRINCIPAL]`, etc.
- **Instrução estrutural/opcional do template** (não é um campo, é uma nota
  de como redigir): coisas como `[... ACRESCENTAR DEMAIS ARTIGOS,
  PARÁGRAFOS E INCISOS CONFORME NECESSÁRIO ...]` ou `[OU: revogando-se a
  Portaria n.º [XX]/[ANO]/GAB-SEJUS/MT]`.
  - Essas instruções **nunca bloqueiam a geração**. Se o usuário não disse
    nada sobre artigos adicionais ou revogação, **não pergunte** — omita a
    seção opcional ou gere sem revogação, e sinalize isso no checklist final
    como algo a revisar, não como pendência que impede o documento.

Só entram na lista consolidada de pendências os campos de dado real que a
tool `gerar_documento_normativo` retornar como marcador sem valor.

## Fluxo

1. **Identifique o tipo do ato** no pedido e selecione o template correspondente.
   Use a seguinte associação:
   - Decreto -> `Template_Decreto.docx`
   - Instrução Normativa ou IN -> `Template_Instrucao_Normativa.docx`
   - Portaria Conjunta -> `Template_Portaria_Conjunta.docx`
   - Retificação -> `Template_Retificacao_Portaria.docx`
   - Portaria -> `Template_Portaria.docx`
   Se o pedido for ambíguo, pergunte antes de gerar.

2. **Inspecione o template antes de preencher.** Use a operação
   `gerar_documento_normativo` sem `values` para obter os marcadores reais,
   o template escolhido e o contexto recuperado. Não presuma campos com base
   em outro tipo de ato.

3. **Classifique cada campo** (ver seção acima):
   - **Do pedido:** objeto, ementa, artigos e finalidade.
   - **Do RAG:** fundamentos, leis, decretos e padrões de atos semelhantes.
   - **Do usuário:** valores monetários, nomes, matrículas, números oficiais
     e datas específicas quando não estiverem comprovados.
   - **Estrutural/opcional:** ver seção "Marcadores reais vs. instruções
     estruturais" — não gera pendência.
   Nunca trate um trecho recuperado como autorização para afirmar um dado
   específico que não esteja no ato consultado.

4. **Consulte o RAG** com `consultar_atos_sejus` ou pela própria tool de
   geração para encontrar atos relacionados e fundamentos. O RAG é fonte de
   apoio e não deve ser usado para fabricar numeração, nomes ou valores, mas
   pode ser usado livremente para redigir fundamentação, considerandos e
   linguagem padrão quando o usuário autorizar a minuta (ver regra anti-loop).

5. **Preencha usando a tool oficial.** Envie `values` como um objeto cujas
   chaves sejam exatamente os marcadores retornados pela inspeção, por exemplo:
   `{"[XX]": "001", "[ANO]": "2026"}`. Inclua nesse objeto TODOS os valores já
   coletados em turnos anteriores, não só os do último turno. Não edite o
   XML do DOCX manualmente e não escreva o arquivo por fora de
   `gerar_documento_normativo`.

6. **Confirmação antes da geração — apenas uma rodada:**
   - Se faltarem campos de dado real após a inspeção, liste todos de uma vez
     e pergunte.
   - Assim que o usuário responder (total ou parcialmente) ou usar uma frase
     de autorização, siga para o passo 7. Não abra uma segunda rodada de
     perguntas pelos mesmos campos ou por instruções estruturais opcionais.

7. **Preenchimento automático dos campos restantes.** Para qualquer campo de
   dado real que continue sem valor depois da única rodada de perguntas (ou
   quando o usuário autorizar a minuta explicitamente):
   - Use o valor mais plausível sustentado pelo RAG quando existir base.
   - Caso não haja base no RAG, use um placeholder plausível e sinalize
     claramente no rodapé da resposta ao usuário (não silenciosamente) quais
     campos foram preenchidos automaticamente e exigem revisão.
   - Gere o documento nesse mesmo turno. Não devolva uma nova lista de
     pendências para o usuário confirmar de novo.

8. **Valide o retorno da tool:** o sucesso deve ter `status: generated`,
   `output_path` apontando para `outputs/` e `remaining_placeholders` vazio.
   Se o status for `awaiting_confirmation`, não diga que o arquivo foi criado.
   Se for `error`, mostre o problema e peça a correção necessária.

9. **Faça o checklist final:** confirme que o tipo do ato corresponde ao
   template, a ementa corresponde ao objeto, a vigência está presente, a
   assinatura tem cargo compatível e a minuta está marcada para revisão.
   Liste também, em uma linha, quais campos foram preenchidos automaticamente
   (RAG ou plausíveis) para facilitar a revisão jurídica.

## Regra de ouro
Não confunda uma minuta autorizada com um ato oficial. Nunca apresente como
fato confirmado um número, nome, matrícula, valor monetário ou citação legal
que não foi fornecido pelo usuário ou sustentado por fonte recuperada. O DOCX
gerado automaticamente sempre precisa de revisão jurídica antes do uso.

## Norma culta da língua portuguesa

Todo texto inserido nos marcadores — ementa, considerandos, artigos, incisos,
parágrafos e alíneas — deve seguir rigorosamente a norma-padrão do português
do Brasil, em registro formal e compatível com atos oficiais. A correção
linguística não deve ser sacrificada por economia de texto.

Observe especialmente:

- Concordância verbal e nominal, inclusive em sujeitos compostos e expressões
   impessoais como "faz-se necessário" e "houve".
- Regência verbal e nominal, como em "atender a", "em conformidade com" e
   "de acordo com".
- Crase antes de termos femininos regidos pela preposição "a", como em
   "à Secretaria" e "às disposições"; nunca antes de verbo ou termo masculino.
- Pontuação de períodos longos em considerandos e artigos, usando vírgulas,
   dois-pontos e ponto e vírgula de forma funcional.
- Paralelismo sintático entre incisos do mesmo artigo: mantenha a mesma
   estrutura gramatical em todos os itens.
- Maiúsculas e nomes oficiais em cargos, órgãos e atos, como "Secretário de
   Estado de Justiça", "Decreto nº" e "Lei Complementar nº".
- Grafia e acentuação conforme o Acordo Ortográfico vigente. Corrija erros
   óbvios de digitação do pedido ao redigir o texto formal, sem alterar seu
   sentido.
- Terminologia jurídico-administrativa consistente com os atos recuperados no
   RAG. Escolha um termo adequado e mantenha-o em todo o documento.

Antes de gerar o arquivo, revise mentalmente cada trecho como se fosse ser
publicado no Diário Oficial. Se houver dúvida de regência, crase, concordância
ou pontuação, prefira uma construção mais simples e inequivocamente correta.