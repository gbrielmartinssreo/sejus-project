# SEJUS Project

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
- No chat, peca uma minuta; o agente pergunta se voce quer informar os campos
  (numero, data, signatario, cargo, ementa) ou se prefere que ela seja
  preenchida automaticamente com dados plausiveis para revisao.
- A minuta gerada e renderizada como documento formatado na propria pagina
  (CSS com impressao A4), com botoes de copiar texto e imprimir/exportar PDF
  via `window.print()` — sem depender da formatacao de um arquivo DOCX/PDF.

O texto da minuta e escrito pelo modelo de linguagem a partir do pedido e do
contexto recuperado no RAG; o numero de artigos varia conforme a complexidade
do tema. Sempre que a minuta for preenchida automaticamente, ela exige revisao.

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
renderizada na interface web.

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
src/sejus_project/tools/     Tools de consulta, arquivos e documentos
src/sejus_project/web/       Servidor FastAPI, render de minuta e frontend
outputs/                     DOCX gerados
```

## Validacao

Para verificar sintaxe e estilo dos arquivos Python:

```bash
uv run python -m compileall -q src app tests
uv run ruff check src app tests
```

O `ruff` pode apontar problemas preexistentes em alguns scripts de `app/` que
nao fazem parte das features atuais.

Para executar a suite completa de testes:

```bash
uv run pytest tests/ -q
```

- `tests/test_document_generation.py` — selecao de modelo por tipo de ato,
  fluxo de confirmacao de campos, montagem da estrutura e preservacao de
  formatacao (RAG simulado, sem chamadas externas).
- `tests/test_web.py` — render da minuta em HTML (com escape), pagina de chat,
  endpoint `/api/chat` e upload de arquivos.
- `tests/prompts/` — cenarios de pedido por tipo de ato usados nos testes de
  geracao de documentos.