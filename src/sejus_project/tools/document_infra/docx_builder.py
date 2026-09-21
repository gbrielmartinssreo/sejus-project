"""Montagem fisica de arquivos DOCX a partir da estrutura de uma minuta.

Responsabilidade: duplicar um modelo DOCX real e reconstruir o corpo com os
paragrafos da estrutura (numero, ementa, considerandos, articulacao, fechamento,
data e assinaturas), clonando a formatacao dos paragrafos de referencia do
proprio modelo (ver docx_engine.py). Nao depende de LLM: recebe a estrutura ja
normalizada e devolve o caminho do arquivo gerado.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from docx.oxml.ns import qn

from sejus_project.tools.document_infra.docx_engine import (
    all_paragraphs,
    assinalar_insercao,
    build_paragraph,
    clear_body,
    find_reference,
    paragraph_text,
    realce_amarelo,
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
    caixa baixa.
    """
    texto = (texto or "").translate(_TIPOGRAFIA_EQUIVALENTE)
    return _SIMPLES.sub(" ", texto.strip()).casefold()


_RE_ARTIGO_PAR = re.compile(r"^\s*art\.?\s*(\d+)", re.IGNORECASE)
_RE_SUBITEM_PAR = re.compile(
    r"^\s*(?:§\s*\d|par[áa]grafo\s+[úu]nico|[ivxl]{1,4}\s*[-–—])",
    re.IGNORECASE,
)


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
    * adicionado (``adicoes_estruturais``) → novo artigo em verde, após o
      artigo-base (seus incisos/§) ou no fim do miolo; adição SEM ``lastro``
      (sem ato analogo no acervo) sai destacada em AMARELO como proposta de
      revisão;
    * não citado → fica intacto (o original nunca some por truncamento).

    Cabeçalho, rodapé de imprensa e demais partes do original são preservados.
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

    def paragrafo_novo(texto: str, rotulo: str = "", papel: str = "artigo", destaque: str = "verde"):
        if not texto.strip() or not _pedir_paragrafo(refs, papel):
            return None
        ref = _referencia_para(refs, papel)
        w_p = build_paragraph(ref, rotulo, texto)
        if destaque == "amarelo":
            realce_amarelo(w_p)
        else:
            verde(w_p)
        return w_p

    def _ancoras(item: dict) -> list[str]:
        return [
            _chave_linha(item.get(campo) or "")
            for campo in ("trecho_original", "rotulo")
        ]

    def _encontrar(item: dict, usados: set[int]) -> int | None:
        alvos = [a for a in _ancoras(item) if a]
        for i, wp in enumerate(miolo_pars):
            if i in usados:
                continue
            chave = _chave_linha(paragraph_text(wp))
            if any(chave == a or chave.startswith(a) for a in alvos):
                return i
        return None

    acoes: dict[int, dict] = {}
    for item in alteracoes:
        if not isinstance(item, dict):
            continue
        i = _encontrar(item, set(acoes))
        if i is not None:
            acoes[i] = {"tipo": "alterado", "novo_texto": item.get("novo_texto") or ""}
    for item in remocoes:
        if not isinstance(item, dict):
            continue
        i = _encontrar(item, set(acoes))
        if i is not None:
            acoes[i] = {"tipo": "removido"}

    for i in sorted(acoes):
        wp = miolo_pars[i]
        acao = acoes[i]
        if acao["tipo"] in ("alterado", "removido"):
            tachar(wp)
        if acao["tipo"] == "alterado":
            novo = paragrafo_novo(acao["novo_texto"])
            if novo is not None:
                wp.addnext(novo)

    insercoes: dict[int | None, list] = {}
    for ad in adicoes:
        if not isinstance(ad, dict):
            continue
        texto = (ad.get("texto") or "").strip()
        if not texto:
            continue
        # O 'texto' de adicoes_estruturais já é o parágrafo COMPLETO (inclui
        # o rótulo 'Art. Nº-A ...'): não duplica o rótulo no build_paragraph.
        # Adição sem 'lastro' (sem ato análogo no acervo) sai em AMARELO como
        # proposta de revisão; com 'lastro' mantém o verde.
        destaque = "amarelo" if not (ad.get("lastro") or "").strip() else "verde"
        w_p = paragrafo_novo(texto, destaque=destaque)
        if w_p is None:
            continue
        alvo = _indice_ancora_adicao(miolo_pars, ad.get("o_que") or "", ad.get("posicao") or "")
        insercoes.setdefault(alvo, []).append(w_p)

    for alvo in sorted(insercoes, key=lambda x: -1 if x is None else x):
        if alvo is not None:
            no = miolo_pars[alvo]
        elif miolo_pars:
            no = miolo_pars[-1]
        else:
            no = ancora
        for w_p in insercoes[alvo]:
            no.addnext(w_p)
            no = w_p

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{perfil.name}_{uuid.uuid4().hex[:8]}.docx"
    doc.save(str(output_path))
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

    return Document(str(caminho))
