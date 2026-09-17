"""Converte o DOCX gerado em PDF usando o LibreOffice headless.

O PDF é renderizado pelo próprio processador de texto, então a prévia no
navegador vira literalmente o arquivo de saída (outputs/*.docx) — a mesma
página, fonte e layout que o Word vai mostrar.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# Caminhos comuns de instalação do LibreOffice.
_SOFFICE_CANDIDATOS = (
    Path("C:/Program Files/LibreOffice/program/soffice.exe"),
    Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
    Path("/usr/bin/soffice"),
    Path("/usr/local/bin/soffice"),
)

_SOFFICE = next((c for c in _SOFFICE_CANDIDATOS if c.is_file()), None)
if _SOFFICE is None:
    _SOFFICE = shutil.which("soffice")


def soffice_disponivel() -> bool:
    return _SOFFICE is not None


def _converter(docx: Path, pdf: Path, perfil: Path) -> bool:
    cmd = [
        str(_SOFFICE),
        "--headless",
        "--norestore",
        "--nolockcheck",
        f"-env:UserInstallation=file:///{perfil.as_posix()}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(pdf.parent),
        str(docx),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except (subprocess.SubprocessError, OSError):
        return False
    return pdf.is_file()


def docx_para_pdf(docx: str | Path, outdir: Path | None = None) -> Path | None:
    """Converte o DOCX em PDF (junto do arquivo) e devolve o caminho.

    Usa um perfil de instalação persistente para ficar rápido; se a conversão
    falhar (ex.: perfil travado), remove e tenta mais uma vez.
    """
    if _SOFFICE is None:
        return None
    docx_path = Path(docx)
    outdir = Path(outdir) if outdir is not None else docx_path.parent
    outdir.mkdir(parents=True, exist_ok=True)
    pdf = outdir / f"{docx_path.stem}.pdf"
    if pdf.is_file():
        return pdf
    perfil = outdir / ".soffice_profile"
    if not _converter(docx_path, pdf, perfil):
        shutil.rmtree(perfil, ignore_errors=True)
        if not _converter(docx_path, pdf, perfil):
            return None
    return pdf if pdf.is_file() else None


def garantir_pdf(docx: str | Path) -> Path | None:
    """PDF cacheado do DOCX (o mesmo arquivo é reutilizado entre requisições)."""
    return docx_para_pdf(docx)