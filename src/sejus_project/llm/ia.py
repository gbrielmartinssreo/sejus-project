import os

from dotenv import load_dotenv

load_dotenv()

_client = None
_provider = None


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
    return _get_client().chat.completions.create(
        model=_get_model(),
        messages=messages,
        tools=tools,
    )
