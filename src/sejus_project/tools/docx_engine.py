"""Motor de manipulacao DOCX para gerar minutas a partir de modelos reais.

Responsabilidade: localizar parametros de formatacao dentro de um DOCX modelo
(inclusive tabelas aninhadas, comuns em capturas do Diario Oficial) e construir
paragrafos novos clonando o XML dos paragrafos de referencia, preservando ao
maximo a formatacao original (fonte, tamanho, negrito, alinhamento, espacamento).
"""
from __future__ import annotations

import copy
import re

from docx import Document
from docx.oxml.ns import qn

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def all_paragraphs(document: Document) -> list:
    """Retorna todos os elementos ``w:p`` do corpo, inclusive os que estao
    dentro de tabelas (aninhadas ou nao)."""
    return list(document.element.body.iter(qn("w:p")))


def paragraph_text(w_p) -> str:
    """Texto completo de um elemento ``w:p``."""
    return "".join((t.text or "") for t in w_p.iter(qn("w:t")))


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