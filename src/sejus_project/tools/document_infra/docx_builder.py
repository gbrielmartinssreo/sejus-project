"""Montagem fisica de arquivos DOCX a partir da estrutura de uma minuta.

Responsabilidade: duplicar um modelo DOCX real e reconstruir o corpo com os
paragrafos da estrutura (numero, ementa, considerandos, articulacao, fechamento,
data e assinaturas), clonando a formatacao dos paragrafos de referencia do
proprio modelo (ver docx_engine.py). Nao depende de LLM: recebe a estrutura ja
normalizada e devolve o caminho do arquivo gerado.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from docx.oxml.ns import qn

from sejus_project.tools.document_infra.docx_engine import (
    all_paragraphs,
    assinalar_insercao,
    build_paragraph,
    clear_body,
    find_reference,
    paragraph_text,
)
from sejus_project.tools.document_infra.modelos import PerfilModelo

_SIMPLES = re.compile(r"\s+")

_VERBO_ATO = {
    "portaria": "Esta Portaria",
    "portaria conjunta": "Esta Portaria Conjunta",
    "instrução normativa": "Esta Instrução Normativa",
    "instrucao normativa": "Esta Instrução Normativa",
    "decreto": "Este Decreto",
    "retificação": "Esta Retificação",
    "retificacao": "Esta Retificação",
}


# ---------------------------------------------------------------------------
# Montagem do DOCX
# ---------------------------------------------------------------------------

_QUEDA_REFERENCIA = (
    "artigo",
    "resolutivo",
    "paragrafo",
)

# Autor das revisões de inserção (track changes) gravadas no DOCX melhorado.
_AUTOR_REVISAO = "editor"

# Linhas típicas do rodapé de imprensa do Diário Oficial. Usado para saber onde
# termina o corpo normativo e preservar o rodapé quando o modelo do usuário for
# uma captura do Diário (cabeçalho e rodapé ficam no corpo, e não na seção).
_RE_RODAPE_IMPRENSA = re.compile(
    r"(govern[oa] do estado de mato grosso|seplag|imprensa oficial|iomat)",
    re.IGNORECASE,
)


def _idx_rodape_imprensa(children: list) -> int | None:
    """Índice do último parágrafo do corpo que parece rodapé de imprensa."""
    idx = None
    for i, ch in enumerate(children):
        if ch.tag == qn("w:p") and _RE_RODAPE_IMPRENSA.search(paragraph_text(ch)):
            idx = i
    return idx


def _preparar_corpo(doc, refs: dict, preservar_moldura: bool):
    """Prepara o corpo para a reconstrução da minuta.

    Devolve o elemento-âncora: os novos parágrafos são inseridos imediatamente
    antes dele.

    * ``preservar_moldura=True`` (modelo do usuário — captura do Diário):
      mantém os parágrafos antes do título (cabeçalho do Diário Oficial) e o
      rodapé de imprensa no final, removendo apenas o miolo normativo.
    * Caso contrário (templates oficiais): limpa o corpo e insere antes do
      sectPr (comportamento original — cabeçalho/rodapé vivem na seção).
    """
    body = doc.element.body

    def _sect_pr_ancora():
        sect_pr = body.find(qn("w:sectPr"))
        return sect_pr if sect_pr is not None else body

    if not preservar_moldura:
        clear_body(doc)
        return _sect_pr_ancora()

    children = list(body)
    titulo_wp = refs.get("titulo")
    idx_titulo = next(
        (i for i, ch in enumerate(children) if ch is titulo_wp),
        None,
    )
    if titulo_wp is None or idx_titulo is None:
        clear_body(doc)
        return _sect_pr_ancora()

    idx_rodape = _idx_rodape_imprensa(children)
    if idx_rodape is not None and idx_rodape < idx_titulo:
        idx_rodape = None
    fim = idx_rodape if idx_rodape is not None else len(children)

    for i, ch in enumerate(children):
        if idx_titulo <= i < fim and ch.tag in (qn("w:p"), qn("w:tbl")):
            body.remove(ch)

    if idx_rodape is not None:
        return children[idx_rodape]
    return _sect_pr_ancora()


def _referencias(doc, perfil: PerfilModelo) -> dict:
    paragraphos = all_paragraphs(doc)
    refs = {}
    for papel, pattern in perfil.patterns.items():
        refs[papel] = find_reference(paragraphos, pattern)
    return refs


def _pedir_paragrafo(refs: dict, papel: str) -> bool:
    if refs.get(papel) is not None:
        return True
    for fallback in _QUEDA_REFERENCIA:
        if refs.get(fallback) is not None:
            return True
    return False


def _referencia_para(refs: dict, papel: str):
    ref = refs.get(papel)
    if ref is not None:
        return ref
    for fallback in _QUEDA_REFERENCIA:
        if refs.get(fallback) is not None:
            return refs[fallback]
    return None


def _adicionar_vigencia_faltante(estrutura: dict, tipo_ato: str) -> None:
    fechamento = [f for f in estrutura.get("fechamento") or [] if isinstance(f, dict)]
    if any("entra em vigor" in (f.get("texto") or "").casefold() for f in fechamento):
        return
    verbo = _VERBO_ATO.get(tipo_ato, "Este ato")
    fechamento.append(
        {
            "rotulo": "",
            "texto": f"{verbo} entra em vigor na data de sua publicação.",
        }
    )
    estrutura["fechamento"] = fechamento


def _chave_rotulo(rotulo: str) -> str:
    """Normaliza um rotulo ('Art. 6º-A', 'art. 6°a' ...) para comparacao."""
    chave = _SIMPLES.sub(" ", (rotulo or "").strip().casefold())
    return chave.rstrip(".")


def montar_docx(
    perfil: PerfilModelo,
    estrutura: dict,
    output_dir: Path,
    insercoes_rastreadas: set[str] | None = None,
) -> Path:
    """Duplica o modelo e monta a minuta preservando a formatacao.

    Artigos cujo rotulo esteja em ``insercoes_rastreadas`` (formato
    normalizado) sao gravados como revisao de insercao do Word (``<w:ins>``):
    o revisor precisa aceitar/rejeitar essas adicoes antes de publicar."""
    doc = _abrir_ou_criar(perfil.file)
    refs = _referencias(doc, perfil)
    _adicionar_vigencia_faltante(estrutura, perfil.act_types[0])

    ancora = _preparar_corpo(doc, refs, perfil.preservar_moldura)
    visados = {_chave_rotulo(r) for r in (insercoes_rastreadas or set())}
    n_insercoes = {"n": 0}

    def adicionar(papel: str, rotulo: str, texto: str, rastrear: bool = False) -> None:
        if not texto.strip():
            return
        if not _pedir_paragrafo(refs, papel):
            return
        ref = _referencia_para(refs, papel)
        w_p = build_paragraph(ref, rotulo, texto)
        if rastrear:
            n_insercoes["n"] += 1
            assinalar_insercao(w_p, n_insercoes["n"], _AUTOR_REVISAO)
        ancora.addprevious(w_p)

    adicionar("titulo", "", estrutura.get("numero", ""))
    adicionar("ementa", "", estrutura.get("ementa", ""))

    for considerando in estrutura.get("considerandos", []):
        adicionar("considerando", "", considerando)

    adicionar("preambulo", "", estrutura.get("preambulo", ""))
    adicionar("resolutivo", "", estrutura.get("resolutivo", ""))

    for artigo in estrutura.get("corpo", []):
        if artigo.get("tipo") == "capitulo":
            adicionar("capitulo", "", artigo.get("texto", ""))
            continue
        rastrear = _chave_rotulo(artigo.get("rotulo", "")) in visados
        adicionar("artigo", artigo.get("rotulo", ""), artigo["texto"], rastrear=rastrear)
        for sub in artigo.get("subitens", []):
            papel = "paragrafo" if sub.get("tipo") == "paragrafo" else "inciso"
            adicionar(papel, sub.get("rotulo", ""), sub["texto"])

    for fechamento in estrutura.get("fechamento", []):
        adicionar("artigo", fechamento.get("rotulo", ""), fechamento["texto"])

    adicionar("data", "", estrutura.get("local_data", ""))

    for assinatura in estrutura.get("assinaturas", []):
        adicionar("assinatura", "", assinatura.get("nome", ""))
        adicionar("assinatura", "", assinatura.get("cargo", ""))

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{perfil.name}_{uuid.uuid4().hex[:8]}.docx"
    doc.save(str(output_path))
    return output_path


def _abrir_ou_criar(path: str):
    """Abre o DOCX do modelo.

    O modelo pode estar num caminho relativo ou absoluto; caso nao exista,
    cria um documento vazio para nao quebrar a geracao (o LLM ja avisará
    a revisao). Na pratica os modelos existem no repositorio."""
    caminho = Path(path)
    if not caminho.exists():
        raise FileNotFoundError(f"Modelo de documento nao encontrado: {caminho}")
    from docx import Document

    return Document(str(caminho))
