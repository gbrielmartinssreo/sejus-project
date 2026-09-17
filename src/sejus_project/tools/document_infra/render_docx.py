"""Renderiza um DOCX gerado em HTML com estilos inline.

Usado para que a prévia no navegador (front) seja idêntica ao arquivo de
saída (outputs/*.docx). A formatação é resolvida pela mesma hierarquia do
OpenXML: docDefaults -> Normal -> cadeia de estilos (basedOn) -> propriedades
do parágrafo (pPr/rPr) -> propriedades diretas de cada run.
"""
from __future__ import annotations

import html as _html

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

_ALINHAMENTO = {
    WD_ALIGN_PARAGRAPH.LEFT: "left",
    WD_ALIGN_PARAGRAPH.CENTER: "center",
    WD_ALIGN_PARAGRAPH.RIGHT: "right",
    WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
}


def _e(texto: object) -> str:
    return _html.escape(str(texto or ""), quote=True)


def _pt(valor) -> str | None:
    if valor is None:
        return None
    try:
        return f"{valor.pt:.1f}pt"
    except AttributeError:
        return None


def _sobrepor(alvo: dict, dados: dict) -> None:
    for chave, valor in dados.items():
        alvo[chave] = valor


def _sobrepor_se_falta(alvo: dict, dados: dict) -> None:
    for chave, valor in dados.items():
        if chave not in alvo:
            alvo[chave] = valor


def _nome_fonte(valor: str | None) -> str | None:
    if not valor:
        return None
    return f"'{valor}', 'Times New Roman', Georgia, serif"


def _css(dados: dict) -> str:
    """Monta a string CSS inline a partir de um dicionário de propriedades."""
    trechos = []
    for chave, valor in dados.items():
        if chave == "font-family":
            nome = _nome_fonte(valor)
            if nome:
                trechos.append(f"font-family:{nome}")
        elif valor is not None:
            trechos.append(f"{chave}:{valor}")
    return ";".join(trechos)


def _doc_defaults_fonte(doc: Document) -> dict:
    """Fonte padrão do documento (docDefaults/rPrDefault)."""
    pred = doc.styles.element.find(qn("w:docDefaults"))
    if pred is None:
        return {}
    rpr = pred.find(qn("w:rPrDefault"))
    if rpr is None:
        return {}
    rpr = rpr.find(qn("w:rPr"))
    if rpr is None:
        return {}
    return _font_do_rpr(rpr)


def _doc_defaults_pf(doc: Document) -> dict:
    """Props de parágrafo padrão do documento (docDefaults/pPrDefault)."""
    pred = doc.styles.element.find(qn("w:docDefaults"))
    if pred is None:
        return {}
    ppr = pred.find(qn("w:pPrDefault"))
    if ppr is None:
        return {}
    ppr = ppr.find(qn("w:pPr"))
    if ppr is None:
        return {}
    return _pf_do_elemento(ppr)


def _font_do_rpr(rpr) -> dict:
    """Lê propriedades de fonte de um <w:rPr>."""
    dados = {}
    rf = rpr.find(qn("w:rFonts"))
    if rf is not None:
        nome = rf.get(qn("w:ascii")) or rf.get(qn("w:hAnsi")) or rf.get(qn("w:eastAsia"))
        if nome:
            dados["font-family"] = nome
    sz = rpr.find(qn("w:sz"))
    if sz is not None and sz.get(qn("w:val")):
        dados["font-size"] = f"{int(sz.get(qn('w:val'))) / 2:.1f}pt"
    bold = _bool_rpr(rpr, "w:b")
    if bold is not None:
        dados["font-weight"] = "700" if bold else "400"
    italico = _bool_rpr(rpr, "w:i")
    if italico is not None:
        dados["font-style"] = "italic" if italico else "normal"
    sublinhado = rpr.find(qn("w:u"))
    if sublinhado is not None:
        val = sublinhado.get(qn("w:val"))
        if val is None or val not in ("none", "0"):
            dados["text-decoration"] = "underline"
    cor = rpr.find(qn("w:color"))
    if cor is not None:
        val = cor.get(qn("w:val"))
        if val and val != "auto":
            dados["color"] = f"#{val}"
    return dados


def _bool_rpr(rpr, tag: str) -> bool | None:
    el = rpr.find(qn(tag))
    if el is None:
        return None
    val = el.get(qn("w:val"))
    return val is None or val.casefold() in ("true", "1", "on")


def _pf_do_rpr(rpr) -> dict:
    """Lê propriedades de parágrafo de um <w:pPr>."""
    dados = _pf_do_elemento(rpr)
    return dados


def _pf_do_elemento(pPr) -> dict:
    """Extrai alinhamento/recuos/espaçamento de um <w:pPr> ou <w:pPr> de estilo."""
    dados = {}
    jc = pPr.find(qn("w:jc"))
    if jc is not None:
        mapa = {
            "left": "left",
            "center": "center",
            "right": "right",
            "both": "justify",
            "justify": "justify",
        }
        alinhamento = mapa.get(jc.get(qn("w:val")))
        if alinhamento:
            dados["text-align"] = alinhamento
    ind = pPr.find(qn("w:ind"))
    if ind is not None:
        for attr, css in (
            ("w:left", "margin-left"),
            ("w:right", "margin-right"),
            ("w:firstLine", "text-indent"),
        ):
            valor = ind.get(qn(attr))
            if valor:
                dados[css] = f"{int(valor) / 20:.1f}pt"
    esp = pPr.find(qn("w:spacing"))
    if esp is not None:
        for attr, css in (
            ("w:before", "margin-top"),
            ("w:after", "margin-bottom"),
        ):
            valor = esp.get(qn(attr))
            if valor:
                dados[css] = f"{int(valor) / 20:.1f}pt"
    return dados


def _font_do_estilo(estilo) -> dict:
    """Propriedades de fonte declaradas num estilo (próprias, não herdadas)."""
    dados = {}
    try:
        fonte = estilo.font
    except (KeyError, AttributeError):
        return dados
    if fonte.name:
        dados["font-family"] = fonte.name
    tamanho = _pt(fonte.size)
    if tamanho:
        dados["font-size"] = tamanho
    if fonte.bold is not None:
        dados["font-weight"] = "700" if fonte.bold else "400"
    if fonte.italic is not None:
        dados["font-style"] = "italic" if fonte.italic else "normal"
    if fonte.underline is not None:
        dados["text-decoration"] = "underline" if fonte.underline else "none"
    try:
        cor = fonte.color.rgb
        if cor is not None:
            dados["color"] = f"#{cor}"
    except AttributeError:
        pass
    return dados


def _pf_do_estilo(estilo) -> dict:
    """Propriedades de parágrafo declaradas num estilo (próprias delas)."""
    dados = {}
    try:
        pf = estilo.paragraph_format
    except (KeyError, AttributeError):
        return dados
    alinhamento = _ALINHAMENTO.get(pf.alignment)
    if alinhamento:
        dados["text-align"] = alinhamento
    for prop, css in (
        ("left_indent", "margin-left"),
        ("right_indent", "margin-right"),
    ):
        valor = _pt(getattr(pf, prop))
        if valor:
            dados[css] = valor
    first_line = _pt(pf.first_line_indent)
    if first_line:
        dados["text-indent"] = first_line
    antes = _pt(pf.space_before)
    depois = _pt(pf.space_after)
    if antes:
        dados["margin-top"] = antes
    if depois:
        dados["margin-bottom"] = depois
    return dados


def _cadeia_estilos(estilo):
    """Estilo aplicado e seus ancestrais (basedOn), do mais específico ao
    genérico (ex.: Body Text -> Normal)."""
    cadeia = []
    vistos = set()
    atual = estilo
    while atual is not None and id(atual) not in vistos:
        cadeia.append(atual)
        vistos.add(id(atual))
        try:
            atual = atual.base_style
        except (KeyError, AttributeError):
            atual = None
    return cadeia


def _fonte_efetiva(p: Paragraph, doc: Document) -> dict:
    """Fonte efetiva do parágrafo (base p/ os runs), pela hierarquia OOXML."""
    alvo = {}
    _sobrepor(alvo, _doc_defaults_fonte(doc))
    _sobrepor(alvo, _font_do_estilo(p.style))
    for estilo in _cadeia_estilos(p.style):
        _sobrepor_se_falta(alvo, _font_do_estilo(estilo))
    ppr = p._p.pPr
    rpr = ppr.find(qn("w:rPr")) if ppr is not None else None
    if rpr is not None:
        _sobrepor(alvo, _font_do_rpr(rpr))
    return alvo


def _pf_efetivo(p: Paragraph, doc: Document) -> dict:
    """Propriedades de parágrafo efetivas, pela hierarquia OOXML."""
    alvo = {}
    _sobrepor(alvo, _doc_defaults_pf(doc))
    _sobrepor(alvo, _pf_do_estilo(p.style))
    for estilo in _cadeia_estilos(p.style):
        _sobrepor_se_falta(alvo, _pf_do_estilo(estilo))
    ppr = p._p.pPr
    if ppr is not None:
        _sobrepor(alvo, _pf_do_elemento(ppr))
    alvo.setdefault("text-align", "left")
    alvo.setdefault("line-height", "1.0")
    return alvo


def _font_direto(run) -> dict:
    """Propriedades de fonte definidas diretamente num run."""
    dados = {}
    if run.font.name:
        dados["font-family"] = run.font.name
    tamanho = _pt(run.font.size)
    if tamanho:
        dados["font-size"] = tamanho
    if run.font.bold is not None:
        dados["font-weight"] = "700" if run.font.bold else "400"
    if run.font.italic is not None:
        dados["font-style"] = "italic" if run.font.italic else "normal"
    if run.font.underline is not None:
        dados["text-decoration"] = "underline" if run.font.underline else "none"
    try:
        cor = run.font.color.rgb
        if cor is not None:
            dados["color"] = f"#{cor}"
    except AttributeError:
        pass
    return dados


def _run_estilo(run, base: dict) -> str:
    efetivo = dict(base)
    _sobrepor(efetivo, _font_direto(run))
    return _css(efetivo)


def _par_estilo(p: Paragraph, doc: Document) -> str:
    return _css(_pf_efetivo(p, doc))


def _par_para_html(w_p, doc: Document) -> str:
    p = Paragraph(w_p, doc)
    estilo_par = _par_estilo(p, doc)
    if not p.text.strip():
        return f'<p class="preview-par" style="{estilo_par}">&nbsp;</p>'
    base = _fonte_efetiva(p, doc)
    trechos = []
    for run in p.runs:
        if not (run.text or "").strip():
            continue
        estilo_run = _run_estilo(run, base)
        trechos.append(
            f'<span style="{estilo_run}">{_e(run.text)}</span>'
        )
    return f'<p class="preview-par" style="{estilo_par}">{"".join(trechos)}</p>'


def _tabela_para_html(w_tbl, doc: Document) -> str:
    tabela = Table(w_tbl, doc)
    linhas = []
    for row in tabela.rows:
        celulas = []
        for cell in row.cells:
            texto = "\n".join(p.text for p in cell.paragraphs)
            celulas.append(
                f'<td style="border:0.5pt solid #999;padding:2pt 6pt;'
                f'vertical-align:top;">{_e(texto)}</td>'
            )
        linhas.append("<tr>" + "".join(celulas) + "</tr>")
    return (
        '<table class="preview-tabela" '
        'style="border-collapse:collapse;width:100%;margin:6pt 0;">'
        + "".join(linhas)
        + "</table>"
    )


def docx_para_html(path) -> str:
    """Converte um DOCX em HTML com estilos inline, na ordem do corpo."""
    doc = Document(str(path))
    estilo_base = _css(_doc_defaults_fonte(doc)) or "font-family:'Times New Roman', Georgia, serif"
    partes = []
    for child in doc.element.body.iterchildren():
        tag = child.tag
        if tag == qn("w:p"):
            partes.append(_par_para_html(child, doc))
        elif tag == qn("w:tbl"):
            partes.append(_tabela_para_html(child, doc))
    return (
        '<div class="minuta-documento" '
        f'style="background:#fff;padding:24pt 28pt;{estilo_base}">'
        + "".join(partes)
        + "</div>"
    )