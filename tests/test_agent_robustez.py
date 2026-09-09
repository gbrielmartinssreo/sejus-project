"""Testes de robustez do agente e da tool de geração (erros nunca estouram)."""

import json

from sejus_project.agent import agent
from sejus_project.tools import document_generation as generation


def test_executar_tool_captura_excecao_na_funcao(monkeypatch):
    def estourar(**kwargs):
        raise RuntimeError("falha interna da tool")

    monkeypatch.setitem(agent.FUNCTIONS, "tool_falha", estourar)

    class ToolCall:
        class Function:
            name = "tool_falha"
            arguments = '{"request": "x"}'

        id = "call_1"
        function = Function()

    resultado = agent._executar_tool(ToolCall())

    assert isinstance(resultado, str)
    saida = json.loads(resultado)
    assert saida["status"] == "error"
    assert "tool_falha" in saida["error"]
    assert "falha interna da tool" in saida["detail"]


def test_executar_tool_captura_json_invalido(monkeypatch):
    monkeypatch.setitem(agent.FUNCTIONS, "tool_ok", lambda **k: "ok")

    class ToolCall:
        class Function:
            name = "tool_ok"
            arguments = "{isso nao e json"

        id = "call_1"
        function = Function()

    saida = json.loads(agent._executar_tool(ToolCall()))
    assert saida["status"] == "error"


def test_executar_captura_falha_do_llm(monkeypatch):
    monkeypatch.setattr(agent, "messages", [])

    def perguntar_quebrado(*args, **kwargs):
        raise ConnectionError("API fora do ar")

    monkeypatch.setattr(agent, "perguntar", perguntar_quebrado)

    resp = agent.executar("gerar portaria")

    assert "Não foi possível consultar" in resp
    assert "API fora do ar" in resp


def test_short_circuit_melhoria_sem_nome_usa_mais_recente(monkeypatch):
    """Sem nomear o arquivo, o agente chama a tool de melhoria sem filename
    (que usa a importação mais recente) e lista as alternativas na resposta."""
    monkeypatch.setattr(agent, "messages", [])

    def fake_melhorar(filename=None, diretrizes=None):
        assert filename is None
        return json.dumps({
            "status": "improved",
            "filename": "recente.txt",
            "alteracoes": [
                {"tipo": "corrigido", "o_que": "Fundamento legal", "detalhe": "Atualizado."}
            ],
            "outros": ["antigo.txt", "outro.pdf"],
        }, ensure_ascii=False)

    monkeypatch.setattr(agent, "melhorar_documento_usuario", fake_melhorar)

    resposta = agent.executar("Melhore o arquivo que mandei")

    assert "recente.txt" in resposta
    assert "corrigido" in resposta
    assert "antigo.txt" in resposta
    assert "outro.pdf" in resposta


def test_gerar_documento_rejeita_pedido_gigante():
    resultado = json.loads(
        generation.gerar_documento_normativo("a" * (generation.MAX_REQUEST_CHARS + 1))
    )
    assert resultado["status"] == "error"
    assert "grande" in resultado["error"]


def test_limpar_estado_reseta_pendente():
    generation.limpar_estado()
    assert generation.has_pending_document() is False
    assert generation.ultima_minuta() is None
