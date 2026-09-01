import json


def temperatura():
    return "27°C"


tools = [
    {
        "type": "function",
        "function": {
            "name": "temperatura",
            "description": "Retorna a temperatura atual.",
            "parameters": {
                "type": "object",
                "properties": {},
            }
        }
    }
]


def executar_tool(tool_call):
    function_name = tool_call.function.name
    arguments = json.loads(tool_call.function.arguments)

    if function_name == "temperatura":
        return temperatura()

    return f"Ferramenta desconhecida: {function_name}"