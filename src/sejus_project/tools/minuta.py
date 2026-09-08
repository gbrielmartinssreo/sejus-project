"""Geracao dinamica de minutas: LLM produz a estrutura do ato e o motor DOCX monta o arquivo.

Fluxo:
1. ``gerar_estrutura_minuta`` chama o LLM (function calling) com o pedido do
   usuario, o tipo de ato, o contexto recuperado do RAG e os campos que o
   usuario eventualmente informou. O LLM devolve uma estrutura JSON livre
   (numero, ementa, considerandos, articulacao com incisos/paragrafos,
   fechamento, local/data e assinaturas).
2. ``montar_docx`` duplica o modelo e reconstroi o corpo lancando mao dos
   paragrafos de referencia do modelo (docx_engine), preservando a formatacao.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from sejus_project.llm.ia import perguntar
from sejus_project.tools.docx_engine import (
    all_paragraphs,
    append_paragraph,
    build_paragraph,
    clear_body,
    find_reference,
)
from sejus_project.tools.modelos import PerfilModelo

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

_DEFAULT_RESOLUTIVO = {
    "portaria": "RESOLVE:",
    "portaria conjunta": "R E S O L V E M:",
    "instrução normativa": "RESOLVE:",
    "instrucao normativa": "RESOLVE:",
    "decreto": "D E C R E T A:",
    "retificação": "RESOLVE:",
    "retificacao": "RESOLVE:",
}


# ---------------------------------------------------------------------------
# Schema do function calling usado para extrair a estrutura do ato
# ---------------------------------------------------------------------------

STRUTURA_DEFINITION = {
    "type": "function",
    "function": {
        "name": "apresentar_estrutura_minuta",
        "description": (
            "Apresenta a estrutura completa de uma minuta de ato normativo "
            "para montagem em DOCX. A estrutura deve refletir o pedido do "
            "usuario e os atos recuperados como fundamento, seguindo o estilo "
            "formal dos atos da SEJUS/MT."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "numero": {
                    "type": "string",
                    "description": (
                        "Linha de identificacao do ato, ex: "
                        "'PORTARIA Nº 001/2026/GAB-SEJUS/MT'."
                    ),
                },
                "ementa": {
                    "type": "string",
                    "description": "Ementa do ato, iniciando com 'Dispõe sobre ...'.",
                },
                "preambulo": {
                    "type": "string",
                    "description": (
                        "Preambulo, ex: 'O SECRETÁRIO DE ESTADO DE JUSTIÇA, no "
                        "uso das atribuições que lhe confere o art. 71 da "
                        "Constituição Estadual,'."
                    ),
                },
                "considerandos": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de considerandos, cada um iniciando com 'CONSIDERANDO'.",
                },
                "resolutivo": {
                    "type": "string",
                    "description": "Verbo resolutivo, ex: 'RESOLVE:'.",
                },
                "corpo": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rotulo": {"type": "string", "description": "ex: 'Art. 1º'."},
                            "texto": {"type": "string"},
                            "subitens": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "tipo": {
                                            "type": "string",
                                            "description": "'inciso' ou 'paragrafo'.",
                                        },
                                        "rotulo": {
                                            "type": "string",
                                            "description": "ex: 'I -' ou 'Parágrafo único.'.",
                                        },
                                        "texto": {"type": "string"},
                                    },
                                    "required": ["tipo", "texto"],
                                },
                            },
                        },
                        "required": ["rotulo", "texto"],
                    },
                    "description": "Articulacao do ato (artigos e itens).",
                },
                "fechamento": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rotulo": {"type": "string"},
                            "texto": {"type": "string"},
                        },
                        "required": ["rotulo", "texto"],
                    },
                    "description": "Artigos finais (vigencia e revogacao).",
                },
                "local_data": {
                    "type": "string",
                    "description": "Local e data, ex: 'Cuiabá-MT, 8 de setembro de 2026.'",
                },
                "assinaturas": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "nome": {"type": "string"},
                            "cargo": {"type": "string"},
                        },
                        "required": ["nome", "cargo"],
                    },
                    "description": "Nomes e cargos dos signatarios.",
                },
            },
            "required": ["numero", "ementa", "corpo"],
        },
    },
}


def _resumir_contexto(contexto: list[dict]) -> str:
    linhas = []
    for item in contexto or []:
        tipo = item.get("act_type") or "ATO"
        numero = item.get("act_number") or ""
        texto = (item.get("text") or "").strip()[:1200]
        linhas.append(f"- {tipo} {numero}\n  {texto}")
    return "\n".join(linhas) if linhas else "(nenhum ato recuperado)"


def _sistema():
    return (
        "Voce e um redator experiente de atos normativos da Secretaria de "
        "Estado de Justiça de Mato Grosso (SEJUS/MT). Sua tarefa e redigir "
        "minutas completas, fieis ao estilo juridico dos atos publicados "
        "(Portarias, Instruções Normativas, Decretos, Portarias Conjuntas). "
        "Use linguagem formal, coesa e tecnica. Baseie a articulacao no pedido "
        "do usuario e nos atos recuperados como fundamento, mas nao copie "
        "literalmente o texto recuperado quando ele pertencer a outro ato. "
        "Estruture o numero de artigos conforme a complexidade do tema. "
        "Retorne apenas o JSON da função apresentar_estrutura_minuta."
    )


def _usuario(pedido, tipo_ato, perfil, contexto, valores):
    partes = [
        "Pedido do usuario:",
        pedido,
        "",
        f"Tipo de ato: {tipo_ato}",
        f"Modelo de referencia (formato): {perfil.name}",
        "",
        "Atos recuperados como fundamento (RAG):",
        _resumir_contexto(contexto),
    ]
    if valores:
        partes.extend(
            [
                "",
                "Campos informados pelo usuario (use-os, nao os invente):",
                json.dumps(valores, ensure_ascii=False, indent=2),
            ]
        )
    return "\n".join(partes)


def _limpar(texto: str) -> str:
    return _SIMPLES.sub(" ", (texto or "").strip())


def _padronizar(estrutura: dict, tipo_ato: str) -> dict:
    estrutura["numero"] = _limpar(estrutura.get("numero") or "")
    estrutura["ementa"] = _limpar(estrutura.get("ementa") or "")
    estrutura["preambulo"] = _limpar(estrutura.get("preambulo") or "")
    estrutura["resolutivo"] = _limpar(
        estrutura.get("resolutivo") or _DEFAULT_RESOLUTIVO.get(tipo_ato, "RESOLVE:")
    )
    estrutura["considerandos"] = [
        _limpar(c) for c in estrutura.get("considerandos") or [] if _limpar(c)
    ]
    estrutura["local_data"] = _limpar(estrutura.get("local_data") or "")

    corpo = []
    for item in estrutura.get("corpo") or []:
        if not isinstance(item, dict) or not _limpar(item.get("texto") or ""):
            continue
        novo = {
            "rotulo": _limpar(item.get("rotulo") or ""),
            "texto": _limpar(item["texto"]),
        }
        subitens = []
        for sub in item.get("subitens") or []:
            if isinstance(sub, dict) and _limpar(sub.get("texto") or ""):
                subitens.append(
                    {
                        "tipo": _limpar(sub.get("tipo") or "inciso"),
                        "rotulo": _limpar(sub.get("rotulo") or ""),
                        "texto": _limpar(sub["texto"]),
                    }
                )
        novo["subitens"] = subitens
        corpo.append(novo)
    estrutura["corpo"] = corpo

    fechamento = []
    for item in estrutura.get("fechamento") or []:
        if isinstance(item, dict) and _limpar(item.get("texto") or ""):
            fechamento.append(
                {"rotulo": _limpar(item.get("rotulo") or ""), "texto": _limpar(item["texto"])}
            )

    # Regra suave: o ato precisa de artigo de vigencia (garantia tambem em
    # montar_docx; aqui apenas normalizamos o que veio do LLM).
    estrutura["fechamento"] = fechamento

    assinaturas = []
    for item in estrutura.get("assinaturas") or []:
        if isinstance(item, dict) and _limpar(item.get("nome") or ""):
            assinaturas.append(
                {"nome": _limpar(item["nome"]), "cargo": _limpar(item.get("cargo") or "")}
            )
    estrutura["assinaturas"] = assinaturas

    return estrutura


def gerar_estrutura_minuta(
    pedido: str,
    tipo_ato: str,
    perfil: PerfilModelo,
    contexto: list[dict],
    valores: dict | None = None,
) -> dict:
    """Chama o LLM e devolve a estrutura estruturada da minuta."""
    mensagens = [
        {"role": "system", "content": _sistema()},
        {"role": "user", "content": _usuario(pedido, tipo_ato, perfil, contexto, valores)},
    ]

    resposta = perguntar(mensagens, [STRUTURA_DEFINITION])
    message = resposta.choices[0].message
    if not message.tool_calls:
        raise ValueError("O modelo nao devolveu uma estrutura de minuta valida.")

    argumentos = message.tool_calls[0].function.arguments or "{}"
    estrutura = json.loads(argumentos)
    return _padronizar(estrutura, tipo_ato)


# ---------------------------------------------------------------------------
# Montagem do DOCX
# ---------------------------------------------------------------------------

_QUEDA_REFERENCIA = (
    "artigo",
    "resolutivo",
    "paragrafo",
)

_SIMPLIFICAR_ROTULO = re.compile(r"\s+")


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


def montar_docx(
    perfil: PerfilModelo,
    estrutura: dict,
    output_dir: Path,
) -> Path:
    """Duplica o modelo e monta a minuta preservando a formatacao."""
    doc = _abrir_ou_criar(perfil.file)
    refs = _referencias(doc, perfil)
    _adicionar_vigencia_faltante(estrutura, perfil.act_types[0])

    clear_body(doc)
    body = doc.element.body

    def adicionar(papel: str, rotulo: str, texto: str) -> None:
        if not texto.strip():
            return
        if not _pedir_paragrafo(refs, papel):
            return
        ref = _referencia_para(refs, papel)
        w_p = build_paragraph(ref, rotulo, texto)
        append_paragraph(body, w_p)

    adicionar("titulo", "", estrutura.get("numero", ""))
    adicionar("ementa", "", estrutura.get("ementa", ""))

    for considerando in estrutura.get("considerandos", []):
        adicionar("considerando", "", considerando)

    adicionar("preambulo", "", estrutura.get("preambulo", ""))
    adicionar("resolutivo", "", estrutura.get("resolutivo", ""))

    for artigo in estrutura.get("corpo", []):
        adicionar("artigo", artigo.get("rotulo", ""), artigo["texto"])
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