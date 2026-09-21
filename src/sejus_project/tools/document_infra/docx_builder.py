"""Montagem fisica de arquivos DOCX a partir da estrutura de uma minuta.

Responsabilidade: duplicar um modelo DOCX real e reconstruir o corpo com os
paragrafos da estrutura (numero, ementa, considerandos, articulacao, fechamento,
data e assinaturas), clonando a formatacao dos paragrafos de referencia do
proprio modelo (ver docx_engine.py). Nao depende de LLM: recebe a estrutura ja
normalizada e devolve o caminho do arquivo gerado.
"""
from __future__ import annotations

import difflib
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
    tachar,
    verde,
)
from sejus_project.tools.document_infra.modelos import PerfilModelo

_SIMPLES = re.compile(r"\s+")

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
    """Chave de comparação por linha/parágrafo (espaços colapsados, caixa baixa)."""
    return _SIMPLES.sub(" ", (texto or "").strip()).casefold()


def _paragrafos_estrutura(estrutura: dict) -> list[dict]:
    """Converte a estrutura da minuta numa lista ordenada de parágrafos.

    Cada item guarda o ``papel`` (para escolher a referência de formatação), o
    ``rotulo`` e o ``texto`` — mesmos dados que ``montar_docx`` usa para montar
    o corpo, agora com a ordem de redação do documento preservada.
    """
    paragrafos: list[dict] = []

    def add(papel: str, rotulo: str, texto: str) -> None:
        texto = (texto or "").strip()
        if texto:
            paragrafos.append(
                {"papel": papel, "rotulo": (rotulo or "").strip(), "texto": texto}
            )

    add("titulo", "", estrutura.get("numero", ""))
    add("ementa", "", estrutura.get("ementa", ""))

    for considerando in estrutura.get("considerandos") or []:
        add("considerando", "", considerando)

    add("preambulo", "", estrutura.get("preambulo", ""))
    add("resolutivo", "", estrutura.get("resolutivo", ""))

    for artigo in estrutura.get("corpo") or []:
        if artigo.get("tipo") == "capitulo":
            add("capitulo", "", artigo.get("texto", ""))
            continue
        add("artigo", artigo.get("rotulo", ""), artigo.get("texto", ""))
        for sub in artigo.get("subitens") or []:
            papel = "paragrafo" if sub.get("tipo") == "paragrafo" else "inciso"
            add(papel, sub.get("rotulo", ""), sub.get("texto", ""))

    for fechamento in estrutura.get("fechamento") or []:
        add("artigo", fechamento.get("rotulo", ""), fechamento.get("texto", ""))

    add("data", "", estrutura.get("local_data", ""))
    for assinatura in estrutura.get("assinaturas") or []:
        add("assinatura", "", assinatura.get("nome", ""))
        add("assinatura", "", assinatura.get("cargo", ""))

    return paragrafos


def _chave_paragrafos(meta: dict) -> str:
    """Linha de comparação de um parágrafo da estrutura (rótulo + texto)."""
    texto = f"{meta['rotulo']} {meta['texto']}".strip() if meta["rotulo"] else meta["texto"]
    return _chave_linha(texto)


def montar_docx_revisado(
    perfil: PerfilModelo,
    estrutura: dict,
    output_dir: Path,
    adicoes_rotulos: set[str] | None = None,
) -> Path:
    """Duplica o DOCX original e marca visualmente as mudanças da melhoria.

    Diferente de ``montar_docx`` (que reconstroi o corpo a partir do zero),
    aqui o resultado é uma cópia do próprio arquivo original: cada parágrafo do
    miolo normativo é comparado (diff linha a linha) contra a estrutura
    melhorada —

    * igual → fica intacto;
    * removido → fica visível com tachado;
    * alterado → o antigo sai tachado seguido do novo em verde;
    * adicionado → entra em verde.

    Cabeçalho, rodapé de imprensa e demais partes do original são preservados.
    ``adicoes_rotulos`` (rótulos normalizados dos artigos novos) é mantido como
    metadado para diagnóstico — visualmente toda adição fica verde.
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
    antes = [_chave_linha(paragraph_text(wp)) for wp in miolo_pars]
    novos = _paragrafos_estrutura(estrutura)
    depois = [_chave_paragrafos(p) for p in novos]

    def novo_paragrafo(meta: dict):
        papel = meta["papel"]
        if not _pedir_paragrafo(refs, papel):
            return None
        ref = _referencia_para(refs, papel)
        w_p = build_paragraph(ref, meta["rotulo"], meta["texto"])
        verde(w_p)
        return w_p

    matcher = difflib.SequenceMatcher(None, antes, depois, autojunk=False)
    for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
        if opcode in ("equal", "delete", "replace"):
            prev_wp = None
            for k in range(i1, i2):
                prev_wp = miolo_pars[k]
                if opcode != "equal":
                    tachar(prev_wp)
            if opcode == "replace":
                for meta in novos[j1:j2]:
                    w_p = novo_paragrafo(meta)
                    if w_p is not None:
                        prev_wp.addnext(w_p)
                        prev_wp = w_p
        else:  # insert
            alvo = miolo_pars[i2] if i2 < len(miolo_pars) else ancora
            for meta in novos[j1:j2]:
                w_p = novo_paragrafo(meta)
                if w_p is not None:
                    alvo.addprevious(w_p)

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
