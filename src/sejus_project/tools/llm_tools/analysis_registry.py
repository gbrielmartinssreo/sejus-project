"""Registro de análises por sessão e documento, com identificação da versão.

Guarda, por ``(sessão, arquivo)``, a análise COMPLETA produzida pelo agente
(contexto) e, separadamente, os apontamentos ACIONÁVEIS extraídos dela, cada
um com ID estável derivado do próprio texto. O estado é versionado pelo hash
do conteúdo do documento analisado, de modo que a correção posterior use
exatamente a análise da versão analisada — nunca a de outro documento nem de
uma versão diferente.

Elogios e constatações de conformidade permanecem apenas na análise completa;
não viram apontamentos (não são instruções de alteração). Análises feitas ao
longo de vários turnos são acumuladas por ID, preservando o vínculo com o
documento realmente analisado.

O módulo não chama LLM: a extração é determinística (heurística de linguagem).
"""
from __future__ import annotations

import hashlib
import re

_SESSAO_ATUAL = 0
_ORDEM = 0

# (sessao, arquivo_normalizado) -> entrada de análise
_ANALISES: dict[tuple[int, str], dict] = {}


def _proxima_ordem() -> int:
    global _ORDEM
    _ORDEM += 1
    return _ORDEM


def hash_conteudo(texto: str) -> str:
    """Hash estável do conteúdo do documento (identifica a versão analisada)."""
    return hashlib.sha1((texto or "").encode("utf-8", "ignore")).hexdigest()


def nova_sessao() -> int:
    """Abre uma nova sessão e descarta as análises das sessões anteriores."""
    global _SESSAO_ATUAL
    _SESSAO_ATUAL += 1
    _ANALISES.clear()
    return _SESSAO_ATUAL


def sessao_atual() -> int:
    """ID da sessão corrente (inicializa em 1 se ainda não houver)."""
    global _SESSAO_ATUAL
    if _SESSAO_ATUAL <= 0:
        _SESSAO_ATUAL = 1
    return _SESSAO_ATUAL


def _chave(sessao: int, arquivo: str) -> tuple[int, str]:
    return (sessao, (arquivo or "").strip().casefold())


# ---------------------------------------------------------------------------
# Extração determinística de apontamentos acionáveis
# ---------------------------------------------------------------------------

_RE_BULLET = re.compile(r"^\s*(?:[-*•‣–]|\d+[.)])\s+(?P<texto>.+)$")
_RE_HEADING = re.compile(r"^\s*#{1,6}\s+")

# Constatações que NUNCA são instrução de alteração (mesmo que citem "erro").
_RE_SEM_ACAO = [
    re.compile(
        r"n[ãa]o\s+(?:detectei|foram?\s+(?:encontrad|identific)|h[áa]|"
        r"consta[m]?|se\s+(?:detect|encontr|identific))\w*.{0,40}"
        r"(erro|problema|inconsist|diverg|irregularidade|falha)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:nada\s+a|sem)\s+(?:corrigir|alterar|ajustar|observar)\b",
        re.IGNORECASE,
    ),
]

# Elogios/constatações estruturais: só são descartados se não houver verbo de
# ação no item (um item pode elogiar E apontar algo a fazer).
_RE_CONFORMIDADE = [
    re.compile(
        r"\b(est[áa]|est[ãa]o|é|s[ãa]o|foi|foram)\b.{0,40}\b("
        r"consistente|conformidade|correta?|corret[oa]s?|adequad[oa]s?|"
        r"de acordo|regulares?|ok|coerente|precisa|clara)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(boa\s+reda[çc][ãa]o|reda[çc][ãa]o\s+(?:formal|t[ée]cnica)|"
        r"linguagem\s+clara|bem\s+delinead|bem\s+regulamentad|est[áa]\s+bem)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(formata[çc][ãa]o|reda[çc][ãa]o|numera[çc][ãa]o|linguagem|texto|"
        r"estrutura|sistem[áa]tica|fundamenta[çc][ãa]o)\b.{0,60}\b("
        r"adequada|correta|clara|precisa|consistente|coerente|regular|"
        r"de acordo|conforme)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\brefer[êe]ncia\s+correta\b", re.IGNORECASE),
    re.compile(r"\bcorresponde\s+aos\s+requisitos\b", re.IGNORECASE),
]

# Verbos/expressões que indicam uma instrução de alteração FORTE (não são,
# por si, elogio). Usado para não descartar um item que aponta algo a fazer.
_RE_CORRECAO_FORTE = re.compile(
    r"\b(corrig|corrija|ajust|consert|arrum|refa|retific|inclu|inser|"
    r"acrescent|adicion(?!al)|prever|preveja|retir|remov|exclu|substitu|troq|"
    r"trocar|atualiz|complement|detalh|padroniz|uniformiz|recomend|suger|"
    r"sugest|falta|ausente|omiss|inconsist|diverg|incongru|redund|amb[íi]gu|"
    r"imprecis|equ[íi]voc|incorret|pendente|observ|garant|contempl|deve|"
    r"devem|necess[áa]ri|n[ãa]o\s+consta|n[ãa]o\s+prev)\w*",
    re.IGNORECASE,
)

# Instrução de alteração em sentido amplo (inclui verbos que também aparecem em
# elogios, como 'adequada'/'alterada' — só valem se não forem conformidade).
_RE_ACIONAVEL = re.compile(
    r"\b(corrig|corrija|ajust|consert|arrum|refa|retific|inclu|inser|"
    r"acrescent|adicion(?!al)|prever|preveja|retir|remov|exclu|substitu|troq|"
    r"trocar|atualiz|complement|detalh|padroniz|uniformiz|recomend|suger|"
    r"sugest|falta|ausente|omiss|inconsist|diverg|incongru|redund|amb[íi]gu|"
    r"imprecis|equ[íi]voc|incorret|pendente|observ|garant|contempl|deve|"
    r"devem|necess[áa]ri|n[ãa]o\s+consta|n[ãa]o\s+prev|adequ|alter|revis)\w*",
    re.IGNORECASE,
)


def _normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", (texto or "").strip())


def _agrupar_itens(analise: str) -> list[str]:
    """Divide a análise em itens (bullets com continuação ou parágrafos).

    Uma linha sem marcador só continua o item anterior se estiver indentada;
    caso contrário, inicia um item novo (evita colar um parágrafo posterior ao
    bullet e mudar o ID do apontamento)."""
    itens: list[str] = []
    atual: list[str] = []

    def _fechar() -> None:
        if atual:
            itens.append(_normalizar(" ".join(atual)))
            atual.clear()

    for linha in (analise or "").splitlines():
        if not linha.strip():
            _fechar()
            continue
        if _RE_HEADING.match(linha):
            _fechar()
            continue
        m = _RE_BULLET.match(linha)
        if m:
            _fechar()
            atual.append(m.group("texto"))
            continue
        if atual and linha[:1] in (" ", "\t"):
            atual.append(linha.strip())
            continue
        _fechar()
        atual.append(linha.strip())
    _fechar()
    return [i for i in itens if i]


def _e_acionavel(item: str) -> bool:
    if any(padrao.search(item) for padrao in _RE_SEM_ACAO):
        return False
    if any(padrao.search(item) for padrao in _RE_CONFORMIDADE) and not (
        _RE_CORRECAO_FORTE.search(item)
    ):
        return False
    return bool(_RE_ACIONAVEL.search(item))


def _id_apontamento(item: str) -> str:
    base = _normalizar(item).casefold()
    return "ap-" + hashlib.sha1(base.encode("utf-8", "ignore")).hexdigest()[:8]


def extrair_apontamentos(analise: str) -> list[dict]:
    """Apontamentos acionáveis (com ID estável) contidos na análise.

    Constatações de conformidade e elogios ficam de fora. Se a análise não
    apontar nenhuma alteração, a lista vem vazia (não se inventa correção).
    """
    apontamentos: list[dict] = []
    vistos: set[str] = set()
    for item in _agrupar_itens(analise):
        if not _e_acionavel(item):
            continue
        identificador = _id_apontamento(item)
        if identificador in vistos:
            continue
        vistos.add(identificador)
        apontamentos.append({"id": identificador, "texto": item})
    return apontamentos


# ---------------------------------------------------------------------------
# Armazenamento por sessão + documento + versão
# ---------------------------------------------------------------------------


def registrar(
    sessao: int,
    arquivo: str,
    sha1: str,
    analise_completa: str,
    apontamentos: list[dict] | None = None,
) -> dict:
    """Registra/atualiza a análise de ``arquivo`` na ``sessao``.

    Mesmo documento e mesma versão (sha1): acumula apontamentos por ID, para
    preservar o vínculo em análises feitas ao longo de vários turnos. Versão
    diferente (documento alterado): substitui pela análise nova.
    """
    if apontamentos is None:
        apontamentos = extrair_apontamentos(analise_completa)

    chave = _chave(sessao, arquivo)
    entrada = _ANALISES.get(chave)

    if entrada is not None and entrada.get("sha1") == sha1:
        existentes = {a["id"]: a for a in entrada.get("apontamentos") or []}
        for apontamento in apontamentos:
            existentes.setdefault(apontamento["id"], apontamento)
        entrada["apontamentos"] = list(existentes.values())
        # Só substitui o texto-contexto quando a nova leitura trouxe apontamentos
        # ou quando ainda não havia análise completa — assim um turno de
        # acompanhamento (sem apontamentos) não apaga a análise já produzida.
        if (analise_completa or "").strip() and (
            apontamentos or not (entrada.get("analise_completa") or "").strip()
        ):
            entrada["analise_completa"] = analise_completa
        entrada["acumulada"] = True
        entrada["ordem"] = _proxima_ordem()
    else:
        entrada = {
            "arquivo": arquivo,
            "sessao": sessao,
            "sha1": sha1,
            "analise_completa": analise_completa,
            "apontamentos": list(
                {a["id"]: a for a in apontamentos}.values()
            ),
            "acumulada": False,
            "ordem": _proxima_ordem(),
        }
        _ANALISES[chave] = entrada

    return entrada


def obter(sessao: int, arquivo: str, sha1: str | None = None) -> dict | None:
    """Análise registrada para ``arquivo`` na ``sessao``.

    Quando ``sha1`` é informado, exige a MESMA versão do documento analisado;
    versão diferente devolve ``None`` para não aplicar uma análise desatualizada.
    """
    entrada = _ANALISES.get(_chave(sessao, arquivo))
    if entrada is None:
        return None
    if sha1 is not None and entrada.get("sha1") != sha1:
        return None
    return entrada


def limpar() -> None:
    """Descarta todas as análises (usado no reset da conversa)."""
    _ANALISES.clear()


def mais_recente(sessao: int) -> dict | None:
    """Análise registrada mais recentemente na sessão (qualquer documento)."""
    candidatos = [
        entrada
        for (sessao_entrada, _), entrada in _ANALISES.items()
        if sessao_entrada == sessao
    ]
    if not candidatos:
        return None
    return max(candidatos, key=lambda entrada: entrada.get("ordem", 0))