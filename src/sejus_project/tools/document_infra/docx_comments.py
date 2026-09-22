"""Comentários nativos do Word (``w:comment``) para os DOCX de melhoria.

O python-docx não tem suporte a comentários. Este módulo cria as partes OOXML
ausentes que o Word espera num arquivo comentado — ``word/comments.xml`` e as
partes auxiliares ``commentsExtended.xml``, ``commentsIds.xml`` e
``commentsExtensible.xml`` —, registra o relacionamento delas tanto no
``[Content_Types].xml`` quanto nas relações do documento e ancora cada
comentário ao trecho citado via ``w:commentRangeStart`` / ``w:commentRangeEnd``
/ ``w:commentReference``.

A entrada é ``adicionar_comentario(documento, texto_ancora, texto_comentario,
autor="Agente SEJUS")``: recebe o ``Document`` python-docx sendo montado (antes
de salvar), o trecho de texto a comentar (primeiro parágrafo do corpo que o
contiver) e o texto do comentário. As partes são criadas uma única vez por
documento e atualizadas a cada chamada.
"""
from __future__ import annotations

import hashlib

from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml.ns import qn

from sejus_project.tools.document_infra.docx_engine import paragraph_text

_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
_NS_W16CID = "http://schemas.microsoft.com/office/word/2016/wordml/cid"
_NS_W16 = "http://schemas.microsoft.com/office/word/2018/wordml"
_NS_W16CEX = "http://schemas.microsoft.com/office/word/2018/wordml/cex"
_NS_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_MC_IGNORABLE = 'mc:Ignorable="w15 w16cid w16 w16cex"'
_MC_DECL = f'xmlns:mc="{_NS_MC}"'

_PARTES_COMENTARIOS = (
    (
        "/word/comments.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
    ),
    (
        "/word/commentsExtended.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml",
        "http://schemas.microsoft.com/office/2014/word/relationships/commentsExtended",
    ),
    (
        "/word/commentsIds.xml",
        "application/vnd.ms-word.commentsIds+xml",
        "http://schemas.microsoft.com/office/2016/01/word/relationships/commentsIds",
    ),
    (
        "/word/commentsExtensible.xml",
        "application/vnd.ms-word.commentsExtensible+xml",
        "http://schemas.microsoft.com/office/2018/08/word/relationships/commentsExtensible",
    ),
)


def _txt_escape(texto: str) -> str:
    return (
        texto.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _iniciais(autor: str) -> str:
    partes = [p for p in autor.replace(".", " ").split() if p]
    if not partes:
        return "??"
    return "".join(p[0].upper() for p in partes[:2])


def _para_id_comentario(comentario_id: int) -> str:
    digest = hashlib.md5(f"sejus:{comentario_id}".encode()).hexdigest()[:8]
    return f"{int(digest, 16):08x}"


def _xml_comentarios(estado) -> bytes:
    itens = []
    for c in estado["comentarios"]:
        itens.append(
            f'<w:comment w:id="{c["id"]}" w:author="{_txt_escape(c["autor"])}" '
            f'w:initials="{_txt_escape(c["iniciais"])}" '
            f'w:date="{c["data"]}">'
            f'<w:p><w:pPr><w:rPr><w:color w:val="404040"/>'
            f'<w:sz w:val="22"/></w:rPr></w:pPr>'
            f'<w:r><w:t xml:space="preserve">{_txt_escape(c["texto"])}</w:t></w:r>'
            f"</w:p></w:comment>"
        )
    corpo = "\n".join(itens)
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:comments xmlns:w="{_NS_W}">{corpo}</w:comments>'
    ).encode()


def _xml_comments_extended(estado) -> bytes:
    itens = []
    for c in estado["comentarios"]:
        para_id = _para_id_comentario(c["id"])
        itens.append(f'<w15:commentEx w15:paraId="{para_id}" w15:paraIdParent="0" w15:done="0"/>')
    corpo = "\n".join(itens)
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w15:commentsEx xmlns:w15="{_NS_W15}" xmlns:mc="{_NS_MC}" '
        f'mc:Ignorable="w15">{corpo}</w15:commentsEx>'
    ).encode()


def _xml_comments_ids(estado) -> bytes:
    itens = []
    for c in estado["comentarios"]:
        para_id = _para_id_comentario(c["id"])
        itens.append(
            f'<w16cid:commentId w16cid:paraId="{para_id}" '
            f'w16:durableId="{_para_id_comentario(c["id"])}"/>'
        )
    corpo = "\n".join(itens)
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w16cid:commentsIds xmlns:w16cid="{_NS_W16CID}" '
        f'xmlns:w16="{_NS_W16}" {_MC_DECL} {_MC_IGNORABLE}>{corpo}</w16cid:commentsIds>'
    ).encode()


def _xml_comments_extensible(estado) -> bytes:
    itens = []
    for c in estado["comentarios"]:
        itens.append(
            f'<w16cex:commentEx '
            f'w16cex:durableId="{_para_id_comentario(c["id"])}" '
            f'w15:paraId="{_para_id_comentario(c["id"])}"/>'
        )
    corpo = "\n".join(itens)
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w16cex:commentsExtensible xmlns:w16cex="{_NS_W16CEX}" '
        f'xmlns:w15="{_NS_W15}" {_MC_DECL} {_MC_IGNORABLE}>{corpo}</w16cex:commentsExtensible>'
    ).encode()


_GERADORES = {
    "/word/comments.xml": _xml_comentarios,
    "/word/commentsExtended.xml": _xml_comments_extended,
    "/word/commentsIds.xml": _xml_comments_ids,
    "/word/commentsExtensible.xml": _xml_comments_extensible,
}


def _garantir_partes(documento) -> dict:
    """Cria (uma única vez, por documento) as partes de comentários OOXML."""
    part = documento.part
    estado = getattr(part, "_sejus_comentarios", None)
    if estado is not None:
        return estado
    estado = {"contador": 0, "comentarios": [], "partes": {}}
    for partname, ctype, reltype in _PARTES_COMENTARIOS:
        novo = Part(
            PackURI(partname),
            ctype,
            _GERADORES[partname](estado),
            part.package,
        )
        part.relate_to(novo, reltype)
        estado["partes"][partname] = novo
    part._sejus_comentarios = estado
    return estado


def _refrescar_partes(estado) -> None:
    for partname, gerador in _GERADORES.items():
        estado["partes"][partname]._blob = gerador(estado)


def _ancorar_no_paragrafo(w_p, comentario_id: int) -> bool:
    """Envolve os runs do parágrafo com commentRangeStart/End + commentReference."""
    runs = w_p.findall(qn("w:r"))
    if not runs:
        return False
    primeiro = runs[0]
    ultimo = runs[-1]
    inicio = w_p.makeelement(
        qn("w:commentRangeStart"), {qn("w:id"): str(comentario_id)}
    )
    fim = w_p.makeelement(qn("w:commentRangeEnd"), {qn("w:id"): str(comentario_id)})
    run_ref = w_p.makeelement(qn("w:r"), {})
    run_ref.append(
        w_p.makeelement(qn("w:commentReference"), {qn("w:id"): str(comentario_id)})
    )
    primeiro.addprevious(inicio)
    ultimo.addnext(fim)
    fim.addnext(run_ref)
    return True


def _paragrafo_com_ancora(documento, texto_ancora):
    alvo = texto_ancora or ""
    if not alvo.strip():
        return None
    for w_p in documento.element.body.iter(qn("w:p")):
        if alvo in paragraph_text(w_p):
            return w_p
    return None


def adicionar_comentario(
    documento,
    texto_ancora: str,
    texto_comentario: str,
    autor: str = "Agente SEJUS",
) -> bool:
    """Adiciona um comentário nativo do Word ao ``documento`` (antes de salvar).

    ``texto_ancora`` identifica o parágrafo a comentar (primeiro parágrafo do
    corpo que contiver a string). Retorna ``False`` se nada foi ancorado."""
    w_p = _paragrafo_com_ancora(documento, texto_ancora)
    if w_p is None:
        return False
    estado = _garantir_partes(documento)
    comentario = {
        "id": estado["contador"],
        "autor": autor,
        "iniciais": _iniciais(autor),
        "data": _data_atual(),
        "texto": texto_comentario.strip(),
    }
    estado["contador"] += 1
    estado["comentarios"].append(comentario)
    if not _ancorar_no_paragrafo(w_p, comentario["id"]):
        return False
    _refrescar_partes(estado)
    return True


def _data_atual() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")