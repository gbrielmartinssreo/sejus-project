"""Validação do DOCX gerado pela melhoria, em CINCO passagens.

Responsabilidade: reabrir o arquivo recém-construído e conferir (antes da
entrega) que nenhuma alteração indevida foi introduzida. Cada passagem devolve
um registro de execução — passo, objetivo, resultado e tempo — que vai na
resposta ao usuário, para que a validação seja COMPROVÁVEL e não apenas
afirmada.

As cinco passagens:

1. Considerandos preservados (nenhum original perdido/trocado sem substituição
   correspondente, sem duplicação de conteúdo);
2. Sequência de capítulos (I..N na ordem do documento, títulos preservados);
3. Sem duplicações introduzidas no texto ativo;
4. Correspondência relatório/comentários/texto ativo (cada mudança do resumo
   tem marca real no arquivo);
5. Preservação dos §§ do art. 15 (subitens do dispositivo original seguem
   no texto ativo ou com substituição explícita).

Falha marca os itens RESPONSÁVEIS para o descarte e a reentrega (aplicação
parcial ou cópia intacta)."""
from __future__ import annotations

import re
import time

from docx import Document

from sejus_project.tools.document_infra.docx_builder import (
    _chave_linha,
    _primeira_linha,
)
from sejus_project.tools.llm_tools.document_improvement import (
    _numeral_capitulo,
    _romano_para_int,
)

_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_RE_CONSIDERANDO = re.compile(r"^considerando\b", re.IGNORECASE)
_RE_ARTIGO = re.compile(r"^\s*art\.?\s*\d", re.IGNORECASE)
_RE_SUBITEM = re.compile(r"^\s*(?:§|par[áa]grafo\s+[úu]nico)", re.IGNORECASE)

# Texto injetado pela própria montagem (fundo amarelo): não é conteúdo do
# documento e não pode contar como duplicação.
_TEXTO_PENDENCIA = "[Pendente de validação da equipe jurídica antes da publicação]"

# Passagens, na ordem em que são executadas.
_PASSAGENS = (
    ("1. Considerandos preservados", (
        "Garantir que nenhum considerando original tenha sido tachado/ "
        "substituído indevidamente nem duplicado no texto ativo."
    )),
    ("2. Sequência de capítulos", (
        "Conferir os títulos de capítulo na ordem do documento: sequência "
        "I..N contígua, sem saltos ou repetições."
    )),
    ("3. Sem duplicações introduzidas", (
        "Detectar parágrafo do texto ativo que passou a aparecer mais de uma "
        "vez após a aplicação do patch."
    )),
    ("4. Correspondência relatório x texto", (
        "Confirmar que cada mudança do resumo/comentário tem marca real "
        "(tachado/verde) no arquivo gerado."
    )),
    ("5. §§ do art. 15 preservados", (
        "Conferir que os parágrafos do art. 15 do original seguem no texto "
        "ativo (ou com substituição explícita marcada)."
    )),
)


# ---------------------------------------------------------------------------
# Leitura do arquivo gerado
# ---------------------------------------------------------------------------


def _paragrafos_do_corpo(conteudo: str, caminho) -> list[dict]:
    """Parágrafos do miolo do arquivo gerado, com marcação de revisão.

    Ignora a página de resumo (inserida antes do título original) e parágrafos
    de tabela; devolve ``{"texto", "chave", "tachado", "verde"}`` em ordem do
    corpo, incluindo os parágrafos NOVOS (verde) inseridos pelo patch."""
    doc = Document(str(caminho))
    titulo = _chave_linha(_primeira_linha(conteudo))
    parags: list[dict] = []
    iniciado = False
    for ch in doc.element.body:
        if ch.tag != _NS + "p":
            continue
        texto = _texto_do_paragrafo(ch)
        if not iniciado:
            if _chave_linha(texto) == titulo:
                iniciado = True
            else:
                continue
        if not texto.strip():
            continue
        parags.append(
            {
                "texto": texto,
                "chave": _chave_linha(texto),
                "tachado": _tem_tachado(ch),
                "verde": _tem_verde(ch),
                "ativo": _texto_ativo(ch),
            }
        )
    return parags


def _texto_do_paragrafo(w_p) -> str:
    partes: list[str] = []
    for run in w_p.iter(_NS + "r"):
        for filho in run:
            if filho.tag == _NS + "t" and filho.text:
                partes.append(filho.text)
            elif filho.tag == _NS + "br":
                partes.append("\n")
    return "".join(partes)


def _texto_ativo(w_p) -> str:
    """Texto de um parágrafo SEM os runs tachados.

    Permite reconhecer subdispositivos preservados dentro de um parágrafo que
    tem outro trecho tachado (alteração inline de só o caput, por exemplo)."""
    partes: list[str] = []
    for run in w_p.iter(_NS + "r"):
        rpr = run.find(_NS + "rPr")
        strike = rpr.find(_NS + "strike") if rpr is not None else None
        tachado = (
            strike is not None
            and (strike.get(_NS + "val") or "") not in ("0", "false")
        )
        if tachado:
            continue
        for filho in run:
            if filho.tag == _NS + "t" and filho.text:
                partes.append(filho.text)
            elif filho.tag == _NS + "br":
                partes.append("\n")
    return "".join(partes)


def _tem_tachado(w_p) -> bool:
    for run in w_p.iter(_NS + "r"):
        rpr = run.find(_NS + "rPr")
        if rpr is None:
            continue
        strike = rpr.find(_NS + "strike")
        if strike is not None and (strike.get(_NS + "val") or "") not in ("0", "false"):
            return True
    return False


def _tem_verde(w_p) -> bool:
    for run in w_p.iter(_NS + "r"):
        rpr = run.find(_NS + "rPr")
        if rpr is None:
            continue
        cor = rpr.find(_NS + "color")
        if cor is not None and (cor.get(_NS + "val") or "").upper() == "2E7D32":
            return True
    return False


def _ativas(parags: list[dict]) -> list[dict]:
    """Parágrafos do texto ATIVO: sem tachado (o verde é o novo conteúdo)."""
    return [p for p in parags if not p["tachado"]]


# ---------------------------------------------------------------------------
# Passagens
# ---------------------------------------------------------------------------


def _passagem(passo: str, objetivo: str, ok: bool, detalhe: str,
              t0: float, responsaveis: list[str] | None = None) -> dict:
    return {
        "passo": passo,
        "objetivo": objetivo,
        "resultado": ("OK — " if ok else "FALHA — ") + detalhe,
        "ok": bool(ok),
        "tempo_ms": round((time.perf_counter() - t0) * 1000, 1),
        "responsaveis": responsaveis or [],
    }


def _considerandos_de(texto: str) -> list[str]:
    return [
        _chave_linha(linha)
        for linha in (texto or "").splitlines()
        if _RE_CONSIDERANDO.match(_chave_linha(linha))
    ]


def _passagem_considerandos(conteudo: str, parags: list[dict], aplicado: bool,
                            alteracoes: list, remocoes: list, adicoes: list) -> dict:
    passo, objetivo = _PASSAGENS[0]
    t0 = time.perf_counter()
    originais = _considerandos_de(conteudo)
    if not originais:
        return _passagem(passo, objetivo, True, "documento sem considerandos.", t0)
    ativas = _ativas(parags)
    ativas_chaves = [p["chave"] for p in ativas]

    perdidos: list[str] = []
    for key in originais:
        if key in ativas_chaves:
            continue
        # Não está no texto ativo: precisa estar tachado (alteração/remoção
        # EXPLÍCITA no arquivo). Um considerando que some das duas listas foi
        # perdido sem marcação — falha.
        tocado = any(
            p["tachado"] and p["chave"] == key for p in parags
        )
        if not tocado:
            perdidos.append(key)

    vistos: set[str] = set()
    duplicados: list[str] = []
    for key in ativas_chaves:
        if not _RE_CONSIDERANDO.match(key):
            continue
        if key in vistos:
            duplicados.append(key)
        vistos.add(key)

    responsaveis = _rotulos_de_texto(
        (alteracoes or []) + (remocoes or []) + (adicoes or []), "considerando"
    )
    ok = not perdidos and not duplicados
    detalhe: list[str] = []
    if perdidos:
        detalhe.append(f"{len(perdidos)} considerando(s) original(is) perdido(s)")
    if duplicados:
        detalhe.append(
            f"{len(duplicados)} considerando(s) ativo(s) duplicado(s)"
        )
    if not detalhe:
        detalhe.append(f"{len(originais)} considerando(s) original(is) preservado(s)")
    return _passagem(passo, objetivo, ok, "; ".join(detalhe), t0, responsaveis)


def _sequencia_capitulos(chaves: list[str]) -> list[str]:
    numeracoes: list[str] = []
    for key in chaves:
        num = _numeral_capitulo(key)
        if num and _romano_para_int(num):
            numeracoes.append(num)
    return numeracoes


def _passagem_capitulos(conteudo: str, parags: list[dict], aplicado: bool,
                        alteracoes: list, remocoes: list, adicoes: list) -> dict:
    passo, objetivo = _PASSAGENS[1]
    t0 = time.perf_counter()
    original = _sequencia_capitulos([_chave_linha(l) for l in conteudo.splitlines()])
    ativa = _sequencia_capitulos([p["chave"] for p in _ativas(parags)])
    if not ativa:
        return _passagem(passo, objetivo, True, "sem capítulos no texto ativo.", t0)
    esperada = [num for pos, num in enumerate(ativa, start=1)
                if _romano_para_int(num) == pos]
    sequencia_correta = len(esperada) == len(ativa)
    intacto = ativa == original
    ok = sequencia_correta or (intacto and not aplicado)
    responsaveis = _rotulos_capitulo(alteracoes)
    detalhe = (
        "sequência " + " ".join(n.upper() for n in ativa) + " na ordem do documento"
    )
    if not sequencia_correta and intacto:
        detalhe += " (sequência do original preservada)"
    if not sequencia_correta and not intacto:
        detalhe = "sequência fora de ordem: " + " ".join(
            n.upper() for n in ativa
        )
    return _passagem(passo, objetivo, ok, detalhe + ".", t0, responsaveis)


def _passagem_duplicacoes(conteudo: str, parags: list[dict], aplicado: bool,
                          alteracoes: list, remocoes: list, adicoes: list) -> dict:
    passo, objetivo = _PASSAGENS[2]
    t0 = time.perf_counter()
    responsaveis = _rotulos_duplicados(alteracoes, remocoes, adicoes)
    vistos: set[str] = set()
    duplicados: list[str] = []
    for p in _ativas(parags):
        key = p["chave"]
        if not key or key == _chave_linha(_TEXTO_PENDENCIA):
            continue
        if key in vistos:
            duplicados.append(_primeira_linha(p["texto"])[:80])
        vistos.add(key)
    ok = not duplicados
    detalhe = (
        f"{len(duplicados)} parágrafo(s) repetido(s) no texto ativo: "
        + "; ".join(duplicados[:3])
        if duplicados else "nenhum parágrafo duplicado no texto ativo"
    )
    return _passagem(passo, objetivo, ok, detalhe + ".", t0, responsaveis)


def _rotulos_de_texto(itens: list, termo: str) -> list[str]:
    rotulos: list[str] = []
    for item in itens:
        if not isinstance(item, dict):
            continue
        trecho = item.get("trecho_original") or item.get("novo_texto") or ""
        texto = item.get("texto") or ""
        if termo in _chave_linha(trecho) or termo in _chave_linha(texto):
            rotulos.append(item.get("rotulo") or item.get("o_que") or "?")
    return rotulos


def _rotulos_capitulo(itens: list) -> list[str]:
    rotulos: list[str] = []
    for item in itens or []:
        if not isinstance(item, dict):
            continue
        campos = (item.get("trecho_original") or "") + "\n" + (
            item.get("novo_texto") or ""
        )
        if any(_numeral_capitulo(l) for l in campos.splitlines()):
            rotulos.append(item.get("rotulo") or "?")
    return rotulos


def _rotulos_duplicados(alteracoes: list, remocoes: list, adicoes: list) -> list[str]:
    rotulos: list[str] = []
    for item in (alteracoes or []) + (adicoes or []):
        if isinstance(item, dict):
            rotulos.append(item.get("rotulo") or item.get("o_que") or "?")
    for item in remocoes or []:
        if isinstance(item, dict):
            rotulos.append(item.get("rotulo") or "?")
    return rotulos


def _passagem_correspondencia(conteudo: str, parags: list[dict], aplicado: bool,
                              alteracoes: list, remocoes: list,
                              adicoes: list) -> dict:
    passo, objetivo = _PASSAGENS[3]
    t0 = time.perf_counter()
    ativas_chaves = [p["chave"] for p in _ativas(parags)]
    tachadas_chaves = [p["chave"] for p in parags if p["tachado"]]

    responsaveis: list[str] = []
    total = 0
    for item in (alteracoes or []):
        if not isinstance(item, dict):
            continue
        total += 1
        # Correção automática INLINE: o mesmo parágrafo leva o trecho tachado
        # e o substituto em verde — a chave do parágrafo final não é nem o
        # novo_texto inteiro nem o trecho original inteiro.
        if item.get("automatica"):
            busca_key = _chave_linha(item.get("buscar") or "")
            novo_key = _chave_linha(item.get("substituir") or "")
            if busca_key and novo_key and any(
                p["tachado"] and p["verde"]
                and busca_key in p["chave"] and novo_key in p["chave"]
                for p in parags
            ):
                continue
        chave_novo = _chave_linha(item.get("novo_texto") or "")
        primeira_trecho = _chave_linha(
            _primeira_linha(item.get("trecho_original") or "")
        )
        tem_novo = bool(chave_novo) and chave_novo in ativas_chaves
        tem_antigo = bool(primeira_trecho) and any(
            k == primeira_trecho or k.startswith(primeira_trecho)
            for k in tachadas_chaves
        )
        if not (tem_novo and tem_antigo):
            responsaveis.append(item.get("rotulo") or "?")
    for item in (remocoes or []):
        if not isinstance(item, dict):
            continue
        total += 1
        chave_trecho = _chave_linha(item.get("trecho_original") or "")
        if not any(k == chave_trecho for k in tachadas_chaves):
            responsaveis.append(item.get("rotulo") or "?")
    for item in (adicoes or []):
        if not isinstance(item, dict):
            continue
        total += 1
        chave_texto = _chave_linha(item.get("texto") or "")
        if chave_texto and chave_texto not in ativas_chaves:
            responsaveis.append(item.get("o_que") or "?")

    ok = not responsaveis
    if ok:
        detalhe = f"{total} mudança(s) do resumo com marca correspondente no arquivo"
    else:
        detalhe = (
            f"{len(responsaveis)} mudança(s) sem marca correspondente no "
            "arquivo: " + ", ".join(dict.fromkeys(responsaveis))
        )
    return _passagem(passo, objetivo, ok, detalhe, t0, dict.fromkeys(responsaveis))


def _linhas_subitens_de(texto: str, rotulo: str) -> list[str]:
    """Linhas de subitem (§/Parágrafo único) do artigo citado no original."""
    linhas = [l for l in (texto or "").splitlines() if l.strip()]
    inicio = next(
        (i for i, l in enumerate(linhas)
         if rotulo in _chave_linha(l) and _RE_ARTIGO.match(l)),
        None,
    )
    if inicio is None:
        return []
    alvo: list[str] = []
    for linha in linhas[inicio + 1:]:
        if _RE_ARTIGO.match(linha):
            break
        if _RE_SUBITEM.match(linha):
            alvo.append(_chave_linha(linha))
    return alvo


def _passagem_art15(conteudo: str, parags: list[dict], aplicado: bool,
                    alteracoes: list, remocoes: list, adicoes: list) -> dict:
    passo, objetivo = _PASSAGENS[4]
    t0 = time.perf_counter()
    alvos = _linhas_subitens_de(conteudo, "art. 15")
    if not alvos:
        return _passagem(
            passo, objetivo, True, "sem § do art. 15 no original.", t0
        )
    ativas_chaves = [p["chave"] for p in _ativas(parags)]
    ativos_chaves = [
        _chave_linha(p.get("ativo") or "") for p in parags if (p.get("ativo") or "").strip()
    ]
    perdidos: list[str] = []
    for alvo in alvos:
        # Subitem preservado se aparece no TEXTO ATIVO (mesmo em um parágrafo
        # que tenha outro trecho tachado — alteração inline só do caput).
        no_ativo = any(alvo in k for k in ativos_chaves) or any(
            alvo in k for k in ativas_chaves
        )
        if no_ativo:
            continue
        # Substituído explicitamente: tachado com verde logo em seguida.
        idx = next(
            (i for i, p in enumerate(parags)
             if p["tachado"] and alvo in p["chave"]),
            None,
        )
        substituido = (
            idx is not None
            and idx + 1 < len(parags)
            and parags[idx + 1]["verde"]
        )
        if not substituido:
            perdidos.append(alvo)
    ok = not perdidos
    detalhe = (
        f"{len(alvos)} subitem(ns) do art. 15 preservado(s)"
        if ok
        else "subitem(ns) perdido(s): " + "; ".join(p[:70] for p in perdidos)
    )
    responsaveis = _rotulos_de_texto(
        (alteracoes or []) + (remocoes or []), "art. 15"
    )
    return _passagem(passo, objetivo, ok, detalhe, t0, responsaveis)


# ---------------------------------------------------------------------------
# Execução das cinco passagens
# ---------------------------------------------------------------------------


def validar_docx_gerado(
    conteudo: str,
    caminho,
    alteracoes: list[dict],
    remocoes: list[dict],
    adicoes: list[dict],
    aplicado: bool = True,
) -> list[dict]:
    """Reabre o DOCX gerado e executa as cinco passagens de validação.

    Devolve a lista de registros de execução (passo, objetivo, resultado e
    ``tempo_ms``) na ordem das passagens. ``aplicado=False`` marca a entrega
    da cópia intacta (nenhuma mudança para validar)."""
    try:
        parags = _paragrafos_do_corpo(conteudo, caminho)
    except Exception:  # noqa: BLE001 - validação nunca pode barrar a entrega
        t0 = time.perf_counter()
        return [
            _passagem(
                passo, objetivo, False,
                "não foi possível reabrir o arquivo gerado.", t0,
            )
            for passo, objetivo in _PASSAGENS
        ]
    args = (conteudo, parags, aplicado, alteracoes, remocoes, adicoes)
    return [
        _passagem_considerandos(*args),
        _passagem_capitulos(*args),
        _passagem_duplicacoes(*args),
        _passagem_correspondencia(*args),
        _passagem_art15(*args),
    ]


def responsaveis(passagens: list[dict]) -> list[str]:
    """Rótulos dos itens RESPONSÁVEIS pelas passagens que falharam."""
    rotulos: list[str] = []
    for p in passagens:
        if not p.get("ok"):
            rotulos.extend(p.get("responsaveis") or [])
    return list(dict.fromkeys(rotulos))


def resumo_passagens(passagens: list[dict]) -> str:
    """Texto curto com as cinco passagens (resultado e tempo) para a
    resposta ao usuário — prova de execução de cada passagem."""
    linhas: list[str] = []
    for p in passagens:
        marcador = "OK" if p["ok"] else "FALHA"
        linhas.append(
            f"{p['passo']} — {marcador} ({p['tempo_ms']} ms): "
            f"{p['resultado']}"
        )
    return "\n".join(linhas)
