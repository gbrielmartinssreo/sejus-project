import inspect
import json

from sejus_project.agent.skills.loader import (
    build_system_message,
)
from sejus_project.llm.ia import perguntar
from sejus_project.tools.document_generation import (
    definition as document_generation_definition,
)
from sejus_project.tools.document_generation import (
    gerar_documento_normativo,
    has_pending_document,
    limpar_estado,
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

SYSTEM_INSTRUCTIONS = (
    "Voce e o agente da SEJUS. Responda em portugues. "
    "Quando o usuario pedir um arquivo DOCX, use a ferramenta "
    "gerar_documento_normativo. Se a ferramenta retornar campos pendentes, "
    "pergunte ao usuario se ele quer informar os campos (numero, data, "
    "signatario etc.) ou se prefere que a minuta seja preenchida "
    "automaticamente. Se o usuario autorizar inventar com base no RAG "
    "ou disser para gerar o arquivo, faca uma nova chamada da ferramenta "
    "sem values (ou com values parciais), marque claramente que a minuta "
    "exige revisao. Nao responda somente com uma minuta em texto quando o "
    "usuario pediu o arquivo."
)

def _messages_for_llm() -> list[dict]:
    return [build_system_message(SYSTEM_INSTRUCTIONS, messages), *messages]

# --- Configuração da poda de histórico -------------------------------------
# Estratégia: truncar CONTEÚDO BRUTO de resultados de tools antigas de forma
# agressiva (raramente reutilizado depois de alguns turnos, e costuma ser a
# maior fonte de inflação de tokens — ex: chunks retornados pelo RAG) e
# truncar respostas do assistant de forma mais generosa (o texto final tem
# citações e resumos úteis, mas ainda assim precisa de um teto — uma única
# resposta grande, como uma tabela markdown extensa, pode sozinha estourar
# o limite de tokens por minuto em conversas de vários turnos).

# Quantas mensagens "tool" mais recentes devem ser mantidas por completo.
MANTER_TOOL_RESULTS_COMPLETOS = 4
# Tamanho máximo (em caracteres) de um tool result truncado.
TRUNCAR_TOOL_RESULT_PARA = 300

# Quantas respostas "assistant" mais recentes devem ser mantidas por completo.
MANTER_ASSISTANT_COMPLETOS = 3
# Tamanho máximo (em caracteres) de uma resposta assistant truncada.
# Bem mais generoso que o de tool — preserva a maior parte do texto final,
# incluindo normalmente a citação/conclusão, só corta o excesso.
TRUNCAR_ASSISTANT_PARA = 1500


def _podar_tool_results_antigos():
    """Trunca o conteúdo de tool results antigos, preservando os mais recentes.

    Fica com retornos brutos de funções como consultar_atos_sejus — dado bruto,
    pode ser truncado de forma curta sem grande perda.
    """

    indices_tool = [
        i for i, m in enumerate(messages) if m.get("role") == "tool"
    ]

    if len(indices_tool) <= MANTER_TOOL_RESULTS_COMPLETOS:
        return

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


def _podar_assistant_antigos():
    """Trunca respostas antigas do assistant, com limite bem mais generoso
    que o de tool results.

    Objetivo: evitar que uma única resposta grande (ex. tabela markdown
    extensa, resumo longo de documento) fique intacta para sempre no
    histórico e acabe estourando o limite de tokens por minuto em
    conversas de vários turnos — sem descartar de forma agressiva o
    conteúdo, que costuma carregar citações relevantes.
    """

    indices_assistant = [
        i
        for i, m in enumerate(messages)
        if m.get("role") == "assistant" and isinstance(m.get("content"), str)
    ]

    if len(indices_assistant) <= MANTER_ASSISTANT_COMPLETOS:
        return

    indices_para_podar = indices_assistant[:-MANTER_ASSISTANT_COMPLETOS]

    for i in indices_para_podar:
        conteudo = messages[i].get("content", "") or ""

        if (
            len(conteudo) > TRUNCAR_ASSISTANT_PARA
            and not conteudo.startswith("[resposta anterior truncada")
        ):
            messages[i]["content"] = (
                conteudo[:TRUNCAR_ASSISTANT_PARA]
                + f"... [resposta anterior truncada — {len(conteudo)} caracteres originais]"
            )


def _executar_tool(tool_call):
    """Executa uma ferramenta baseada no tool_call, sem nunca estourar exceções.
    Qualquer falha interna vira um resultado de tool com ``status: error`` para
    que o LLM consiga explicar o problema em texto — e a interface nunca receba
    um 500 com HTML."""

    function_name = tool_call.function.name

    function = FUNCTIONS.get(function_name)

    if not function:
        return f"Ferramenta desconhecida: {function_name}"

    try:
        arguments = json.loads(
            tool_call.function.arguments or "{}"
        )

        signature = inspect.signature(function)

        if not signature.parameters:
            result = function()
        else:
            result = function(**arguments)
    except Exception as error:  # noqa: BLE001 - erro vira tool result
        return json.dumps(
            {
                "status": "error",
                "error": f"A ferramenta {function_name} falhou.",
                "detail": str(error),
            },
            ensure_ascii=False,
        )

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

    # Finaliza diretamente uma minuta pendente quando o usuario autoriza
    # dados plausiveis ou pede o arquivo, sem depender de nova tool call do LLM.
    normalized_question = question.casefold()
    generation_phrases = (
        "gere o arquivo",
        "gerar o arquivo",
        "pode gerar",
        "pode preencher",
        "pode inventar",
        "prossiga",
        "sim",
        "ok",
        "okay",
        "concordo",
        "confirmo",
        "confirma",
        "continua",
        "prossegue",
        "pode seguir",
    )
    if has_pending_document() and any(
        phrase in normalized_question for phrase in generation_phrases
    ):
        result = json.loads(gerar_documento_normativo(question))
        if result.get("status") == "generated":
            return (
                "Documento gerado com sucesso. "
                f"Arquivo: {result['output_path']}\n\n"
                "A minuta foi preenchida automaticamente e precisa ser revisada."
            )

    # Loop para permitir chamadas de ferramentas
    ultimo_resultado_tool = None

    for _ in range(5):

        _podar_tool_results_antigos()
        _podar_assistant_antigos()

        try:
            response = perguntar(_messages_for_llm(), TOOLS)
        except Exception as error:  # noqa: BLE001 - LLM/API indisponivel
            mensagem_erro = (
                f"Não foi possível consultar o modelo de linguagem: {error}"
            )
            messages.append({"role": "assistant", "content": mensagem_erro})
            return mensagem_erro

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


def limpar_conversa():
    """Apaga o histórico da conversa e o estado de geração pendente."""
    messages.clear()
    limpar_estado()