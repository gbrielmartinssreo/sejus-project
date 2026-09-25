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

from sejus_project.tools.llm_tools import analise_formatacao

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
        # Conclusão de conformidade no formato "Rótulo: ok/adequadas/correta."
        # (sem verbo — ex.: "Competência e assinaturas: adequadas."). É
        # constatação, nunca tarefa.
        r"^[^:]{1,80}:\s*(?:ok\b|adequad[oa]s?\b|corret[oa]s?\b|conformes?\b|"
        r"consistente\b|coerente\b|de\s+acordo\b|regular(?:es)?\b|"
        r"dentro\s+dos\s+padr[õo]es)\s*[.;]?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(est[áa]|est[ãa]o|é|s[ãa]o|foi|foram)\b.{0,40}\b("
        r"consistente|conformidade|correta?|corret[oa]s?|adequad[oa]s?|"
        r"de\s+acordo|regulares?|ok|coerente|precisa|clara)\b",
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

# Falas de abertura/encerramento e resumos do próprio analista: descrevem o
# relatório, não pedem alteração no ato. Nunca viram apontamento.
_RE_INTRO_CONCLUSAO = re.compile(
    r"(^\s*(?:aqui\s+est[áa]|segue\s+(?:a|o)\s|esta\s+[ée]\s+a|"
    r"conclus[ãa]o\b|em\s+s[íi]ntese\b|resumo\b|s[íi]ntese\b)|"
    r"crit[ée]rios\s+de\s+revis|"
    r"\bo\s+documento\s+(?:est[áa]|foi|permanece|encontra).{0,240}"
    r"\brecomenda-?se\b)",
    re.IGNORECASE,
)

# Constatações de conformidade/elogio estrutural que citam 'critérios',
# 'previsão' ou resultado positivo sem pedir mudança concreta.
_RE_ELOGIO_ESTRUTURAL = re.compile(
    r"\b(estabelece\s+crit[ée]rios|previs[ãa]o\s+detalhada|"
    r"previs[ãa]o\s+de\s+crit[ée]rios|promovendo\s+(?:transpar[êe]ncia|"
    r"seguran[çc]a\s+jur[íi]dica)|estrutura\s+(?:clara|organizada)|"
    r"fundamenta[çc][ãa]o\s+legal\s+adequada|"
    r"est[áa]\s+bem\s+(?:estruturad|fundamentad)|"
    r"crit[ée]rios\s+objetivos)\b",
    re.IGNORECASE,
)

# Instrução corretiva EXPLÍCITA (imperativo/infinitivo ou marcador de alerta).
# Diferente de _RE_CORRECAO_FORTE, ignora gerúndios/particípios soltos
# ('incluindo', 'detalhada') que aparecem em elogios.
_RE_CORRECAO_IMPERATIVA = re.compile(
    r"(⚠|\b(corrija|ajuste|consert|arrume|refa[çz]a|retifique|inclua|"
    r"insira|acrescente|preveja|retire|remova|exclua|substitua|troque|"
    r"atualize|complemente|detalhe|padronize|uniformize|renumere|reordene|"
    r"reorganize|esclare[çc]a|explicite|harmonize|garanta|contemple|"
    r"recomenda-?se|sugere-?se)\w*)",
    re.IGNORECASE,
)

# Verbos/expressões que indicam uma instrução de alteração FORTE (não são,
# por si, elogio). Usado para não descartar um item que aponta algo a fazer.
_RE_CORRECAO_FORTE = re.compile(
    r"\b(corrig|corrija|ajust|consert|arrum|refa|retific|inclu|inser|"
    r"acrescent|adicion(?!al)|prever|preveja|retir|remov|exclu|substitu|troq|"
    r"trocar|atualiz|complement|detalh|padroniz|uniformiz|recomend|suger|"
    r"sugest|falta|ausente|omiss|inconsist|diverg|incongru|redund|amb[íi]gu|"
    r"imprecis|equ[íi]voc|incorret|pendente|observ|garant|contempl|deve|"
    r"devem|necess[áa]ri|n[ãa]o\s+consta|n[ãa]o\s+prev|renumer|reorden|"
    r"reorganiz|sequenci|esclarec|clarific|harmoniz|explicit|correlacion|"
    r"reformul|reescrev|reelabor|reconstru|readequ)\w*",
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
    r"devem|necess[áa]ri|n[ãa]o\s+consta|n[ãa]o\s+prev|adequ|alter|revis|"
    r"renumer|reorden|reorganiz|sequenci|esclarec|clarific|harmoniz|"
    r"explicit|correlacion|reformul|reescrev|reelabor|reconstru|readequ)\w*",
    re.IGNORECASE,
)

# Promessas de análise, ofertas e falas de processo NUNCA são apontamento:
# não pedem alteração no documento (ex.: "vou proceder", "posso detalhar",
# "se desejar", "relatório formal", "seguir com as correções").
_RE_PROMESSA = re.compile(
    r"(\b(vou|irei|farei|proceder|procederei|aguarde|aguardar|"
    r"j[áa]\s+retorno|retorno\s+com|posso\s+(?:detalhar|preparar|produzir|"
    r"fazer|gerar|aprofundar|seguir|comparar)|poderia\s+(?:detalhar|"
    r"preparar|produzir|fazer|gerar)|gostaria\s+que|deseja\s+(?:que|"
    r"seguir|prosseguir|detalhar|o\s+relat[óo]rio)|quer\s+que\s+eu|"
    r"prefere\s+que|se\s+desejar|relat[óo]rio\s+formal|"
    r"produzir\s+um\s+arquivo|sugest[õo]es\s+revisadas|"
    r"seguir\s+com\s+as\s+corre[çc][õo]es|fico\s+no\s+aguardo|me\s+avise|"
    r"posso\s+preparar|segue\s+a\s+an[áa]lise|segue\s+o\s+relat[óo]rio)\b)",
    re.IGNORECASE,
)

_RE_PERGUNTA = re.compile(
    r"^\s*(?:deseja|quer|gostaria|poderia|pode|posso|como\s+prefere|"
    r"prefere|confirma|qual|quais|quando|onde)\b",
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
    texto = (item or "").strip()
    if not texto:
        return False
    # Perguntas, promessas de análise e ofertas não são instruções de alteração.
    if texto.endswith("?") or _RE_PERGUNTA.search(texto):
        return False
    if _RE_PROMESSA.search(texto):
        return False
    if _RE_INTRO_CONCLUSAO.search(texto):
        return False
    if any(padrao.search(texto) for padrao in _RE_SEM_ACAO):
        return False
    # Elogio estrutural ('estabelece critérios objetivos', 'previsão detalhada',
    # 'promovendo transparência') só vale se trouxer instrução corretiva
    # explícita — caso contrário é constatação, não tarefa.
    if _RE_ELOGIO_ESTRUTURAL.search(texto) and not (
        _RE_CORRECAO_IMPERATIVA.search(texto)
    ):
        return False
    if any(padrao.search(texto) for padrao in _RE_CONFORMIDADE) and not (
        _RE_CORRECAO_FORTE.search(texto)
    ):
        return False
    # Veto de classificação: só item classificado como PROBLEMA pelo mesmo
    # classificador da exibição vira tarefa. Elogio ("ementa bem definida"),
    # descrição positiva do ato ("prevê a prestação de relatórios") e pedido de
    # validação ("confirmar se o prazo é suficiente") aparecem na análise, mas
    # não são correções — não podem virar apontamento acionável.
    if not analise_formatacao.eh_tarefa_de_correcao(texto):
        return False
    return bool(_RE_ACIONAVEL.search(texto))


def _id_apontamento(item: str) -> str:
    base = _normalizar(item).casefold()
    return "ap-" + hashlib.sha1(base.encode("utf-8", "ignore")).hexdigest()[:8]


def extrair_apontamentos(analise: str, origem: str | None = None) -> list[dict]:
    """Apontamentos acionáveis (com ID estável) contidos na análise.

    Constatações de conformidade, elogios, promessas de análise e perguntas
    ficam de fora. Se a análise não apontar nenhuma alteração, a lista vem
    vazia (não se inventa correção). ``origem`` identifica de qual análise/
    aprofundamento o apontamento veio (preservada ao consolidar)."""
    apontamentos: list[dict] = []
    vistos: set[str] = set()
    for item in _agrupar_itens(analise):
        if not _e_acionavel(item):
            continue
        identificador = _id_apontamento(item)
        if identificador in vistos:
            continue
        vistos.add(identificador)
        apontamento = {"id": identificador, "texto": item}
        if origem:
            apontamento["origem"] = origem
        apontamentos.append(apontamento)
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
    origem: str | None = None,
) -> dict:
    """Registra/atualiza a análise de ``arquivo`` na ``sessao``.

    Mesmo documento e mesma versão (sha1): acumula apontamentos por ID, para
    preservar o vínculo em análises feitas ao longo de vários turnos. Versão
    diferente (documento alterado): substitui pela análise nova. ``origem``
    marca de qual análise/aprofundamento cada apontamento veio."""
    if apontamentos is None:
        apontamentos = extrair_apontamentos(analise_completa, origem)

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