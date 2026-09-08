# SEJUS Project

Agente para consulta de atos normativos da SEJUS usando RAG e para geracao de
minutas em documentos DOCX a partir de templates.

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

## Executar o agente

Inicie a CLI interativa com:

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

## Gerar documentos DOCX

A geracao usa modelos DOCX reais da SEJUS como base de formatacao:

- `docs/templates-plus/` — modelos de referencia
- `docs/templates/arruma-manualmente/` — atos reais capturados do Diario Oficial
- `docs/templates/Template_Decreto.docx` — esqueleto de formatacao para Decreto

Na CLI, solicite o documento ao agente. O fluxo e:

1. O tipo de ato e detectado no pedido e o modelo correspondente e escolhido
   automaticamente (instrucao normativa, portaria conjunta, retificacao,
   decreto ou portaria).
2. O RAG recupera atos relacionados ao pedido.
3. O agente pergunta se voce quer informar os campos (numero, data,
   signatario, cargo, ementa) ou se prefere que a minuta seja preenchida
   automaticamente com dados plausiveis para revisao.
4. Apos a confirmacao, o LLM redige a estrutura da minuta (ementa,
   considerandos, preambulo, articulacao com incisos e paragrafos, fechamento)
   e a tool monta a copia em `outputs/` preservando a formatacao do modelo.

Para autorizar uma minuta com dados plausiveis, informe explicitamente que o
agente pode usar o banco e que o documento sera revisado. O arquivo original do
modelo nunca e sobrescrito. Se ja houver uma minuta pendente, comandos como
`pode inventar`, `pode gerar`, `sim` ou `gere o arquivo` finalizam a geracao
diretamente pela CLI.

O texto da minuta e escrito pelo modelo de linguagem a partir do pedido do
usuario e do contexto recuperado no RAG, e o numero de artigos varia conforme a
complexidade do tema. Cabecalhos, rodapes, marca d'agua e estilos do modelo sao
preservados.

## Estrutura principal

```text
app/                         Scripts de execucao e indexacao
docs/fontes-rag/pdf/        PDFs de origem
docs/fontes-rag/markdown/   Corpus usado pelo RAG
docs/templates/              Templates DOCX
qdrant_data/                 Indice local persistido
src/sejus_project/agent/     Loop do agente e function calling
src/sejus_project/rag/       Ingestao, chunking, embeddings e Qdrant
src/sejus_project/tools/     Tools de consulta, arquivos e documentos
outputs/                     DOCX gerados
```

## Validacao

Para verificar sintaxe e estilo dos arquivos Python:

```bash
uv run python -m compileall -q src app
uv run ruff check src app
```

O `ruff` pode apontar problemas preexistentes em scripts que nao foram
alterados pela feature de geracao de documentos.

Para executar os testes da geracao de documentos:

```bash
uv run pytest tests/test_document_generation.py -q
```

Os cenarios ficam em `tests/prompts/`. Cada prompt e associado a um tipo de
ato e testa a selecao do template, a consulta ao RAG e a abertura do DOCX
gerado sem placeholders pendentes. O RAG e simulado nos testes para evitar
download de modelos e chamadas externas.
