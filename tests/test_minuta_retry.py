"""Testes do retry quando o LLM devolve JSON truncado (estouro de max_tokens)."""
import json

import pytest

from sejus_project.tools import minuta


class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, tool_calls):
        self.content = None
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResponse:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]


_TRUNCATED_ARGS = '{"numero": "DECRETO Nº 1/2025", "corpo": [{"rotulo": "Art. 1º", "texto": "texto nao'
_VALID_ARGS = '{"numero": "DECRETO Nº 1/2025", "corpo": []}'
_TOOL_DEF = {"function": {"name": "apresentar_estrutura_minuta"}}


def test_retry_parse_succeeds_after_initial_truncation(monkeypatch):
    """Na 1ª tentativa o JSON vem cortado; no retry ele é válido."""
    chamadas = []
    first_call_done = {"v": False}

    def fake_perguntar(mensagens, tools, **kwargs):
        chamadas.append(kwargs.get("max_tokens"))
        if not first_call_done["v"]:
            first_call_done["v"] = True
            return _FakeResponse(_FakeMessage([_FakeToolCall("call_1", "apresentar_estrutura_minuta", _TRUNCATED_ARGS)]))
        return _FakeResponse(_FakeMessage([_FakeToolCall("call_2", "apresentar_estrutura_minuta", _VALID_ARGS)]))

    monkeypatch.setattr(minuta, "perguntar", fake_perguntar)

    resultado = minuta._extrair_json_com_retry(
        [{"role": "user", "content": "pedido"}],
        _TOOL_DEF,
        max_tokens=4096,
    )

    assert resultado == json.loads(_VALID_ARGS)
    assert chamadas[0] == 4096
    assert chamadas[1] == 8192


def test_retry_preservar_completo_nao_manda_reduzir(monkeypatch):
    """No modo preservar_completo, o retry manda reproduzir tudo sem reduzir."""
    capturado = {"tool": None}
    calls = [0]

    def fake_perguntar(mensagens, tools, **kwargs):
        calls[0] += 1
        for m in mensagens:
            if m.get("role") == "tool":
                capturado["tool"] = m["content"]
        if calls[0] == 1:
            return _FakeResponse(_FakeMessage([_FakeToolCall("c1", "x", _TRUNCATED_ARGS)]))
        return _FakeResponse(_FakeMessage([_FakeToolCall("c2", "x", _VALID_ARGS)]))

    monkeypatch.setattr(minuta, "perguntar", fake_perguntar)

    minuta._extrair_json_com_retry(
        [{"role": "user", "content": "pedido"}],
        _TOOL_DEF,
        max_tokens=4096,
        preservar_completo=True,
    )

    mensagem = capturado["tool"]
    assert "COMPLETO" in mensagem
    assert "sem omitir" in mensagem
    assert "reduzindo" not in mensagem


def test_retry_padrao_ainda_manda_reduzir_corpo(monkeypatch):
    """Sem preservar_completo, o comportamento atual (reduzir corpo) é mantido."""
    capturado = {"tool": None}
    calls = [0]

    def fake_perguntar(mensagens, tools, **kwargs):
        calls[0] += 1
        for m in mensagens:
            if m.get("role") == "tool":
                capturado["tool"] = m["content"]
        if calls[0] == 1:
            return _FakeResponse(_FakeMessage([_FakeToolCall("c1", "x", _TRUNCATED_ARGS)]))
        return _FakeResponse(_FakeMessage([_FakeToolCall("c2", "x", _VALID_ARGS)]))

    monkeypatch.setattr(minuta, "perguntar", fake_perguntar)

    minuta._extrair_json_com_retry(
        [{"role": "user", "content": "pedido"}],
        _TOOL_DEF,
        max_tokens=4096,
    )

    assert "reduzindo" in capturado["tool"]


def test_retry_injects_tool_feedback_into_messages(monkeypatch):
    """A mensagem de feedback com o erro de JSON é injetada entre as duas chamadas."""
    mensagens_terceira = {"capturado": []}
    calls = [0]

    def fake_perguntar(mensagens, tools, **kwargs):
        calls[0] += 1
        mensagens_terceira["capturado"] = list(mensagens)
        if calls[0] == 1:
            return _FakeResponse(_FakeMessage([_FakeToolCall("call_1", "x", _TRUNCATED_ARGS)]))
        return _FakeResponse(_FakeMessage([_FakeToolCall("call_2", "x", _VALID_ARGS)]))

    monkeypatch.setattr(minuta, "perguntar", fake_perguntar)

    minuta._extrair_json_com_retry(
        [{"role": "user", "content": "pedido"}],
        _TOOL_DEF,
        max_tokens=4096,
    )

    papeis = [m["role"] for m in mensagens_terceira["capturado"]]
    assert "tool" in papeis
    assert "assistant" in papeis


def test_raises_clear_error_when_retry_also_fails(monkeypatch):
    """Se o retry também vier truncado, levanta ValueError com mensagem clara."""

    def fake_perguntar(mensagens, tools, **kwargs):
        return _FakeResponse(_FakeMessage([_FakeToolCall("call_x", "x", _TRUNCATED_ARGS)]))

    monkeypatch.setattr(minuta, "perguntar", fake_perguntar)

    with pytest.raises(ValueError, match="resposta incompleta"):
        minuta._extrair_json_com_retry(
            [{"role": "user", "content": "pedido"}],
            _TOOL_DEF,
            max_tokens=4096,
        )


def test_no_retry_when_json_is_valid(monkeypatch):
    """Se o JSON é válido na 1ª tentativa, não há retry."""
    chamadas = []

    def fake_perguntar(mensagens, tools, **kwargs):
        chamadas.append(kwargs.get("max_tokens"))
        return _FakeResponse(_FakeMessage([_FakeToolCall("call_1", "x", _VALID_ARGS)]))

    monkeypatch.setattr(minuta, "perguntar", fake_perguntar)

    minuta._extrair_json_com_retry(
        [{"role": "user", "content": "pedido"}],
        _TOOL_DEF,
        max_tokens=4096,
    )

    assert chamadas == [4096]


def test_no_retry_when_tool_call_absent(monkeypatch):
    """Se o LLM não retorna tool_calls, levanta ValueError imediatamente."""

    def fake_perguntar(mensagens, tools, **kwargs):
        msg = _FakeMessage(tool_calls=None)
        return _FakeResponse(msg)

    monkeypatch.setattr(minuta, "perguntar", fake_perguntar)

    with pytest.raises(ValueError, match="nao devolveu uma estrutura"):
        minuta._extrair_json_com_retry(
            [{"role": "user", "content": "pedido"}],
            _TOOL_DEF,
            max_tokens=4096,
        )
