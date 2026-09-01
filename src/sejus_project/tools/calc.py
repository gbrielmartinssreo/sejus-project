def calcular(expressao):
    return str(eval(expressao))


definition = {
    "type": "function",
    "function": {
        "name": "calcular",
        "description": "Realiza um cálculo matemático.",
        "parameters": {
            "type": "object",
            "properties": {
                "expressao": {
                    "type": "string",
                    "description": "Expressão matemática."
                }
            },
            "required": ["expressao"]
        }
    }
}