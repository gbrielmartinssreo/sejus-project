"""Montagem fisica de arquivos DOCX a partir da estrutura de uma minuta.

Responsabilidade: duplicar um modelo DOCX real e reconstruir o corpo com os
paragrafos da estrutura (numero, ementa, considerandos, articulacao, fechamento,
data e assinaturas), clonando a formatacao dos paragrafos de referencia do
proprio modelo (ver docx_engine.py). Nao depende de LLM: recebe a estrutura ja
normalizada e devolve o caminho do arquivo gerado.
"""
from __future__ import annotations

import copy
import re
import shutil
import uuid
from pathlib import Path

from docx.enum.text import WD_BREAK
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from sejus_project.tools.document_infra.docx_comments import adicionar_comentario
from sejus_project.tools.document_infra.docx_engine import (
    XML_SPACE,
    _run_text,
    all_paragraphs,
    assinalar_insercao,
    build_paragraph,
    clear_body,
    destachar,
    find_reference,
    normalizar_medidas,
    paragraph_text,
    set_run_text,
    sombrear,
    tachar,
    verde,
)
from sejus_project.tools.document_infra.modelos import PerfilModelo

_SIMPLES = re.compile(r"\s+")

# Equivalentes tipográficos para _chave_linha: travessões/hífens variantes -> '-',
# aspas curvas -> retas, espaços de largura variável -> espaço simples.
_TIPOGRAFIA_EQUIVALENTE = str.maketrans(
    {
        "–": "-",
        "—": "-",
        "‒": "-",
        "‑": "-",
        "−": "-",
        "‘": "'",
        "’": "'",
        "‚": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "«": '"',
        "»": '"',
        "\u00a0": " ",
        "\u2007": " ",
        "\u2009": " ",
        "\u202f": " ",
        "\u2003": " ",
    }
)

_VERBO_ATO = {
    "portaria": "Esta Portaria",
    "portaria conjunta": "Esta Portaria Conjunta",
    "instrução normativa": "Esta Instrução Normativa",
    "instrucao normativa": "Esta Instrução Normativa",
    "decreto": "Este Decreto",
    "retificação": "Esta Retificação",
    "retificacao": "Esta Retificação",
}


# ---------------------------------------------------------------------------
# Montagem do DOCX
# ---------------------------------------------------------------------------

_QUEDA_REFERENCIA = (
    "artigo",
    "resolutivo",
    "paragrafo",
)

# Autor das revisões de inserção (track changes) gravadas no DOCX melhorado.
_AUTOR_REVISAO = "editor"

# Linhas típicas do rodapé de imprensa do Diário Oficial. Usado para saber onde
# termina o corpo normativo e preservar o rodapé quando o modelo do usuário for
# uma captura do Diário (cabeçalho e rodapé ficam no corpo, e não na seção).
_RE_RODAPE_IMPRENSA = re.compile(
    r"(govern[oa] do estado de mato grosso|seplag|imprensa oficial|iomat)",
    re.IGNORECASE,
)


def _idx_rodape_imprensa(children: list) -> int | None:
    """Índice do último parágrafo do corpo que parece rodapé de imprensa."""
    idx = None
    for i, ch in enumerate(children):
        if ch.tag == qn("w:p") and _RE_RODAPE_IMPRENSA.search(paragraph_text(ch)):
            idx = i
    return idx


def _miolo_indices(doc, refs: dict, preservar_moldura: bool) -> tuple[int, int, object]:
    """Localiza a faixa do corpo normativo (inicio, fim, ancora) sem remover.

    * ``preservar_moldura=True`` (modelo do usuário — captura do Diário): o
      miolo vai do título até o rodapé de imprensa (ou o fim do corpo); o
      cabeçalho antes do título e o rodapé ficam preservados.
    * Caso contrário (templates oficiais): todo o corpo é o miolo.
    """
    body = doc.element.body
    children = list(body)
    sect_pr = body.find(qn("w:sectPr"))
    ancora = sect_pr if sect_pr is not None else body

    if not preservar_moldura:
        return 0, len(children), ancora

    titulo_wp = refs.get("titulo")
    idx_titulo = next(
        (i for i, ch in enumerate(children) if ch is titulo_wp),
        None,
    )
    if titulo_wp is None or idx_titulo is None:
        return 0, len(children), ancora

    idx_rodape = _idx_rodape_imprensa(children)
    if idx_rodape is not None and idx_rodape < idx_titulo:
        idx_rodape = None
    fim = idx_rodape if idx_rodape is not None else len(children)
    ancora_miolo = children[idx_rodape] if idx_rodape is not None else ancora
    return idx_titulo, fim, ancora_miolo


def _preparar_corpo(doc, refs: dict, preservar_moldura: bool):
    """Prepara o corpo para a reconstrução da minuta.

    Devolve o elemento-âncora: os novos parágrafos são inseridos imediatamente
    antes dele.

    * ``preservar_moldura=True`` (modelo do usuário — captura do Diário):
      mantém os parágrafos antes do título (cabeçalho do Diário Oficial) e o
      rodapé de imprensa no final, removendo apenas o miolo normativo.
    * Caso contrário (templates oficiais): limpa o corpo e insere antes do
      sectPr (comportamento original — cabeçalho/rodapé vivem na seção).
    """
    body = doc.element.body
    inicio, fim, ancora = _miolo_indices(doc, refs, preservar_moldura)

    if not preservar_moldura or refs.get("titulo") is None:
        clear_body(doc)
        return ancora

    children = list(body)
    for i, ch in enumerate(children):
        if inicio <= i < fim and ch.tag in (qn("w:p"), qn("w:tbl")):
            body.remove(ch)

    return ancora


def _referencias(doc, perfil: PerfilModelo) -> dict:
    paragraphos = all_paragraphs(doc)
    refs = {}
    for papel, pattern in perfil.patterns.items():
        refs[papel] = find_reference(paragraphos, pattern)
    return refs


def _pedir_paragrafo(refs: dict, papel: str) -> bool:
    if refs.get(papel) is not None:
        return True
    for fallback in _QUEDA_REFERENCIA:
        if refs.get(fallback) is not None:
            return True
    return False


def _referencia_para(refs: dict, papel: str):
    ref = refs.get(papel)
    if ref is not None:
        return ref
    for fallback in _QUEDA_REFERENCIA:
        if refs.get(fallback) is not None:
            return refs[fallback]
    return None


def _adicionar_vigencia_faltante(estrutura: dict, tipo_ato: str) -> None:
    fechamento = [f for f in estrutura.get("fechamento") or [] if isinstance(f, dict)]
    if any("entra em vigor" in (f.get("texto") or "").casefold() for f in fechamento):
        return
    verbo = _VERBO_ATO.get(tipo_ato, "Este ato")
    fechamento.append(
        {
            "rotulo": "",
            "texto": f"{verbo} entra em vigor na data de sua publicação.",
        }
    )
    estrutura["fechamento"] = fechamento


def _chave_rotulo(rotulo: str) -> str:
    """Normaliza um rotulo ('Art. 6º-A', 'art. 6°a' ...) para comparacao."""
    chave = _SIMPLES.sub(" ", (rotulo or "").strip().casefold())
    return chave.rstrip(".")


def montar_docx(
    perfil: PerfilModelo,
    estrutura: dict,
    output_dir: Path,
    insercoes_rastreadas: set[str] | None = None,
) -> Path:
    """Duplica o modelo e monta a minuta preservando a formatacao.

    Artigos cujo rotulo esteja em ``insercoes_rastreadas`` (formato
    normalizado) sao gravados como revisao de insercao do Word (``<w:ins>``):
    o revisor precisa aceitar/rejeitar essas adicoes antes de publicar."""
    doc = _abrir_ou_criar(perfil.file)
    refs = _referencias(doc, perfil)
    _adicionar_vigencia_faltante(estrutura, perfil.act_types[0])

    ancora = _preparar_corpo(doc, refs, perfil.preservar_moldura)
    visados = {_chave_rotulo(r) for r in (insercoes_rastreadas or set())}
    n_insercoes = {"n": 0}

    def adicionar(papel: str, rotulo: str, texto: str, rastrear: bool = False) -> None:
        if not texto.strip():
            return
        if not _pedir_paragrafo(refs, papel):
            return
        ref = _referencia_para(refs, papel)
        w_p = build_paragraph(ref, rotulo, texto)
        if rastrear:
            n_insercoes["n"] += 1
            assinalar_insercao(w_p, n_insercoes["n"], _AUTOR_REVISAO)
        ancora.addprevious(w_p)

    adicionar("titulo", "", estrutura.get("numero", ""))
    adicionar("ementa", "", estrutura.get("ementa", ""))

    for considerando in estrutura.get("considerandos", []):
        adicionar("considerando", "", considerando)

    adicionar("preambulo", "", estrutura.get("preambulo", ""))
    adicionar("resolutivo", "", estrutura.get("resolutivo", ""))

    for artigo in estrutura.get("corpo", []):
        if artigo.get("tipo") == "capitulo":
            adicionar("capitulo", "", artigo.get("texto", ""))
            continue
        rastrear = _chave_rotulo(artigo.get("rotulo", "")) in visados
        adicionar("artigo", artigo.get("rotulo", ""), artigo["texto"], rastrear=rastrear)
        for sub in artigo.get("subitens", []):
            papel = "paragrafo" if sub.get("tipo") == "paragrafo" else "inciso"
            adicionar(papel, sub.get("rotulo", ""), sub["texto"])

    for fechamento in estrutura.get("fechamento", []):
        adicionar("artigo", fechamento.get("rotulo", ""), fechamento["texto"])

    adicionar("data", "", estrutura.get("local_data", ""))

    for assinatura in estrutura.get("assinaturas", []):
        adicionar("assinatura", "", assinatura.get("nome", ""))
        adicionar("assinatura", "", assinatura.get("cargo", ""))

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{perfil.name}_{uuid.uuid4().hex[:8]}.docx"
    doc.save(str(output_path))
    return output_path


# ---------------------------------------------------------------------------
# Montagem do DOCX com mudanças marcadas (cópia revisada do original)
# ---------------------------------------------------------------------------

def _chave_linha(texto: str) -> str:
    """Chave de comparação por linha/parágrafo.

    Normaliza variantes de tipografia (travessões e hífens, aspas curvas e os
    vários espaços unicode) para que diferenças puramente tipográficas não
    virem falsos 'replace' no diff, depois colapsa espaços e converte para
    caixa baixa. Sinal de pontuação final ('.', ',', ';', ':') é descartado:
    '...nas unidades penais.' e '...nas unidades penais;' são o MESMO
    considerando — sem isso a rede de duplicação deixaria passar conteúdo
    repetido que só difere na pontuação final.
    """
    texto = (texto or "").translate(_TIPOGRAFIA_EQUIVALENTE)
    chave = _SIMPLES.sub(" ", texto.strip()).casefold()
    return chave.rstrip(" \t.,;:")


_RE_ARTIGO_PAR = re.compile(r"^\s*art\.?\s*(\d+)", re.IGNORECASE)
_RE_SUBITEM_PAR = re.compile(
    r"^\s*(?:§\s*\d|par[áa]grafo\s+[úu]nico|[ivxl]{1,4}\s*[-–—])",
    re.IGNORECASE,
)


def _ancora_rigorosa(
    linhas: list[str | None],
    item: dict,
    usados: set[int] | None = None,
) -> int | None:
    """Parágrafo do original que a mudança alcança, SEM ambiguidade.

    Prioriza ``trecho_original`` (o alvo mais específico): casa o parágrafo
    único OU o par título+subtítulo imediatamente seguinte (a forma usada pela
    renumeração canônica de capítulos). O alvo só vale quando identifica
    EXATAMENTE UM parágrafo; com múltiplas correspondências devolve ``None`` —
    o item é descartado em vez de atingir o parágrafo errado (ex.: rótulo
    genérico 'CONSIDERANDO' ou 'CAPÍTULO III', que existem mais de uma vez no
    documento). Sem ``trecho_original``, o ``rotulo`` só ancora se for único.
    """
    usados = set() if usados is None else usados

    def _candidatos(alvo: str) -> list[int]:
        alvo_chave = _chave_linha(alvo)
        if not alvo_chave:
            return []
        inds: list[int] = []
        for i, linha in enumerate(linhas):
            if i in usados:
                continue
            if not (linha or "").strip():
                continue
            chave = _chave_linha(linha)
            if chave == alvo_chave or chave.startswith(alvo_chave):
                inds.append(i)
                continue
            if i + 1 < len(linhas):
                prox = linhas[i + 1]
                if not (prox or "").strip():
                    continue
                chave_par = chave + " " + _chave_linha(prox)
                if chave_par == alvo_chave or chave_par.startswith(alvo_chave):
                    inds.append(i)
        return inds

    trecho = (item.get("trecho_original") or "").strip()
    if trecho:
        inds = _candidatos(trecho)
        if len(inds) == 1:
            return inds[0]
        return None
    rotulo = (item.get("rotulo") or "").strip()
    if rotulo:
        inds = _candidatos(rotulo)
        if len(inds) == 1:
            return inds[0]
    return None


def _marca_inicial(texto: str) -> str | None:
    """Marca/abertura de um parágrafo-subitem (§ 2º, Parágrafo único, II -)
    normalizada para comparação.

    Usa a MESMA normalização de ``_chave_linha`` (inclui a troca de travessões/
    hífens por '-'), senão a marca do original ('II –') não casa com a linha do
    novo texto ('ii - ...') e o parágrafo substituído não é reconhecido como
    absorvido — deixando o conteúdo antigo ativo no DOCX."""
    m = _RE_SUBITEM_PAR.match(texto or "")
    if not m:
        return None
    return _chave_linha(m.group(0))


def _proximos_subitens_texto(
    textos: list[str | None],
    idx: int,
    limite: int = 8,
) -> list[tuple[int, str]]:
    """(índice, texto) dos parágrafos logo após ``idx`` candidatos a absorção.

    Pára ao cruzar um novo artigo; parágrafos anulados (None, já removidos)
    são pulados. Além dos subitens (§/inciso), inclui o PRIMEIRO parágrafo
    comum imediatamente seguinte (ex.: subtítulo de capítulo "DAS ...") — a
    base para detectar parágrafos absorvidos por um ``novo_texto`` que funde
    caput + parágrafo/inciso (ou título + subtítulo) num único bloco."""
    itens: list[tuple[int, str]] = []
    for j in range(idx + 1, min(len(textos), idx + 1 + limite)):
        texto = textos[j]
        if not texto:
            continue
        if not (texto or "").strip():
            continue
        if _RE_ARTIGO_PAR.match(texto):
            break
        if _RE_SUBITEM_PAR.match(texto):
            itens.append((j, texto))
            continue
        itens.append((j, texto))
        break
    return itens


def _paragrafos_absorvidos(
    novo_texto: str,
    itens: list[tuple[int, str]],
) -> list[int]:
    """Índices absolutos (dentro de ``itens``) dos parágrafos originais que um
    ``novo_texto`` absorve ao fundir caput + parágrafo/inciso num único bloco.

    Um parágrafo seguinte é considerado absorvido quando o novo texto
    (a) reproduz literalmente o conteúdo completo do parágrafo, ou
    (b) com mais de uma linha, reemite a abertura (marca §/Parágrafo
    único/inciso) do parágrafo — reformulação que guarda o dispositivo.
    Estes parágrafos precisam ser marcados como removidos também, senão o
    conteúdo antigo fica DUPLICADO no .docx final."""
    linhas = [
        _chave_linha(x)
        for x in (novo_texto or "").splitlines()
        if (x or "").strip()
    ]
    if len(linhas) < 2:
        return []
    absorvidos: list[int] = []
    for j, texto in itens:
        if _chave_linha(texto) in linhas:
            absorvidos.append(j)
            continue
        marca = _marca_inicial(texto)
        if marca and any(l.startswith(marca) for l in linhas):
            absorvidos.append(j)
    return absorvidos


def _numero_artigo_de(texto: str) -> str | None:
    """Número-base de um parágrafo de artigo ('Art. 6º-A ...' -> '6')."""
    m = _RE_ARTIGO_PAR.match(texto or "")
    return m.group(1) if m else None


def _indice_ancora_adicao(miolo_pars: list, rotulo: str, posicao: str) -> int | None:
    """Índice do parágrafo após o qual inserir um artigo novo.

    Procura o último parágrafo de artigo cujo número-base casar com o rótulo
    (ex.: 'Art. 6º-A' -> '6'; fallback: número citado em ``posicao``) e avança
    sobre os incisos/parágrafos do artigo localizado — o artigo novo entra
    DEPOIS deles. Sem âncora, devolve None (captura no fim do miolo).
    """
    base = _numero_artigo_de(rotulo)
    alvos = [base] if base else []
    alvos += re.findall(r"\d+", posicao or "")
    if not alvos:
        return None
    candidatos = [
        i
        for i, wp in enumerate(miolo_pars)
        if _numero_artigo_de(paragraph_text(wp)) in alvos
    ]
    if not candidatos:
        return None
    idx = candidatos[-1]
    while (
        idx + 1 < len(miolo_pars)
        and _RE_SUBITEM_PAR.match(paragraph_text(miolo_pars[idx + 1]))
    ):
        idx += 1
    return idx


_TEXTO_PENDENCIA = "[Pendente de validação da equipe jurídica antes da publicação]"


def _primeira_linha(texto: str) -> str:
    for linha in (texto or "").splitlines():
        if linha.strip():
            return linha.strip()
    return (texto or "").strip()


def _marcar_absorvidos(
    acoes: dict[int, dict],
    i: int,
    novo_texto: str,
    miolo_pars: list,
) -> None:
    """Marca como "absorvido" os parágrafos-originals que o ``novo_texto`` de
    uma alteração funde (caput + parágrafo/inciso num único bloco). Sem isso o
    texto antigo dos subitens permaneceria DUPLICADO no .docx final."""
    textos = [paragraph_text(wp) for wp in miolo_pars]
    itens = _proximos_subitens_texto(textos, i, limite=8)
    for j in _paragrafos_absorvidos(novo_texto, itens):
        acoes.setdefault(j, {"tipo": "absorvido"})


def _ancora_para_comentario(item: dict) -> str:
    """Trecho do documento onde ancorar o comentário de uma mudança: prioriza
    o texto novo (único), senão o trecho original citado."""
    if item.get("automatica"):
        # Correção injetada no MESMO parágrafo: o texto final não repete nem o
        # original nem o novo integralmente. Ancora no prefixo do trecho entre
        # o início e a variação (intacto após a inserção verde/tachado).
        trecho = (item.get("trecho_original") or "").strip()
        buscar = item.get("buscar") or ""
        if trecho and buscar:
            pos = trecho.find(buscar)
            if pos >= 0:
                return trecho[: min(pos, 80)].strip() or ""
        return _primeira_linha(trecho)[:80]
    for campo in ("novo_texto", "texto", "trecho_original"):
        valor = (item.get(campo) or "").strip()
        if valor:
            return _primeira_linha(valor)[:80]
    return ""


_TEXTO_ORIGEM_APONTAMENTO = "Origem: apontamento da análise, aprovado pelo usuário."

_TEXTO_SEM_LASTRO = (
    "Mudança sem lastro identificado no acervo (a referência pode ter sido "
    "inventada ou a correção é apenas redacional)."
)

_RE_CABECALHO_CAPITULO = re.compile(
    r"^\s*cap[íi]tulo\s+[ivxl]+", re.IGNORECASE
)

_TEXTO_RENUMERACAO_ENGINE = (
    "Correção estrutural: numeração de capítulos reordenada para manter a "
    "sequência numérica do documento (I..N), sem lastro em ato externo."
)

_TEXTO_RENUMERACAO_APONTAMENTO = (
    "Correção estrutural: numeração de capítulos reordenada para corrigir a "
    "duplicidade na numeração (sequência I..N pela ordem do documento)."
)


def _eh_renumeracao(item: dict) -> bool:
    """Diz se a mudança renumera um título de capítulo (novo texto começa com
    'CAPÍTULO N'). Essas correções não dependem de lastro em ato externo."""
    for campo in ("trecho_original", "novo_texto"):
        primeira = _primeira_linha(item.get(campo) or "")
        if primeira and _RE_CABECALHO_CAPITULO.match(primeira):
            return True
    return False


def _texto_comentario(item: dict) -> str:
    """Texto do comentário nativo de uma mudança, conforme a origem dela.

    * Com ``lastro``: mantém o texto atual — 'Lastro: <ato>.' mais as ressalvas
      de validação/coerência quando houver (lastro validado ou aviso de
      divergência), para a equipe jurídica conferir a fonte.
    * Renumeração de capítulo: mensagem estrutural — o comentário explica a
      duplicidade corrigida (I..N) e NÃO inventa lastro em ato externo;
      registra o achado da análise quando a mudança o executa.
    * Sem lastro, mas com origem em apontamento da análise aprovado: registra a
      origem (com o ID do achado quando disponível).
    * Sem lastro e sem origem de análise (iniciativa do modelo): aviso
      equivalente ao de 'lastro não localizado'.
    """
    lastro = (item.get("lastro") or "").strip()
    if lastro:
        texto = f"Lastro: {lastro}."
        for ressalva in (
            (item.get("lastro_aviso") or "").strip(),
            (item.get("coerencia_aviso") or "").strip(),
        ):
            if ressalva:
                texto += f" {ressalva}"
        return texto
    achado = (item.get("achado_id") or "").strip()
    if item.get("automatica"):
        texto = (
            "Correção automática do sistema (redação): "
            + (item.get("detalhe") or "ajuste de redação sem alterar o conteúdo.")
        )
        if achado:
            texto += f" Origem: achado {achado} da análise (aprovado)."
        return texto
    if _eh_renumeracao(item):
        texto = (
            _TEXTO_RENUMERACAO_ENGINE
            if not item.get("origem_apontamento")
            else _TEXTO_RENUMERACAO_APONTAMENTO
        )
        if achado:
            texto += f" Origem: achado {achado} da análise (aprovado)."
        return texto
    if item.get("origem_apontamento"):
        if achado:
            return f"Origem: achado {achado} da análise (aprovado pelo usuário)."
        return _TEXTO_ORIGEM_APONTAMENTO
    return _TEXTO_SEM_LASTRO


def _aplicar_automatica_inline(w_p, item: dict) -> None:
    """Renderização cirúrgica de uma correção automática de redação.

    Dentro do MESMO parágrafo, tacha apenas o trecho ``buscar`` e insere
    ``substituir`` logo depois em verde — o parágrafo não sai inteiro tachado
    nem nasce um parágrafo novo, e o conteúdo (objetivo) do texto é preservado.
    """
    texto = paragraph_text(w_p)
    buscar = item.get("buscar") or ""
    substituir = item.get("substituir") or ""
    if not buscar or buscar not in texto:
        return
    inicio = texto.index(buscar)
    fim = inicio + len(buscar)
    antes, depois = texto[:inicio], texto[fim:]
    runs = w_p.findall(qn("w:r"))
    if not runs:
        return
    label_pr = runs[0].find(qn("w:rPr"))
    best_pr = max(runs, key=_run_text).find(qn("w:rPr"))
    for r in runs:
        w_p.remove(r)

    def _run(texto_: str, pr, cor: str | None = None, tachado: bool = False):
        run = w_p.makeelement(qn("w:r"), {})
        if pr is not None:
            run.append(copy.deepcopy(pr))
        rPr = run.find(qn("w:rPr"))
        if cor or tachado:
            if rPr is None:
                rPr = run.makeelement(qn("w:rPr"), {})
                run.insert(0, rPr)
            if cor:
                color = rPr.find(qn("w:color"))
                if color is None:
                    color = rPr.makeelement(qn("w:color"), {})
                    rPr.append(color)
                color.set(qn("w:val"), cor)
            if tachado:
                strike = rPr.find(qn("w:strike"))
                if strike is None:
                    strike = rPr.makeelement(qn("w:strike"), {})
                    rPr.append(strike)
                strike.set(qn("w:val"), "true")
        t = run.makeelement(qn("w:t"), {})
        t.text = texto_
        if texto_ and texto_ != texto_.strip():
            t.set(XML_SPACE, "preserve")
        run.append(t)
        return run

    if antes:
        w_p.append(_run(antes, label_pr))
    if buscar:
        w_p.append(_run(buscar, best_pr, cor="C62828", tachado=True))
    if substituir:
        w_p.append(_run(substituir, best_pr, cor="2E7D32"))
    if depois:
        w_p.append(_run(depois, best_pr))


# ---------------------------------------------------------------------------
# Preservacao de subdispositivos dentro de um MESMO paragrafo fisico do Word
# ---------------------------------------------------------------------------
# No Word, um unico <w:p> pode conter caput + § 1º + § 2º separados por <w:br/>.
# A extracao enxerga cada dispositivo como uma linha, mas tratar o <w:p> inteiro
# como alvo fazia a alteracao de so o caput tachar/repor o paragrafo todo — os
# § sumiam do texto ativo. As funcoes abaixo separam o paragrafo fisico em
# DISPOSITIVOS e alteram/removem apenas o dispositivo alvo.

_RE_MARCA_SUBITEM_INLINE = re.compile(
    r"^\s*(§\s*\d+[ºo°]?|par[áa]grafo\s+[úu]nico|[ivxl]{1,4}\s*[-–—]|[a-z]\))",
    re.IGNORECASE,
)


def _separar_runs_nas_quebras(w_p) -> None:
    """Deixa cada ``<w:br/>``/``<w:cr/>`` num run proprio.

    Com isso os limites de dispositivo (quebra) coincidem com limites de run,
    o que permite marcar/inserir sem cortar runs no meio."""
    for run in list(w_p.findall(qn("w:r"))):
        filhos = list(run)
        if not any(c.tag in (qn("w:br"), qn("w:cr")) for c in filhos):
            continue
        rpr = run.find(qn("w:rPr"))
        segmentos: list = []
        atual: list = []
        for c in filhos:
            if c.tag == qn("w:rPr"):
                continue
            if c.tag in (qn("w:br"), qn("w:cr")):
                segmentos.append(atual)
                atual = []
                segmentos.append(c)
            else:
                atual.append(c)
        segmentos.append(atual)
        novos = []
        for segmento in segmentos:
            if isinstance(segmento, list) and not segmento:
                continue
            novo = w_p.makeelement(qn("w:r"), {})
            if rpr is not None:
                novo.append(copy.deepcopy(rpr))
            if isinstance(segmento, list):
                for node in segmento:
                    novo.append(copy.deepcopy(node))
            else:
                novo.append(copy.deepcopy(segmento))
            novos.append(novo)
        for novo in novos:
            run.addprevious(novo)
        w_p.remove(run)


def _runs_com_offset(w_p) -> tuple[list, int]:
    """(run, inicio, fim, tem_quebra) na ordem, com offsets do texto (w:t)."""
    info: list = []
    pos = 0
    for run in w_p.findall(qn("w:r")):
        tem_quebra = any(c.tag in (qn("w:br"), qn("w:cr")) for c in run)
        texto = "".join((t.text or "") for t in run.findall(qn("w:t")))
        info.append((run, pos, pos + len(texto), tem_quebra))
        pos += len(texto)
    return info, pos


def _texto_do_intervalo(info: list, a: int, b: int) -> str:
    out: list[str] = []
    for run, ini, fim, _br in info:
        if fim <= a or ini >= b:
            continue
        texto = "".join((t.text or "") for t in run.findall(qn("w:t")))
        out.append(texto[max(0, a - ini): max(0, b - ini)])
    return "".join(out)


def _dispositivos_do_paragrafo(w_p) -> tuple[list, list, int]:
    """Divide o ``w:p`` em dispositivos delimitados por ``<w:br/>``.

    Devolve ``(spans, info, total)`` onde cada span tem ``inicio``/``fim`` em
    offsets de texto e ``texto`` correspondente."""
    _separar_runs_nas_quebras(w_p)
    info, total = _runs_com_offset(w_p)
    if total <= 0:
        return [], info, 0
    limites = {0, total}
    for _run_, ini, fim, tem_quebra in info:
        if tem_quebra and ini > 0:
            limites.add(ini)
    marcas = sorted(limites)
    spans = []
    for a, b in zip(marcas, marcas[1:]):
        spans.append(
            {"inicio": a, "fim": b, "texto": _texto_do_intervalo(info, a, b)}
        )
    return spans, info, total


def _marcar_run(run, cor: str = "C62828") -> None:
    """Tacha e pinta de vermelho um run (texto substituido/removido)."""
    rpr = run.get_or_add_rPr()
    rpr.get_or_add_strike().val = True
    rpr.get_or_add_color().val = RGBColor.from_string(cor)


def _run_verde_como(modelo, texto: str):
    """Clona ``modelo`` como run verde (sem tachado) com o texto novo."""
    novo = copy.deepcopy(modelo)
    rpr = novo.find(qn("w:rPr"))
    if rpr is not None:
        for tag in (qn("w:strike"), qn("w:dstrike")):
            el = rpr.find(tag)
            if el is not None:
                rpr.remove(el)
        color = rpr.find(qn("w:color"))
        if color is None:
            color = rpr.makeelement(qn("w:color"), {})
            rpr.append(color)
        color.set(qn("w:val"), "2E7D32")
    set_run_text(novo, (texto or "").strip())
    return novo


def _alterar_dispositivo_inline(w_p, item: dict) -> bool:
    """Altera/remove SO o dispositivo alvo dentro de um ``w:p`` com varios.

    Devolve True quando aplicou inline (nao se deve tachar/repor o paragrafo
    inteiro). Falso quando o alvo cobre o paragrafo todo (fluxo normal) ou o
    paragrafo tem um unico dispositivo."""
    alvo = (item.get("trecho_original") or item.get("rotulo") or "").strip()
    if not alvo:
        return False
    spans, info, total = _dispositivos_do_paragrafo(w_p)
    if len(spans) <= 1:
        return False
    alvo_chave = _chave_linha(alvo)
    if not alvo_chave:
        return False

    idxs: list[int] = []
    acumulado = ""
    for k, sp in enumerate(spans):
        ch = _chave_linha(sp["texto"])
        if not ch:
            continue
        if not idxs:
            if ch == alvo_chave or ch.startswith(alvo_chave) or alvo_chave.startswith(ch):
                idxs = [k]
                acumulado = ch
        else:
            combinado = (acumulado + " " + ch).strip()
            # So avanca quando o ALVO abrange tambem este dispositivo.
            if alvo_chave.startswith(combinado):
                idxs.append(k)
                acumulado = combinado
            else:
                break
    if not idxs:
        return False
    # Alvo cobre todos os dispositivos: e substituicao do paragrafo inteiro.
    if len(idxs) == len(spans):
        return False

    inicio = spans[idxs[0]]["inicio"]
    fim = spans[idxs[-1]]["fim"]
    novo_texto = item.get("novo_texto") if item.get("tipo") != "removido" else None
    modelo = None
    ultimo = None
    for run, ini, run_fim, _br in info:
        if run_fim <= inicio or ini >= fim or run_fim == ini:
            continue
        _marcar_run(run)
        if modelo is None or _run_text(run) > _run_text(modelo):
            modelo = run
        ultimo = run
    if ultimo is None:
        return False
    if novo_texto and (novo_texto or "").strip():
        ultimo.addnext(_run_verde_como(modelo, novo_texto))
    return True


def _comentarios_das_mudancas(
    alteracoes: list[dict],
    remocoes: list[dict],
    adicoes: list[dict],
) -> list[tuple[str, str]]:
    """(âncora, texto do comentário) para CADA mudança marcada.

    Diferente de antes (apenas mudanças com ``lastro`` identificado), agora todo
    trecho que vai marcado verde/tachado recebe comentário nativo do Word, para
    a equipe localizar rapidamente o que mudou. O texto varia conforme a origem:
    lastro validado, aviso de divergência, apontamento aprovado da análise ou
    iniciativa do modelo (``_texto_comentario``)."""
    comentarios: list[tuple[str, str]] = []
    for item in (*alteracoes, *remocoes, *adicoes):
        if not isinstance(item, dict):
            continue
        ancora = _ancora_para_comentario(item)
        if not ancora:
            continue
        comentarios.append((ancora, _texto_comentario(item)))
    return comentarios


def _inserir_pendencias(doc, pendentes: list) -> None:
    """(4.2) Sombra o parágrafo com fundo amarelo e (4.3) insere logo abaixo o
    aviso em itálico de pendência de validação jurídica."""
    for w_p in pendentes:
        sombrear(w_p, "FFF3B0")
        marca = doc.add_paragraph()
        run = marca.add_run(_TEXTO_PENDENCIA)
        run.italic = True
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
        w_p.addnext(marca._p)


def _inserir_comentarios(doc, comentarios: list[tuple[str, str]]) -> None:
    """(4.4) Comentários nativos do Word ancorados ao texto das mudanças."""
    for ancora, texto in comentarios:
        adicionar_comentario(doc, ancora, texto)


def _tentar_estilo_tabela(tabela, nome: str) -> None:
    try:
        tabela.style = nome
    except KeyError:
        pass


def _linha_do_resumo(
    tabela,
    rotulo: str,
    tipo: str,
    mudanca: str,
    motivo: str,
) -> None:
    celulas = tabela.add_row().cells
    valores = (rotulo, tipo, _primeira_linha(mudanca), (motivo or "").strip())
    for celula, valor in zip(celulas, valores):
        celula.text = valor


def _inserir_resumo(doc, alteracoes, remocoes, adicoes) -> None:
    """(4.1) Página de resumo no início do arquivo: título, legenda das cores,
    tabela com uma linha por mudança e quebra de página. Cada bloco é inserido
    imediatamente antes do primeiro elemento original de ``body``, na ordem de
    exibição desejada (4.5)."""
    body = doc.element.body
    if not len(body):
        return
    ref = body[0]

    titulo = doc.add_paragraph()
    run_titulo = titulo.add_run("RESUMO DAS ALTERAÇÕES PROPOSTAS")
    run_titulo.bold = True
    run_titulo.font.size = Pt(14)
    ref.addprevious(titulo._p)

    legenda = doc.add_paragraph()
    run_legenda = legenda.add_run(
        "Legenda: (verde) texto proposto; (tachado) texto removido; "
        "(fundo amarelo) trecho pendente de decisão jurídica; "
        "(comentário) lastro identificado. Alterações em amarelo e os "
        "comentários exigem validação da equipe jurídica antes da publicação."
    )
    run_legenda.italic = True
    run_legenda.font.size = Pt(9)
    ref.addprevious(legenda._p)

    if alteracoes or remocoes or adicoes:
        tabela = doc.add_table(rows=1, cols=4)
        _tentar_estilo_tabela(tabela, "Table Grid")
        celulas = tabela.rows[0].cells
        for celula, nome in zip(celulas, ("Artigo", "Tipo", "Mudança", "Motivo")):
            celula.text = nome
            for paragrafo in celula.paragraphs:
                for run in paragrafo.runs:
                    run.bold = True
        for item in alteracoes:
            if not isinstance(item, dict):
                continue
            _linha_do_resumo(
                tabela,
                item.get("rotulo")
                or _primeira_linha(item.get("trecho_original") or "")[:60],
                "Correção",
                item.get("novo_texto") or "",
                item.get("motivo") or item.get("detalhe") or "",
            )
        for item in remocoes:
            if not isinstance(item, dict):
                continue
            _linha_do_resumo(
                tabela,
                item.get("rotulo")
                or _primeira_linha(item.get("trecho_original") or "")[:60],
                "Remoção",
                item.get("trecho_original") or "",
                item.get("motivo") or item.get("detalhe") or "",
            )
        for item in adicoes:
            if not isinstance(item, dict):
                continue
            _linha_do_resumo(
                tabela,
                item.get("o_que") or "novo artigo",
                "Adição estrutural",
                item.get("texto") or "",
                item.get("motivo") or item.get("detalhe") or "",
            )
        ref.addprevious(tabela._tbl)

    quebra = doc.add_paragraph()
    run_quebra = quebra.add_run()
    run_quebra.add_break(WD_BREAK.PAGE)
    ref.addprevious(quebra._p)


def montar_docx_revisado(
    perfil: PerfilModelo,
    alteracoes: list[dict],
    remocoes: list[dict],
    adicoes: list[dict],
    output_dir: Path,
) -> Path:
    """Duplica o DOCX original e marca visualmente as mudanças (patch) da
    melhoria.

    Diferente de ``montar_docx`` (que reconstrói o corpo a partir do zero),
    aqui o resultado é uma cópia do próprio arquivo original: cada mudança das
    listas é ancorada ao parágrafo correspondente do miolo normativo (por
    ``trecho_original``/``rotulo``, chave normalizada para ignorar tipografia) —

    * alterado → o antigo sai tachado seguido do novo em verde;
    * removido → fica visível com tachado;
    * absorvido → parágrafo coberto por um novo texto que funde caput +
      parágrafo/inciso também sai tachado (sem reposição, evita duplicação);
    * adicionado (``adicoes_estruturais``) → novo artigo em verde, após o
      artigo-base (seus incisos/§) ou no fim do miolo;
    * não citado → fica intacto (o original nunca some por truncamento).

    Cabeçalho, rodapé de imprensa e demais partes do original são preservados.

    No topo do arquivo é inserida uma página de resumo (4.1): título, legenda
    das cores, tabela com uma linha por mudança e quebra de página. Parágrafos
    com ``requer_decisao_juridica=True`` ganham fundo amarelo (4.2) e um aviso
    em destaque em itálico logo abaixo (4.3). Toda mudança marcada
    verde/tachado recebe comentário nativo do Word ancorado no texto (4.4),
    carregando o lastro validado, o aviso de divergência ou a origem
    (apontamento aprovado da análise / iniciativa do modelo).
    """
    doc = _abrir_ou_criar(perfil.file)
    refs = _referencias(doc, perfil)
    inicio, fim, ancora = _miolo_indices(doc, refs, perfil.preservar_moldura)

    children = list(doc.element.body)
    miolo_pars = [
        ch
        for i, ch in enumerate(children)
        if inicio <= i < fim and ch.tag == qn("w:p")
    ]
    textos_miolo = [paragraph_text(wp) for wp in miolo_pars]

    def paragrafo_novo(texto: str, rotulo: str = "", papel: str = "artigo"):
        if not texto.strip() or not _pedir_paragrafo(refs, papel):
            return None
        ref = _referencia_para(refs, papel)
        w_p = build_paragraph(ref, rotulo, texto)
        # O parágrafo de referência pode já ter sido tachado neste laço; o texto
        # NOVO não pode herdar tachado/cor (senão pareceria conteúdo removido).
        destachar(w_p)
        verde(w_p)
        return w_p

    def _encontrar(item: dict, usados: set[int]) -> int | None:
        return _ancora_rigorosa(textos_miolo, item, usados)

    acoes: dict[int, dict] = {}

    def _alvos_usados() -> set[int]:
        # Paragrafos ja tratados via inline continuam disponiveis: dois
        # dispositivos diferentes do MESMO <w:p> podem ter alteracoes distintas.
        return {i for i, acao in acoes.items() if acao.get("tipo") != "inline"}

    for item in alteracoes:
        if not isinstance(item, dict):
            continue
        i = _encontrar(item, _alvos_usados())
        if i is None:
            continue
        if item.get("automatica"):
            acoes[i] = {"tipo": "automatica", "item": item}
            continue
        # Paragrafo fisico com varios dispositivos (caput + §): altera SO o
        # dispositivo alvo, preservando os §/incisos nao abrangidos.
        if _alterar_dispositivo_inline(miolo_pars[i], item):
            acoes.setdefault(i, {"tipo": "inline"})
            continue
        acoes[i] = {
            "tipo": "alterado",
            "novo_texto": item.get("novo_texto") or "",
            "rotulo": item.get("rotulo") or "",
            "requer_decisao_juridica": bool(item.get("requer_decisao_juridica")),
        }
        _marcar_absorvidos(acoes, i, item.get("novo_texto") or "", miolo_pars)
    for item in remocoes:
        if not isinstance(item, dict):
            continue
        i = _encontrar(item, _alvos_usados())
        if i is None:
            continue
        removido = dict(item)
        removido["tipo"] = "removido"
        if _alterar_dispositivo_inline(miolo_pars[i], removido):
            acoes.setdefault(i, {"tipo": "inline"})
            continue
        acoes[i] = {"tipo": "removido"}

    pendentes: list[object] = []
    for i in sorted(acoes):
        wp = miolo_pars[i]
        acao = acoes[i]
        if acao["tipo"] == "automatica":
            _aplicar_automatica_inline(wp, acao["item"])
            continue
        if acao["tipo"] == "inline":
            continue
        if acao["tipo"] in ("alterado", "removido", "absorvido"):
            tachar(wp)
        if acao["tipo"] == "alterado":
            # O 'novo_texto' já inclui o rótulo, exatamente como o original.
            novo = paragrafo_novo(acao["novo_texto"])
            if novo is not None:
                wp.addnext(novo)
                if acao.get("requer_decisao_juridica"):
                    pendentes.append(novo)

    insercoes: dict[int | None, list] = {}
    for ad in adicoes:
        if not isinstance(ad, dict):
            continue
        texto = (ad.get("texto") or "").strip()
        if not texto:
            continue
        # O 'texto' de adicoes_estruturais já é o parágrafo COMPLETO (inclui
        # o rótulo 'Art. Nº-A ...'): não duplica o rótulo no build_paragraph.
        w_p = paragrafo_novo(texto)
        if w_p is None:
            continue
        alvo = _indice_ancora_adicao(miolo_pars, ad.get("o_que") or "", ad.get("posicao") or "")
        insercoes.setdefault(alvo, []).append(
            (w_p, bool(ad.get("requer_decisao_juridica")))
        )

    for alvo in sorted(insercoes, key=lambda x: -1 if x is None else x):
        if alvo is not None:
            no = miolo_pars[alvo]
        elif miolo_pars:
            no = miolo_pars[-1]
        else:
            no = ancora
        for w_p, requer_pendente in insercoes[alvo]:
            no.addnext(w_p)
            no = w_p
            if requer_pendente:
                pendentes.append(w_p)

    _inserir_pendencias(doc, pendentes)
    _inserir_comentarios(doc, _comentarios_das_mudancas(alteracoes, remocoes, adicoes))
    _inserir_resumo(doc, alteracoes, remocoes, adicoes)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{perfil.name}_{uuid.uuid4().hex[:8]}.docx"
    doc.save(str(output_path))
    return output_path


def copiar_docx(perfil: PerfilModelo, output_dir: Path) -> Path:
    """Entrega uma cópia INTACTA do documento original.

    Usado como fallback quando nenhuma correção pôde ser aplicada: o arquivo
    preserva o conteúdo original (sem marcas nem página de resumo) para o
    usuário não ficar sem download."""
    origem = Path(perfil.file)
    if not origem.is_file():
        raise FileNotFoundError(f"Documento original nao encontrado: {origem}")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{perfil.name}_{uuid.uuid4().hex[:8]}.docx"
    shutil.copyfile(origem, output_path)
    return output_path


def _abrir_ou_criar(path: str):
    """Abre o DOCX do modelo.

    O modelo pode estar num caminho relativo ou absoluto; caso nao exista,
    cria um documento vazio para nao quebrar a geracao (o LLM ja avisará
    a revisao). Na pratica os modelos existem no repositorio."""
    caminho = Path(path)
    if not caminho.exists():
        raise FileNotFoundError(f"Modelo de documento nao encontrado: {caminho}")
    from docx import Document

    doc = Document(str(caminho))
    # Corrige medidas gravadas como float (ex.: margens 1285.866...) que
    # fazem o python-docx falhar em int() ao ler secao/recuos.
    normalizar_medidas(doc)
    return doc
