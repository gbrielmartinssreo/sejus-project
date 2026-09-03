"""Tool de function calling para gerar atos a partir dos templates DOCX."""
from __future__ import annotations

import json

from sejus_project.tools.docx_templates import (
    TemplateError,
    fill_template,
    inspect_template,
    list_templates,
)
from sejus_project.tools.retrieval import retrieve

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


def gerar_documento_normativo(
    request: str,
    template_name: str | None = None,
    values: dict[str, str] | None = None,
) -> str:
    """Inspeciona, recupera contexto e, se confirmado, gera o DOCX."""
    try:
        selected = _select_template(request, template_name)
        inspection = inspect_template(selected)
        context = _context_for_request(request, selected)

        if not values:
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
        result.update(
            {
                "status": "generated",
                "request": request,
                "sources": _source_summary(context),
            }
        )
        return json.dumps(result, ensure_ascii=False)
    except (TemplateError, OSError, ValueError) as error:
        return json.dumps(
            {"status": "error", "error": str(error)}, ensure_ascii=False
        )