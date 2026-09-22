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

from docx.enum.text import WD_BREAK
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from sejus_project.tools.document_infra.docx_comments import adicionar_comentario
from sejus_project.tools.document_infra.docx_engine import (
    all_paragraphs,
    assinalar_insercao,
    build_paragraph,
    clear_body,
    find_reference,
    paragraph_text,
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
    caixa baixa.
    """
    texto = (texto or "").translate(_TIPOGRAFIA_EQUIVALENTE)
    return _SIMPLES.sub(" ", texto.strip()).casefold()


_RE_ARTIGO_PAR = re.compile(r"^\s*art\.?\s*(\d+)", re.IGNORECASE)
_RE_SUBITEM_PAR = re.compile(
    r"^\s*(?:§\s*\d|par[áa]grafo\s+[úu]nico|[ivxl]{1,4}\s*[-–—])",
    re.IGNORECASE,
)


def _marca_inicial(texto: str) -> str | None:
    """Marca/abertura de um parágrafo-subitem (§ 2º, Parágrafo único, II -)
    normalizada para comparação case e acento-insensível."""
    m = _RE_SUBITEM_PAR.match(texto or "")
    if not m:
        return None
    return _SIMPLES.sub(" ", m.group(0).strip().casefold().rstrip("."))


def _proximos_subitens_texto(
    textos: list[str | None],
    idx: int,
    limite: int = 8,
) -> list[tuple[int, str]]:
    """(índice, texto) dos parágrafos-subitem (§/inciso) logo após ``idx``.

    Pára ao cruzar um novo artigo; parágrafos anulados (None, já removidos)
    são pulados. Devolve apenas subitens — a base para detectar parágrafos
    absorvidos por um ``novo_texto`` que funde caput + parágrafo/inciso."""
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
        else:
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
    for campo in ("novo_texto", "texto", "trecho_original"):
        valor = (item.get(campo) or "").strip()
        if valor:
            return _primeira_linha(valor)[:80]
    return ""


def _comentarios_das_mudancas(
    alteracoes: list[dict],
    remocoes: list[dict],
    adicoes: list[dict],
) -> list[tuple[str, str]]:
    """(âncora, texto do comentário) para cada mudança com ``lastro``.

    O comentário nativo do Word carrega o lastro identificado (e a ressalva,
    quando houver), para a equipe jurídica conferir a fonte antes da publicação."""
    comentarios: list[tuple[str, str]] = []
    for item in (*alteracoes, *remocoes, *adicoes):
        if not isinstance(item, dict):
            continue
        lastro = (item.get("lastro") or "").strip()
        if not lastro:
            continue
        ancora = _ancora_para_comentario(item)
        if not ancora:
            continue
        texto = f"Lastro: {lastro}."
        ressalvas = [
            (item.get("lastro_aviso") or "").strip(),
            (item.get("coerencia_aviso") or "").strip(),
        ]
        for ressalva in ressalvas:
            if ressalva:
                texto += f" {ressalva}"
        comentarios.append((ancora, texto))
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
    em destaque em itálico logo abaixo (4.3). Alterações/adições com ``lastro``
    viram comentários nativos do Word ancorados no texto (4.4).
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

    def paragrafo_novo(texto: str, rotulo: str = "", papel: str = "artigo"):
        if not texto.strip() or not _pedir_paragrafo(refs, papel):
            return None
        ref = _referencia_para(refs, papel)
        w_p = build_paragraph(ref, rotulo, texto)
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
        i = _encontrar(item, set(acoes))
        if i is not None:
            acoes[i] = {"tipo": "removido"}

    pendentes: list[object] = []
    for i in sorted(acoes):
        wp = miolo_pars[i]
        acao = acoes[i]
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
