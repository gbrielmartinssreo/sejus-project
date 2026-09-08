"""Renderiza a estrutura JSON de uma minuta em HTML (e texto puro).

O LLM devolve a minuta estruturada (ver sejus_project.tools.minuta) e este
modulo transforma essa estrutura em um documento formatado via CSS. Toda
string vinda do LLM e escapada antes de entrar no HTML.
"""
from __future__ import annotations

import html as _html


def _e(texto) -> str:
    return _html.escape(texto or "", quote=True)


def minuta_para_texto(estructura: dict) -> str:
    """Converte a estrutura em texto puro (ex.: botao 'Copiar')."""
    linhas: list[str] = []

    def add_par(*partes):
        texto = " ".join(p for p in partes if p).strip()
        if texto:
            linhas.append(texto)

    add_par(estructura.get("numero"))
    add_par(estructura.get("ementa"))

    for considerando in estructura.get("considerandos") or []:
        add_par(considerando)

    add_par(estructura.get("preambulo"))
    add_par(estructura.get("resolutivo"))

    for artigo in estructura.get("corpo") or []:
        add_par(artigo.get("rotulo"), artigo.get("texto"))
        for sub in artigo.get("subitens") or []:
            add_par(sub.get("rotulo"), sub.get("texto"))

    for fechamento in estructura.get("fechamento") or []:
        add_par(fechamento.get("rotulo"), fechamento.get("texto"))

    add_par(estructura.get("local_data"))

    for assinatura in estructura.get("assinaturas") or []:
        add_par(assinatura.get("nome"))
        add_par(assinatura.get("cargo"))

    return "\n".join(linhas)


def minuta_para_html(estructura: dict) -> str:
    """Converte a estrutura em um documento HTML oficial (escapes aplicados)."""
    partes: list[str] = []
    p = partes.append

    def _par(classe: str, *textos):
        texto = " ".join(t for t in textos if t).strip()
        if texto:
            p(f'<p class="minuta-{classe}">{_e(texto)}</p>')

    if estructura.get("numero"):
        p(f'<h1 class="minuta-numero">{_e(estructura["numero"])}</h1>')

    _par("ementa", estructura.get("ementa"))

    for considerando in estructura.get("considerandos") or []:
        _par("considerando", considerando)

    _par("preambulo", estructura.get("preambulo"))
    _par("resolutivo", estructura.get("resolutivo"))

    for artigo in estructura.get("corpo") or []:
        corpo_artigo: list[str] = []
        corpo_artigo.append(
            '<div class="minuta-artigo">'
            f'<span class="minuta-rotulo">{_e(artigo.get("rotulo"))}</span> '
            f'<span class="minuta-texto">{_e(artigo.get("texto"))}</span>'
        )
        for sub in artigo.get("subitens") or []:
            classe = "paragrafo" if sub.get("tipo") == "paragrafo" else "inciso"
            corpo_artigo.append(
                f'<div class="minuta-{classe}">'
                f'<span class="minuta-rotulo">{_e(sub.get("rotulo"))}</span> '
                f'<span class="minuta-texto">{_e(sub.get("texto"))}</span>'
                "</div>"
            )
        corpo_artigo.append("</div>")
        p("".join(corpo_artigo))

    for fechamento in estructura.get("fechamento") or []:
        _par("fechamento", fechamento.get("rotulo"), fechamento.get("texto"))

    _par("localdata", estructura.get("local_data"))

    assinaturas = estructura.get("assinaturas") or []
    if assinaturas:
        blocos = []
        for ass in assinaturas:
            linha = []
            if ass.get("nome"):
                linha.append(f'<span class="minuta-nome">{_e(ass["nome"])}</span>')
            if ass.get("cargo"):
                linha.append(f'<span class="minuta-cargo">{_e(ass["cargo"])}</span>')
            blocos.append('<div class="minuta-assinatura">' + " ".join(linha) + "</div>")
        p('<div class="minuta-assinaturas">' + "".join(blocos) + "</div>")

    return '<div class="minuta-documento">' + "".join(partes) + "</div>"