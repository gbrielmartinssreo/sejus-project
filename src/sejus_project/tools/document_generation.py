"""Tool de function calling para gerar atos a partir dos templates DOCX."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sejus_project.tools.docx_templates import (
    TemplateError,
    fill_template,
    inspect_template,
    list_templates,
)
from sejus_project.tools.retrieval import retrieve

_pending_document: dict | None = None

TEMPLATE_BY_TYPE = {
    "decreto": "Template_Decreto.docx",
    "instrução normativa": "Template_Instrucao_Normativa.docx",
    "instrucao normativa": "Template_Instrucao_Normativa.docx",
    "portaria conjunta": "Template_Portaria_Conjunta.docx",
    "retificação": "Template_Retificacao_Portaria.docx",
    "retificacao": "Template_Retificacao_Portaria.docx",
    "portaria": "Template_Portaria.docx",
}


def _select_template(request: str, template_name: str | None) -> str:
    if template_name:
        return template_name
    normalized = request.casefold()
    for act_type, candidate in TEMPLATE_BY_TYPE.items():
        if act_type in normalized:
            return candidate
    return "Template_Portaria.docx"


def _context_for_request(request: str, template_name: str) -> list[dict]:
    act_type = None
    if "portaria" in template_name.casefold():
        act_type = "PORTARIA"
    return retrieve(f"{request}\nTipo de ato: {template_name}", limit=8, act_type=act_type)


definition = {
    "type": "function",
    "function": {
        "name": "gerar_documento_normativo",
        "description": (
            "Inspeciona um template DOCX da SEJUS, consulta atos normativos "
            "relacionados e gera uma copia preenchida em outputs/. Use quando "
            "o usuario pedir uma minuta ou documento ja preenchido. Na primeira "
            "chamada, informe request e opcionalmente template_name, sem values, "
            "para obter os campos e contexto. Depois solicite ao usuario a "
            "confirmacao dos campos obrigatorios. Se o usuario autorizar "
            "inventar uma minuta ou disser para gerar o arquivo, chame novamente "
            "com values contendo TODOS os marcadores retornados, usando o pedido "
            "e o RAG como base e sinalizando que o resultado exige revisao. "
            "Nao responda apenas com texto quando o usuario pediu um arquivo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "Pedido do usuario e objeto pretendido para o ato.",
                },
                "template_name": {
                    "type": "string",
                    "description": (
                        "Opcional. Nome exato do template DOCX. Se omitido, "
                        "escolha pelo tipo mencionado no pedido."
                    ),
                },
                "values": {
                    "type": "object",
                    "description": (
                        "Opcional. Mapa dos marcadores encontrados no template "
                        "para valores confirmados pelo usuario, por exemplo "
                        "{\"[XX]\": \"12\", \"[ANO]\": \"2026\"}."
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["request"],
        },
    },
}


def _source_summary(results: list[dict]) -> list[dict]:
    return [
        {
            "source_file": result.get("source_file"),
            "act_type": result.get("act_type"),
            "act_number": result.get("act_number"),
            "score": result.get("score"),
            "text": result.get("text", ""),
        }
        for result in results
    ]


def has_pending_document() -> bool:
    return _pending_document is not None


def _is_generation_confirmation(request: str) -> bool:
    normalized = request.casefold()
    phrases = (
        "gere o arquivo",
        "gerar o arquivo",
        "pode gerar",
        "pode preencher",
        "pode inventar",
        "prossiga",
    )
    return any(phrase in normalized for phrase in phrases)


def _automatic_values(placeholders: list[str], request: str) -> dict[str, str]:
    """Gera valores de rascunho para a confirmacao explicita do usuario."""
    values = {}
    current_date = datetime.now(UTC).date()
    year = str(current_date.year)
    subject = request.strip().rstrip(".")
    for placeholder in placeholders:
        normalized = placeholder.casefold()
        if "ano" in normalized:
            value = year
        elif normalized in {"[xx]", "[n]"}:
            value = "001"
        elif "ementa" in normalized or "objeto" in normalized:
            value = f"Dispoe sobre {subject}"
        elif "fundament" in normalized:
            value = (
                "Considerando a necessidade administrativa relacionada ao objeto "
                "deste ato e a legislacao aplicavel"
            )
        elif "artigo 1" in normalized:
            value = f"Fica estabelecida a medida referente a {subject}."
        elif "artigo 2" in normalized:
            value = "A unidade competente adotara as providencias necessarias."
        elif "signat" in normalized or "nome" in normalized:
            value = "A DEFINIR"
        elif "cargo" in normalized:
            value = "Secretario de Estado de Justica"
        elif "revog" in normalized:
            value = "Mantem-se a regulamentacao vigente"
        elif "acrescentar" in normalized or normalized == "[...]":
            value = "Demais disposicoes serao definidas na revisao da minuta."
        elif "data" in normalized or "dia" in normalized or "mes" in normalized:
            value = current_date.strftime("%d de %B de %Y")
        else:
            value = "A DEFINIR"
        values[placeholder] = value
    return values


def gerar_documento_normativo(
    request: str,
    template_name: str | None = None,
    values: dict[str, str] | None = None,
) -> str:
    """Inspeciona, recupera contexto e, se confirmado, gera o DOCX."""
    global _pending_document
    try:
        if not values and _pending_document and _is_generation_confirmation(request):
            selected = _pending_document["template"]
            request = _pending_document["request"]
            inspection = inspect_template(selected)
            values = _automatic_values(inspection["placeholders"], request)

        selected = _select_template(request, template_name)
        inspection = inspect_template(selected)
        context = _context_for_request(request, selected)

        if not values:
            _pending_document = {
                "request": request,
                "template": selected,
            }
            return json.dumps(
                {
                    "status": "awaiting_confirmation",
                    "template": inspection["template"],
                    "placeholders": inspection["placeholders"],
                    "context": _source_summary(context),
                    "available_templates": list_templates(),
                    "message": (
                        "Preencha e confirme os marcadores antes de gerar o documento."
                    ),
                },
                ensure_ascii=False,
            )

        missing = sorted(set(inspection["placeholders"]) - set(values))
        if missing:
            return json.dumps(
                {
                    "status": "awaiting_confirmation",
                    "template": inspection["template"],
                    "missing_fields": missing,
                    "provided_fields": sorted(values),
                    "context": _source_summary(context),
                },
                ensure_ascii=False,
            )

        result = fill_template(selected, values)
        _pending_document = None
        result.update(
            {
                "status": "generated",
                "request": request,
                "sources": _source_summary(context),
                "review_required": True,
                "auto_filled": all(
                    value != "A DEFINIR" for value in values.values()
                ),
            }
        )
        return json.dumps(result, ensure_ascii=False)
    except (TemplateError, OSError, ValueError) as error:
        return json.dumps(
            {"status": "error", "error": str(error)}, ensure_ascii=False
        )