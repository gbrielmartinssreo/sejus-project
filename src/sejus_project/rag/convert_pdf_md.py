from pathlib import Path
from markitdown import MarkItDown

# Usar caminho relativo baseado no arquivo atual
script_dir = Path(__file__).parent
projeto_root = script_dir.parent
entrada = projeto_root / "src" / "sejus-project" / "rag" / "fontes-rag" / "pdf"
saida = projeto_root / "src" / "sejus-project" / "rag" / "fontes-rag" / "markdown"

saida.mkdir(parents=True, exist_ok=True)

md = MarkItDown()

arquivos = list(entrada.glob("*.pdf"))
print(f"Encontrados {len(arquivos)} PDFs")

for pdf in arquivos:
    try:
        print(f"Convertendo: {pdf.name}")
        resultado = md.convert(str(pdf))

        arquivo_md = saida / f"{pdf.stem}.md"
        arquivo_md.write_text(resultado.text_content, encoding="utf-8")

        print(f"[OK] Convertido: {pdf.name}")
    except Exception as e:
        print(f"[ERRO] Falha ao converter {pdf.name}: {type(e).__name__}: {str(e)}")
        continue

print(f"\nConversao concluida!")