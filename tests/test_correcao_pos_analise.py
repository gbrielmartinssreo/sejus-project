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
    assert cobertura[0]["status"] == "falhou"
    assert cobertura[0]["motivo"]


def test_cobertura_pendente_quando_lastro_invalido():
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
    assert cobertura[0]["status"] == "pendente"
    assert "lastro" in cobertura[0]["motivo"]


def test_cobertura_apontamento_sem_declaracao_falhou():
    cobertura = minuta._validar_cobertura(
        _DOC, [], [], [], [{"id": "ap-1", "texto": "corrigir algo"}], []
    )
    assert cobertura[0]["status"] == "falhou"
    assert "encaminhado" in cobertura[0]["motivo"]


def test_cobertura_nao_aplicado_com_impedimento_concreto():
    cobertura = minuta._validar_cobertura(
        _DOC,
        [],
        [],
        [],
        [{"id": "ap-1", "texto": "criar taxa"}],
        [
            {
                "apontamento_id": "ap-1",
                "status": "nao_aplicado",
                "motivo": "cria despesa sem previsão orçamentária na lei vigente",
            }
        ],
    )
    assert cobertura[0]["status"] == "nao_aplicado"


def test_cobertura_motivo_vago_vira_falha():
    cobertura = minuta._validar_cobertura(
        _DOC,
        [],
        [],
        [],
        [{"id": "ap-1", "texto": "corrigir algo"}],
        [
            {
                "apontamento_id": "ap-1",
                "status": "nao_aplicado",
                "motivo": "não foi alterado",
            }
        ],
    )
    assert cobertura[0]["status"] == "falhou"
    assert "concreto" in cobertura[0]["motivo"]


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
    assert estrutura["_cobertura_analise"][0]["status"] == "falhou"


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


def test_correcao_sem_analise_e_sem_arquivo_nao_e_acionada(monkeypatch):
    monkeypatch.setattr(agent, "messages", [])
    monkeypatch.setattr(agent, "analise_para_correcao", lambda filename=None: None)
    monkeypatch.setattr(generation, "_ultimo_arquivo_importado", lambda: None)
    assert agent._tratar_correcao_direta("corrija o documento") is None


def test_correcao_sem_analise_usa_arquivo_importado(monkeypatch):
    """Sem análise registrada, um pedido de correção do arquivo enviado ainda
    usa o motor de melhoria (não a geração de ato novo)."""
    monkeypatch.setattr(agent, "messages", [])
    monkeypatch.setattr(agent, "analise_para_correcao", lambda filename=None: None)
    monkeypatch.setattr(generation, "_ultimo_arquivo_importado", lambda: "doc.docx")
    capturado = {}

    def fake_melhorar(**kwargs):
        capturado.update(kwargs)
        return json.dumps(
            {"status": "improved", "filename": "doc.docx", "alteracoes": []},
            ensure_ascii=False,
        )

    monkeypatch.setattr(agent, "melhorar_documento_usuario", fake_melhorar)

    resposta = agent._tratar_correcao_direta("me gera o arquivo com as correções")

    assert capturado["filename"] == "doc.docx"
    assert "correções" in capturado["diretrizes"]
    assert resposta


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
        lambda filename, analise, origem=None: registros.append(
            (filename, analise, origem)
        ),
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
        ("doc.docx", "Análise: falta incluir o artigo de vigência.", "análise")
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
    monkeypatch.setattr(
        agent, "arquivo_para_correcao_sem_analise", lambda q: None
    )
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


# ---------------------------------------------------------------------------
# Consolidação de apontamentos: análise inicial + aprofundamento
# ---------------------------------------------------------------------------


def test_extrair_apontamentos_renumerar_capitulos():
    """Caso de aceite: 'renumerar o segundo CAPÍTULO III' é tarefa acionável."""
    apontamentos = analysis_registry.extrair_apontamentos(
        "- Renumerar o segundo CAPÍTULO III e ajustar os seguintes."
    )
    assert apontamentos
    assert "renumerar" in apontamentos[0]["texto"].lower()


def test_extrair_exclui_promessas_ofertas_e_perguntas():
    analise = (
        "Já analisei o conteúdo.\n"
        "Vou agora proceder com a análise detalhada, considerando:\n"
        "Sugiro renumerar o segundo CAPÍTULO III e ajustar os seguintes.\n"
        "Recomendo esclarecer o art. 3º.\n"
        "Se desejar, posso preparar um relatório formal e produzir um arquivo.\n"
        "Deseja seguir com as correções e gerar o arquivo corrigido?\n"
    )
    textos = [a["texto"] for a in analysis_registry.extrair_apontamentos(analise)]
    assert any("renumerar" in t.lower() for t in textos)
    assert any("esclarecer o art. 3º" in t for t in textos)
    assert not any("proceder" in t.lower() for t in textos)
    assert not any("posso preparar" in t.lower() for t in textos)
    assert not any("deseja seguir" in t.lower() for t in textos)


def test_aprofundamento_consolida_apontamentos_no_mesmo_documento(monkeypatch, tmp_path):
    """Turno sem nova leitura ('e o que está ruim?') acumula apontamentos no
    documento analisado, preservando a origem."""
    analysis_registry.nova_sessao()
    monkeypatch.setattr(agent, "messages", [])
    conteudo = "PORTARIA Nº 1/2026\nCAPÍTULO III\nCAPÍTULO III\nArt. 3º Texto."
    monkeypatch.setattr(generation, "_resolve_file", lambda n: Path(tmp_path) / n)
    monkeypatch.setattr(generation, "extract_file_text", lambda p: conteudo)
    monkeypatch.setitem(
        agent.FUNCTIONS,
        "analisar_arquivo_usuario",
        lambda **k: json.dumps({"filename": "doc.docx", "text": conteudo}),
    )

    tool_call = SimpleNamespace(
        id="c1",
        function=SimpleNamespace(name="analisar_arquivo_usuario", arguments="{}"),
    )
    respostas = [
        _resposta(_Msg(None, [tool_call])),
        _resposta(
            _Msg(
                "Já analisei o conteúdo.\n"
                "Vou agora proceder com a análise detalhada.\n"
                "Falta incluir o artigo de vigência.",
                None,
            )
        ),
    ]
    monkeypatch.setattr(agent, "perguntar", lambda *a, **k: respostas.pop(0))
    agent.executar("faça a análise do documento")

    respostas2 = [
        _resposta(
            _Msg(
                "Sugiro renumerar o segundo CAPÍTULO III e ajustar os seguintes.\n"
                "Recomendo esclarecer o art. 3º.\n"
                "Se desejar, posso preparar um relatório formal.",
                None,
            )
        )
    ]
    monkeypatch.setattr(agent, "perguntar", lambda *a, **k: respostas2.pop(0))
    agent.executar("e o que está ruim?")

    entrada = analysis_registry.obter(analysis_registry.sessao_atual(), "doc.docx")
    textos = [a["texto"] for a in entrada["apontamentos"]]
    assert any("renumerar o segundo CAPÍTULO III" in t for t in textos)
    assert any("esclarecer o art. 3º" in t for t in textos)
    assert any("vigência" in t for t in textos)
    assert not any("posso preparar" in t for t in textos)
    assert not any("proceder" in t.lower() for t in textos)
    origens = {a.get("origem") for a in entrada["apontamentos"]}
    assert "aprofundamento" in origens


def test_correcao_apos_aprofundamento_envia_todos_os_apontamentos(monkeypatch):
    """O pedido de correção encaminha a lista CONSOLIDADA (inicial + aprofundamento)."""
    monkeypatch.setattr(agent, "messages", [])
    capturado = {}

    def fake_melhorar(filename=None, diretrizes=None, apontamentos=None):
        capturado["apontamentos"] = apontamentos
        return json.dumps(
            {"status": "improved", "filename": filename, "alteracoes": []},
            ensure_ascii=False,
        )

    monkeypatch.setattr(agent, "melhorar_documento_usuario", fake_melhorar)
    monkeypatch.setattr(
        agent,
        "analise_para_correcao",
        lambda filename=None: {
            "filename": "doc.docx",
            "apontamentos": [
                {"id": "ap-vig", "texto": "Falta incluir o artigo de vigência."},
                {"id": "ap-renum", "texto": "Renumerar o segundo CAPÍTULO III."},
            ],
            "analise_completa": "...",
        },
    )

    agent.executar("corrija o documento e me entregue o arquivo")

    ids = {a["id"] for a in capturado["apontamentos"]}
    assert ids == {"ap-vig", "ap-renum"}


# ---------------------------------------------------------------------------
# Substituição de bloco: incisos II e IV reformulados não podem duplicar
# ---------------------------------------------------------------------------

_DOC19 = (
    "INSTRUÇÃO NORMATIVA Nº 1/2026\n"
    "Art. 19 A comercialização dos produtos poderá ser realizada por meio de:\n"
    "I - feiras, exposições e eventos institucionais;\n"
    "II - lojas físicas, pontos de venda ou plataformas virtuais mantidas ou "
    "parceiras da FUNAC e SEJUS;\n"
    "III - venda direta intermediada por familiares da PPL, desde que cumprida "
    "a metodologia de pagamento;\n"
    "IV - parcerias e conveniamentos com entidades públicas ou privadas.\n"
    "Parágrafo único. Os pontos de venda virtuais poderão ser criados."
)

_ALTERACAO19 = {
    "tipo": "corrigido",
    "rotulo": "Art. 19",
    "trecho_original": (
        "Art. 19 A comercialização dos produtos poderá ser realizada por meio de:"
    ),
    "novo_texto": (
        "Art. 19 A comercialização dos produtos poderá ser realizada por meio de:\n"
        "I – feiras, exposições e eventos institucionais; \n"
        "II – lojas físicas, pontos de venda ou plataformas virtuais mantidas "
        "ou parceiras da Fundação Nova Chance (FUNAC) e da Secretaria de Estado "
        "de Justiça (SEJUS); \n"
        "III – venda direta intermediada por familiares da PPL, desde que "
        "cumprida a metodologia de pagamento; \n"
        "IV – parcerias e convênios com entidades públicas ou privadas.\n"
        "Parágrafo único. Os pontos de venda virtuais poderão ser criados."
    ),
    "detalhe": "Reformulação dos incisos II e IV.",
}


def test_aplicar_patch_absorve_incisos_reformulados():
    depois = minuta._aplicar_patch_no_texto(_DOC19, [_ALTERACAO19], [])
    # Conteúdo ANTIGO dos incisos II e IV não permanece no texto final.
    assert "parceiras da FUNAC e SEJUS" not in depois
    assert "conveniamentos" not in depois
    # Conteúdo NOVO aparece uma única vez.
    assert depois.count("(FUNAC) e da Secretaria de Estado") == 1
    assert depois.count("convênios com entidades públicas") == 1


def _modelo_art19(tmp_path):
    from docx import Document

    from sejus_project.tools.document_infra.modelos import PORTARIA, PerfilModelo

    model_path = str(tmp_path / "Modelo.docx")
    doc = Document()
    for linha in _DOC19.splitlines()[1:]:
        doc.add_paragraph(linha)
    doc.save(model_path)
    return PerfilModelo(
        name="IN_ART19",
        file=model_path,
        act_types=PORTARIA.act_types,
        patterns=PORTARIA.patterns,
        preservar_moldura=True,
    )


def test_montar_docx_revisado_tacha_incisos_reformulados(tmp_path):
    from docx import Document

    from sejus_project.tools.document_infra import docx_builder
    from sejus_project.tools.document_infra.docx_engine import paragraph_text

    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    output_path = docx_builder.montar_docx_revisado(
        _modelo_art19(tmp_path), [_ALTERACAO19], [], [], tmp_path
    )

    doc = Document(str(output_path))
    paragrafos = [
        (paragraph_text(p), p.find(ns + "r/" + ns + "rPr/" + ns + "strike") is not None)
        for p in doc.element.body.iter(ns + "p")
    ]

    antigos = [
        (t, s)
        for t, s in paragrafos
        if "parceiras da FUNAC e SEJUS" in t or "conveniamentos" in t
    ]
    assert antigos, "os incisos antigos deveriam existir marcados"
    assert all(strike for _, strike in antigos), (
        "incisos II e IV antigos deveriam estar tachados (absorvidos)"
    )

    # O texto NOVO (verde, sem tachado) aparece uma vez para II e IV.
    novos = [
        t
        for t, strike in paragrafos
        if not strike
        and ("(FUNAC) e da Secretaria de Estado" in t or "convênios com entidades" in t)
    ]
    assert len(novos) == 1


_DOC_COMPLETO = (
    "INSTRUÇÃO NORMATIVA Nº 1/2026\n"
    "CAPÍTULO III\n"
    "DAS DISPOSIÇÕES GERAIS\n"
    "CAPÍTULO III\n"
    "DO CONTROLE\n"
    "Art. 19 A comercialização dos produtos poderá ser realizada por meio de:\n"
    "I - feiras, exposições e eventos institucionais;\n"
    "II - lojas físicas, pontos de venda ou plataformas virtuais mantidas ou "
    "parceiras da FUNAC e SEJUS;\n"
    "III - venda direta intermediada por familiares da PPL;\n"
    "IV - parcerias e conveniamentos com entidades públicas ou privadas.\n"
    "Parágrafo único. Os pontos de venda virtuais poderão ser criados.\n"
    "Art. 20 Todos os valores serão recolhidos ao FUNPEN.\n"
    "Art. 33 Esta Instrução Normativa entra em vigor na data de sua publicação."
)


def test_fluxo_completo_analise_aprofundamento_correcao_docx(monkeypatch, tmp_path):
    """upload → análise inicial → 'e o que está ruim?' → correção → DOCX.

    Confere que a melhoria recebe os apontamentos do aprofundamento, que os
    incisos II e IV aparecem uma única vez no DOCX e que cada apontamento tem
    resultado (aplicado / impedimento concreto / falha), sem omissão."""
    from docx import Document

    from sejus_project.tools.document_infra.docx_engine import paragraph_text
    from sejus_project.tools.document_infra.modelos import PORTARIA, PerfilModelo

    analysis_registry.nova_sessao()
    monkeypatch.setattr(agent, "messages", [])

    modelo = tmp_path / "USUARIO_ARTESANATO.docx"
    doc = Document()
    for linha in _DOC_COMPLETO.splitlines():
        doc.add_paragraph(linha)
    doc.save(str(modelo))

    monkeypatch.setattr(generation, "_resolve_file", lambda n: modelo)
    monkeypatch.setattr(generation, "extract_file_text", lambda p: _DOC_COMPLETO)
    monkeypatch.setattr(generation, "retrieve", lambda *a, **k: [])
    monkeypatch.setattr(generation, "_salvar_propostas_disc", lambda *a, **k: None)
    monkeypatch.setattr(
        generation.modelos, "detectar_tipo_ato", lambda t: "instrução normativa"
    )
    perfil = PerfilModelo(
        name="USUARIO_ARTESANATO",
        file=str(modelo),
        act_types=PORTARIA.act_types,
        patterns=PORTARIA.patterns,
        preservar_moldura=True,
    )
    monkeypatch.setattr(
        generation.modelos, "crear_perfil_de_arquivo", lambda *a, **k: perfil
    )

    # Turno 1: leitura + análise inicial.
    monkeypatch.setitem(
        agent.FUNCTIONS,
        "analisar_arquivo_usuario",
        lambda **k: json.dumps({"filename": modelo.name, "text": _DOC_COMPLETO}),
    )
    tool_call = SimpleNamespace(
        id="c1",
        function=SimpleNamespace(name="analisar_arquivo_usuario", arguments="{}"),
    )
    respostas = [
        _resposta(_Msg(None, [tool_call])),
        _resposta(
            _Msg(
                "Já analisei o conteúdo.\n"
                "Vou agora proceder com a análise detalhada.\n"
                "Falta incluir o artigo de vigência explícito.",
                None,
            )
        ),
    ]
    monkeypatch.setattr(agent, "perguntar", lambda *a, **k: respostas.pop(0))
    agent.executar("faça a análise do documento")

    # Turno 2: aprofundamento sem nova leitura.
    respostas2 = [
        _resposta(
            _Msg(
                "Sugiro renumerar o segundo CAPÍTULO III e ajustar os seguintes.\n"
                "Reformular os incisos II e IV do art. 19.\n"
                "Se desejar, posso preparar um relatório formal.",
                None,
            )
        )
    ]
    monkeypatch.setattr(agent, "perguntar", lambda *a, **k: respostas2.pop(0))
    agent.executar("e o que está ruim?")

    entrada = analysis_registry.obter(analysis_registry.sessao_atual(), modelo.name)
    apontamentos = entrada["apontamentos"]
    textos = [a["texto"] for a in apontamentos]
    assert any("renumerar o segundo CAPÍTULO III" in t for t in textos)
    assert any("Reformular os incisos II e IV" in t for t in textos)
    assert any("vigência" in t for t in textos)
    assert not any("posso preparar" in t for t in textos)

    id_renumerar = next(
        a["id"] for a in apontamentos if "renumerar o segundo CAPÍTULO III" in a["texto"]
    )
    id_incisos = next(
        a["id"] for a in apontamentos if "Reformular os incisos II e IV" in a["texto"]
    )

    # Patch da melhoria: aplica a reformulação dos incisos; renumerar vira
    # impedimento concreto; vigência fica sem entrada (falha de execução).
    patch = {
        "numero": "INSTRUÇÃO NORMATIVA Nº 1/2026",
        "ementa": "Dispõe sobre o trabalho artesanal.",
        "alteracoes": [_ALTERACAO19],
        "remocoes": [],
        "adicoes_estruturais": [],
        "apontamentos_analise": [
            {"apontamento_id": id_incisos, "status": "aplicado", "referencia": "Art. 19"},
            {
                "apontamento_id": id_renumerar,
                "status": "nao_aplicado",
                "motivo": (
                    "requer decisão da equipe jurídica sobre a nova sequência de "
                    "capítulos antes da publicação"
                ),
            },
        ],
    }
    monkeypatch.setattr(
        minuta, "_extrair_json_com_retry", lambda *a, **k: dict(patch)
    )

    # Pedido de correção → melhoria (sem formulário de geração).
    resposta = agent.executar("consegue fazer a correção e me dar o arquivo corrigido?")
    assert "apontamentos" in resposta.lower()

    cobertura = {
        c["apontamento_id"]: c for c in generation.ultima_comparacao()["apontamentos_analise"]
    }
    assert cobertura[id_incisos]["status"] == "aplicado"
    assert cobertura[id_renumerar]["status"] == "nao_aplicado"
    assert "decisão" in cobertura[id_renumerar]["motivo"]
    vigencia = next(
        c for c in cobertura.values() if "vigência" in c["apontamento"]
    )
    assert vigencia["status"] == "falhou"

    # DOCX: incisos II e IV antigos tachados; novos aparecem uma vez.
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    output_path = generation.ultima_minuta()["output_path"]
    doc_final = Document(str(output_path))
    paragrafos = [
        (
            paragraph_text(p),
            p.find(ns + "r/" + ns + "rPr/" + ns + "strike") is not None,
        )
        for p in doc_final.element.body.iter(ns + "p")
    ]
    antigos = [
        (t, s)
        for t, s in paragrafos
        if "parceiras da FUNAC e SEJUS" in t or "conveniamentos" in t
    ]
    assert antigos and all(strike for _, strike in antigos)
    novos = [
        t
        for t, strike in paragrafos
        if not strike
        and ("(FUNAC) e da Secretaria de Estado" in t or "convênios com entidades" in t)
    ]
    assert len(novos) == 1


# ---------------------------------------------------------------------------
# Fallback de entrega: sempre disponibiliza um arquivo
# ---------------------------------------------------------------------------

_ALTERACAO_INVALIDA = {
    "tipo": "corrigido",
    "rotulo": "Art. 999º",
    "trecho_original": "Art. 999º Dispositivo inexistente.",
    "novo_texto": "Art. 999º Corrigido.",
    "detalhe": "Âncora inexistente.",
}


def _preparar_melhoria(monkeypatch, tmp_path, patch):
    from docx import Document

    from sejus_project.tools.document_infra.modelos import PORTARIA, PerfilModelo

    generation.limpar_estado()
    modelo = tmp_path / "USUARIO.docx"
    doc = Document()
    for linha in _DOC19.splitlines():
        doc.add_paragraph(linha)
    doc.save(str(modelo))

    monkeypatch.setattr(generation, "_resolve_file", lambda n: modelo)
    monkeypatch.setattr(generation, "extract_file_text", lambda p: _DOC19)
    monkeypatch.setattr(generation, "retrieve", lambda *a, **k: [])
    monkeypatch.setattr(generation, "_salvar_propostas_disc", lambda *a, **k: None)
    monkeypatch.setattr(generation, "OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(
        generation.modelos, "detectar_tipo_ato", lambda t: "instrução normativa"
    )
    perfil = PerfilModelo(
        name="USUARIO_X",
        file=str(modelo),
        act_types=PORTARIA.act_types,
        patterns=PORTARIA.patterns,
        preservar_moldura=True,
    )
    monkeypatch.setattr(
        generation.modelos, "crear_perfil_de_arquivo", lambda *a, **k: perfil
    )
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", lambda *a, **k: dict(patch))
    return modelo


def _patch_base(**extra):
    dados = {
        "numero": "INSTRUÇÃO NORMATIVA Nº 1/2026",
        "ementa": "Dispõe sobre o trabalho artesanal.",
        "alteracoes": [],
        "remocoes": [],
        "adicoes_estruturais": [],
    }
    dados.update(extra)
    return dados


def test_fallback_nenhuma_correcao_entrega_copia_intacta(monkeypatch, tmp_path):
    modelo = _preparar_melhoria(
        monkeypatch, tmp_path, _patch_base(alteracoes=[_ALTERACAO_INVALIDA])
    )

    resultado = json.loads(
        generation.melhorar_documento_usuario(filename="USUARIO.docx")
    )

    assert resultado["status"] == "improved"
    assert resultado["fallback"] is True
    assert resultado["mensagem"] == (
        "Não foi possível aplicar as correções. "
        "Este arquivo preserva o conteúdo original."
    )
    output = Path(resultado["output_path"])
    assert output.is_file()
    # Cópia INTACTA (byte a byte) do original — nada de marcas nem resumo.
    assert output.read_bytes() == modelo.read_bytes()


def test_fallback_parcial_entrega_documento_parcialmente_corrigido(monkeypatch, tmp_path):
    _preparar_melhoria(
        monkeypatch,
        tmp_path,
        _patch_base(alteracoes=[_ALTERACAO19, _ALTERACAO_INVALIDA]),
    )

    resultado = json.loads(
        generation.melhorar_documento_usuario(filename="USUARIO.docx")
    )

    assert resultado["fallback"] is False
    assert len(resultado["alteracoes"]) == 1
    assert resultado["descartados"]
    output = Path(resultado["output_path"])
    assert output.is_file()

    from docx import Document

    from sejus_project.tools.document_infra.docx_engine import paragraph_text

    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    doc = Document(str(output))
    paragrafos = [
        (paragraph_text(p), p.find(ns + "r/" + ns + "rPr/" + ns + "strike") is not None)
        for p in doc.element.body.iter(ns + "p")
    ]
    antigos = [
        (t, s)
        for t, s in paragrafos
        if "parceiras da FUNAC e SEJUS" in t or "conveniamentos" in t
    ]
    assert antigos and all(strike for _, strike in antigos)


def test_todas_correcoes_entregam_documento_corrigido(monkeypatch, tmp_path):
    _preparar_melhoria(monkeypatch, tmp_path, _patch_base(alteracoes=[_ALTERACAO19]))

    resultado = json.loads(
        generation.melhorar_documento_usuario(filename="USUARIO.docx")
    )

    assert resultado["fallback"] is False
    assert len(resultado["alteracoes"]) == 1
    assert Path(resultado["output_path"]).is_file()
    assert not resultado.get("descartados")


def test_fallback_quando_llm_falha_entrega_copia_intacta(monkeypatch, tmp_path):
    """Falha de geração do patch (LLM) também entrega a cópia intacta."""
    modelo = _preparar_melhoria(monkeypatch, tmp_path, _patch_base())

    def _falha(*args, **kwargs):
        raise ValueError("modelo nao devolveu estrutura valida")

    monkeypatch.setattr(minuta, "_extrair_json_com_retry", _falha)

    resultado = json.loads(
        generation.melhorar_documento_usuario(filename="USUARIO.docx")
    )

    assert resultado["status"] == "improved"
    assert resultado["fallback"] is True
    output = Path(resultado["output_path"])
    assert output.is_file()
    assert output.read_bytes() == modelo.read_bytes()