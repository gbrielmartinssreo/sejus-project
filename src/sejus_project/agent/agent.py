import json

from sympy import content

from sejus_project.llm.ia import perguntar

from sejus_project.tools.calc import definition as calc_definition, calcular
from sejus_project.tools.temperatura import tools as temperatura_tools, temperatura
from sejus_project.tools.more import definition as more_definition, more_epic


TOOLS = [
    calc_definition,
    *temperatura_tools,
    more_definition
]


FUNCTIONS = {
    "calcular": calcular,
    "temperatura": temperatura,
    "more_epic": more_epic
}


import inspect
import json


def _executar_tool(tool_call):
    """Executa uma ferramenta baseada no tool_call."""

    function_name = tool_call.function.name

    arguments = json.loads(
        tool_call.function.arguments or "{}"
    )

    function = FUNCTIONS.get(function_name)

    if not function:
        return f"Ferramenta desconhecida: {function_name}"

    signature = inspect.signature(function)

    if not signature.parameters:
        return function()

    return function(**arguments)


def executar(question):
    """Executa o agente com a pergunta do usuário."""

    messages = [
        {
            "role": "user",
            "content": question
        }
    ]

    response = perguntar(messages, TOOLS)

    message = response.choices[0].message

    if not message.tool_calls:
        return message.content

    messages.append(message)

    for tool_call in message.tool_calls:

        resultado = _executar_tool(tool_call)

        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": resultado
        })

    response = perguntar(messages, TOOLS)

    return response.choices[0].message.content