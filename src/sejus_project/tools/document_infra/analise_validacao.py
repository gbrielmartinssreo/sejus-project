"""Passagens de ANÁLISE do original + conferência dos achados (pré-melhoria).

As passagens pós-geração (``docx_validacao``) validam o ARQUIVO produzido. Estas
passagens validam o ORIGINAL e os ACHADOS da análise ANTES de a melhoria
executar, produzindo um registro separado com chamada, resultado e duração:

    1. ortografia ..... padrões de erro verificáveis de forma determinística;
    2. estrutura ...... sequência de capítulos (I..N) e duplicidades;
    3. clareza ........ marcadores de seção colados ao texto do parágrafo;
    4. fundamentação .. considerandos com/sem indicação de fundamento;
    5. conferência .... validade de cada achado contra trecho+localização do
                       original — contraditados são descartados/reformulados.

A conferência impede que um achado contra o documento chegue à melhoria (ex.:
afirmar que a ementa não trata de governança quando ela trata; sugerir incluir
uma lei que já consta; apontar repetição de palavra que não aparece). Também
produz as CORREÇÕES AUTOMÁTICAS de redação (trechos verificáveis) que entram
no patch como itens do sistema, registradas na mesma rastreabilidade.
"""
from __future__ import annotations

import re
import time
import unicodedata

# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

_STOP = {
    "a", "as", "o", "os", "um", "uma", "uns", "umas", "de", "da", "do", "das",
    "dos", "em", "no", "na", "nos", "nas", "por", "para", "com", "sem",
    "sob", "sobre", "ao", "aos", "à", "às", "que", "quando", "quanto", "como",
    "e", "ou", "mas", "se", "é", "são", "ser", "mais", "menos", "até",
    "também", "já", "não", "nem", "então", "apenas", "só", "seja", "sendo",
    "serem", "sua", "suas", "seu", "seus", "dela", "deles", "dele", "desse",
    "dessa", "deste", "desta", "esse", "essa", "este", "esta", "meio",
}


def _sem_acentos(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in texto if not unicodedata.combining(c))


def _normalizar(texto: str) -> str:
    return _sem_acentos(texto or "").casefold()


def _chave(texto: str) -> str:
    """Chave de comparação: sem acentos, caixa baixa e espaçamento colapsado."""
    return re.sub(r"\s+", " ", _normalizar(texto)).strip()


def _linhas_de(texto: str) -> list[str]:
    return [linha.strip() for linha in (texto or "").splitlines() if linha.strip()]


def _corrido(texto: str) -> str:
    """Número normalizado sem separadores ('4.320/1964' -> '43201964')."""
    return re.sub(r"[\s./-]", "", _normalizar(texto))


# Sinônimos jurídicos usados no descarte por ausência: o documento emprega o
# termo vizinho (ex.: 'vigor' onde o achado afirma falta de 'vigência').
# Chaves no formato de _chave (sem acento, caixa baixa).
_SINONIMOS_AUSENCIA = {
    "vigencia": "vigor",
    "vigencias": "vigor",
}


# ---------------------------------------------------------------------------
# Correções automáticas de redação (detecção determinística)
# ---------------------------------------------------------------------------

_CORRECOES_REDACAO = [
    (
        "ortografia",
        re.compile(r"\bparafins\b", re.IGNORECASE),
        "para fins",
        "ortografia: 'parafins' é a junção indevida de 'para fins'.",
        "parágrafo em que ocorre 'parafins'",
    ),
    (
        "referencia",
        re.compile(r"\b(par[áa]grafo\s+[úu]nico\s+do\s+)20\b", re.IGNORECASE),
        None,  # preenchido por _substituir (mantém o prefixo)
        "referência: 'parágrafo único do 20' deve indicar o dispositivo "
        "('parágrafo único do art. 20').",
        "parágrafo que remete ao art. 20",
    ),
]


def _substituir(m: re.Match, tipo: str, original: str, detalhe: str,
                localizacao: str) -> dict | None:
    """Item de correção automática para um trecho verificado.

    O item carrega o trecho completo do parágrafo (ângora do patch) e, em
    ``buscar``/``substituir``, a substituição mínima para a renderização
    cirúrgica no .docx. ``original`` é o parágrafo inteiro; a regex ``m``
    indica onde o texto varia."""
    inicio, fim = m.span()
    buscar = original[inicio:fim]
    if tipo == "referencia":
        substituir = f"{m.group(1)}art. 20"
    else:
        substituir = _CATALOGO_ORTOGRAFIA.get(m.group(0).casefold())
        if not substituir:
            substituir = None
    if not substituir or buscar not in original:
        return None
    if substituir.casefold() == buscar.casefold():
        return None
    rotulo = original[:70].strip()
    return {
        "tipo": "corrigido",
        "rotulo": rotulo,
        "trecho_original": original,
        "novo_texto": original[:inicio] + substituir + original[fim:],
        "buscar": buscar,
        "substituir": substituir,
        "localizacao": localizacao,
        "detalhe": f"{detalhe} Substitui '{buscar}' por '{substituir}'.",
        "automatica": True,
        "origem": "sistema (correção automática)",
    }


_CATALOGO_ORTOGRAFIA = {
    "parafins": "para fins",
}


def detectar_correcoes_redacao(texto: str) -> list[dict]:
    """Correções automáticas de redação verificáveis no ORIGINAL.

    Cada correção é um item de patch (``tipo='corrigido'``, ``automatica=True``)
    com âncora estrita no parágrafo inteiro; ``buscar``/``substituir`` carregam
    a variação mínima. Sem correspondência nenhuma, devolve lista vazia."""
    correcoes: list[dict] = []
    for tipo, padrao, _fixo, detalhe, localizacao in _CORRECOES_REDACAO:
        for linha in _linhas_de(texto):
            match = padrao.search(linha)
            if not match:
                continue
            item = _substituir(match, tipo, linha, detalhe, localizacao)
            if item and not any(
                c.get("trecho_original") == linha and c.get("buscar") == item["buscar"]
                for c in correcoes
            ):
                correcoes.append(item)
    return correcoes


# ---------------------------------------------------------------------------
# Passagens
# ---------------------------------------------------------------------------


def _passagem(passo: str, objetivo: str, ok: bool, resultado: str,
              t0: float, chamadas: list[dict] | None = None) -> dict:
    return {
        "passo": passo,
        "objetivo": objetivo,
        "resultado": ("OK — " if ok else "FALHA — ") + resultado,
        "ok": bool(ok),
        "tempo_ms": round((time.perf_counter() - t0) * 1000, 1),
        "chamadas": chamadas or [],
    }


def _fragments_distintos(texto: str) -> list[str]:
    """Fragmento de 2..5 palavras com vocabulário (exclui stopwords)
    presentes em ``texto``, em ordem, para localizar termos candidatos."""
    palavras = [p for p in re.findall(r"[a-zA-Zá-úÁ-Úà-ùÀ-Ùâ-ûÂ-Ûã-õÃ-ÕçÇ]+", texto or "")
                if p.casefold() not in _STOP]
    if not palavras:
        return []
    candidatos: list[str] = []
    for n in (2, 3, 4):
        for i in range(len(palavras) - n + 1):
            candidatos.append(" ".join(palavras[i:i + n]))
    # Palavras simples significativas (evita falsos positivos com 'de', 'que').
    candidatos.extend(p for p in palavras if len(p) >= 6)
    vistos = set()
    unicos: list[str] = []
    for cand in candidatos:
        chave = _chave(cand)
        if chave and chave not in vistos:
            vistos.add(chave)
            unicos.append(cand)
    return unicos


# ---------------------------------------------------------------------------
# 1. Ortografia
# ---------------------------------------------------------------------------


def _passagem_ortografia(texto: str) -> dict:
    t0 = time.perf_counter()
    correcoes = detectar_correcoes_redacao(texto)
    chamadas = [{"funcao": "detectar_correcoes_redacao", "alvos": len(_linhas_de(texto))}]
    if not correcoes:
        return _passagem(
            "ortografia",
            "identificar erros verificáveis de grafia/junção no original",
            True,
            "nenhum padrão conhecido de erro encontrado.",
            t0, chamadas,
        )
    detalhes = [
        f"correção automática '{c['buscar']}' -> '{c['substituir']}' "
        f"(em '{_chave(c['localizacao'])}')."
        for c in correcoes
    ]
    return _passagem(
        "ortografia",
        "identificar erros verificáveis de grafia/junção no original",
        False,
        "; ".join(detalhes) + " Correção automática lançada no patch.",
        t0, chamadas,
    )


# ---------------------------------------------------------------------------
# 2. Estrutura
# ---------------------------------------------------------------------------


def _passagem_estrutura(texto: str) -> dict:
    t0 = time.perf_counter()
    from sejus_project.tools.llm_tools.document_improvement import (
        _renumeracao_canonica_capitulos,
    )

    planos = _renumeracao_canonica_capitulos(texto)
    chamadas = [
        {"funcao": "_renumeracao_canonica_capitulos",
         "alvos": len(_linhas_de(texto))}
    ]
    linhas = _linhas_de(texto)
    numerais = [re.match(r"^\s*cap[ií]tulo\s+([ivxl]+)\b", _sem_acentos(l), re.I).group(1)
                for l in linhas if re.match(r"^\s*cap[ií]tulo\s+[ivxl]+\b", _sem_acentos(l), re.I)]
    duplicados = [r for r in dict.fromkeys(numerais) if numerais.count(r) > 1]
    problemas: list[str] = []
    if duplicados:
        problemas.append(
            "numeração repetida de capítulo: " + ", ".join(f"CAPÍTULO {r.upper()}" for r in duplicados)
        )
    if planos:
        problemas.append(
            f"sequência de capítulos fora da forma canônica I..N em "
            f"{len(planos)} cabeçalho(s) — renumeração automática lançada."
        )
    if not problemas:
        return _passagem(
            "estrutura",
            "validar sequência de capítulos e duplicidades",
            True,
            "sequência de capítulos já contígua (I..N) e sem repetições.",
            t0, chamadas,
        )
    return _passagem(
        "estrutura",
        "validar sequência de capítulos e duplicidades",
        False,
        "; ".join(problemas),
        t0, chamadas,
    )


# ---------------------------------------------------------------------------
# 3. Clareza
# ---------------------------------------------------------------------------

_RE_CLAREZA = [
    (
        re.compile(r"\.\s*(§\s*\d+º)\b", re.IGNORECASE),
        "seção '§ Nº' colada ao fim de sentença do mesmo parágrafo (deveria "
        "iniciar novo parágrafo).",
    ),
    (
        re.compile(r"[a-zç]\s*:\s*([IVX]+\s*[-–])", re.IGNORECASE),
        "subseção 'N –' colada ao texto anterior sem quebra de parágrafo.",
    ),
    (
        re.compile(r"[a-zç]\.\s*[Pp]ar[áa]grafo\s+[úu]nico\.", re.IGNORECASE),
        "'Parágrafo único.' colado ao fim de sentença (sem novo parágrafo).",
    ),
]


def _passagem_clareza(texto: str) -> dict:
    t0 = time.perf_counter()
    achados: list[tuple[str, str]] = []
    for linha in _linhas_de(texto):
        for padrao, descricao in _RE_CLAREZA:
            if padrao.search(linha):
                achados.append((linha[:70], descricao))
    chamadas = [
        {"funcao": "padroes_textuais_clareza", "alvos": len(_linhas_de(texto))}
    ]
    if not achados:
        return _passagem(
            "clareza",
            "detectar marcadores de seção colados ilegalmente a parágrafos",
            True,
            "nenhum marcador (§, inciso, parágrafo único) colado ao texto.",
            t0, chamadas,
        )
    detalhes = []
    for contexto, descricao in achados:
        detalhes.append(f"'{contexto}…' — {descricao}")
    return _passagem(
        "clareza",
        "detectar marcadores de seção colados ilegalmente a parágrafos",
        False,
        "possíveis falhas de formatação: " + " | ".join(d for _, d in achados) + ".",
        t0, chamadas,
    )


# ---------------------------------------------------------------------------
# 4. Fundamentação
# ---------------------------------------------------------------------------

_RE_CITACAO = re.compile(
    r"(?:lei|constitui|decreto|resolu\w+|portaria|emenda|c[óo]digo|norma)\w*"
    r"\s+(?:n[ºo.]?\s*)?\d",
    re.IGNORECASE,
)


def _passagem_fundamentacao(texto: str) -> dict:
    t0 = time.perf_counter()
    considerandos = [
        linha for linha in _linhas_de(texto)
        if re.match(r"^\s*(?:considerando|considerandos)", _chave(linha))
    ]
    chamadas = [
        {"funcao": "checar_considerandos", "alvos": len(considerandos)}
    ]
    sem_fundamento = [c for c in considerandos if not _RE_CITACAO.search(c)]
    if not considerandos:
        return _passagem(
            "fundamentação",
            "verificar que cada considerando indica fundamento normativo",
            True,
            "documento sem considerandos.",
            t0, chamadas,
        )
    if not sem_fundamento:
        return _passagem(
            "fundamentação",
            "verificar que cada considerando indica fundamento normativo",
            True,
            f"{len(considerandos)} considerando(s) com indicação de fundamento.",
            t0, chamadas,
        )
    return _passagem(
        "fundamentação",
        "verificar que cada considerando indica fundamento normativo",
        False,
        f"{len(sem_fundamento)} considerando(s) sem indicação de fundamento "
        "(ex.: 'CONSIDERANDO a remição…') — conferir antes da publicação.",
        t0, chamadas,
    )


# ---------------------------------------------------------------------------
# 5. Conferência dos achados
# ---------------------------------------------------------------------------

_RE_ARTIGO = re.compile(r"\b(?:art\w*\.?\s*)(\d{1,3})[º°o]?\b", re.IGNORECASE)
_RE_CAPITULO = re.compile(r"\bcap[ií]tulo\s+([ivxl]+)\b", re.IGNORECASE)
_RE_NEGACAO = re.compile(
    r"\b(?:"
    r"n[ãa]o\s+(?:menciona|prev\w*|traz|contempla|aborda|abrange|cita|"
    r"consta|disciplina|estabelece|detalh\w*|indica|h[áa])|"
    r"aus[êe]ncia\s+de|"
    r"falta\s+(?:de\s+)?(?:previs|discipl|dispositivo|inclu\w*|prev\w*|"
    r"explicit\w*|contempl\w*|estabelec\w*)|"
    r"inexist[êe]ncia|inexistente|"
    r"sem\s+(?:previs|discipl|men[çc])"
    r")",
    re.IGNORECASE,
)
_RE_INCLUIR = re.compile(
    r"\b(?:inclu(?:ir|a|a-se)|inser(?:ir|a)|acrescent(?:ar|e)|adicion(?:ar|e)|"
    r"complement(?:ar|e)|citar|referenciar|constar|explicit(?:ar|e)|"
    r"fundamentar|prever|preveja)\b",
    re.IGNORECASE,
)
_RE_CITACAO_NORMATIVA = re.compile(
    r"(?:lei|decreto|resolu[çc][ãa]o|portaria|emenda|constitui[çc][ãa]o)\s*"
    r"(?:n[ºo.]?\s*)?(\d{1,4}(?:[\s./]\d{1,4})*)",
    re.IGNORECASE,
)
_RE_REPETICAO = re.compile(
    r"\b(repeti\w*|repete\w*|duplici\w*|redund\w*|usad\w*\s+repetid\w*|"
    r"ocorr\w*\s+(?:mais\s+de\s+uma\s+)?ve(?:z|zes))\b",
    re.IGNORECASE,
)
_RE_ASPAS = re.compile(r"['“”\"]([^'“”\"]{2,50})['“”\"]")
_RE_PALAVRA_TERMO = re.compile(r"\b(?:a\s+palavra|o\s+termo|o\s+voc[áa]bulo)\b", re.IGNORECASE)
# Verbos de INSTRUÇÃO de alteração (imperativos/substantivos precisos). Exclui
# formas descritivas (gerúndios, participios: 'incluindo', 'atualizada',
# 'detalhada') que não pedem alteração.
_RE_INSTRUCAO = re.compile(
    r"\b(?:recomend|suger[ei]|sugest|deve|devem|dever[áa]|corrig|ajust|"
    r"renumer|reorden|reorganiz|inclu(?:ir|a|a-se)|inser(?:ir|a)|"
    r"acrescent(?:ar|e)|adicion(?:ar|e)|prever|preveja|explicit(?:ar|e)|"
    r"complement(?:ar|e)|detalh(?:ar|e)|padroniz|uniformiz|garant|assegur|"
    r"falta|ausente|necess[áa]ri|omiss|substitu(?:ir|a)|reformul|esclarec|"
    r"harmoniz|revisar|simplific(?:ar|e))\w*\b",
    re.IGNORECASE,
)
# Elogios/complacências que NÃO pedem alteração (palavras de ornamentação).
_RE_CONFORMIDADE = re.compile(
    r"\b(abrangente|atualizada|atualizado|pertinentes?|equilibrad[oa]s?|"
    r"consistente|coerente|bem\s+(?:delinead|definid|regulamentad)|"
    r"boas?\s+pr[áa]ticas?|pre[é]via|"
    r"estabelece\s+crit[ée]rios|previs[ãa]o\s+detalhada|"
    r"promovendo\s+(?:transpar[êe]ncia|seguran[çc]a)|"
    r"estrutura\s+(?:clara|organizada)|crit[ée]rios\s+objetivos)\b",
    re.IGNORECASE,
)
# Falas de abertura/encerramento e resumos do analista: não pedem alteração.
_RE_INTRO_CONCLUSAO = re.compile(
    r"(^\s*(?:aqui\s+est[áa]|segue\s+(?:a|o)\s|esta\s+[ée]\s+a|"
    r"conclus[ãa]o\b|em\s+s[íi]ntese\b|resumo\b|s[íi]ntese\b)|"
    r"crit[ée]rios\s+de\s+revis|"
    r"\bo\s+documento\s+(?:est[áa]|foi|permanece|encontra).{0,240}"
    r"\brecomenda-?se\b)",
    re.IGNORECASE,
)
# Contraste/condicional que indica que o item, apesar do elogio, PEDE mudança.
_RE_CONTRASTE = re.compile(r"\b(mas|por[ée]m|todavia|contudo|embora)\b", re.IGNORECASE)
# Referências a artigos de NORMAS EXTERNAS ('art. 71 da Constituição Estadual'):
# não localizam dispositivo do documento e não podem virar 'artigo inexistente'.
_RE_NORMA_EXTERNA = re.compile(
    r"\b(constitui|lei|decreto|resolu|estadual|federal|regulamento|c[óo]digo)\b",
    re.IGNORECASE,
)

_RE_LOCALIZA = [
    re.compile(r"\bementa\b", re.IGNORECASE),
    re.compile(r"\bpre[âa]mbulo\b", re.IGNORECASE),
    re.compile(r"\bconsiderand", re.IGNORECASE),
    re.compile(r"\bcap[ií]tulo\b", re.IGNORECASE),
    re.compile(r"\bart\b\.?\s*(\d|[ivx])", re.IGNORECASE),
]


def _eh_artigo_externo(texto: str, pos: int) -> bool:
    """Diz se um 'Art. N' citado no achado refere norma EXTERNA."""
    janela = _normalizar(texto[max(0, pos - 45):pos + 45])
    return bool(_RE_NORMA_EXTERNA.search(janela))


def _numero_documento(texto: str, ignorar_externos: bool = False) -> set[int]:
    """Números dos artigos citados em um achado (localização reivindicada)."""
    numeros: set[int] = set()
    for match in _RE_ARTIGO.finditer(texto or ""):
        if ignorar_externos and _eh_artigo_externo(texto, match.start()):
            continue
        numeros.add(int(match.group(1)))
    return numeros


def _clausula_negada(apontamento: str) -> str:
    """Trecho após o verbo de negação até o fim da cláusula/sentença."""
    match = _RE_NEGACAO.search(apontamento or "")
    if not match:
        return ""
    resto = apontamento[match.end():]
    corte = re.compile(r"[.,;]|\b(recomend|suges|deve-se|importante|o\s+que\s+[éè])\b", re.I).search(resto)
    return resto[:corte.start()] if corte else resto


def _trecho_de(conteudo: str, texto_achado: str) -> tuple[str, str]:
    """(trecho, rótulo) do documento que o achado reivindica localizar.

    Resolve a localização na ordem: artigo citado, capítulo citado, ementa,
    considerandos e, por fim, o documento inteiro. Devolve ('', '') quando o
    artigo citado não existe no original. Artigos de normas EXTERNAS
    ('art. 71 da Constituição Estadual') não localizam dispositivo do
    documento."""
    linhas = _linhas_de(conteudo)
    numeros = _numero_documento(texto_achado, ignorar_externos=True)
    if numeros:
        indice_artigo = None
        rotulo = None
        for i, linha in enumerate(linhas):
            m = re.match(
                r"^\s*art\w*\.?\s*(\d{1,3})o?(?:\s|\.|$)",
                _sem_acentos(linha), re.I,
            )
            if m and int(m.group(1)) in numeros:
                indice_artigo = i
                rotulo = f"Art. {m.group(1)}"
                break
        if indice_artigo is None:
            return "", "artigo não localizado"
        fim = indice_artigo + 1
        for j in range(indice_artigo + 1, len(linhas)):
            if re.match(r"^\s*(?:art\w*\.?\s*|cap[ií]tulo\b)", _sem_acentos(linhas[j]), re.I):
                break
            fim = j + 1
        return "\n".join(linhas[indice_artigo:fim]), rotulo or ""
    m = _RE_CAPITULO.search(texto_achado)
    if m:
        alvo = m.group(1).casefold()
        for i, linha in enumerate(linhas):
            n = re.match(r"^\s*cap[ií]tulo\s+([ivxl]+)\b", _sem_acentos(linha), re.I)
            if n and n.group(1).casefold() == alvo:
                fim = i + 1
                for j in range(i + 1, len(linhas)):
                    if re.match(r"^\s*cap[ií]tulo\b", _sem_acentos(linhas[j]), re.I):
                        break
                    fim = j + 1
                return "\n".join(linhas[i:fim]), f"CAPÍTULO {m.group(1).upper()}"
        return "", "capítulo não localizado"
    if "ementa" in _chave(texto_achado):
        return "\n".join(linhas[:8]), "Ementa/preâmbulo"
    if "considerand" in _chave(texto_achado):
        inicio = fim = None
        for i, linha in enumerate(linhas):
            if re.match(r"^\s*considerand", _sem_acentos(linha), re.I):
                inicio = i
                break
        if inicio is not None:
            for j in range(inicio + 1, len(linhas)):
                if re.match(r"^\s*art\w*\.?\s*", _sem_acentos(linhas[j]), re.I):
                    fim = j
                    break
            return "\n".join(linhas[inicio:fim if fim else len(linhas)]), "Considerandos"
    return conteudo, "documento"


def _motivo_descarte_ausencia(apontamento: str, trecho: str, rotulo: str,
                              documento: str) -> str | None:
    """Achado afirma AUSÊNCIA, mas o sujeito da cláusula negada existe.

    Só contradita quando a palavra/frase negada está de fato no original.
    Para negações de escopo genérico ('a IN não…', 'não há…') exige frase com
    vocabulário; quando a própria cláusula nomeia a localização (''a ementa
    não menciona…'), uma palavra distinta naquele trecho basta para
    contradizer o achado."""
    match = _RE_NEGACAO.search(apontamento or "")
    if not match:
        return None
    clausula = _clausula_negada(apontamento)
    if not clausula.strip():
        return None
    antes = _chave(apontamento[max(0, match.start() - 35):match.start()])
    vizinho = _chave(antes + " " + clausula)
    localiza_especifica = any(padrao.search(vizinho) for padrao in _RE_LOCALIZA)
    base = _chave(trecho) if localiza_especifica and _chave(trecho) else _chave(documento)
    rotulo_localizado = rotulo if localiza_especifica else "documento"
    for candidato in _fragments_distintos(clausula):
        chave_candidato = _chave(candidato)
        if not chave_candidato:
            continue
        n_palavras = len(candidato.split())
        if not localiza_especifica and n_palavras < 2:
            continue
        if chave_candidato in base:
            # Fragmento curto demais ('trabalho artesanal') não prova que a
            # cláusula negada inteira já consta: exige trecho específico.
            if n_palavras < 3:
                continue
            return (
                f"achado contradiz o original: o documento já trata de "
                f"'{candidato}' (localização: {rotulo_localizado})."
            )
        # Equivalências de conceito: o documento usa um sinônimo jurídico",
        # ('vigor' no lugar de 'vigência') que também desmente a ausência.
        for palavra in re.findall(r"[a-zà-ú]+", chave_candidato):
            sinonimo = _SINONIMOS_AUSENCIA.get(palavra)
            if sinonimo and sinonimo in base:
                return (
                    f"achado contradiz o original: o documento já trata do "
                    f"tema '{candidato}' (equivalente '{sinonimo}') "
                    f"(localização: {rotulo_localizado})."
                )
    return None


def _motivo_descarte_citacao(apontamento: str, documento: str,
                             rotulo: str) -> str | None:
    """Achado manda incluir/fundamentar uma norma que JÁ consta no documento.

    Só dispara em instruções com verbo preciso de inclusão (não em falas
    descritivas como 'incluindo LEP (Lei nº …)')."""
    if not _RE_INCLUIR.search(apontamento):
        return None
    citacoes = _RE_CITACAO_NORMATIVA.findall(apontamento or "")
    digitos_documento = [
        "".join(c for c in _normalizar(linha) if c.isdigit())
        for linha in _linhas_de(documento)
    ]
    for numero in citacoes:
        alvo = _corrido(numero)
        seg = re.match(r"([^/]+)", numero)
        major = _corrido(seg.group(1)) if seg else ""
        if not alvo:
            continue
        if any(
            alvo in linha_digitos
            or (major and len(major) >= 3 and major in linha_digitos)
            for linha_digitos in digitos_documento
        ):
            return (
                f"achado contradiz o original: o fundamento 'Lei {numero}' já "
                f"consta no documento (localização: {rotulo or 'documento'})."
            )
    return None


def _motivo_descarte_repeticao(apontamento: str, trecho: str, rotulo: str,
                               documento: str) -> str | None:
    """Achado aponta REPETIÇÃO/uso de termo que a verificação desmente.

    A contagem é feita no documento inteiro (uma duplicação de capítulo como
    'CAPÍTULO III' existe no documento, ainda que o trecho isolado a oculte)."""
    if not _RE_REPETICAO.search(apontamento):
        return None
    termos = [t.strip() for t in re.findall(r"['“”\"]([^'“”\"]{2,50})['“”\"]", apontamento or "")]
    if not termos and _RE_PALAVRA_TERMO.search(apontamento):
        m = re.search(r"(?:a\s+palavra|o\s+termo)\s+'?([^,.;:]{2,40})", apontamento, re.I)
        if m:
            termos.append(m.group(1).strip())
    if not termos:
        return None
    doc_chave = _chave(documento)
    trecho_chave = _chave(trecho)
    articula = bool(_numero_documento(apontamento, ignorar_externos=True))
    if articula and not trecho_chave:
        # artigo citado mas inexistente: o descarte cabe à checagem de localização
        return None
    # Escopo da contagem: o trecho quando o achado aponta um dispositivo
    # ('no art. 9º'), o documento inteiro quando a repetição é documental
    # ('Repetição do título "CAPÍTULO III"').
    escopo = trecho_chave if articula else doc_chave
    rotulo_escopo = rotulo if articula else "documento"
    for termo in termos:
        chave_termo = _chave(termo)
        if not chave_termo:
            continue
        ocorrencias = escopo.count(chave_termo)
        if not ocorrencias:
            return (
                f"achado contradiz o original: o termo '{termo}' não ocorre "
                f"no trecho apontado (localização: {rotulo_escopo})."
            )
        if ocorrencias < 2:
            return (
                f"achado contradiz o original: o termo '{termo}' ocorre "
                f"{ocorrencias} vez(es) no trecho apontado "
                f"({rotulo_escopo}) — não há repetição."
            )
    return None


def _motivo_descarte_artigo_inexistente(apontamento: str, trecho: str,
                                        rotulo: str) -> str | None:
    if (
        _numero_documento(apontamento, ignorar_externos=True)
        and not trecho
        and rotulo == "artigo não localizado"
    ):
        return (
            "o achado cita artigo que não foi localizado no documento "
            "(falha de localização)."
        )
    return None


def _motivo_descarte_constatacao(apontamento: str) -> str | None:
    """Elogio/complacência sem instrução: fica na análise, não vira tarefa.

    Não dispara quando há verbo de instrução, negação/ausência ou contraste
    ('mas', 'porém'), que indicam que o item, apesar do tom, pede mudança."""
    texto = apontamento or ""
    if _RE_INTRO_CONCLUSAO.search(texto):
        return (
            "fala de abertura/encerramento ou resumo da análise, não é "
            "instrução de alteração — mantida apenas na análise."
        )
    if not _RE_CONFORMIDADE.search(texto):
        return None
    if _RE_INSTRUCAO.search(texto):
        return None
    if _RE_NEGACAO.search(texto):
        return None
    if _RE_CONTRASTE.search(texto):
        return None
    return (
        "constatação de conformidade, não é instrução de alteração — mantida "
        "apenas na análise."
    )


def conferir_achados(documento: str, apontamentos: list[dict]) -> tuple[list[dict], list[dict]]:
    """Valida cada apontamento contra trecho+localização do original.

    Devolve (apontamentos_validos, descartados). Todo achado contraditado pelo
    documento (ausência falsa, norma já presente, repetição inexistente,
    artigo inexistente no original) ou que seja mera constatação de conformidade
    é DESCARTADO antes de a melhoria executar — chega apenas o que a
    verificação confirma."""
    validos: list[dict] = []
    descartados: list[dict] = []
    for apontamento in apontamentos or []:
        if not isinstance(apontamento, dict):
            continue
        texto = apontamento.get("texto") or ""
        trecho, rotulo = _trecho_de(documento, texto)
        motivos: list[str] = []
        for checagem in (
            lambda: _motivo_descarte_constatacao(texto),
            lambda: _motivo_descarte_citacao(texto, documento, rotulo),
            lambda: _motivo_descarte_ausencia(texto, trecho, rotulo, documento),
            lambda: _motivo_descarte_repeticao(texto, trecho, rotulo, documento),
            lambda: _motivo_descarte_artigo_inexistente(texto, trecho, rotulo),
        ):
            motivo = checagem()
            if motivo:
                motivos.append(motivo)
        if motivos:
            descartados.append({
                "apontamento_id": apontamento.get("id") or "",
                "apontamento": texto,
                "motivo": " ".join(dict.fromkeys(motivos)),
                "localizacao": rotulo or "documento",
                "status": "descartado",
            })
            continue
        validos.append(apontamento)
    return validos, descartados


def _passagem_conferencia(documento: str, apontamentos: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    t0 = time.perf_counter()
    validos, descartados = conferir_achados(documento, apontamentos)
    chamadas = [{"funcao": "conferir_achados", "alvos": len(apontamentos or [])}]
    if not apontamentos:
        return _passagem(
            "conferência dos achados",
            "validar cada achado contra o trecho e a localização no original",
            True,
            "nenhum achado a conferir.",
            t0, chamadas,
        ), [], []
    if not descartados:
        return _passagem(
            "conferência dos achados",
            "validar cada achado contra o trecho e a localização no original",
            True,
            f"{len(validos)} achado(s) confirmado(s) no original.",
            t0, chamadas,
        ), validos, []
    return _passagem(
        "conferência dos achados",
        "validar cada achado contra o trecho e a localização no original",
        False,
        f"{len(descartados)} achado(s) contradiz(em) o original e foi(ram) "
        f"descartado(s) da melhoria; {len(validos)} confirmado(s).",
        t0, chamadas,
    ), validos, descartados


_PASSAGEM_FUNCOES = [
    ("ortografia", _passagem_ortografia),
    ("estrutura", _passagem_estrutura),
    ("clareza", _passagem_clareza),
    ("fundamentação", _passagem_fundamentacao),
]


def rodar_passagens_analise(documento: str,
                            apontamentos: list[dict]) -> dict:
    """Passagens de ANÁLISE + conferência; devolve um dicionário consolidado.

    Retorna ``passagens`` (registros com chamada/resultado/duração),
    ``correcoes`` (itens de patch automáticos para a melhoria),
    ``achados_descartados`` e ``apontamentos_validos`` (achados confirmados no
    original que podem ir à melhoria)."""
    passagens: list[dict] = []
    for _, funcao in _PASSAGEM_FUNCOES:
        passagens.append(funcao(documento))
    passagem_conf, validos, descartados = _passagem_conferencia(documento, apontamentos)
    passagens.append(passagem_conf)
    return {
        "passagens": passagens,
        "correcoes": detectar_correcoes_redacao(documento),
        "achados_descartados": descartados,
        "apontamentos_validos": validos,
    }