from pathlib import Path
from sejus_project.rag.ingestion import load_documents
from sejus_project.rag.chunking import chunk_documents
from sejus_project.rag.embedding import Embedder
from sejus_project.rag.indexing import QdrantIndexer, QdrantIndexerConfig

# Definir o diretório raiz do projeto
script_dir = Path(__file__).parent
projeto_root = script_dir.parent.parent.parent

# 1. Ler e limpar os .md
docs = load_documents(projeto_root / "rag" / "fontes-rag" / "markdown")
print(f"{len(docs)} documentos carregados")

# 2. Dividir em chunks
chunks = chunk_documents(docs)
print(f"{len(chunks)} chunks gerados")

# 3. Gerar embeddings + indexar no Qdrant
embedder = Embedder()  # baixa o modelo na primeira vez (~2GB)
indexer = QdrantIndexer(QdrantIndexerConfig(
    collection_name="sejus_atos",
    vector_size=embedder.dimension,
))
indexer.create_collection(recreate=True)  # True = apaga e recria do zero
indexer.index_chunks(chunks, embedder)


#python ingestion.py --input fontes-rag/           # só testa a leitura/limpeza
#python chunking.py --input fontes-rag/ --output chunks.jsonl   # ingestion + chunking
#python indexing.py --chunks chunks.jsonl --recreate            # embedding + indexação