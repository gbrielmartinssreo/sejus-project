# SEJUS Project
![Status do Projeto](https://img.shields.io/badge/Status-Desenvolvimento-yellow)

Agente para consulta de atos normativos da SEJUS usando RAG e para geracao de
minutas. A interacao principal e via chat no navegador (localhost); a CLI
continua disponivel opcionalmente.

## Requisitos

- Python 3.14 ou superior
- [uv](https://docs.astral.sh/uv/)
- Uma chave da OpenAI

## Instalacao

Na raiz do projeto, execute:

```bash
uv sync
```

Crie um arquivo `.env` com sua chave:

```env
OPENAI_API_KEY=sua-chave-openai
OPENAI_MODEL=gpt-4o-mini
```

`OPENAI_MODEL` e opcional. O projeto ainda aceita `GROQ_API_KEY` e `GROQ_MODEL`
como fallback temporario quando `OPENAI_API_KEY` nao estiver configurada. Nunca
versione ou compartilhe o arquivo `.env`.

## Interface web (chat localhost)

O chat no navegador e o jeito principal de usar o agente. Inicie o servidor:

```bash
uv run uvicorn sejus_project.web.server:app --reload
```

Abra `http://localhost:8000`.

- `POST /api/chat` — envia a mensagem e devolve a resposta (markdown), a
  estrutura da ultima minuta gerada e o estado de confirmacao de campos.
- `POST /api/upload` — salva um arquivo (`.txt`, `.md`, `.pdf`, `.docx`) em
  `importacoes_usuario/` para a tool `analisar_arquivo_usuario`.
- `GET /api/minuta/docx` — baixa o DOCX de saída da última minuta/melhoria gerada.
- `GET /api/minuta/pdf` — converte esse DOCX em PDF via LibreOffice.
- `GET /api/arquivo/{nome}` — baixa um arquivo de `importacoes_usuario/` (usado
  na comparação antes/depois).
- `POST /api/modelo` — define um `.docx` enviado pelo usuário como modelo de
  formatação ativo para a próxima geração.
- `POST /api/conversa/limpar` — reseta o histórico e o estado de geração.
- O botão **Revisar e gerar DOCX** (estrela na barra de ações) dispara de uma
  vez o fluxo análise → correção: analisa o arquivo enviado e já entrega o DOCX
  corrigido com a comparação antes/depois.
- O botão **Usar como modelo** define um `.docx` como modelo de formatação
  ativo (endpoint `POST /api/modelo`); a próxima minuta segue exatamente aquele
  layout.
- No chat, peca uma minuta; o agente pergunta se voce quer informar os campos
  (numero, data, signatario, cargo, ementa) ou se prefere que ela seja
  preenchida automaticamente com dados plausiveis para revisao.
- A minuta gerada e renderizada como documento formatado na propria pagina
  (CSS com impressao A4), com botoes de copiar texto e imprimir/exportar PDF
  via `window.print()` — sem depender da formatacao de um arquivo DOCX/PDF.

O texto da minuta e escrito pelo modelo de linguagem a partir do pedido e do
contexto recuperado no RAG; o numero de artigos varia conforme a complexidade
do tema. Sempre que a minuta for preenchida automaticamente, ela exige revisao.

## Melhorar e comparar um documento (.docx)

Depois do `POST /api/upload`, peça no chat uma melhoria do arquivo enviado
(ex.: "melhore esta portaria"). A melhoria trabalha em **modo patch**: o modelo
não reescreve o documento — ele devolve apenas as mudanças ancoradas ao texto
original (`alteracoes`, `remocoes` e `adicoes_estruturais`). O sistema copia o
original e aplica o patch, então parágrafos não citados permanecem intactos e o
resultado nunca é truncado por limite de tokens. A página mostra a comparação
antes/depois (alterações, remoções, adições estruturais, lacunas e mudanças
descartadas na validação).

Para entradas `.docx`, o resultado servido por `GET /api/minuta/docx` é uma
**cópia do arquivo original com as mudanças já marcadas**:

- texto alterado/adicionado sai em verde;
- texto alterado/removido fica visível com tachado;
- um parágrafo coberto por um novo texto que **funde** caput + subitem sai
  tachado, sem o subitem ser reinserido (evita duplicação);
- quando só um dispositivo de um parágrafo físico com vários (caput + §/incisos
  no mesmo `<w:p>`) é alterado ou removido, apenas o dispositivo-alvo é marcado
  e reposto — os subdispositivos não abrangidos são preservados;
- parágrafos iguais permanecem intactos.

A melhoria também inclui:

- **Página de resumo** no início do `.docx`: título, legenda das cores e uma
  tabela com uma linha por mudança (artigo, tipo, mudança, motivo).
- **Fundo amarelo + aviso** em itálico logo abaixo para itens marcados com
  `requer_decisao_juridica: true` (pendentes de decisão da equipe jurídica
  antes da publicação).
- **Comentários nativos do Word** ancorados ao texto para toda mudança com
  `lastro` identificado (conteúdo do lastro e avisos de divergência).

O schema do patch exige o **lastro** em toda alteração/remoção/adição que
inova (o modelo usa `[PRAZO A DEFINIR PELA SECRETARIA]` quando o prazo ainda
não foi fixado) e não permite revogação genérica sem citar a norma. Um `lastro`
cujo assunto não coincida com o do texto alterado também marca o item com
`requer_decisao_juridica` e adiciona um aviso de divergência temática.

Uma mudança que executa um **apontamento aprovado da análise** (origem
`origem_analise_aprovada`) não depende de ato no acervo: a ausência de lastro
no RAG não bloqueia a aplicação nem gera `requer_decisao_juridica` automático —
só critério jurídico de fato (depende de decisão explícita, conflita com norma
superior ou cria despesa sem previsão).

`GET /api/minuta/pdf` converte essa cópia marcada em PDF (via LibreOffice),
então as marcas verdes/tachado aparecem também no PDF. O arquivo original
continua disponível em `GET /api/arquivo/{nome}` para conferência.

Para `.pdf`/`.txt`/`.md` (sem um DOCX original que sirva de base para a
cópia), a melhoria é reconstruída sobre o template do tipo de ato, sem marcas
de diferença.

## Corrigir um documento depois da análise

O fluxo de análise e o de melhoria são conectados: quando o usuário envia um
documento, pede uma análise e em seguida pede para **corrigir e entregar o
arquivo corrigido** (ex.: "consegue fazer a correção?", "me dê o arquivo
corrigido"), o pedido vai **direto para a melhoria do documento enviado** — não
abre o formulário de geração de um ato novo.

O botão **Revisar e gerar DOCX** da interface é um atalho exatamente para esse
fluxo, sem depender de pedido em texto: com análise registrada para a mesma
versão do arquivo ele reutiliza os apontamentos existentes (inclusive
aprofundamentos); sem análise, roda uma **análise isolada** (o motor do agente,
em histórico separado, para não poluir a conversa) e já executa a melhoria com
os apontamentos consolidados.

- A análise completa do documento é preservada por **sessão e documento**,
  versionada pelo hash do conteúdo. Só a versão efetivamente analisada é usada
  na correção.
- Dessa análise são extraídos, separadamente, os **apontamentos acionáveis**,
  com IDs estáveis. Elogios, constatações de conformidade, promessas de análise
  e perguntas não são instruções de alteração e não geram mudanças; se a análise
  não apontar nada a corrigir, nenhuma correção é inventada.
- Os apontamentos são **consolidados ao longo dos turnos**: aprofundamentos da
  análise (ex.: "e o que está ruim?") que não releem o arquivo continuam
  vinculados ao mesmo documento e entram na lista, cada um com sua origem.
- A melhoria recebe essa lista como **tarefas a executar** (além das instruções
  do usuário) e aplica ao original pelo mesmo motor de patch, com as marcas
  verde/tachado e a comparação antes/depois.
- A entrega inclui a **cobertura dos apontamentos**, sempre montada a partir da
  lista original (o modelo não pode omitir itens nem trocá-los por outras
  sugestões). A declaração do modelo não basta — o sistema confere se a mudança
  existe de fato no patch e está ancorada. Cada item recebe um status:
  - **aplicado**: alteração executada e verificada no documento;
  - **pendente**: alteração inserida, mas requer decisão jurídica (lastro);
  - **falhou**: não encaminhado, referência inexistente, trecho não localizado
    ou justificativa vaga ("não foi alterado" não justifica);
  - **não aplicado**: impedimento concreto informado (ex.: depende de decisão
    jurídica, conflita com norma superior, cria despesa sem previsão).
  - **descartado**: mudança proposta, mas removida na validação pós-geração
    (âncora inexistente, duplicação ou contradição com o original) — não é
    rebaixado para outro status nem contado como falha.
- **A entrega nunca fica sem arquivo**: se todas as correções funcionarem, sai o
  DOCX corrigido; se só algumas, sai o DOCX parcialmente corrigido com as
  pendências listadas; se nenhuma, sai uma **cópia intacta** do original com o
  aviso "Não foi possível aplicar as correções. Este arquivo preserva o conteúdo
  original.". Mudanças inválidas (sem âncora ou que duplicariam texto) são
  descartadas — nunca se aplica um patch inválido para forçar alteração, nem se
  apresenta a cópia original como corrigida.

## Executar na CLI (opcional)

```bash
uv run app/main.py
```

Digite uma pergunta em portugues. Para encerrar, use `sair`, `exit` ou `quit`.

Exemplos:

```text
Qual e o prazo previsto para o grupo de trabalho?
Consulte as regras sobre uso de IMPO.
Gere uma portaria sobre limpeza da cadeia em Cuiaba.
```

## Preparar o RAG

Os PDFs ficam em `docs/fontes-rag/pdf/` e os arquivos Markdown indexados ficam
em `docs/fontes-rag/markdown/`.

Para converter PDFs novos em Markdown:

```bash
uv run app/convert_pdf_md.py
```

Para gerar os embeddings e recriar a colecao local do Qdrant:

```bash
uv run app/fill_database.py
```

O segundo comando apaga e recria a colecao `sejus_atos` em `qdrant_data/`.
Execute-o novamente somente quando quiser atualizar o indice.

## Gerar documentos DOCX (legado)

A geracao de `.docx` em `outputs/` e um artefato secundario mantido pela tool
`gerar_documento_normativo`; hoje o principal meio de entrega e a minuta
renderizada na interface web. (A melhoria de um `.docx` enviado segue o fluxo
com marcas descrito em "Melhorar e comparar um documento (.docx)".)

Como funciona: o tipo de ato e detectado no pedido e um modelo DOCX real da
SEJUS e escolhido automaticamente como base de formatacao. O agente pergunta se
voce quer informar os campos ou preencher automaticamente; apos a confirmacao,
o LLM redige a estrutura (ementa, considerandos, preambulo, articulacao com
incisos e paragrafos, fechamento) e o arquivo e gerado em `outputs/`.

Premissas:

- O arquivo original do modelo nunca e sobrescrito.
- Com uma minuta pendente, comandos como `pode inventar`, `sim` ou
  `gere o arquivo` finalizam a geracao.
- Modelos de referencia: `docs/templates-plus/`,
  `docs/templates/arruma-manualmente/` e `docs/templates/Template_Decreto.docx`.

## Estrutura principal

```text
app/                         Scripts de execucao e indexacao
docs/fontes-rag/pdf/        PDFs de origem
docs/fontes-rag/markdown/   Corpus usado pelo RAG
docs/templates/              Templates DOCX
importacoes_usuario/        Arquivos enviados pelo usuario (upload do chat)
qdrant_data/                 Indice local persistido
src/sejus_project/agent/     Loop do agente e function calling
src/sejus_project/rag/       Ingestao, chunking, embeddings e Qdrant
src/sejus_project/tools/     Tools da LLM (llm_tools/) e infra DOCX (document_infra/)
src/sejus_project/web/       Servidor FastAPI, render de minuta e frontend
outputs/                     DOCX gerados
```

## Validacao

Para verificar sintaxe e estilo dos arquivos Python:

```bash
uv run python -m compileall -q src app tests
uv run ruff check src app tests
```

O `ruff` pode apontar problemas preexistentes em alguns scripts de `app/` e em
código legado de `src/sejus_project/tools/` (ex.: globals de propostas não
atribuídos e variáveis não usadas em `document_generation.py`), que não fazem
parte das features atuais.

Para executar a suite completa de testes:

```bash
uv run pytest tests/ -q
```

- `tests/test_document_generation.py` — selecao de modelo por tipo de ato,
  fluxo de confirmacao de campos, montagem da estrutura, preservacao de
  formatacao e o `montar_docx_revisado` (marcas verde/tachado na cópia do
  DOCX original) (RAG simulado, sem chamadas externas).
- `tests/test_web.py` — render da minuta em HTML (com escape), pagina de chat,
  endpoint `/api/chat` e upload de arquivos.
- `tests/prompts/` — cenarios de pedido por tipo de ato usados nos testes de
  geracao de documentos.
- `tests/test_minuta_melhoria.py` e `tests/test_integridade_melhoria.py` —
  fluxo de melhoria em modo patch: teto de tokens com clamp, sanidade do
  patch (âncoras/campos), retry e construção da estrutura a partir do
  original + patch sem perda de capítulos/incisos. Em `test_integridade`: a
  cópia marcada (`montar_docx_revisado`) com página de resumo, sombreamento
  amarelo + aviso de pendência, comentários nativos do Word (4 partes OOXML)
  e a não duplicação de parágrafo fundido; em `test_minuta_melhoria`: o lastro
  estendido a `alteracoes`/`remocoes`, o `requer_decisao_juridica` e a
  checagem de coerência temática.
- `tests/test_correcao_pos_analise.py` — fluxo análise → correção: apontamentos
  acionáveis com ID estável, registro por sessão/documento/hash, cobertura
  conferida contra o patch efetivo (inclusive o status `descartado`) e o
  encaminhamento direto para a melhoria.
- `tests/test_revisar_gerar_subdispositivos.py` — botão "Revisar e gerar DOCX"
  (atalho análise → correção) e a preservação de §/incisos quando a alteração
  atinge só o caput de um parágrafo físico do Word.
