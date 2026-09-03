import inspect
import json

from sejus_project.llm.ia import perguntar
from sejus_project.tools.document_generation import (
    definition as document_generation_definition,
)
from sejus_project.tools.document_generation import (
    gerar_documento_normativo,
)
from sejus_project.tools.more import definition as more_definition
from sejus_project.tools.more import more_epic
from sejus_project.tools.retrieval import consultar_atos_sejus
from sejus_project.tools.retrieval import definition as retrieval_definition
from sejus_project.tools.user_files import analisar_arquivo_usuario
from sejus_project.tools.user_files import definition as user_files_definition

TOOLS = [
    more_definition,
    retrieval_definition,
    user_files_definition,
    document_generation_definition,
]


FUNCTIONS = {
    "more_epic": more_epic,
    "consultar_atos_sejus": consultar_atos_sejus,
    "analisar_arquivo_usuario": analisar_arquivo_usuario,
    "gerar_documento_normativo": gerar_documento_normativo,
}


# Histórico da conversa
messages = []

# --- Configuração da poda de histórico -------------------------------------
# Estratégia conservadora: NÃO resumimos e NÃO removemos mensagens de
# user/assistant (onde fica o texto final, com citações de atos etc.).
# Apenas truncamos o CONTEÚDO BRUTO de resultados de tools antigas, já que
# esse conteúdo raramente é reutilizado depois de alguns turnos e costuma
# ser a maior fonte de inflação de tokens (ex: chunks retornados pelo RAG).

# Quantas mensagens "tool" mais recentes devem ser mantidas por completo.
# Tool results mais antigos que isso são truncados.
MANTER_TOOL_RESULTS_COMPLETOS = 4

# Tamanho máximo (em caracteres) de um tool result truncado.
TRUNCAR_TOOL_RESULT_PARA = 300


def _podar_tool_results_antigos():
    """Trunca o conteúdo de tool results antigos, preservando os mais recentes.

    Não mexe em mensagens 'user' ou 'assistant' — só em 'tool', que é onde
    ficam os retornos brutos de funções como consultar_atos_sejus. A resposta
    final do assistente (que já deve conter a citação relevante em texto)
    permanece intacta no histórico.
    """

    indices_tool = [
        i for i, m in enumerate(messages) if m.get("role") == "tool"
    ]

    # Nada a podar se ainda estamos dentro do limite
    if len(indices_tool) <= MANTER_TOOL_RESULTS_COMPLETOS:
        return

    # Todos os índices exceto os N mais recentes serão candidatos à poda
    indices_para_podar = indices_tool[:-MANTER_TOOL_RESULTS_COMPLETOS]

    for i in indices_para_podar:
        conteudo = messages[i].get("content", "") or ""

        if (
            isinstance(conteudo, str)
            and len(conteudo) > TRUNCAR_TOOL_RESULT_PARA
            and not conteudo.startswith("[resultado truncado")
        ):
            messages[i]["content"] = (
                conteudo[:TRUNCAR_TOOL_RESULT_PARA]
                + f"... [resultado truncado — {len(conteudo)} caracteres originais]"
            )


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
        result = function()
    else:
        result = function(**arguments)

    return (
        result
        if isinstance(result, str)
        else json.dumps(result, ensure_ascii=False)
    )


def executar(question):
    """Executa o agente mantendo o histórico da conversa."""

    # Adiciona a pergunta ao histórico
    messages.append({
        "role": "user",
        "content": question
    })

    # Loop para permitir chamadas de ferramentas
    ultimo_resultado_tool = None

    for _ in range(5):

        _podar_tool_results_antigos()

        response = perguntar(messages, TOOLS)

        message = response.choices[0].message

        # Se o agente respondeu normalmente
        if not message.tool_calls:

            messages.append({
                "role": "assistant",
                "content": message.content
            })

            return message.content or "Não foi possível gerar uma resposta."

        # Adiciona a mensagem do agente ao histórico
        messages.append(
            message.model_dump(exclude_none=True)
        )

        # Executa as ferramentas solicitadas
        for tool_call in message.tool_calls:

            resultado = _executar_tool(tool_call)
            ultimo_resultado_tool = resultado

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": resultado,
            })

    if ultimo_resultado_tool:
        return (
            "A ferramenta não concluiu a operação. "
            f"Último estado retornado: {ultimo_resultado_tool}"
        )
    return "Não foi possível concluir a consulta."