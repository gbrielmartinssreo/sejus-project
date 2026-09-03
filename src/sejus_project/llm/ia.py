import os

from dotenv import load_dotenv

load_dotenv()

_client = None
_provider = None

SYSTEM_INSTRUCTIONS = (
    "Voce e o agente da SEJUS. Responda em portugues. "
    "Quando o usuario pedir um arquivo DOCX, use a ferramenta "
    "gerar_documento_normativo. Se a ferramenta retornar campos pendentes, "
    "pergunte pelos dados. Se o usuario autorizar inventar com base no RAG "
    "ou disser para gerar o arquivo, faca uma nova chamada da ferramenta "
    "enviando values com todos os placeholders retornados, usando dados "
    "plausiveis e marcando claramente que sao uma minuta para revisao. "
    "Nao responda somente com uma minuta em texto quando o usuario pediu o arquivo."
)


def _get_client():
    global _client, _provider

    if _client is not None:
        return _client

    openai_key = os.getenv("OPENAI_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")

    if openai_key:
        from openai import OpenAI

        _client = OpenAI(api_key=openai_key)
        _provider = "openai"
    elif groq_key:
        from groq import Groq

        _client = Groq(api_key=groq_key)
        _provider = "groq"
    else:
        raise RuntimeError(
            "Configure OPENAI_API_KEY no .env para usar o agente. "
            "GROQ_API_KEY continua aceito apenas como fallback temporario."
        )

    return _client


def _get_model() -> str:
    if _provider == "groq":
        return os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def perguntar(messages, tools):
    request_messages = messages
    if not messages or messages[0].get("role") != "system":
        request_messages = [
            {"role": "system", "content": SYSTEM_INSTRUCTIONS},
            *messages,
        ]
    return _get_client().chat.completions.create(
        model=_get_model(),
        messages=request_messages,
        tools=tools,
    )
