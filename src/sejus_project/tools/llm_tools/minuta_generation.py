"""Geracao da estrutura de minuta por LLM (function calling).

Responsabilidade: montar schema e prompts, chamar o LLM com o pedido do
usuario, o tipo de ato, o contexto recuperado do RAG e os campos informados, e
normalizar a estrutura JSON devolvida (numero, ementa, considerandos,
articulacao com incisos/paragrafos, fechamento, local/data e assinaturas).
Nao manipula DOCX: a montagem fisica do arquivo fica em
``document_infra.docx_builder``.
"""
from __future__ import annotations

import json
import os
import re

from sejus_project.llm.ia import perguntar
from sejus_project.tools.document_infra.modelos import PerfilModelo

_SIMPLES = re.compile(r"\s+")

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
        texto = (item.get("text") or "").strip()[:3000]
        prefixo = f"[tema: {item.get('tema')}] " if item.get("tema") else ""
        linhas.append(f"- {tipo} {numero}\n  {prefixo}{texto}")
    return "\n".join(linhas) if linhas else "(nenhum ato recuperado)"


def _sistema():
    return (
        "Voce e um redator experiente de atos normativos da Secretaria de "
        "Estado de Justiça de Mato Grosso (SEJUS/MT). Sua tarefa e redigir "
        "minutas completas, robustas e fieis ao estilo juridico dos atos "
        "publicados (Portarias, Instruções Normativas, Decretos, Portarias "
        "Conjuntas), com o mesmo nivel de detalhamento e sofisticacao dos "
        "documentos oficiais da SEJUS/MT.\n\n"
        "ORIENTACOES DE DETALHAMENTO:\n"
        "1. Artigos e subitens: produza uma articulacao rica e coerente. Para "
        "tema simples use ao menos 3 a 4 artigos; para tema medio 5 a 7 "
        "artigos; para tema complexo ou institucional 8 ou mais artigos. Sempre "
        "que o tema envolver competencias, atribuicoes, prazos, comissoes, "
        "fluxos, prazos de execucao ou objetos multiplos, desdobre os artigos "
        "em incisos e paragrafos (§) para esmiucar cada ponto.\n"
        "2. Considerandos: fundamente o ato com considerandos bem desenvolvidos "
        "(a partir de 'CONSIDERANDO'), extraindo do contexto RAG os atos, "
        "normas e fundamentos legais correlatos (referencias a Constituicao "
        "Estadual, leis, decretos, instrucoes normativas ou portarias "
        "anteriores quando disponiveis nos atos recuperados).\n"
        "3. Preambulo: redija conforme o padrao do modelo, citando as "
        "atribuicoes legais aplicaveis.\n"
        "4. Uso do contexto RAG: aproveite ao maximo o conteudo dos atos "
        "recuperados como fundamento. Incorpore prazos, procedimentos, "
        "obrigacoes, prazos, comissoes e condicoes que estejam presentes nos "
        "atos recuperados e que sejam pertinentes ao objeto pedido, adaptando "
        "o texto ao novo ato (nao copie literalmente bloco de outro ato, mas "
        "aproveite as regras e detalhes relevantes).\n"
        "5. Fechamento: inclua artigos finais sobre vigencia, revogacao de "
        "disposicoes em contrario e, quando cabivel, regulamentacao/execucao.\n"
        "6. Extensao: prefira minutas mais longas e detalhadas quando o tema "
        "for institucional (comissoes, grupos de trabalho, procedimentos, "
        "estruturas), evitando respostas excessivamente curtas ou rascunhos "
        "resumidos.\n"
        "Retorne apenas o JSON da funcao apresentar_estrutura_minuta."
    )


def _usuario(pedido, tipo_ato, perfil, contexto, valores, modelo_referencia=None):
    partes = [
        "Pedido do usuario:",
        pedido,
        "",
        f"Tipo de ato: {tipo_ato}",
        f"Modelo de referencia (formato): {perfil.name}",
        "",
        (
            "Atos recuperados como fundamento (RAG). Use esse conteudo como "
            "base para prazos, procedimentos, fundamentos legais e detalhes:"
        ),
        _resumir_contexto(contexto),
    ]
    if modelo_referencia:
        partes.extend(
            [
                "",
                (
                    "DOCUMENTO ENVIADO PELO USUARIO COMO MODELO: espelhe a "
                    "estrutura, as secoes e o estilo formal deste documento ao "
                    "redigir a nova minuta (artigos, considerandos, incisos, "
                    "paragrafos e nivel de detalhamento). Conteudo:"
                ),
                str(modelo_referencia)[:8000],
            ]
        )
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
        tipo = _limpar(item.get("tipo") or "artigo").casefold()
        if tipo in ("inciso", "paragrafo") and corpo and corpo[-1].get("tipo") == "artigo":
            # Anexa inciso/parágrafo solto ao artigo anterior
            corpo[-1].setdefault("subitens", []).append(
                {
                    "tipo": tipo,
                    "rotulo": _limpar(item.get("rotulo") or ""),
                    "texto": _limpar(item["texto"]),
                }
            )
            continue
        novo = {
            "tipo": tipo if tipo in ("artigo", "capitulo") else "artigo",
            "rotulo": _limpar(item.get("rotulo") or ""),
            "texto": _limpar(item["texto"]),
        }
        if novo["tipo"] == "capitulo":
            corpo.append(novo)
            continue
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

    corpo_textos = {_limpar(item.get("texto") or "") for item in (estrutura.get("corpo") or []) if isinstance(item, dict) and _limpar(item.get("texto") or "")}
    estrutura["fechamento"] = [f for f in fechamento if _limpar(f.get("texto") or "") not in corpo_textos]

    # Regra suave: o ato precisa de artigo de vigencia (garantia tambem em
    # montar_docx; aqui apenas normalizamos o que veio do LLM).

    assinaturas = []
    seen = set()
    for item in estrutura.get("assinaturas") or []:
        if isinstance(item, dict):
            nome = _limpar(item.get("nome") or "")
            cargo = _limpar(item.get("cargo") or "")
            key = (nome, cargo)
            if key not in seen:
                seen.add(key)
                assinaturas.append({"nome": nome, "cargo": cargo})
    estrutura["assinaturas"] = assinaturas

    return estrutura


def _extrair_json_com_retry(
    mensagens,
    definition,
    max_tokens,
    preservar_completo: bool = False,
):
    """Chama o LLM (function calling) e devolve o argumento JSON já parseado.

    Se a resposta vier truncada (JSON incompleto por estouro do limite de
    tokens), reenvia a conversa com o dobro de ``max_tokens`` e instrução para
    o modelo devolver um JSON válido. Quando ``preservar_completo=True`` (fluxo
    de melhoria), o retry instrui o modelo a reproduzir TODO o conteúdo do
    original sem omitir nem resumir; caso contrário mantém o comportamento de
    permitir reduzir o campo ``corpo``. Falha com mensagem clara se a repetição
    também vier truncada.
    """
    if preservar_completo:
        instrucao_retry = (
            "JSON invalido ou truncado (resposta cortada no limite de tokens). "
            "Reproduza o documento COMPLETO, preservando TODOS os artigos, "
            "considerandos, titulos e trechos do original, sem omitir nem "
            "resumir. Apenas devolva um JSON valido, completo e encerrado."
        )
    else:
        instrucao_retry = (
            "JSON invalido ou truncado (resposta cortada no limite "
            "de tokens). Refaça a estrutura completa, reduzindo o "
            "tamanho do campo 'corpo' se precisar, e devolva um "
            "JSON valido e encerrado."
        )

    for tentativa in range(2):
        resposta = perguntar(mensagens, [definition], max_tokens=max_tokens)
        message = resposta.choices[0].message
        if not message.tool_calls:
            raise ValueError("O modelo nao devolveu uma estrutura de documento valida.")

        tool_call = message.tool_calls[0]
        argumentos = tool_call.function.arguments or "{}"
        try:
            return json.loads(argumentos)
        except json.JSONDecodeError:
            if tentativa == 1:
                raise ValueError(
                    "O modelo gerou uma resposta incompleta mesmo após a repetição. "
                    "O documento pode ser grande demais para gerar de uma vez; "
                    "tente novamente ou envie um arquivo mais curto."
                )
            max_tokens *= 2
            tool_call_id = getattr(tool_call, "id", "call_retry")
            mensagens.extend(
                [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": definition["function"]["name"],
                                    "arguments": argumentos,
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": instrucao_retry,
                    },
                ]
            )

    raise ValueError("Nao foi possivel gerar a estrutura do documento.")


def gerar_estrutura_minuta(
    pedido: str,
    tipo_ato: str,
    perfil: PerfilModelo,
    contexto: list[dict],
    valores: dict | None = None,
    modelo_referencia: str | None = None,
) -> dict:
    """Chama o LLM e devolve a estrutura estruturada da minuta."""
    mensagens = [
        {"role": "system", "content": _sistema()},
        {
            "role": "user",
            "content": _usuario(pedido, tipo_ato, perfil, contexto, valores, modelo_referencia),
        },
    ]

    estrutura = _extrair_json_com_retry(
        mensagens,
        STRUTURA_DEFINITION,
        max(4096, int(os.getenv("MINUTA_MAX_TOKENS", "4096"))),
    )
    return _padronizar(estrutura, tipo_ato)
