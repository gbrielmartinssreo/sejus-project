"""Testes de robustez do agente e da tool de geração (erros nunca estouram)."""

import json
import threading
import time
from types import SimpleNamespace

import pytest

from sejus_project.agent import agent
from sejus_project.tools.llm_tools import document_generation as generation
from sejus_project.tools.llm_tools import user_files


@pytest.fixture(autouse=True)
def _limpar_upload_da_sessao():
    """Isola o registro de upload da sessão entre os testes."""
    user_files.limpar_upload_sessao()
    yield
    user_files.limpar_upload_sessao()


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


# ---------------------------------------------------------------------------
# Upload da sessão → análise: a tool sempre encontra o arquivo recém-enviado
# ---------------------------------------------------------------------------


def _preparar_pasta(monkeypatch, tmp_path):
    monkeypatch.setattr(user_files, "IMPORTACOES_DIR", tmp_path)
    return tmp_path


def test_analise_sem_nome_prefere_upload_registrado_sobre_mtime(monkeypatch, tmp_path):
    """Upload registrado da sessão vence o arquivo mais novo por mtime (ex.:
    um modelo de formatação enviado em seguida não 'sequestra' o arquivo)."""
    pasta = _preparar_pasta(monkeypatch, tmp_path)
    (pasta / "minuta_enviada.txt").write_text("conteudo enviado pelo usuario")
    user_files.registrar_upload("minuta_enviada.txt")
    time.sleep(0.05)
    (pasta / "modelo_formato.txt").write_text("modelo de formatacao mais novo")

    resultado = json.loads(user_files.analisar_arquivo_usuario())

    assert resultado.get("filename") == "minuta_enviada.txt"
    assert "conteudo enviado pelo usuario" in resultado["text"]


def test_analise_com_nome_errado_cai_para_upload_da_sessao(monkeypatch, tmp_path):
    """LLM chuta um filename que não existe: a tool lê o upload da sessão e
    avisa, em vez de devolver 'arquivo não encontrado' (fim do loop de repetir
    o nome)."""
    pasta = _preparar_pasta(monkeypatch, tmp_path)
    (pasta / "minuta_saeb.md").write_text("Art. 1º Texto da minuta enviada.")
    user_files.registrar_upload("minuta_saeb.md")

    resultado = json.loads(
        user_files.analisar_arquivo_usuario("arquivo_inventado_2026.pdf")
    )

    assert "error" not in resultado
    assert resultado["filename"] == "minuta_saeb.md"
    assert resultado["filename_solicitado"] == "arquivo_inventado_2026.pdf"
    assert "aviso" in resultado
    assert "Art. 1º" in resultado["text"]


def test_analise_com_nome_existente_nao_muda_de_arquivo(monkeypatch, tmp_path):
    """Nome EXATO e existente continua vencendo o fallback: pedir análise de um
    arquivo específico lê esse arquivo, mesmo com outro registro na sessão."""
    pasta = _preparar_pasta(monkeypatch, tmp_path)
    (pasta / "enviado.txt").write_text("upload da sessao")
    user_files.registrar_upload("enviado.txt")
    (pasta / "especifico.txt").write_text("arquivo citado pelo nome")

    resultado = json.loads(
        user_files.analisar_arquivo_usuario("especifico.txt")
    )

    assert resultado.get("filename") == "especifico.txt"
    assert "arquivo citado pelo nome" in resultado["text"]


def test_sem_upload_registrado_usar_mtime_continua(monkeypatch, tmp_path):
    """Sem registro de sessão (ex.: arquivos colocados manualmente na pasta),
    o fallback por mtime segue funcionando como antes."""
    pasta = _preparar_pasta(monkeypatch, tmp_path)
    (pasta / "so_este.txt").write_text("conteudo do unico arquivo")

    resultado = json.loads(user_files.analisar_arquivo_usuario())

    assert resultado.get("filename") == "so_este.txt"
    assert "conteudo do unico arquivo" in resultado["text"]


def test_limpar_estado_limpa_upload_registrado(monkeypatch, tmp_path):
    """Nova sessão descarta o registro do upload, mas o mtime continua
    encontrando o arquivo em disco."""
    pasta = _preparar_pasta(monkeypatch, tmp_path)
    (pasta / "doc.txt").write_text("x")
    user_files.registrar_upload("doc.txt")
    assert user_files.upload_sessao() == "doc.txt"

    generation.limpar_estado()

    assert user_files.upload_sessao() is None
    resultado = json.loads(user_files.analisar_arquivo_usuario())
    assert resultado.get("filename") == "doc.txt"


# ---------------------------------------------------------------------------
# Sequência de aceite: upload + análise no primeiro turno (testada.html
# [5]–[13]): o agente reconhece o upload. Em vez de pedir o arquivo de novo,
# confirma pelo nome ("é este que deseja analisar?") antes de analisar.
# ---------------------------------------------------------------------------


class _MsgAnalise:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none=False):
        dado = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            dado["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in self.tool_calls
            ]
        return dado


def _resposta_analise(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_primeiro_turno_apos_upload_pergunta_confirmacao_sem_pedir_reenvio(monkeypatch, tmp_path):
    """Upload registrado + pedido de análise sem citar o nome: o agente NÃO
    chama o LLM e NÃO pede reenvio; reconhece o upload e pergunta se é o
    arquivo certo, citando o nome encontrado."""
    pasta = _preparar_pasta(monkeypatch, tmp_path)
    (pasta / "minuta_saeb.md").write_text("Art. 1º Conteudo da minuta enviada.")
    user_files.registrar_upload("minuta_saeb.md")
    monkeypatch.setattr(agent, "messages", [])

    def perguntar_nao_deve_ser_chamado(*args, **kwargs):
        raise AssertionError("o LLM não deveria ser chamado: o upload já é reconhecido")

    monkeypatch.setattr(agent, "perguntar", perguntar_nao_deve_ser_chamado)

    resposta = agent.executar("Faça uma análise da IN que enviei")

    assert "minuta_saeb.md" in resposta
    assert "deseja" in resposta.casefold()
    assert "envie" not in resposta.casefold()
    assert "enviar" not in resposta.casefold()
    assert agent.messages[-1]["role"] == "assistant"


def test_apos_confirmacao_analisa_arquivo_no_proximo_turno(monkeypatch, tmp_path):
    """Confirmado que é o arquivo certo, o turno seguinte analisa o documento:
    o LLM chama a tool com filename inventado, a tool real lê o upload da
    sessão e a análise vincula o arquivo certo."""
    pasta = _preparar_pasta(monkeypatch, tmp_path)
    (pasta / "minuta_saeb.md").write_text("Art. 1º Conteudo da minuta enviada.")
    user_files.registrar_upload("minuta_saeb.md")
    monkeypatch.setattr(agent, "messages", [])

    registros = []
    monkeypatch.setattr(
        agent,
        "registrar_analise",
        lambda filename, analise, origem=None: registros.append(filename),
    )

    # Turno 1: upload reconhecido -> pergunta de confirmação (sem LLM).
    agent.executar("Faça uma análise da IN que enviei")

    tool_call = SimpleNamespace(
        id="c1",
        function=SimpleNamespace(
            name="analisar_arquivo_usuario",
            arguments=json.dumps({"filename": "saeb-2026.pdf"}),
        ),
    )

    chamadas = 0
    leituras = []

    def perguntar_falso(messages, tools):
        nonlocal chamadas
        chamadas += 1
        ultima = messages[-1]
        if ultima.get("role") == "tool":
            dados = json.loads(ultima["content"])
            leituras.append(dados)
            return _resposta_analise(
                _MsgAnalise(
                    f"Análise de {dados.get('filename')}: falta incluir prazo.",
                    None,
                )
            )
        return _resposta_analise(_MsgAnalise(None, [tool_call]))

    monkeypatch.setattr(agent, "perguntar", perguntar_falso)

    # Turno 2: usuário confirma -> análise no mesmo turno.
    resposta = agent.executar("Sim, é este")

    assert leituras, "a tool de análise deveria ter sido chamada após a confirmação"
    assert "error" not in leituras[0]
    assert leituras[0]["filename"] == "minuta_saeb.md"
    assert "Art. 1º" in leituras[0]["text"]
    assert chamadas == 2
    assert "não localizei" not in resposta.lower()
    assert "nao localizei" not in resposta.lower()
    assert "minuta_saeb.md" in registros


def test_resposta_do_llm_que_pede_reenvio_vira_confirmacao(monkeypatch, tmp_path):
    """Rede de segurança: mesmo que o LLM responda em texto 'envie o arquivo'
    (sem chamar a tool), o agente troca a resposta pela confirmação do upload
    registrado — nunca devolve um pedido de reenvio ao usuário."""
    pasta = _preparar_pasta(monkeypatch, tmp_path)
    (pasta / "minuta_saeb.md").write_text("Art. 1º Conteudo da minuta enviada.")
    user_files.registrar_upload("minuta_saeb.md")
    monkeypatch.setattr(agent, "messages", [])

    def perguntar_falso(messages, tools):
        return _resposta_analise(
            _MsgAnalise(
                "Claro, por favor, envie a Instrução Normativa (IN) que deseja "
                "que eu analise. Pode ser o arquivo em DOCX.",
                None,
            )
        )

    monkeypatch.setattr(agent, "perguntar", perguntar_falso)

    resposta = agent.executar("pode verificar isso?")

    assert "minuta_saeb.md" in resposta
    assert "deseja" in resposta.casefold()
    assert "envie a Instrução" not in resposta


# ---------------------------------------------------------------------------
# Endpoint de upload: registra a sessão e o chat aguarda uploads em andamento
# ---------------------------------------------------------------------------


def test_upload_registra_arquivo_na_sessao(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from sejus_project.web import server

    monkeypatch.setattr(server, "IMPORTACOES_DIR", tmp_path)
    monkeypatch.setattr(user_files, "IMPORTACOES_DIR", tmp_path)
    client = TestClient(server.app)

    resp = client.post(
        "/api/upload",
        files={"arquivo": ("recem_enviado.txt", b"conteudo", "text/plain")},
    )

    assert resp.status_code == 200
    assert resp.json()["filename"] == "recem_enviado.txt"
    assert user_files.upload_sessao() == "recem_enviado.txt"
    assert server._uploads_em_andamento() == 0
    assert not any(p.name.endswith(".parcial") for p in tmp_path.iterdir())

    # E a tool já encontra o arquivo mesmo com nome inventado pelo LLM.
    resultado = json.loads(
        user_files.analisar_arquivo_usuario("nome_inventado.txt")
    )
    assert resultado.get("filename") == "recem_enviado.txt"
    assert "conteudo" in resultado["text"]


def test_chat_aguarda_upload_em_andamento():
    """O /api/chat não processa a pergunta enquanto o upload ainda grava."""
    from sejus_project.web import server

    with server._UPLOADS_LOCK:
        server._UPLOADS_ATIVOS += 1

    def liberar():
        time.sleep(0.1)
        with server._UPLOADS_LOCK:
            server._UPLOADS_ATIVOS -= 1

    thread = threading.Thread(target=liberar)
    thread.start()
    try:
        inicio = time.monotonic()
        server._aguardar_upload_em_andamento(timeout=5)
        assert time.monotonic() - inicio >= 0.1
        assert server._uploads_em_andamento() == 0
    finally:
        thread.join()
