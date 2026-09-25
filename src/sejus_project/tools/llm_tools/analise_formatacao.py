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

# Chave interna: bullets que vieram de um cabeçalho NÃO canônico (ex.:
# "## Análise da IN"). Não é uma classe de saída — os itens são classificados
# pelo conteúdo.
_SECAO_SOLTA = "\x00soltos"

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_RE_HEADING = re.compile(r"^\s*#{1,6}\s+(?P<rotulo>.+?)\s*:?\s*$")
_RE_HEADING_BOLD = re.compile(r"^\s*\*{1,2}(?P<rotulo>[^*]{2,80})\*{1,2}\s*:?\s*$")
_RE_LABEL_LINHA = re.compile(r"^\s*(?P<rotulo>[A-Za-zÀ-ÿ0-9][^:]{0,80}?)\s*:\s*$")
# Rótulo de categoria solto na linha, sem "##" e sem ":" (o modelo às vezes
# escreve só "Pontos fortes"). Só vale se a linha inteira for o rótulo.
_RE_ROTULO_LIVRE = re.compile(r"^\s*\*{0,2}(?P<rotulo>[^#*\n]{2,60})\*{0,2}\s*$")
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

    __slots__ = ("classe", "introducao", "secao", "texto")

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
        if (
            _RE_HEADING.match(l)
            or _RE_HEADING_BOLD.match(l)
            or _RE_LABEL_LINHA.match(l)
        ) and _classificar_secao(_rotulo_do(l)):
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


def _declara_classes_canonicas(linhas: list[str]) -> bool:
    """O texto nomeia ao menos uma das classes canônicas (em qualquer forma).

    Ex.: "## Pontos fortes", "**Pontos fracos**", "Pontos de atenção" ou
    "Checklist de conformidade". Nesse caso a resposta é uma análise de
    documento e os bullets sem cabeçalho devem ser classificados.
    """
    for linha in linhas:
        s = linha.strip()
        if not s:
            continue
        m = (
            _RE_HEADING.match(s)
            or _RE_HEADING_BOLD.match(s)
            or _RE_LABEL_LINHA.match(s)
            or _RE_ROTULO_LIVRE.match(s)
        )
        if m and _classificar_secao((m.group("rotulo") or "").strip()):
            return True
    return False


# ---------------------------------------------------------------------------
# Classificação de cada bullet
# ---------------------------------------------------------------------------

# Fundamentos concretos citados no próprio item: ART/LEI/DECRETO/PORTARIA com
# número, ou divergência interna observável no documento.
_RE_FUNDAMENTO = re.compile(
    # \b no fim é obrigatório: sem ele, "atividades artesanais" casava "arti"
    # como se fosse citação de artigo (fundo falso).
    r"\b(?:art\.?\s*[0-9ivxIVX]+\b|artigo\s+[0-9ivx]|lei\s*(?:n[ºo]?\.?\s*)?\d|"
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
    r"menciona|aborda|cita|discorre|trata|estabelece|disp[õo]e|define|indica|"
    r"especifica|previs[ãa]o|including)|aus[êe]ncia\s+de|ausente\b|"
    r"falt[ao]\s+(?:prever|"
    r"incluir|constar|previs)|falt[ãa]\s+\w+|sem\s+(?:previs[ãa]o|cl[áa]usula|"
    r"dispositivo|prazo|revoga[çc][ãa]o|recurso|anexo|vig[êe]ncia|prever|"
    r"indica[çc][ãa]o|detalhamento|abordagem)|omiss[ãa]o\s+de|"
    r"n[ãa]o\s+(?:est[áa]|est[ãa]o|s[ãa]o|foram)\s+(?:previst\w*|inclu[íi]d\w*|"
    r"preenchid\w*|contidad\w*|definid\w*|indicad\w*|assentad\w*|abordad\w*|"
    r"citad\w*|citando)|n[ãa]o\s+abordad\w*|n[ãa]o\s+detalh\w*)\b",
    re.IGNORECASE,
)

# Defeito OBJETIVO citado no item: falta, erro, inconsistência, divergência...
# Só isto (e a instrução corretiva) justifica "Problemas identificados".
_RE_DEFEITO = re.compile(
    r"\bfalt\w*|\bexcede\b|\bultrapassa\b|\berro|\berros\b|"
    r"inconsist\w*|diverg\w*|contradit[óo]ri\w*|contradi[çc][ãa]o|amb[íi]gu\w*|"
    r"incongru\w*|imprecis\w*|redund\w*|duplic\w*|equivoc\w*|repeti[çc][ãa]o|"
    r"n[ãa]o\s+(?:est[áa]|est[ãa]o|[ée]|s[ãa]o|foram)\s+"
    r"(?:corret|adequad|conform|coerente|consistente)|inadequad\w*|"
    r"n[ãa]o\s+correspond\w*|incoerente|ilegal|inválid\w*|caduc\w*",
    re.IGNORECASE,
)

# Pedido EXPLÍCITO de validação/confirmação: vira ponto de atenção, mesmo que
# o item contenha uma palavra de conformidade ("estão adequadas").
_RE_VALIDACAO = re.compile(
    r"\b(?:confirm\w+|valid\w+|verific\w+|avaliar|avalia[çc][ãa]o|"
    r"necess[áa]ri\w*\s+(?:verificar|confirmar|validar)|"
    r"cabe\s+(?:avaliar|verificar|confirmar)|"
    r"a\s+ser\s+(?:avaliad|validad|confirmad)|"
    r"sujeito\s+a\s+valida[çc][ãa]o|requer\s+valida[çc][ãa]o)\b",
    re.IGNORECASE,
)

# Conclusão POSITIVA/conformidade do item ("...: ok", "está correta",
# "estão adequadas", "Competência e assinaturas: adequadas."). Aceita ponto
# final e a forma "Rótulo: <conclusão>" sem verbo.
_RE_POSITIVA = re.compile(
    r":\s*(?:ok\b|adequad[oa]s?\b|corret[oa]s?\b|conformes?\b|consistente\b|"
    r"coerente\b|de\s+acordo|regular(?:es)?\b|dentro\s+dos\s+padr[õo]es)\s*[.;]?\s*$|"
    r"\b(?:est[áa]\s+corret\w*|est[ãa]o\s+adequad\w*|est[ãa]o\s+corret\w*|"
    r"est[áa]\s+adequad\w*|corresponde\s+ao\s+(?:previst|disposto)|"
    r"em\s+conformidade\s+com|coerente\s+com\s+o\s+corpo)\b",
    re.IGNORECASE,
)

_RE_NEGACAO_POSITIVA = re.compile(
    r"\b(?:n[ãa]o\s+est[áa]\s+corret\w*|n[ãa]o\s+est[ãa]o\s+adequad\w*|"
    r"n[ãa]o\s+(?:est[áa]|[ée]|foram|se\s+encontra)\s+"
    r"(?:corret|adequad|conform|detalhad\w*|definid\w*|clar\w*)|"
    r"inadequad\w*|incorret\w*)\b",
    re.IGNORECASE,
)

# Especulativas (recomendação vaga, sem erro observável no documento).
_RE_ESPECULATIVA = re.compile(
    r"\b(?:seria\s+(?:[uú]til|interessante|bom|prudente|importante)|"
    r"pode(?:ria)?\s+(?:haver|ser|apresentar|gerar|ensejar|suscitar|trazer|"
    r"representar|causar|detalhar|complementar|revisar)|pode(?:m)?\s+haver\b|"
    r"cabe\s+(?:avaliar|verificar|confirmar|validar)|seria\s+necess[áa]rio\b|"
    r"recomendo\s+(?:avaliar|verificar|confirmar|validar|c[oó]mputar|prever)|"
    r"sugere-se\s+(?:avaliar|verificar|confirmar)|conv[ée]m\s+(?:avaliar|verificar)|"
    r"se\s+for\s+o\s+caso\s+incluir|a\s+ser\s+(?:avaliad|validad|confirmad)|"
    r"sujeito\s+a\s+valida[çc][ãa]o|requer\s+valida[çc][ãa]o|valida[çc][ãa]o\s+"
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
    r"reda[çc][ãa]o\s+(?:formal\s+e\s+t[ée]cnica|clara|est[áa]\s+formal)|"
    r"crit[ée]rios\s+objetivos|previs[ãa]o\s+detalhad\w*|bem\s+cobert|"
    r"aspectos\s+positiv|est[áa]\s+bem\s+(?:coberto|detalhad))\b",
    re.IGNORECASE,
)

# Descrição NEUTRA/POSITIVA do que o ato faz ("define as modalidades",
# "estabelece critérios", "está prevista e razoável"). Sem sinal de defeito,
# é ponto forte — nunca problema.
_RE_DESCRITIVO = re.compile(
    r"\b(?:define|estabelece|estabelecem|normatiza|regulamenta|trata|"
    r"indica|contempla|descreve|apresenta|possui|oferece|organiza|"
    r"detalha|aborda|prev[êe]|preveem|abrang|detalhad\w*|"
    r"est[áa]s?\s+(?:bem\s+)?(?:organizad\w*|definid\w*|detalhad\w*|"
    r"estruturad\w*|clara\w*|cobert\w*|previst\w*|redigid\w*|formal|"
    r"razo[áa]vel|coerente|consistente|objetiv\w*))\b",
    re.IGNORECASE,
)

# (Verbos de ação como "estabelece", "detalha", "inclui" NÃO entram aqui: eles
# aparecem tanto em defecto real ("Falta incluir X") quanto em descrição
# neutra do ato ("estabelece critérios objetivos"). Quem separa os dois casos é
# _RE_DEFEITO (problema), _RE_DESCRITIVO (ponto forte) e _RE_INSTRUCAO.)

_RE_PLACEHOLDER = re.compile(
    r"^\s*[-*•‣▪]?\s*n[ãa]o\s+foram\s+identificados\s+|"
    r"^\s*[-*•‣▪]?\s*nenhum\s+item\s+de\s+",
    re.IGNORECASE,
)

# Sufixos de verbo que indicam ORDEM/INSTRUÇÃO (infinitivo, imperativo,
# gerúndio). Particípios ("incluída", "garantido", "reorganizada") NÃO entram:
# descrevem o ato, não pedem correção.
_VERB = r"(?:ar|ar-se|er|er-se|e|amos|em|ando|ando-se)"

# Instrução corretiva EXPLÍCITA (imperativo ou infinitivo do analista). Tem
# prioridade sobre uma conclusão positiva: "está correta, mas corrija a
# vírgula" é problema.
_RE_INSTRUCAO = re.compile(
    r"\b(?:"
    r"corrig\w*" + _VERB + r"|ajust\w*" + _VERB + r"|consert\w*" + _VERB + r"|"
    r"arrum\w*" + _VERB + r"|refa[çz]\w*" + _VERB + r"|retific\w*" + _VERB + r"|"
    r"retifica[çc][ãa]o(?:es)?|retifica[çc][õo]es|"
    r"inclu\w*" + _VERB + r"|inser\w*" + _VERB + r"|acrescent\w*" + _VERB + r"|"
    r"prever|preveja|prev[êe]r-se|"
    r"retir\w*" + _VERB + r"|remov\w*" + _VERB + r"|exclu\w*" + _VERB + r"|"
    r"substitu\w*" + _VERB + r"|troqu\w*" + _VERB + r"|atualiz\w*" + _VERB + r"|"
    r"complement\w*" + _VERB + r"|detalhar|detalhe|"
    r"padroniz\w*" + _VERB + r"|uniformiz\w*" + _VERB + r"|renumer\w*" + _VERB + r"|"
    r"reorden\w*" + _VERB + r"|reorganiz\w*" + _VERB + r"|"
    r"esclare[çc]\w*" + _VERB + r"|esclarecimento|explicite\b|explicitar\b|"
    r"harmoniz\w*" + _VERB + r"|garant\w*" + _VERB + r"|contempl\w*" + _VERB + r"|"
    r"recomendo\b|recomenda(?:-se)?\b|recomendamos\b|"
    r"sugiro\b|sugere(?:-se)?\b|sugerimos\b|"
    r"verific\w*" + _VERB + r"|reformul\w*" + _VERB + r"|reescrev\w*" + _VERB + r")",
    re.IGNORECASE,
)


def _classificar_item(item: _Item) -> _Item:
    t = (item.texto or "").strip()

    # 0) Placeholder da própria remontagem: não é item da análise.
    if not t or _RE_PLACEHOLDER.search(t):
        item.classe = ""
        return item

    ausencia = bool(_RE_AUSENCIA.search(t))
    fundamento = bool(_RE_FUNDAMENTO.search(t))
    negativa_pos = bool(_RE_NEGACAO_POSITIVA.search(t))
    positiva = bool(_RE_POSITIVA.search(t))
    especulativa = bool(_RE_ESPECULATIVA.search(t))
    elogio = bool(_RE_ELOGIO.search(t))
    descritivo = bool(_RE_DESCRITIVO.search(t))
    defeito = bool(_RE_DEFEITO.search(t))
    validacao = bool(_RE_VALIDACAO.search(t))
    instrucao = bool(_RE_INSTRUCAO.search(t))

    nova_classe = item.secao or ""

    # Problema exige SINAL POSITIVO: defeito objetivo citado, negação de
    # positiva ou ausência com fundamento. Verbo descritivo ("estabelece",
    # "detalha") sozinho NÃO cria problema.
    if negativa_pos or defeito or (ausencia and fundamento):
        # 1) Defeito objetivo => problema.
        nova_classe = SECAO_PROBLEMAS
    elif validacao:
        # 2) Pedido de confirmação/verificação => atenção, mesmo que o item
        #    cite uma palavra de conformidade ("estão adequadas").
        nova_classe = SECAO_ATENCAO
    elif especulativa:
        # 3) Recomendação especulativa / condicional ("poderia detalhar...") =>
        #    atenção: não é defeito constatado.
        nova_classe = SECAO_ATENCAO
    elif positiva:
        # 4) Conclusão de conformidade ("X: ok", "está adequada") NUNCA é
        #    problema, nem por token fraco no rótulo ("numeração:").
        nova_classe = (
            SECAO_PONTOS_FORTES
            if item.secao == SECAO_PONTOS_FORTES
            else SECAO_CHECKLIST
        )
    elif instrucao:
        # 5) Instrução corretiva explícita (imperativo do analista) => problema.
        nova_classe = SECAO_PROBLEMAS
    elif ausencia:
        # 6) Ausência SEM fundamento => ponto de atenção (validação).
        nova_classe = SECAO_ATENCAO
    elif elogio or descritivo:
        # 7) Elogio ou descrição positiva do ato => pontos fortes.
        nova_classe = SECAO_PONTOS_FORTES
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
    itens_por_secao: dict[str, list[_Item]] = {}
    secao_atual = ""

    # O texto declara as classes canônicas? Se sim, os bullets que aparecerem
    # sem cabeçalho (lista solta antes/dentro da análise) também são
    # classificados pelo conteúdo — é o formato que o modelo costuma devolver.
    declara_classes = _declara_classes_canonicas(linhas)

    for linha in linhas:
        s = linha.strip()
        if not s:
            secao_atual = ""
            continue

        # Placeholder da remontagem anterior: descarta, não é item nem intro.
        if _RE_PLACEHOLDER.search(s):
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
            # Cabeçalho não reconhecido (ex.: "## Análise da IN"): o título
            # fica no texto introdutório e os bullets abaixo dele são
            # classificados pelo conteúdo, já que nenhuma classe foi declarada.
            intro.append(linha)
            secao_atual = _SECAO_SOLTA
            continue

        # Rótulo de categoria solto ("Pontos fortes") vira cabeçalho.
        m_livre = _RE_ROTULO_LIVRE.match(s)
        if m_livre:
            classe = _classificar_secao((m_livre.group("rotulo") or "").strip())
            if classe:
                secao_atual = classe
                continue

        mb = _RE_BULLET.match(s)
        if mb and (secao_atual or declara_classes):
            # Itens soltos (sem cabeçalho) não têm classe de origem: são
            # classificados apenas pelo conteúdo.
            origem_item = "" if secao_atual in ("", _SECAO_SOLTA) else secao_atual
            itens_por_secao.setdefault(secao_atual or _SECAO_SOLTA, []).append(
                _Item(mb.group("texto").strip(), origem_item)
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
            origem_item = "" if secao_atual == _SECAO_SOLTA else secao_atual
            itens_por_secao[secao_atual].append(_Item(s, origem_item))
            continue

        intro.append(linha)

    # Classifica cada item e o devolve para a SUA classe canônica final (um item
    # reclassificado não pode ser emitido na seção de origem nem duplicado).
    por_classe: dict[str, list[_Item]] = {secao: [] for secao in _ORDEM_SECOES}
    for secao, itens in itens_por_secao.items():
        for item in itens:
            item = _classificar_item(item)
            alvo = item.classe or (secao if secao in por_classe else "")
            if alvo not in por_classe:
                alvo = SECAO_ATENCAO
            por_classe[alvo].append(item)

    # Deduplica o mesmo texto dentro da mesma seção canônica.
    for secao, itens in por_classe.items():
        vistos: set[str] = set()
        unicos: list[_Item] = []
        for item in itens:
            chave = " ".join((item.texto or "").lower().split())
            if not chave or chave in vistos:
                continue
            vistos.add(chave)
            unicos.append(item)
        por_classe[secao] = unicos

    # Nada foi classificado (ex.: lista solta sem classes declaradas): devolver
    # o texto intacto. Nunca emitir 4 seções vazias no lugar do conteúdo.
    if not any(por_classe.values()):
        return texto

    saida: list[str] = []
    for secao in _ORDEM_SECOES:
        saida.append(f"## {secao}")
        itens = por_classe[secao]
        if itens:
            saida.extend(_secao_para_bullet(item.texto, secao) for item in itens)
        else:
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


def eh_tarefa_de_correcao(texto: str | None) -> bool:
    """O item é um PROBLEMA objetivo (pode virar apontamento acionável)?

    Mesmo classificador da exibição: garante que a lista de tarefas de
    correção nunca include elogio, conformidade ("X: ok") ou pedido de
    validação — que são exibidos, mas não corrigidos. Usado por
    ``analysis_registry`` como veto: item que não é problema nunca vira tarefa.
    """
    if not texto or not texto.strip():
        return False
    return _classificar_item(_Item(texto.strip(), "")).classe == SECAO_PROBLEMAS


# ---------------------------------------------------------------------------
# Oportunidades de melhoria (planner de "Revisar e gerar DOCX")
# ---------------------------------------------------------------------------
# A análise distingue três coisas que NÃO devem ser confundidas:
#   - PROBLEMA  -> tarefa de correção (apontamento acionável, o agente já
#                  promete corrigir);
#   - ATENÇÃO   -> pergunta/hipótese. Não é erro, mas é uma OPORTUNIDADE de
#                  melhoria: pode virar clareza, consolidação ou proposta
#                  estrutural quando houver lastro;
#   - ELOGIO / CONFORMIDADE -> não geram nada.
# O planner precisa enxergar as oportunidades sem que elas virem correção
# automática — daí uma lista separada, com tipo próprio.

TIPO_ISSUE_CONFIRMADO = "confirmed_issue"
TIPO_ATENCAO_ACIONAVEL = "actionable_attention"
TIPO_LACUNA_ESTRUTURAL = "structural_gap"

# Uma atenção só vira oportunidade quando admite ação no texto: pede
# confirmação, sinaliza trecho a explicitar ou aponta fato procedimental a
# disciplinar. Pergunta retórica ou constatação ("o documento é claro") não é
# oportunidade.
_RE_OPORTUNIDADE_ACIONAVEL = re.compile(
    r"\b(?:confirm\w*|valid\w*|verific\w*|esclarec\w*|explicit\w*|detalh\w*|"
    r"reorganiz\w*|consolid\w*|padroniz\w*|uniformiz\w*|harmoniz\w*|"
    r"reformul\w*|reescrev\w*|acrescent\w*|inclu\w*|inser\w*|disciplin\w*|"
    r"complement\w*|prever\w*|prevê|preveja|prev[êe]ndo|"
    r"poderia|pode\s+ser|seria\s+(?:útil|importante|recomend)|"
    r"convém|recomenda|sugere|caberiam|cabe\s+(?:avaliar|detalhar))\b",
    re.IGNORECASE,
)

# Lacunas procedimentais estruturais que valem proposta de artigo/parágrafo
# quando o próprio ato já traz o fato gerador. Não é a lista de "temas" do
# RAG: aqui basta o documento sinalizar o fato (ex.: decisão fundamentada sem
# disciplina de comunicação ao interessado).
_RE_FATO_GERADOR = re.compile(
    r"\b(?:comunic\w*|notific\w*|intim\w*|public\w*|ciência\b|"
    r"registro\s+em\s+controle|registro\b|controle\s+interno|"
    r"responsabilidade\s+(?:solid[áa]ria|comum|conjunta)|"
    r"rastreab\w*|auditoria\w*|presta[çc][ãa]o\s+de\s+contas|"
    r"fiscaliza\w*|monitoramento|decisão\s+fundamentada|"
    r"ato\s+administrativo|prazo|revoga\w*|vigência|suspens[ãa]o|"
    r"cancelamento|denega\w*|indefer\w*|defer\w*)\b",
    re.IGNORECASE,
)


def _itens_por_secao(analise: str | None) -> dict[str, list[str]]:
    """Agrupa os bullets da análise já reestruturada por seção canônica."""
    secoes: dict[str, list[str]] = {secao: [] for secao in _ORDEM_SECOES}
    atual = ""
    for linha in (analise or "").splitlines():
        cabecalho = _RE_HEADING.match(linha)
        if cabecalho:
            atual = _classificar_secao(cabecalho.group("rotulo"))
            continue
        if not atual:
            continue
        bullet = _RE_BULLET.match(linha)
        if not bullet:
            continue
        texto = (bullet.group("texto") or "").strip()
        if not texto or _RE_PLACEHOLDER.match(linha):
            continue
        secoes[atual].append(texto)
    return secoes


def extrair_oportunidades(analise: str | None) -> list[dict]:
    """Extrai do texto da análise as OPORTUNIDADES de melhoria.

    Devolve itens ``{"tipo", "secao", "texto"}`` para o planner proactive:

    - ``confirmed_issue``: o próprio item é um PROBLEMA confirmado. Entra
      também na lista de correções, mas aparece aqui para o planner avaliar
      as camadas mais ousadas (clareza/estrutural/normativa) sobre ele;
    - ``actionable_attention``: ponto de atenção que admite ação no texto;
    - ``structural_gap``: lacuna procedimental estrutural detectada a partir
      de um FATO já presente no ato (decisão fundamentada sem comunicação,
      suspensão sem registro, etc.) — a base para artigo/parágrafo novo.

    Nada aqui vira correção automática: são oportunidades avaliadas pelo
    planner, que decide entre aplicar, propor ou descartar.
    """
    if not analise or not analise.strip():
        return []
    secoes = _itens_por_secao(analise)
    vistas: set[str] = set()
    oportunidades: list[dict] = []

    def _add(tipo: str, secao: str, texto: str) -> None:
        chave = " ".join(texto.lower().split())
        if not chave or chave in vistas:
            return
        vistas.add(chave)
        oportunidades.append(
            {"tipo": tipo, "secao": secao, "texto": texto[:400], "id": f"op-{len(oportunidades)+1}"}
        )

    for texto in secoes.get(SECAO_PROBLEMAS, []):
        _add(TIPO_ISSUE_CONFIRMADO, SECAO_PROBLEMAS, texto)

    for texto in secoes.get(SECAO_ATENCAO, []):
        acionavel = bool(_RE_OPORTUNIDADE_ACIONAVEL.search(texto))
        fato_gerador = bool(_RE_FATO_GERADOR.search(texto))
        if acionavel and fato_gerador:
            # Pede ação E o ato traz o fato que a ação disciplinaria: é a
            # base mais forte para uma proposta estrutural.
            _add(TIPO_LACUNA_ESTRUTURAL, SECAO_ATENCAO, texto)
        elif acionavel:
            _add(TIPO_ATENCAO_ACIONAVEL, SECAO_ATENCAO, texto)
        elif fato_gerador:
            # Fato gerador sem verbo de ação ("não disciplina a comunicação ao
            # interessado"): lacuna estrutural — cabe artigo/parágrafo novo.
            _add(TIPO_LACUNA_ESTRUTURAL, SECAO_ATENCAO, texto)
    return oportunidades



__all__ = [
    "SECAO_ATENCAO",
    "SECAO_CHECKLIST",
    "SECAO_PONTOS_FORTES",
    "SECAO_PROBLEMAS",
    "TIPO_ATENCAO_ACIONAVEL",
    "TIPO_ISSUE_CONFIRMADO",
    "TIPO_LACUNA_ESTRUTURAL",
    "eh_tarefa_de_correcao",
    "extrair_oportunidades",
    "parecer_analise",
    "reestruturar_analise",
]
