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

3. **Classifique cada campo**:
   - **Do pedido:** objeto, ementa, artigos e finalidade.
   - **Do RAG:** fundamentos, leis, decretos e padrões de atos semelhantes.
   - **Do usuário:** valores monetários, nomes, matrículas, números oficiais
     e datas específicas quando não estiverem comprovados.
   Nunca trate um trecho recuperado como autorização para afirmar um dado
   específico que não esteja no ato consultado.

4. **Consulte o RAG** com `consultar_atos_sejus` ou pela própria tool de
   geração para encontrar atos relacionados e fundamentos. O RAG é fonte de
   apoio e não deve ser usado para fabricar numeração, nomes ou valores.

5. **Preencha usando a tool oficial.** Envie `values` como um objeto cujas
   chaves sejam exatamente os marcadores retornados pela inspeção, por exemplo:
   `{"[XX]": "001", "[ANO]": "2026"}`. Não edite o XML do DOCX manualmente
   e não escreva o arquivo por fora de `gerar_documento_normativo`.

6. **Confirmação antes da geração:**
   - Se faltarem dados relevantes, retorne os campos pendentes e pergunte.
   - Se o usuário confirmar dados específicos, envie-os em `values`.
   - Se o usuário autorizar uma minuta com dados plausíveis, pode usar os
     valores automáticos da tool, mas informe que o documento é uma minuta e
     exige revisão jurídica.
   - Um pedido como `pode inventar consultando o banco`, `pode gerar` ou
     `gere o arquivo` finaliza uma minuta pendente na CLI.

7. **Valide o retorno da tool:** o sucesso deve ter `status: generated`,
   `output_path` apontando para `outputs/` e `remaining_placeholders` vazio.
   Se o status for `awaiting_confirmation`, não diga que o arquivo foi criado.
   Se for `error`, mostre o problema e peça a correção necessária.

8. **Faça o checklist final:** confirme que o tipo do ato corresponde ao
   template, a ementa corresponde ao objeto, a vigência está presente, a
   assinatura tem cargo compatível e a minuta está marcada para revisão.

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