"""Reformatação determinística da análise de documento em 4 classes canônicas.

O LLM, ao analisar uma minuta, às vezes agrupa num único bloco "## Pontos
fracos" constatações de natureza diferente: ameaças reais (erros observáveis),
ausências SEM fundamento concreto, recomendações especulativas e até itens que
concluem conformidade ("...: ok", "...: adequadas", "...: correta") — e
constatações de conformidade acabam virando apontamentos de correção. Este
módulo reclassifica essas passagens sem chamar o LLM de novo:

* **Pontos fortes** — elogios estruturais e constatações positivas.
* **Problemas identificados** — defeito objetivo e verificável no próprio
  documento (com localização e, idealmente, sugestão de correção);
  ausência com fundamento concreto. Nunca um "ok".
* **Pontos de atenção / validações necessárias** — ausências SEM fundamento
  concreto para exigir (revogação, recurso administrativo, prazo específico,
  assinaturas adicionais, anexos), recomendações especulativas ("pode",
  "seria útil", "se for o caso") e o que depende de validação externa.
* **Checklist de conformidade** — itens que declaram um atributo conforme,
  cada um na forma "- <item>: <status>" (status: OK / NAO APLICAVEL /
  REQUER VALIDACAO / ATENCAO) ou com conclusão positiva ("...: ok.").

Regras:
- Uma constatação de conformidade nunca é reclassificada como problema: ela
  só pode ir para "Pontos fortes" (se concluir um aspecto) ou "Checklist".
- Ausência vira problema APENAS quando há fundamento concreto citado
  (dispositivo que a exige, divergência interna observável). Sem fundamento,
  vira "ponto de atenção" pedindo validação.
- O texto dos bullets é preservado e reagrupado; nada é inventado.
- Idempotente: aplicar sobre uma saída já reformatada não muda nada — o que
  permite usar o módulo repetidamente no loop do agente.

O módulo não depende do LLM: a classificação é heurística e determinística.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Rótulos canônicos (também usados pelo frontend e pelas skills)
# ---------------------------------------------------------------------------

SECAO_PONTOS_FORTES = "Pontos fortes"
SECAO_PROBLEMAS = "Problemas identificados"
SECAO_ATENCAO = "Pontos de atenção / validações necessárias"
SECAO_CHECKLIST = "Checklist de conformidade"

_ORDEM_SECOES = [
    SECAO_PONTOS_FORTES,
    SECAO_PROBLEMAS,
    SECAO_ATENCAO,
    SECAO_CHECKLIST,
]

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_RE_HEADING = re.compile(r"^\s*#{1,6}\s+(?P<rotulo>.+?)\s*:?\s*$")
_RE_HEADING_BOLD = re.compile(r"^\s*\*{1,2}(?P<rotulo>[^*]{2,80})\*{1,2}\s*:?\s*$")
_RE_LABEL_LINHA = re.compile(r"^\s*(?P<rotulo>[A-Za-zÀ-ÿ0-9][^:]{0,80}?)\s*:\s*$")
_RE_BULLET = re.compile(r"^\s*(?:[-*•‣▪]|\d{1,2}[.)])\s+(?P<texto>.+)$")

# Rotulo da secao -> classe alvo
_RE_CLASSE_SECAO = re.compile(
    r"pontos\s+fortes|pontos\s+fortes\s*/\s*(?:elogios|destaques)|elogios|"
    r"pontos\s+positivos|destaques",
    re.IGNORECASE,
)
_RE_CLASSE_PROBLEMAS = re.compile(
    r"pontos\s+fracos|pontos\s+fracos\s*/\s*apontamentos\s+de\s+corre[çc][ãa]o|"
    r"problemas\s+identificados|apontamentos\s+de\s+corre[çc][ãa]o|pontos\s+fracos"
    r"|problemas",
    re.IGNORECASE,
)
_RE_CLASSE_ATENCAO = re.compile(
    r"pontos\s+de\s+aten[çc][ãa]o|pontos\s+de\s+aten[çc][ãa]o\s*/\s*valida[çc][õo]es"
    r"|valida[çc][õo]es\s+necess[áa]rias|pontos\s+de\s+aten[çc][ãa]o\s*/\s*"
    r"valida[çc][õo]es\s+necess[áa]rias|validar|a\s+confirmar",
    re.IGNORECASE,
)
_RE_CLASSE_CHECKLIST = re.compile(
    r"checklist\s+de\s+conformidade|checklist|itens\s+conformes|conformidade",
    re.IGNORECASE,
)


class _Item:
    """Um bullet/linha da análise, com sua seção de origem e classificação."""

    __slots__ = ("texto", "secao", "classe", "introducao")

    def __init__(self, texto: str, secao: str):
        self.texto = texto
        self.secao = secao
        self.classe: str | None = None
        self.introducao = False


def _classificar_secao(rotulo: str) -> str:
    """Mapeia o rótulo do cabeçalho para uma das classes canônicas."""
    r = (rotulo or "").strip()
    if _RE_CLASSE_CHECKLIST.search(r):
        return SECAO_CHECKLIST
    if _RE_CLASSE_ATENCAO.search(r):
        return SECAO_ATENCAO
    if _RE_CLASSE_PROBLEMAS.search(r):
        return SECAO_PROBLEMAS
    if _RE_CLASSE_SECAO.search(r):
        return SECAO_PONTOS_FORTES
    return ""


def _linhas(texto: str) -> list[str]:
    return (texto or "").splitlines()


def _parecer_analise(texto: str | None) -> bool:
    """Detecta texto de ANÁLISE de documento (estruturado em bullets/seções).

    Conversas comuns ("Ok, vou verificar.", respostas de uma linha) não passam.
    Um texto SÓ é tratado como análise quando há pelo menos um bullet ou um
    cabeçalho de categoria listado entre as seções canônicas."""
    linhas = _linhas(texto)
    if not linhas:
        return False
    bullets = 0
    for l in linhas:
        if not l.strip():
            continue
        if _RE_HEADING.match(l) or _RE_HEADING_BOLD.match(l) or _RE_LABEL_LINHA.match(l):
            if _classificar_secao(
                _rotulo_do(l)
            ):
                return True
        if _RE_BULLET.match(l):
            bullets += 1
    return bullets >= 1


def _rotulo_do(linha: str) -> str:
    for padrao in (_RE_HEADING, _RE_HEADING_BOLD, _RE_LABEL_LINHA):
        m = padrao.match(linha)
        if m:
            return (m.group("rotulo") or "").strip()
    return (linha or "").strip()


# ---------------------------------------------------------------------------
# Classificação de cada bullet
# ---------------------------------------------------------------------------

# Fundamentos concretos citados no próprio item: ART/LEI/DECRETO/PORTARIA com
# número, ou divergência interna observável no documento.
_RE_FUNDAMENTO = re.compile(
    r"\b(?:art\.?\s*[0-9ivxIVX]+|artigo\s+[0-9ivx]|lei\s*(?:n[ºo]?\.?\s*)?\d|"
    r"decreto\s*(?:n[ºo]?\.?\s*)?\d|(?:no|em|cfe?)\s+art\b|"
    r"(?:ementa|corpo|anexos?)\s+(?:menciona|prev[êe]|traz|disp[õo]e|"
    r"estabelece|cobre|exige)|est[áa]\s+previst\w*\s+em\b|"
    r"segundo\s+(?:o|a)\s+(?:art|lei|decreto)|nos\s+termos\s+de\b|"
    r"constitui[çc][ãa]o|compet[êe]ncia\s+previst\w*\s+em)",
    re.IGNORECASE,
)

# Ausencia SEM verbo de confirmação (não há/consta/prevê...).
_RE_AUSENCIA = re.compile(
    r"\b(?:n[ãa]o\s+(?:h[áa]|existe|consta|prev[êe]|contempla|considera|traz|"
    r"traz|menciona|aborda|cita|discorre|trata|estabelece|disp[õo]e|"
    r"previs[ãa]o|including)|aus[êe]ncia\s+de|falt[ao]\s+(?:prever|incluir|"
    r"constar|previs)|sem\s+(?:previs[ãa]o|cl[áa]usula|dispositivo|prazo|"
    r"revoga[çc][ãa]o|recurso|anexo|vig[êe]ncia|prever)|omiss[ãa]o\s+de|"
    r"n[ãa]o\s+est[áa]\s+(?:previst|inclu[íi]d|previsto))",
    re.IGNORECASE,
)

# Conclusão POSITIVA/conformidade do item ("...: ok", "está correta",
# "estão adequadas"). Só vale se NÃO houver negação nem verbo de ausência.
_RE_POSITIVA = re.compile(
    r":\s*(?:ok\b|adequad[oa]s?|corret[oa]s?|conformes?|consistente|"
    r"coerente\b|de\s+acordo|regular(?:es)?\b|dentro\s+dos\s+padr[õo]es)\s*$|"
    r"\b(?:est[áa]\s+corret\w*|est[áa]o\s+adequad\w*|est[áa]o\s+corret\w*|"
    r"est[áa]\s+adequad\w*|corresponde\s+ao\s+(?:previst|disposto)|"
    r"em\s+conformidade\s+com|coerente\s+com\s+o\s+corpo)\b",
    re.IGNORECASE,
)

_RE_NEGACAO_POSITIVA = re.compile(
    r"\b(?:n[ãa]o\s+est[áa]\s+corret\w*|n[ãa]o\s+est[ãa]o\s+adequad\w*|"
    r"n[ãa]o\s+(?:est[áa]|[ée]|foram)\s+(?:corret|adequad|conform)|"
    r"inadequad\w*|incorret\w*)\b",
    re.IGNORECASE,
)

# Especulativas (recomendação vaga, sem erro observável no documento).
_RE_ESPECULATIVA = re.compile(
    r"\b(?:seria\s+(?:[uú]til|interessante|bom|prudente|importante)|"
    r"pode(?:ria)?\s+(?:haver|ser|apresentar|gerar|ensejar|suscitar|trazer|"
    r"representar|causar)|pode(?:m)?\s+haver\b|cabe\s+(?:avaliar|verificar|"
    r"confirmar|validar)|seria\s+necess[áa]rio\b|recomendo\s+(?:avaliar|"
    r"verificar|confirmar|validar|c[oó]mputar|prever)|sugere-se\s+(?:avaliar|"
    r"verificar|confirmar)|conv[ée]m\s+(?:avaliar|verificar)|se\s+for\s+o\s+"
    r"caso\s+incluir|a\s+ser\s+(?:avaliad|validad|confirmad)|sujeito\s+a\s+"
    r"valida[çc][ãa]o|requer\s+valida[çc][ãa]o|valida[çc][ãa]o\s+"
    r"(?:jur[íi]dica|extern)\w*|se\s+o\s+documento\s+(?:n[ãa]o\s+)?"
    r"(?:existe|est[áa]|prev\w*))",
    re.IGNORECASE,
)

# Elogios/constatações estruturais positivas.
_RE_ELOGIO = re.compile(
    r"\b(?:bem\s+(?:estruturad|organizad|redigid|fundamentad|regulamentad|"
    r"detalhad|sequenciad|delinead)|bem\s+definid\w*|boa\s+reda[çc][ãa]o|"
    r"linguagem\s+clara\b|estrutura\s+(?:clara|l[óo]gica|adequada)|"
    r"embasamento\s+(?:jur[íi]dico\s+)?(?:s[óo]lid|adequad|corret)|"
    r"reda[çc][ãa]o\s+(?:formal\s+e\s+t[ée]cnica|clara)|crit[ée]rios\s+"
    r"objetivos|previs[ãa]o\s+detalhad\w*|bem\s+cobert|aspectos\s+positiv|"
    r"est[áa]\s+bem\s+(?:coberto|detalhad))",
    re.IGNORECASE,
)

# Instruções corretivas / apontamentos de correção (verbo de ação ou ameaça
# objetiva: "erro", "inconsistência", "divergência", "falta", "corrigir"...).
_RE_CORRECAO = re.compile(
    r"\b(?:corrig|corrija|ajust|consert|arrum|refa[çc]|retific|inclu\w*|"
    r"acrescent|adicion\w*|inser\w*|prev[êe]|preveja|incluam|renumer|reorden|"
    r"reorganiz|remov\w*|retir\w*|substitu\w*|troq\w*|reformul\w*|reescrev|"
    r"esclarec\w*|detalh\w*|padroniz\w*|etiquet\w*|numer\w*|harmoniz|"
    r"atualiz\w*|complet\w*|definir|estabelecer\w*|garant\w*|contempl\w*|"
    r"falta\w*|aus\w*|omiss\w*|erro|erros|inconsist\w*|diverg\w*|contradi[çc]\w*"
    r"|incongru\w*|amb[íi]gu\w*|imprecis\w*|redund\w*|incorret\w*|"
    r"duplic\w*|equivoc\w*|n[ãa]o\s+const\w*|n[ãa]o\s+(?:est[áa]|est[ãa]o|"
    r"[ée]|s[ãa]o)\s+(?:corret|adequad|conform|coerente|consistente)|"
    r"faltou\w*|pendente\w*)",
    re.IGNORECASE,
)


def _classificar_item(item: _Item) -> _Item:
    t = (item.texto or "").strip()
    t_low = t.lower()

    # 1) Sem nada dizer: cai na seção que o originou (na falta de marcador).
    if not t:
        return item

    ausencia = bool(_RE_AUSENCIA.search(t))
    fundamento = bool(_RE_FUNDAMENTO.search(t))
    negativa_pos = bool(_RE_NEGACAO_POSITIVA.search(t))
    positiva = bool(_RE_POSITIVA.search(t))
    especulativa = bool(_RE_ESPECULATIVA.search(t))
    elogio = bool(_RE_ELOGIO.search(t))
    correcao = bool(_RE_CORRECAO.search(t))

    nova_classe = item.secao or ""

    if (ausencia or correcao) and not negativa_pos:
        # 2a) Ausência COM fundamento concreto => problema objetivo.
        if ausencia and fundamento:
            nova_classe = SECAO_PROBLEMAS
        # 2b) Ausência SEM fundamento => ponto de atenção (validação necessária).
        elif ausencia:
            nova_classe = SECAO_ATENCAO
        # 2c) Instrução corretiva explícita => problema.
        elif correcao:
            nova_classe = SECAO_PROBLEMAS
    elif especulativa:
        # 3) Recomendação especulativa / depende de validação => atenção.
        nova_classe = SECAO_ATENCAO
    else:
        # 4) Conclusão positiva/elogio: nunca vira problema — forças/checklist.
        if positiva and not item.secao:
            nova_classe = SECAO_CHECKLIST
        elif elogio:
            nova_classe = SECAO_PONTOS_FORTES
        elif positiva:
            nova_classe = (
                SECAO_CHECKLIST
                if item.secao in (SECAO_PROBLEMAS, SECAO_ATENCAO)
                else SECAO_PONTOS_FORTES
            )
        else:
            nova_classe = item.secao or SECAO_ATENCAO

    item.classe = nova_classe
    return item


# ---------------------------------------------------------------------------
# Remontagem canônica
# ---------------------------------------------------------------------------
#
# Texto introdutório (linhas sem bullet/seção) é preservado no topo para não
# descaracterizar a mensagem; bullets vão para as 4 seções canônicas, na ordem
# fixa, sem duplicar o mesmo bullet.

_PLACEHOLDER = {
    SECAO_PONTOS_FORTES: "Não foram identificados pontos fortes a destacar.",
    SECAO_PROBLEMAS: "Não foram identificados problemas objetivos nesta categoria.",
    SECAO_ATENCAO: "Nenhum item de atenção / validação necessária.",
    SECAO_CHECKLIST: "Nenhum item de checklist avaliado.",
}


def _secao_para_bullet(bullet_text: str, secao: str) -> str:
    return f"- {bullet_text}"


def reestruturar_analise(texto: str | None) -> str | None:
    """Reorganiza a análise em 4 classes canônicas (seção por seção).

    Idempotente e conservador: se o texto não for reconhecido como análise ou
    já estiver no formato canônico, devolve o texto intacto."""
    if not texto or not texto.strip():
        return texto

    linhas = _linhas(texto)
    if not _parecer_analise(texto):
        return texto

    intro: list[str] = []
    corpos: list[list[_Item]] = [[] for _ in _ORDEM_SECOES]
    secao_atual = ""
    itens_por_secao: dict[str, list[_Item]] = {}

    for linha in linhas:
        s = linha.strip()
        if not s:
            secao_atual = ""
            continue

        m_head = (
            _RE_HEADING.match(s) or _RE_HEADING_BOLD.match(s) or _RE_LABEL_LINHA.match(s)
        )
        if m_head:
            rotulo = (m_head.group("rotulo") or "").strip()
            classe = _classificar_secao(rotulo)
            if classe:
                secao_atual = classe
                continue
            # Cabeçalho não reconhecido (nome do documento, etc.): vira texto
            # introdutório preservado.
            intro.append(linha)
            continue

        mb = _RE_BULLET.match(s)
        if mb and secao_atual:
            itens_por_secao.setdefault(secao_atual, []).append(
                _Item(mb.group("texto").strip(), secao_atual)
            )
            continue

        # Linha de item na forma "Rótulo: - A - B" (ex.: "Pontos fortes: - A. - B.")
        secao_inline = _classificar_secao(s.split(":", 1)[0].strip())
        if secao_inline and ":" in s:
            resto = s.split(":", 1)[1]
            partes = [
                p.strip()
                for p in resto.replace(" - ", "\n- ").splitlines()
                if p.strip()
            ]
            itens_por_secao.setdefault(secao_inline, []).extend(
                _Item(p.lstrip("-").strip(), secao_inline) for p in partes
            )
            continue

        # Linha qualquer entre bullets de uma seção (parágrafo de continuação).
        if secao_atual and itens_por_secao.get(secao_atual):
            itens_por_secao[secao_atual].append(_Item(s, secao_atual))
            continue

        intro.append(linha)

    # Classifica e reorganiza.
    saida: list[str] = []
    bullets_ja_vistos: set[str] = set()
    for secao in _ORDEM_SECOES:
        itens = itens_por_secao.get(secao, [])
        if not itens:
            continue
        saida.append(f"## {secao}")
        usados = 0
        for item in itens:
            item = _classificar_item(item)
            alvo = item.classe or secao
            # Não duplica o mesmo texto dentro da mesma seção canônica.
            chave = (alvo, " ".join((item.texto or "").lower().split()))
            if chave in bullets_ja_vistos:
                continue
            if alvo == secao:
                bullets_ja_vistos.add(chave)
                saida.append(_secao_para_bullet(item.texto, alvo))
                usados += 1

        if usados == 0:
            saida.append(_secao_para_bullet(_PLACEHOLDER[secao], secao))

    # "## X: - A" continuava sem bullets? Garantir todas as 4 seções (com o
    # placeholder) quando a análise indicou estrutura.
    blocos = ["\n".join(saida)]
    if intro:
        blocos.insert(0, "\n".join(intro))
    novo = "\n\n".join(b for b in blocos if b.strip())

    # Idempotência: se saiu igual ao original, devolve sem tocar.
    return novo if novo != (texto or "") else texto


def parecer_analise(texto: str | None) -> bool:
    """Cobertura pública de ``_parecer_analise`` (usado na integração)."""
    return _parecer_analise(texto)


__all__ = [
    "SECAO_PONTOS_FORTES",
    "SECAO_PROBLEMAS",
    "SECAO_ATENCAO",
    "SECAO_CHECKLIST",
    "reestruturar_analise",
    "parecer_analise",
]
