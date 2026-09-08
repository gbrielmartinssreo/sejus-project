"""Registro de modelos DOCX reais da SEJUS e selecao automatica por tipo de ato.

Cada modelo define um perfil declarativo: o arquivo a duplicar e as regex que
localizam, dentro do documento, os paragrafos de referencia de cada papel
(titulo, ementa, preambulo, considerando, resolutivo, artigo, paragrafo,
inciso, vigencia, data, assinatura). Esses paragrafos servem de molde de
formatacao para a montagem da minuta (ver docx_engine.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sejus_project.tools.docx_templates import PROJECT_ROOT, TEMPLATES_DIR

TEMPLATES_PLUS_DIR = PROJECT_ROOT / "docs" / "templates-plus"


@dataclass(frozen=True)
class PerfilModelo:
    name: str
    file: str
    act_types: tuple[str, ...]
    patterns: dict[str, str]


# ---------------------------------------------------------------------------
# Perfis
# ---------------------------------------------------------------------------

IN_FUNCAO_ARMADA = PerfilModelo(
    name="IN_FUNCAO_ARMADA",
    file=str(TEMPLATES_PLUS_DIR / "IN 001-2014_GAB-SEJUDH - FUNÇÃO ARMADA.docx"),
    act_types=("instrução normativa", "instrucao normativa"),
    patterns={
        "titulo": r"^instru[çc][aã]o normativa n[°º. ]",
        "ementa": r"^disp[oõ]es sobre",
        "preambulo": r"^\s*o secret[áa]rio de estado",
        "considerando": r"^\s*considerando",
        "resolutivo": r"^\s*resolv",
        "artigo": r"^\s*art\.?\s*\d",
        "paragrafo": r"^\s*(?:§\s*\d|par[áa]grafo)",
        "inciso": r"^\s*(?:[ivx]{1,3})\s*[-–—]",
        "vigencia": r"entra em vigor",
        "data": r"^\s*cuiab[áa]",
        "assinatura": r"secret[áa]rio de estado de justi[çc]a e direitos humanos",
    },
)

PORTARIA_CONJUNTA = PerfilModelo(
    name="PORTARIA_CONJUNTA",
    file=str(
        TEMPLATES_DIR
        / "arruma-manualmente"
        / "Portaria Conjunta nº 07.2026.GAB-SEJUS.FUNAC.MT - Grupo de trabalho para elaboração do manual POP (1)-1.docx"
    ),
    act_types=("portaria conjunta",),
    patterns={
        "titulo": r"^portaria conjunta n[°º. ]+\d",
        "ementa": r"^\s*disp[oõ]es sobre",
        "preambulo": r"^\s*o secret[áa]rio de estado de justi[çc]a",
        "considerando": r"^\s*considerando",
        "resolutivo": r"^\s*r\s*e\s*s\s*o\s*l\s*v\s*e\s*m",
        "artigo": r"^\s*art\.?\s*\d",
        "paragrafo": r"^\s*par[áa]grafo",
        "inciso": r"^\s*(?:[-–—]\s|(?:[ivx]{1,3})\s*[-–—])",
        "vigencia": r"entra em vigor",
        "data": r"cuiab[áa]",
        "assinatura": r"^\s*(?:winkler|jean carlos|luiz henrique|valter furtado)",
    },
)

RETIFICACAO = PerfilModelo(
    name="RETIFICACAO",
    file=str(
        TEMPLATES_DIR
        / "arruma-manualmente"
        / "Retificação Portaria nº 03.2026.GAB-SEJUS Lotacionograma 1º Trimestre. (1).docx"
    ),
    act_types=("retificação", "retificacao"),
    patterns={
        "titulo": r"^portaria\s*n[°º.]",
        "ementa": r"retifica",
        "preambulo": r"^\s*o secret[áa]rio de estado de justi[çc]a",
        "artigo": r"^\s*art\.?\s*\d",
        "vigencia": r"entra em vigor",
        "assinatura": r"valter furtado",
    },
)

PORTARIA = PerfilModelo(
    name="PORTARIA",
    file=str(
        TEMPLATES_DIR / "arruma-manualmente" / "Portaria 45 e 46.2025.GAB-SEJUS.MT.docx"
    ),
    act_types=("portaria",),
    patterns={
        "titulo": r"^portaria\s*n[°º. ]+\d",
        "ementa": r"^\s*(?:institui|designa|estabelece|disp[oõ]es)",
        "preambulo": r"^\s*o secret[áa]rio de estado de justi[çc]a",
        "considerando": r"^\s*considerando",
        "resolutivo": r"^\s*resolv",
        "artigo": r"^\s*art\.?\s*\d",
        "paragrafo": r"^\s*§\s*\d",
        "inciso": r"^\s*(?:[ivx]{1,3})\s*[-–—]",
        "vigencia": r"entra em vigor",
        "assinatura": r"secret[áa]rio de estado de justi[çc]a",
    },
)

DECRETO = PerfilModelo(
    name="DECRETO_LEGADO",
    file=str(TEMPLATES_DIR / "Template_Decreto.docx"),
    act_types=("decreto",),
    patterns={
        "titulo": r"^decreto n",
        "ementa": r"^disp[oõ]es sobre",
        "preambulo": r"^o governador do estado",
        "considerando": r"^considerando",
        "resolutivo": r"^\s*d\s*e\s*c\s*r\s*e\s*t\s*a",
        "artigo": r"^art\.?\s*\d",
        "vigencia": r"entra em vigor",
        "data": r"pal[áa]cio paiagu[áa]s",
        "assinatura": r"governador do estado",
    },
)

MODELOS = [
    IN_FUNCAO_ARMADA,
    PORTARIA_CONJUNTA,
    RETIFICACAO,
    PORTARIA,
    DECRETO,
]

# Ordem de deteccao: tipos mais especificos primeiro.
_ORDEM_TIPOS = [
    "instrução normativa",
    "instrucao normativa",
    "portaria conjunta",
    "retificação",
    "retificacao",
    "decreto",
    "portaria",
]

ACT_TYPE_FILTER = {
    "instrução normativa": "INSTRUÇÃO NORMATIVA",
    "instrucao normativa": "INSTRUÇÃO NORMATIVA",
    "portaria conjunta": "PORTARIA CONJUNTA",
    "retificação": "RETIFICAÇÃO",
    "retificacao": "RETIFICAÇÃO",
    "decreto": "DECRETO",
    "portaria": "PORTARIA",
}

_TIPO_PADRAO = "portaria"


def detectar_tipo_ato(pedido: str) -> str:
    """Detecta o tipo de ato mencionado no pedido do usuario."""
    normalized = pedido.casefold()
    for tipo in _ORDEM_TIPOS:
        if tipo in normalized:
            return tipo
    return _TIPO_PADRAO


def selecionar_modelo(tipo_ato: str) -> PerfilModelo:
    """Escolhe automaticamente o perfil/modelo adequado para o tipo de ato."""
    for modelo in MODELOS:
        if tipo_ato in modelo.act_types:
            return modelo
    return PORTARIA


def buscar_perfil(nome: str) -> PerfilModelo | None:
    """Busca um perfil pelo nome ou por trecho do caminho do arquivo."""
    nome_n = nome.casefold()
    for modelo in MODELOS:
        if modelo.name.casefold() == nome_n or nome_n in modelo.file.casefold():
            return modelo
    return None


def _normalizar_pedido(pedido: str) -> str:
    return re.sub(r"\s+", " ", pedido).strip()


def normalizar_ementa(texto: str) -> str:
    """Remove espacos extras que o LLM pode inserir nas strings."""
    return _normalizar_pedido(texto)