"""Motor de manipulacao DOCX para gerar minutas a partir de modelos reais.

Responsabilidade: localizar parametros de formatacao dentro de um DOCX modelo
(inclusive tabelas aninhadas, comuns em capturas do Diario Oficial) e construir
paragrafos novos clonando o XML dos paragrafos de referencia, preservando ao
maximo a formatacao original (fonte, tamanho, negrito, alinhamento, espacamento).
"""
from __future__ import annotations

import copy
import re
from datetime import UTC

from docx import Document
from docx.oxml.ns import qn

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def all_paragraphs(document: Document) -> list:
    """Retorna todos os elementos ``w:p`` do corpo, inclusive os que estao
    dentro de tabelas (aninhadas ou nao)."""
    return list(document.element.body.iter(qn("w:p")))


# Medidas OOXML que precisam ser inteiras (twips, half-points, etc.). Alguns
# editores/ferramentas gravam esses valores como float (ex.:
# ``w:right="1285.8661417322844"``), o que faz o python-docx falhar com
# "invalid literal for int()" ao ler a propriedade (margens da seção, recuos,
# espaçamento, tamanho de fonte). Normalizamos para inteiro ao abrir o arquivo.
_MEDIDAS_ATRIBUTOS = (
    qn("w:w"),
    qn("w:left"),
    qn("w:right"),
    qn("w:top"),
    qn("w:bottom"),
    qn("w:firstLine"),
    qn("w:hanging"),
    qn("w:start"),
    qn("w:end"),
    qn("w:before"),
    qn("w:after"),
    qn("w:line"),
    qn("w:space"),
    qn("w:gutter"),
    qn("w:header"),
    qn("w:footer"),
)

_MEDIDAS_TAGS_VAL = (qn("w:sz"), qn("w:szCs"))


def _medida_inteira(valor: str) -> int | None:
    try:
        return round(float(valor))
    except (TypeError, ValueError):
        return None


def normalizar_medidas(document: Document) -> None:
    """Arredonda medidas OOXML que vierem como float para inteiro.

    Evita o erro do python-docx ``invalid literal for int()`` ao ler margens da
    seção, recuos, espaçamento, larguras de tabela e tamanhos de fonte gravados
    como decimal por outros editores."""
    for elemento in document.element.iter():
        for atributo in _MEDIDAS_ATRIBUTOS:
            valor = elemento.get(atributo)
            if valor is None or "." not in valor:
                continue
            normalizado = _medida_inteira(valor)
            if normalizado is not None:
                elemento.set(atributo, str(normalizado))

    for tag in _MEDIDAS_TAGS_VAL:
        for elemento in document.element.iter(tag):
            valor = elemento.get(qn("w:val"))
            if valor is None or "." not in valor:
                continue
            normalizado = _medida_inteira(valor)
            if normalizado is not None:
                elemento.set(qn("w:val"), str(normalizado))


def paragraph_text(w_p) -> str:
    """Texto completo de um elemento ``w:p``."""
    # Normaliza: se for CT_P com _element, usa o _element subjacente
    if hasattr(w_p, "_element") and w_p._element is not None:
        w_p = w_p._element
    # Tenta extrair texto do elemento XML; se w_p ja for um elemento com metodo iter, usa-o
    if hasattr(w_p, "iter"):
        return "".join((t.text or "") for t in w_p.iter(qn("w:t")))
    # Fallback: retorna string vazia ou o que conseguir converter
    return str(w_p) if w_p else ""


def find_reference(w_paragraphs: list, pattern: str):
    """Retorna o primeiro ``w:p`` cujo texto corresponde a ``pattern``."""
    regex = re.compile(pattern, re.IGNORECASE)
    for w_p in w_paragraphs:
        if regex.search(paragraph_text(w_p).strip()):
            return w_p
    return None


def _run_text(run) -> int:
    return len("".join((t.text or "") for t in run.iter(qn("w:t"))))


def set_run_text(run, text: str) -> None:
    """Substitui todo o conteudo textual de um run por ``text``, preservando rPr."""
    for child in list(run):
        if child.tag != qn("w:rPr"):
            run.remove(child)

    t = run.makeelement(qn("w:t"), {})
    t.text = text
    if text and (text != text.strip()):
        t.set(XML_SPACE, "preserve")
    run.append(t)


def build_paragraph(reference, rotulo: str | None, texto: str):
    """Clona um paragrafo de referencia e monta um novo ``w:p``.

    Se ``rotulo`` for informado (ex.: "Art. 1º", "I -"), o rotulo vai no
    primeiro run (mantendo a formatacao do rotulo do modelo, ex.: negrito)
    e o texto do corpo vai num segundo run com a formatacao do run de conteudo
    do modelo. Caso contrario, todo o texto vai no primeiro run.
    """
    w_p = copy.deepcopy(reference)
    runs = [r for r in w_p.findall(qn("w:r"))]

    if not runs:
        run = w_p.makeelement(qn("w:r"), {})
        w_p.append(run)
        runs = [run]

    label_run = runs[0]
    label_pr = label_run.find(qn("w:rPr"))
    best_run = max(runs, key=_run_text)
    best_pr = best_run.find(qn("w:rPr"))

    for extra in runs[1:]:
        w_p.remove(extra)

    if rotulo:
        set_run_text(label_run, rotulo.strip() + " ")
        body_run = copy.deepcopy(label_run)
        body_pr = body_run.find(qn("w:rPr"))
        if body_pr is not None:
            body_run.remove(body_pr)
        if best_pr is not None:
            body_run.insert(0, copy.deepcopy(best_pr))
        set_run_text(body_run, texto.strip())
        label_run.addnext(body_run)
    else:
        if best_pr is not None and label_pr is None:
            label_run.insert(0, copy.deepcopy(best_pr))
        set_run_text(label_run, texto.strip())

    return w_p


def clear_body(document: Document) -> None:
    """Remove todo o conteudo do corpo, preservando ``sectPr`` (cabecalhos,
    rodapes, margens e marca d'agua do modelo sao mantidos)."""
    body = document.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)


def append_paragraph(body, w_p) -> None:
    """Adiciona um ``w:p`` como ultimo filho do corpo, antes de ``sectPr``."""
    sect_pr = body.find(qn("w:sectPr"))
    if sect_pr is not None:
        sect_pr.addprevious(w_p)
    else:
        body.append(w_p)


def assinalar_insercao(w_p, ins_id: int, author: str, date: str | None = None) -> None:
    """Envolve os runs de um ``w:p`` num ``<w:ins>`` (track changes do Word).

    A insercao passa a depender de aceitacao do revisor antes de publicar.
    ``ins_id`` deve ser unico em todo o documento. ``date`` segue ISO 8601
    (padrao do OOXML, ex.: ``2026-09-16T16:00:00Z``)."""
    from datetime import datetime

    runs = [r for r in w_p.findall(qn("w:r"))]
    if not runs:
        return
    if not date:
        date = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    for run in runs:
        ins = w_p.makeelement(
            qn("w:ins"),
            {qn("w:id"): str(ins_id), qn("w:author"): author, qn("w:date"): date},
        )
        w_p.replace(run, ins)
        ins.append(run)


def verde(w_p, hex_color: str = "2E7D32") -> None:
    """Pinta todos os runs de um ``w:p`` numa cor de destaque (texto adicionado)."""
    from docx.shared import RGBColor

    cor = RGBColor.from_string(hex_color)
    for run in w_p.findall(qn("w:r")):
        rPr = run.get_or_add_rPr()
        rPr.get_or_add_color().val = cor


def tachar(w_p) -> None:
    """Aplica tachado em todos os runs de um ``w:p`` (texto removido/recomposto)."""
    for run in w_p.findall(qn("w:r")):
        rPr = run.get_or_add_rPr()
        rPr.get_or_add_strike().val = True


def destachar(w_p) -> None:
    """Remove tachado/cor de revisão herdados ao clonar um parágrafo.

    ``build_paragraph`` copia a formatação do parágrafo de referência; se essa
    referência já tiver sido tachada (alteração anterior no mesmo laço), o
    parágrafo NOVO sairia tachado — parecendo conteúdo removido. Aqui limpamos
    as marcas de comparação para o texto novo nascer sem tachado."""
    for run in w_p.findall(qn("w:r")):
        rPr = run.find(qn("w:rPr"))
        if rPr is None:
            continue
        strike = rPr.find(qn("w:strike"))
        if strike is not None:
            rPr.remove(strike)
        color = rPr.find(qn("w:color"))
        if color is not None:
            rPr.remove(color)


def sombrear(w_p, fill: str = "FFF3B0") -> None:
    """Aplica sombreamento de parágrafo (``w:pPr/w:shd``, fill em hex).

    Usado para destacar trechos pendentes de decisão jurídica — além da cor de
    fonte verde, o parágrafo inteiro ganha fundo amarelo (4.2)."""
    pPr = w_p.get_or_add_pPr()
    shd = pPr.find(qn("w:shd"))
    if shd is None:
        shd = pPr.makeelement(qn("w:shd"), {})
        pPr.append(shd)
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)