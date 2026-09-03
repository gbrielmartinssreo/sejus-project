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

Os templates ficam em `docs/templates/`:

- `Template_Decreto.docx`
- `Template_Instrucao_Normativa.docx`
- `Template_Portaria.docx`
- `Template_Portaria_Conjunta.docx`
- `Template_Retificacao_Portaria.docx`

Na CLI, solicite o documento ao agente. O fluxo e:

1. O agente escolhe e inspeciona o template adequado.
2. O RAG recupera atos relacionados ao pedido.
3. O agente solicita os campos que precisam de confirmacao.
4. Depois da confirmacao, a tool gera uma copia em `outputs/`.

Para autorizar uma minuta com dados plausiveis, informe explicitamente que o
agente pode usar o banco e que o documento sera revisado. O arquivo original do
template nunca e sobrescrito.

Os templates atuais usam marcadores entre colchetes, por exemplo `[XX]`,
`[ANO]` e `[NOME DO SIGNATARIO]`. A tool preenche texto em paragrafos e tabelas.
Cabecalhos, rodapes, imagens e controles avancados do Word ainda nao sao
alterados.

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
