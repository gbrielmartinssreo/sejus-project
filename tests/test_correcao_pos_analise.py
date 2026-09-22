"""Fluxo análise → correção: registro por sessão/documento, apontamentos
acionáveis com ID estável, cobertura validada contra o patch efetivo e
encaminhamento do pedido de correção para a melhoria (não para a geração).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from sejus_project.agent import agent
from sejus_project.tools.llm_tools import analysis_registry
from sejus_project.tools.llm_tools import document_generation as generation
from sejus_project.tools.llm_tools import document_improvement as minuta

# ---------------------------------------------------------------------------
# Extração de apontamentos acionáveis
# ---------------------------------------------------------------------------


def test_extrair_apontamentos_ignora_conformidade_e_elogios():
    analise = (
        "- A IN está consistente em mencionar a Lei nº 7.210/1984.\n"
        "- Faz referência correta ao Regimento Interno.\n"
        "- Não detectei erros ortográficos ou gramaticais relevantes.\n"
        "- A numeração sequencial dos artigos está correta.\n"
        "- Falta prever prazo de validade da autorização.\n"
        "- Recomendo ajustar o fundamento legal do preâmbulo."
    )
    apontamentos = analysis_registry.extrair_apontamentos(analise)
    textos = [a["texto"] for a in apontamentos]
    assert all("consistente" not in t for t in textos)
    assert all("Não detectei" not in t for t in textos)
    assert any("prazo de validade" in t for t in textos)
    assert any("ajustar o fundamento" in t for t in textos)


def test_extrair_apontamentos_vazio_quando_so_conformidade():
    analise = (
        "- A norma está em conformidade com a LEP.\n"
        "- Não foram encontradas inconsistências.\n"
        "- Não há nada a corrigir."
    )
    assert analysis_registry.extrair_apontamentos(analise) == []


def test_id_de_apontamento_estavel_entre_turnos():
    texto = "Falta incluir o artigo de vigência."
    primeiro = analysis_registry.extrair_apontamentos(f"- {texto}")
    segundo = analysis_registry.extrair_apontamentos(
        f"Contexto adicional.\n- {texto}\nMais texto."
    )
    id_primeiro = next(a["id"] for a in primeiro if a["texto"] == texto)
    id_segundo = next(a["id"] for a in segundo if a["texto"] == texto)
    assert id_primeiro == id_segundo
    assert id_primeiro.startswith("ap-")


# ---------------------------------------------------------------------------
# Registro por sessão + documento + versão
# ---------------------------------------------------------------------------


def test_registro_exige_mesma_sessao_e_versao():
    sessao = analysis_registry.nova_sessao()
    analysis_registry.registrar(
        sessao, "doc.docx", "sha1", "análise", [{"id": "ap-1", "texto": "x"}]
    )
    assert analysis_registry.obter(sessao, "doc.docx", "sha1") is not None
    assert analysis_registry.obter(sessao, "doc.docx", "outra-versao") is None
    assert analysis_registry.obter(sessao + 1, "doc.docx", "sha1") is None


def test_registro_acumula_apontamentos_na_mesma_versao():
    sessao = analysis_registry.nova_sessao()
    analysis_registry.registrar(
        sessao, "doc.docx", "sha1", "turno 1", [{"id": "ap-1", "texto": "x"}]
    )
    entrada = analysis_registry.registrar(
        sessao, "doc.docx", "sha1", "turno 2", [{"id": "ap-2", "texto": "y"}]
    )
    ids = {a["id"] for a in entrada["apontamentos"]}
    assert ids == {"ap-1", "ap-2"}
    assert entrada["analise_completa"] == "turno 2"


def test_turno_sem_apontamentos_preserva_analise_completa():
    sessao = analysis_registry.nova_sessao()
    analysis_registry.registrar(
        sessao, "doc.docx", "sha1", "análise rica", [{"id": "ap-1", "texto": "x"}]
    )
    entrada = analysis_registry.registrar(
        sessao, "doc.docx", "sha1", "resposta de acompanhamento", []
    )
    assert entrada["analise_completa"] == "análise rica"
    assert {a["id"] for a in entrada["apontamentos"]} == {"ap-1"}


def test_registro_substitui_em_versao_nova():
    sessao = analysis_registry.nova_sessao()
    analysis_registry.registrar(
        sessao, "doc.docx", "sha1", "v1", [{"id": "ap-1", "texto": "x"}]
    )
    entrada = analysis_registry.registrar(
        sessao, "doc.docx", "sha2", "v2", [{"id": "ap-2", "texto": "y"}]
    )
    assert entrada["sha1"] == "sha2"
    assert {a["id"] for a in entrada["apontamentos"]} == {"ap-2"}


# ---------------------------------------------------------------------------
# Cobertura validada contra o patch efetivo
# ---------------------------------------------------------------------------

_DOC = "PORTARIA Nº 1/2026\nArt. 1º Texto original do artigo.\nArt. 2º Outro texto."


def test_cobertura_aplicada_quando_mudanca_ancorada():
    alteracoes = [
        {
            "tipo": "corrigido",
            "rotulo": "Art. 1º",
            "trecho_original": "Art. 1º Texto original do artigo.",
            "novo_texto": "Art. 1º Texto corrigido do artigo.",
            "detalhe": "Ajuste.",
        }
    ]
    cobertura = minuta._validar_cobertura(
        _DOC,
        alteracoes,
        [],
        [],
        [{"id": "ap-1", "texto": "corrigir o art. 1º"}],
        [{"apontamento_id": "ap-1", "status": "aplicado", "referencia": "Art. 1º"}],
    )
    assert cobertura[0]["status"] == "aplicado"
    assert cobertura[0]["referencia"] == "Art. 1º"


def test_cobertura_rebaixa_quando_ancora_nao_encontrada():
    alteracoes = [
        {
            "tipo": "corrigido",
            "rotulo": "Art. 9º",
            "trecho_original": "Art. 9º Dispositivo inexistente.",
            "novo_texto": "Art. 9º Corrigido.",
            "detalhe": "x",
        }
    ]
    cobertura = minuta._validar_cobertura(
        _DOC,
        alteracoes,
        [],
        [],
        [{"id": "ap-1", "texto": "corrigir o art. 9º"}],
        [{"apontamento_id": "ap-1", "status": "aplicado", "referencia": "Art. 9º"}],
    )
    assert cobertura[0]["status"] == "nao_aplicado"
    assert cobertura[0]["motivo"]


def test_cobertura_rebaixa_quando_lastro_invalido():
    alteracoes = [
        {
            "tipo": "corrigido",
            "rotulo": "Art. 1º",
            "trecho_original": "Art. 1º Texto original do artigo.",
            "novo_texto": "Art. 1º Texto com prazo novo.",
            "detalhe": "x",
            "lastro": "IN fantasma 99/9999",
            "lastro_validado": False,
        }
    ]
    cobertura = minuta._validar_cobertura(
        _DOC,
        alteracoes,
        [],
        [],
        [{"id": "ap-1", "texto": "prever prazo"}],
        [{"apontamento_id": "ap-1", "status": "aplicado", "referencia": "Art. 1º"}],
    )
    assert cobertura[0]["status"] == "nao_aplicado"
    assert "lastro" in cobertura[0]["motivo"]


def test_cobertura_apontamento_sem_declaracao_nao_aplicado():
    cobertura = minuta._validar_cobertura(
        _DOC, [], [], [], [{"id": "ap-1", "texto": "corrigir algo"}], []
    )
    assert cobertura[0]["status"] == "nao_aplicado"
    assert "encaminhado" in cobertura[0]["motivo"]


def test_problemas_cobertura_ausente_e_sem_motivo():
    apontamentos = [{"id": "ap-1", "texto": "x"}]
    assert minuta._problemas_cobertura(apontamentos, [])
    assert minuta._problemas_cobertura(
        apontamentos, [{"apontamento_id": "ap-1", "status": "nao_aplicado"}]
    )
    assert (
        minuta._problemas_cobertura(
            apontamentos,
            [{"apontamento_id": "ap-1", "status": "aplicado", "referencia": "Art. 1º"}],
        )
        == []
    )


class _FakeExtrai:
    def __init__(self, retornos):
        self.retornos = list(retornos)
        self.chamadas = []

    def __call__(self, mensagens, definition, max_tokens, preservar_completo=False):
        self.chamadas.append(
            mensagens[-1].get("content") if mensagens else None
        )
        return self.retornos.pop(0)


def _patch(com_cobertura: bool) -> dict:
    dados = {
        "numero": "PORTARIA Nº 1/2026",
        "ementa": "Dispõe sobre teste.",
        "alteracoes": [
            {
                "tipo": "corrigido",
                "rotulo": "Art. 1º",
                "trecho_original": "Art. 1º Texto original do artigo.",
                "novo_texto": "Art. 1º Texto corrigido do artigo.",
                "detalhe": "Ajuste.",
            }
        ],
        "remocoes": [],
    }
    if com_cobertura:
        dados["apontamentos_analise"] = [
            {"apontamento_id": "ap-1", "status": "aplicado", "referencia": "Art. 1º"}
        ]
    return dados


def _perfil():
    return SimpleNamespace(name="USUARIO_Teste")


def test_melhoria_retenta_sem_cobertura_e_entrega_com_cobertura(monkeypatch):
    fake = _FakeExtrai([_patch(com_cobertura=False), _patch(com_cobertura=True)])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    estrutura, _, _, _, _ = minuta.gerar_estrutura_melhoria(
        _DOC,
        "portaria",
        _perfil(),
        [],
        {"apontamentos": [{"id": "ap-1", "texto": "corrigir o art. 1º"}]},
    )

    assert len(fake.chamadas) == 2
    assert "apontamentos_analise" in fake.chamadas[1]
    cobertura = estrutura["_cobertura_analise"]
    assert cobertura[0]["status"] == "aplicado"


def test_melhoria_rebaixa_cobertura_de_patch_sem_ancora(monkeypatch):
    patch = {
        "numero": "PORTARIA Nº 1/2026",
        "ementa": "Dispõe sobre teste.",
        "alteracoes": [
            {
                "tipo": "corrigido",
                "rotulo": "Art. 9º",
                "trecho_original": "Art. 9º inexistente.",
                "novo_texto": "Art. 9º corrigido.",
                "detalhe": "x",
            }
        ],
        "remocoes": [],
        "apontamentos_analise": [
            {"apontamento_id": "ap-1", "status": "aplicado", "referencia": "Art. 9º"}
        ],
    }
    fake = _FakeExtrai([patch, patch])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    estrutura, _, _, _, _ = minuta.gerar_estrutura_melhoria(
        _DOC,
        "portaria",
        _perfil(),
        [],
        {"apontamentos": [{"id": "ap-1", "texto": "corrigir o art. 9º"}]},
    )
    assert estrutura["_cobertura_analise"][0]["status"] == "nao_aplicado"


# ---------------------------------------------------------------------------
# Injeção automática da análise na melhoria
# ---------------------------------------------------------------------------


def test_melhoria_injeta_apontamentos_da_analise_registrada(monkeypatch, tmp_path):
    sessao = analysis_registry.nova_sessao()
    conteudo = "PORTARIA Nº 1/2026\nArt. 1º Texto original do artigo."
    sha = analysis_registry.hash_conteudo(conteudo)
    analysis_registry.registrar(
        sessao,
        "doc.docx",
        sha,
        "análise anterior",
        [{"id": "ap-1", "texto": "corrigir o art. 1º"}],
    )

    capturado = {}

    def fake_gerar(conteudo_, tipo, perfil, contexto, valores=None):
        capturado["valores"] = valores
        return {"corpo": []}, [], [], [], []

    monkeypatch.setattr(generation, "_resolve_file", lambda n: Path(tmp_path) / n)
    monkeypatch.setattr(generation, "extract_file_text", lambda p: conteudo)
    monkeypatch.setattr(generation, "retrieve", lambda *a, **k: [])
    monkeypatch.setattr(generation, "minuta_para_texto", lambda e: "depois")
    monkeypatch.setattr(
        generation.modelos,
        "crear_perfil_de_arquivo",
        lambda destino, texto, stem: SimpleNamespace(
            name="USUARIO_Teste", act_types=["instrução normativa"]
        ),
    )
    monkeypatch.setattr(
        generation.modelos, "detectar_tipo_ato", lambda t: "instrução normativa"
    )
    monkeypatch.setattr(
        generation.document_improvement, "gerar_estrutura_melhoria", fake_gerar
    )
    monkeypatch.setattr(
        generation.docx_builder,
        "montar_docx_revisado",
        lambda *a, **k: Path(tmp_path) / "out.docx",
    )
    monkeypatch.setattr(generation, "_salvar_propostas_disc", lambda *a, **k: None)

    generation.melhorar_documento_usuario(filename="doc.docx")

    assert capturado["valores"]["apontamentos"][0]["id"] == "ap-1"
    assert capturado["valores"]["analise_completa"] == "análise anterior"


def test_melhoria_nao_injeta_analise_de_versao_diferente(monkeypatch, tmp_path):
    sessao = analysis_registry.nova_sessao()
    analysis_registry.registrar(
        sessao,
        "doc.docx",
        analysis_registry.hash_conteudo("conteúdo antigo"),
        "análise antiga",
        [{"id": "ap-1", "texto": "x"}],
    )
    capturado = {}

    def fake_gerar(conteudo_, tipo, perfil, contexto, valores=None):
        capturado["valores"] = valores
        return {"corpo": []}, [], [], [], []

    monkeypatch.setattr(generation, "_resolve_file", lambda n: Path(tmp_path) / n)
    monkeypatch.setattr(generation, "extract_file_text", lambda p: "conteúdo novo")
    monkeypatch.setattr(generation, "retrieve", lambda *a, **k: [])
    monkeypatch.setattr(generation, "minuta_para_texto", lambda e: "depois")
    monkeypatch.setattr(
        generation.modelos,
        "crear_perfil_de_arquivo",
        lambda destino, texto, stem: SimpleNamespace(
            name="USUARIO_Teste", act_types=["instrução normativa"]
        ),
    )
    monkeypatch.setattr(
        generation.modelos, "detectar_tipo_ato", lambda t: "instrução normativa"
    )
    monkeypatch.setattr(
        generation.document_improvement, "gerar_estrutura_melhoria", fake_gerar
    )
    monkeypatch.setattr(
        generation.docx_builder,
        "montar_docx_revisado",
        lambda *a, **k: Path(tmp_path) / "out.docx",
    )
    monkeypatch.setattr(generation, "_salvar_propostas_disc", lambda *a, **k: None)

    generation.melhorar_documento_usuario(filename="doc.docx")

    assert not (capturado["valores"] or {}).get("apontamentos")


# ---------------------------------------------------------------------------
# Guarda na geração + roteamento no agente
# ---------------------------------------------------------------------------


def test_analise_para_correcao_usa_o_documento_analisado(monkeypatch, tmp_path):
    sessao = analysis_registry.nova_sessao()
    conteudo_a = "DOCUMENTO A"
    conteudo_b = "DOCUMENTO B"
    analysis_registry.registrar(
        sessao,
        "a.docx",
        analysis_registry.hash_conteudo(conteudo_a),
        "análise A",
        [{"id": "ap-a", "texto": "corrigir A"}],
    )
    # b.docx foi importado depois, mas NÃO foi analisado.
    monkeypatch.setattr(
        generation, "_resolve_file", lambda n: Path(tmp_path) / n
    )
    monkeypatch.setattr(
        generation,
        "extract_file_text",
        lambda p: conteudo_b if p.name == "b.docx" else conteudo_a,
    )
    monkeypatch.setattr(generation, "_ultimo_arquivo_importado", lambda: "b.docx")

    analise = generation.analise_para_correcao()

    assert analise["filename"] == "a.docx"
    assert analise["apontamentos"][0]["id"] == "ap-a"


def test_geracao_recusa_correcao_de_documento_analisado(monkeypatch, tmp_path):
    sessao = analysis_registry.nova_sessao()
    conteudo = "INSTRUÇÃO NORMATIVA Nº 1/2026\nArt. 1º Texto."
    analysis_registry.registrar(
        sessao,
        "doc.docx",
        analysis_registry.hash_conteudo(conteudo),
        "análise",
        [{"id": "ap-1", "texto": "x"}],
    )
    monkeypatch.setattr(generation, "_ultimo_arquivo_importado", lambda: "doc.docx")
    monkeypatch.setattr(generation, "_resolve_file", lambda n: Path(tmp_path) / n)
    monkeypatch.setattr(generation, "extract_file_text", lambda p: conteudo)
    generation._pending_document = None

    resultado = json.loads(
        generation.gerar_documento_normativo(
            "consegue fazer a correção e me dar o arquivo corrigido?"
        )
    )
    assert resultado["status"] == "melhoria_necessaria"
    assert resultado["filename"] == "doc.docx"
    assert generation.has_pending_document() is False


def test_correcao_pos_analise_vai_para_melhoria(monkeypatch):
    monkeypatch.setattr(agent, "messages", [])
    capturado = {}

    def fake_melhorar(filename=None, diretrizes=None, apontamentos=None):
        capturado["filename"] = filename
        capturado["apontamentos"] = apontamentos
        return json.dumps(
            {
                "status": "improved",
                "filename": filename,
                "alteracoes": [],
                "apontamentos_analise": [
                    {
                        "apontamento_id": "ap-1",
                        "apontamento": "corrigir X",
                        "status": "aplicado",
                        "referencia": "Art. 1º",
                        "motivo": "",
                    }
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(agent, "melhorar_documento_usuario", fake_melhorar)
    monkeypatch.setattr(
        agent,
        "analise_para_correcao",
        lambda filename=None: {
            "filename": "doc.docx",
            "apontamentos": [{"id": "ap-1", "texto": "corrigir X"}],
            "analise_completa": "análise",
        },
    )

    resposta = agent.executar(
        "consegue fazer a correção e me dar o arquivo corrigido?"
    )

    assert capturado["filename"] == "doc.docx"
    assert capturado["apontamentos"][0]["id"] == "ap-1"
    assert "apontamentos" in resposta.lower()


def test_correcao_sem_analise_nao_e_acionada(monkeypatch):
    monkeypatch.setattr(agent, "messages", [])
    monkeypatch.setattr(agent, "analise_para_correcao", lambda filename=None: None)
    assert agent._tratar_correcao_direta("corrija o documento") is None


class _Msg:
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


def _resposta(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_captura_da_analise_vinculada_ao_documento(monkeypatch):
    monkeypatch.setattr(agent, "messages", [])
    registros = []
    monkeypatch.setattr(
        agent,
        "registrar_analise",
        lambda filename, analise: registros.append((filename, analise)),
    )
    monkeypatch.setitem(
        agent.FUNCTIONS,
        "analisar_arquivo_usuario",
        lambda **k: json.dumps({"filename": "doc.docx", "text": "..."}),
    )

    tool_call = SimpleNamespace(
        id="c1",
        function=SimpleNamespace(
            name="analisar_arquivo_usuario", arguments="{}"
        ),
    )
    respostas = [
        _resposta(_Msg(None, [tool_call])),
        _resposta(_Msg("Análise: falta incluir o artigo de vigência.", None)),
    ]
    monkeypatch.setattr(agent, "perguntar", lambda *a, **k: respostas.pop(0))

    agent.executar("faça a análise do documento")

    assert registros == [
        ("doc.docx", "Análise: falta incluir o artigo de vigência.")
    ]


def test_loop_encaminha_melhoria_necessaria_para_a_melhoria(monkeypatch):
    monkeypatch.setattr(agent, "messages", [])
    capturado = {}

    def fake_melhorar(filename=None, diretrizes=None, apontamentos=None):
        capturado["filename"] = filename
        capturado["apontamentos"] = apontamentos
        return json.dumps(
            {"status": "improved", "filename": filename, "alteracoes": []},
            ensure_ascii=False,
        )

    monkeypatch.setattr(agent, "melhorar_documento_usuario", fake_melhorar)
    monkeypatch.setattr(agent, "analise_para_correcao", lambda filename=None: None)
    monkeypatch.setitem(
        agent.FUNCTIONS,
        "gerar_documento_normativo",
        lambda **k: json.dumps(
            {
                "status": "melhoria_necessaria",
                "filename": "doc.docx",
                "apontamentos": [{"id": "ap-1", "texto": "x"}],
            },
            ensure_ascii=False,
        ),
    )

    tool_call = SimpleNamespace(
        id="c1",
        function=SimpleNamespace(name="gerar_documento_normativo", arguments="{}"),
    )
    respostas = [_resposta(_Msg(None, [tool_call]))]
    monkeypatch.setattr(agent, "perguntar", lambda *a, **k: respostas.pop(0))

    agent.executar("corrija e me dê o arquivo corrigido")

    assert capturado["filename"] == "doc.docx"
    assert capturado["apontamentos"][0]["id"] == "ap-1"


def test_pedido_de_correcao_nao_confunde_ajuste_generico():
    assert (
        generation.pedido_de_correcao(
            "Gere uma portaria que ajuste o fluxo de limpeza"
        )
        is False
    )
    assert generation.pedido_de_correcao("ajuste o documento enviado") is True
    assert generation.pedido_de_correcao("consegue fazer a correção?") is True