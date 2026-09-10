import json
import os
from pathlib import Path

import pytest
from docx import Document
from docx.shared import Pt

from sejus_project.tools import document_generation as generation
from sejus_project.tools import docx_templates, modelos
from sejus_project.tools.modelos import PerfilModelo

PROMPTS_DIR = Path(__file__).parent / "prompts"

ESTRUTURA = {
    "numero": "PORTARIA Nº 001/2026/GAB-SEJUS/MT",
    "ementa": "Dispõe sobre rotina de limpeza nas unidades.",
    "preambulo": (
        "O SECRETÁRIO DE ESTADO DE JUSTIÇA, no uso das atribuições que lhe "
        "confere o art. 71 da Constituição Estadual,"
    ),
    "considerandos": [
        "CONSIDERANDO a necessidade de padronizar a rotina de limpeza;",
        "CONSIDERANDO o disposto na legislação vigente;",
    ],
    "resolutivo": "RESOLVE:",
    "corpo": [
        {
            "rotulo": "Art. 1º",
            "texto": "Instituir o Programa de Limpeza nas unidades da SEJUS.",
            "subitens": [
                {"tipo": "inciso", "rotulo": "I -", "texto": "padronizar os procedimentos diários;"},
                {"tipo": "inciso", "rotulo": "II -", "texto": "definir responsáveis por unidade;"},
            ],
        },
        {
            "rotulo": "Art. 2º",
            "texto": "Criar comissão de fiscalização da rotina de limpeza.",
            "subitens": [],
        },
    ],
    "fechamento": [
        {"rotulo": "", "texto": "Esta Portaria entra em vigor na data de sua publicação."}
    ],
    "local_data": "Cuiabá-MT, 8 de setembro de 2026.",
    "assinaturas": [
        {"nome": "VITOR HUGO BRUZULATO TEIXEIRA", "cargo": "Secretário de Estado de Justiça"},
    ],
}


@pytest.fixture
def fake_retrieval(monkeypatch):
    calls = []

    def retrieve(query, **kwargs):
        calls.append((query, kwargs))
        return [
            {
                "source_file": "ato_teste.md",
                "act_type": kwargs.get("act_type") or "ATO",
                "act_number": "10/2025",
                "score": 0.91,
                "text": "Fundamento normativo recuperado para a minuta.",
            }
        ]

    monkeypatch.setattr(generation, "retrieve", retrieve)
    return calls


@pytest.fixture
def fake_minuta(monkeypatch, tmp_path):
    def gerar_estrutura_minuta(pedido, tipo, perfil, contexto, valores, modelo_referencia=None):
        estrutura = ESTRUTURA.copy()
        return estrutura

    def montar_docx(perfil, estrutura, output_dir):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"gen_{perfil.name}.docx"
        document = Document()
        document.add_paragraph("PORTARIA gerada para teste")
        document.save(str(output_path))
        return output_path

    monkeypatch.setattr(generation.minuta, "gerar_estrutura_minuta", gerar_estrutura_minuta)
    monkeypatch.setattr(generation.minuta, "montar_docx", montar_docx)
    return tmp_path


@pytest.fixture
def fake_melhoria(monkeypatch, tmp_path, fake_minuta):
    """Igual ao fake_minuta, mas com gerar_estrutura_melhoria fake que devolve
    estrutura + lista de alteracoes."""
    capturados = {}

    def gerar_estrutura_melhoria(conteudo, tipo, perfil, contexto, valores=None):
        capturados.update(
            {
                "conteudo": conteudo,
                "tipo": tipo,
                "perfil": perfil,
                "valores": valores,
            }
        )
        return ESTRUTURA.copy(), [
            {
                "tipo": "corrigido",
                "o_que": "Fundamento legal no preâmbulo",
                "detalhe": "Atualizado para o art. vigente recuperado no RAG.",
            },
            {
                "tipo": "adicionado",
                "o_que": "Artigo de vigência",
                "detalhe": "Incluída cláusula de vigência na data de publicação.",
            },
        ]

    monkeypatch.setattr(
        generation.minuta, "gerar_estrutura_melhoria", gerar_estrutura_melhoria
    )
    return capturados


def test_first_call_returns_fields_and_context(fake_retrieval, fake_minuta):
    generation._pending_document = None
    request = "Gere uma portaria sobre limpeza da cadeia em Cuiaba."

    inspection = json.loads(generation.gerar_documento_normativo(request))

    assert inspection["status"] == "awaiting_confirmation"
    assert inspection["modelo"] == "PORTARIA"
    assert inspection["campos"] == generation.CAMPOS_BASE
    assert "PORTARIA" in inspection["available_models"]
    assert inspection["contexto"][0]["source_file"] == "ato_teste.md"
    assert fake_retrieval
    assert "limit" in fake_retrieval[0][1]
    assert fake_retrieval[0][1]["act_type"] == "PORTARIA"


def test_pendencia_sinalizada_no_turno_da_primeira_chamada(fake_retrieval, fake_minuta):
    generation._pending_document = None
    generation._pendencia_mudou_no_turno = False
    request = "Gere uma portaria sobre limpeza da cadeia em Cuiaba."

    inspection = json.loads(generation.gerar_documento_normativo(request))

    assert inspection["status"] == "awaiting_confirmation"
    assert generation.has_pending_document() is True
    assert generation.consumir_pendencia_do_turno() is True
    assert generation.consumir_pendencia_do_turno() is False
    assert generation.has_pending_document() is True


def test_cancelar_pendencia_descarta_estado(fake_retrieval, fake_minuta):
    generation._pending_document = None
    generation._pendencia_mudou_no_turno = False
    request = "Gere uma portaria sobre limpeza da cadeia em Cuiaba."

    json.loads(generation.gerar_documento_normativo(request))
    assert generation.has_pending_document() is True

    generation.cancelar_pendencia()

    assert generation.has_pending_document() is False
    assert generation.consumir_pendencia_do_turno() is False


def test_inform_campos_produces_generated(fake_retrieval, fake_minuta):
    generation._pending_document = None
    request = "Gere uma portaria sobre limpeza da cadeia em Cuiaba."
    values = {
        "numero_ato": "PORTARIA Nº 12/2026/GAB-SEJUS/MT",
        "signatario": "VITOR HUGO BRUZULATO TEIXEIRA",
        "data_ato": "08/09/2026",
    }

    result = json.loads(generation.gerar_documento_normativo(request, values=values))

    assert result["status"] == "generated"
    assert result["modelo"] == "PORTARIA"
    assert result["output_path"].endswith(".docx")
    assert Path(result["output_path"]).is_file()
    assert result["review_required"] is True
    assert result["auto_filled"] is True
    assert result["sources"][0]["source_file"] == "ato_teste.md"


def test_authorization_generates_pending_document(fake_retrieval, fake_minuta):
    generation._pending_document = None
    request = "Gere uma portaria sobre limpeza da cadeia em Cuiaba."

    first = json.loads(generation.gerar_documento_normativo(request))
    assert first["status"] == "awaiting_confirmation"

    result = json.loads(generation.gerar_documento_normativo("pode inventar consultando o banco"))

    assert result["status"] == "generated"
    assert result["modelo"] == "PORTARIA"
    assert result["review_required"] is True
    assert Path(result["output_path"]).is_file()


def test_short_confirmation_generates_pending_document(fake_retrieval, fake_minuta):
    generation._pending_document = None
    request = "Gere uma portaria sobre limpeza da cadeia em Cuiaba."

    json.loads(generation.gerar_documento_normativo(request))
    result = json.loads(generation.gerar_documento_normativo("sim"))

    assert result["status"] == "generated"
    assert result["review_required"] is True
    assert Path(result["output_path"]).is_file()


def test_melhoria_docx_preserva_layout_e_gera_comparacao(fake_retrieval, fake_melhoria, monkeypatch, tmp_path):
    """Melhorar um .docx usa o próprio arquivo como modelo de formatação e
    guarda os dados da comparação antes/depois."""
    from sejus_project.tools import user_files

    generation._ultima_minuta = None
    generation._ultima_comparacao = None
    generation._melhoria_no_turno = False

    arquivo = tmp_path / "portaria_limpeza.docx"
    document = Document()
    document.add_paragraph("PORTARIA Nº 45/2026/GAB-SEJUS/MT")
    document.save(str(arquivo))

    monkeypatch.setattr(user_files, "IMPORTACOES_DIR", tmp_path)
    result = json.loads(
        generation.melhorar_documento_usuario(
            "portaria_limpeza.docx",
            diretrizes="mantenha o mesmo número",
        )
    )

    assert result["status"] == "improved"
    assert Path(result["output_path"]).is_file()
    assert result["alteracoes"]
    assert generation._melhoria_no_turno is True
    assert generation.consumir_melhoria_do_turno() is True

    comparacao = generation.ultima_comparacao()
    assert comparacao["arquivo_original"] == "portaria_limpeza.docx"
    assert "PORTARIA Nº 45/2026/GAB-SEJUS/MT" in comparacao["antes"]
    assert comparacao["alteracoes"][0]["tipo"] == "corrigido"

    capturado = fake_melhoria
    assert capturado["perfil"].preservar_moldura is True
    assert capturado["valores"] == {"diretrizes": "mantenha o mesmo número"}
    assert capturado["tipo"] in ("portaria", "instrução normativa", "decreto", "retificação")


def test_melhoria_txt_usa_template_padrao(fake_retrieval, fake_melhoria, monkeypatch, tmp_path):
    """Arquivos sem formatação (txt/pdf/md) caem no template oficial do tipo."""
    from sejus_project.tools import user_files

    monkeypatch.setattr(user_files, "IMPORTACOES_DIR", tmp_path)
    (tmp_path / "ato_decreto.txt").write_text(
        "DECRETO Nº 1/2026/GAB-SEJUS/MT\nDispõe sobre limpeza das unidades.\n",
        encoding="utf-8",
    )

    result = json.loads(generation.melhorar_documento_usuario("ato_decreto.txt"))

    assert result["status"] == "improved"
    assert fake_melhoria["tipo"] == "decreto"
    assert fake_melhoria["perfil"].name.startswith("DECRETO")
    assert fake_melhoria["perfil"].preservar_moldura is False


def test_melhoria_arquivo_inexistente_retorna_erro(fake_retrieval, fake_melhoria):
    result = json.loads(generation.melhorar_documento_usuario("nao_existe.pdf"))

    assert result["status"] == "error"
    assert "não encontrado" in result["error"].casefold()


def test_melhoria_sem_nome_usa_importacao_mais_recente(fake_retrieval, fake_melhoria, monkeypatch, tmp_path):
    """Sem informar o arquivo, a tool usa a importação mais recente e devolve
    as alternativas na lista 'outros'."""
    from sejus_project.tools import user_files

    generation._ultima_comparacao = None
    monkeypatch.setattr(user_files, "IMPORTACOES_DIR", tmp_path)
    (tmp_path / "antigo.txt").write_text(
        "DECRETO Nº 1/2025/GAB-SEJUS/MT\nTexto antigo.\n", encoding="utf-8"
    )
    (tmp_path / "recente.txt").write_text(
        "INSTRUÇÃO NORMATIVA Nº 10/2026/GAB-SEJUS/MT\nTexto novo.\n",
        encoding="utf-8",
    )
    os.utime(tmp_path / "antigo.txt", (1_700_000_000, 1_700_000_000))
    os.utime(tmp_path / "recente.txt", (1_700_000_001, 1_700_000_001))

    result = json.loads(generation.melhorar_documento_usuario())

    assert result["status"] == "improved"
    assert result["filename"] == "recente.txt"
    assert result["outros"] == ["antigo.txt"]
    assert generation.ultima_comparacao()["arquivo_original"] == "recente.txt"


def test_melhoria_sem_arquivos_retorna_erro(fake_retrieval, fake_melhoria, monkeypatch, tmp_path):
    from sejus_project.tools import user_files

    monkeypatch.setattr(user_files, "IMPORTACOES_DIR", tmp_path)
    result = json.loads(generation.melhorar_documento_usuario())

    assert result["status"] == "error"
    assert "Nenhum arquivo importado" in result["error"]


def test_melhoria_guarda_textos_reais_para_acompanhamento(fake_retrieval, fake_melhoria, monkeypatch, tmp_path):
    """A melhoria guarda os textos reais antes/depois (não só resumos) para o
    agente responder 'o que mudou' e montar tabelas com fidelidade depois."""
    from sejus_project.tools import user_files

    generation._ultima_comparacao = None
    monkeypatch.setattr(user_files, "IMPORTACOES_DIR", tmp_path)
    (tmp_path / "ato_ante.txt").write_text(
        "PORTARIA Nº 45/2026/GAB-SEJUS/MT\nDispõe sobre limpeza das unidades.\n",
        encoding="utf-8",
    )

    result = json.loads(generation.melhorar_documento_usuario("ato_ante.txt"))

    assert result["status"] == "improved"
    assert result["textos"] and result["textos"][0]["antes"]

    dados = generation.ultima_comparacao()
    assert dados["arquivo_original"] == "ato_ante.txt"
    assert dados["depois"]
    assert dados["textos"] and dados["textos"][0]["antes"]
    assert dados["textos"][0]["depois"]
    assert len(dados["sha1"]) == 40


def test_melhoria_repetida_nao_regenera_arquivo(fake_retrieval, fake_melhoria, monkeypatch, tmp_path):
    """Repetir o pedido de melhoria do mesmo arquivo (sem diretrizes novas)
    reaproveita a comparação e não cria outro arquivo — evita duplicar o
    retrabalho em perguntas de acompanhamento."""
    from sejus_project.tools import user_files

    generation._ultima_comparacao = None
    generation._melhoria_no_turno = False
    monkeypatch.setattr(user_files, "IMPORTACOES_DIR", tmp_path)
    (tmp_path / "ato_repetido.txt").write_text(
        "DECRETO Nº 1/2026/GAB-SEJUS/MT\nDispõe sobre limpeza das unidades.\n",
        encoding="utf-8",
    )

    chamadas = {"montar_docx": 0}
    original = generation.minuta.montar_docx

    def contar_montar_docx(perfil, estrutura, output_dir):
        chamadas["montar_docx"] += 1
        return original(perfil, estrutura, output_dir)

    monkeypatch.setattr(generation.minuta, "montar_docx", contar_montar_docx)

    first = json.loads(generation.melhorar_documento_usuario("ato_repetido.txt"))
    second = json.loads(generation.melhorar_documento_usuario("ato_repetido.txt"))

    assert first["status"] == "improved"
    assert second["status"] == "already_improved"
    assert second["arquivo_original"] == "ato_repetido.txt"
    assert second["textos"]
    assert chamadas["montar_docx"] == 1
    assert generation._melhoria_no_turno is True
    generation.consumir_melhoria_do_turno()
    assert generation._melhoria_no_turno is False


def test_melhoria_com_diretrizes_novas_regenera(fake_retrieval, fake_melhoria, monkeypatch, tmp_path):
    """Diretrizes novas para o mesmo arquivo não caem no guarda
    'already_improved' — o retrabalho acontece de verdade."""
    from sejus_project.tools import user_files

    generation._ultima_comparacao = None
    monkeypatch.setattr(user_files, "IMPORTACOES_DIR", tmp_path)
    (tmp_path / "ato_diretriz.txt").write_text(
        "PORTARIA Nº 4/2026/GAB-SEJUS/MT\nDispõe sobre limpeza.\n",
        encoding="utf-8",
    )

    chamadas = {"montar_docx": 0}
    original = generation.minuta.montar_docx

    def contar_montar_docx(perfil, estrutura, output_dir):
        chamadas["montar_docx"] += 1
        return original(perfil, estrutura, output_dir)

    monkeypatch.setattr(generation.minuta, "montar_docx", contar_montar_docx)

    first = json.loads(generation.melhorar_documento_usuario("ato_diretriz.txt"))
    second = json.loads(
        generation.melhorar_documento_usuario(
            "ato_diretriz.txt", diretrizes="mantenha o mesmo organograma"
        )
    )

    assert first["status"] == "improved"
    assert second["status"] == "improved"
    assert chamadas["montar_docx"] == 2


def test_obter_textos_comparacao_devolve_dados_da_ultima_melhoria(fake_retrieval, fake_melhoria, monkeypatch, tmp_path):
    """A tool de acompanhamento devolve textos reais sem gerar arquivo novo."""
    from sejus_project.tools import user_files

    generation._ultima_comparacao = None
    monkeypatch.setattr(user_files, "IMPORTACOES_DIR", tmp_path)
    (tmp_path / "ato_followup.txt").write_text(
        "PORTARIA Nº 8/2026/GAB-SEJUS/MT\nDispõe sobre limpeza.\n",
        encoding="utf-8",
    )

    generation.melhorar_documento_usuario("ato_followup.txt")
    dados = json.loads(generation.obter_textos_comparacao())

    assert dados["status"] == "ok"
    assert dados["arquivo_original"] == "ato_followup.txt"
    assert dados["textos"] and dados["textos"][0]["antes"]
    assert dados["antes"]
    assert dados["depois"]


def test_obter_textos_comparacao_sem_melhoria(fake_retrieval):
    generation._ultima_comparacao = None
    dados = json.loads(generation.obter_textos_comparacao())

    assert dados["status"] == "sem_comparacao"


def test_template_name_overrides_auto_selection(fake_retrieval, fake_minuta):
    generation._pending_document = None
    request = "Gere uma portaria sobre limpeza da cadeia em Cuiaba."

    result = json.loads(
        generation.gerar_documento_normativo(request, template_name="DECRETO_LEGADO")
    )

    assert result["status"] == "awaiting_confirmation"
    assert result["modelo"] == "DECRETO_LEGADO"


def test_unknown_template_returns_error(fake_retrieval, fake_minuta):
    result = json.loads(
        generation.gerar_documento_normativo("portaria de teste", template_name="NAO_EXISTE")
    )

    assert result["status"] == "error"
    assert "NAO_EXISTE" in result["error"]


@pytest.mark.parametrize(
    "pedido, expected_model",
    [
        ("Instrução normativa sobre rotina de limpeza nas unidades.", "IN_FUNCAO_ARMADA"),
        ("Portaria conjunta para criar grupo de trabalho.", "PORTARIA_CONJUNTA"),
        ("Retificação de portaria sobre lotacionograma.", "RETIFICACAO"),
        ("Decreto sobre higiene das unidades prisionais.", "DECRETO_LEGADO"),
        ("Portaria designando servidores para o setor.", "PORTARIA"),
    ],
)
def test_modelo_auto_selecionado_por_tipo(
    pedido, expected_model, fake_retrieval, fake_minuta
):
    generation._pending_document = None

    inspection = json.loads(generation.gerar_documento_normativo(pedido))

    assert inspection["status"] == "awaiting_confirmation"
    assert inspection["modelo"] == expected_model


# ---------------------------------------------------------------------------
# Montagem real (minuta.montar_docx + docx_engine)
# ---------------------------------------------------------------------------


@pytest.fixture
def modelo_portaria_tmp(tmp_path):
    document = Document()
    titulo = document.add_paragraph()
    run = titulo.add_run("PORTARIA Nº 45/2025/GAB-SEJUS/MT")
    run.bold = True
    run.font.size = Pt(13)

    document.add_paragraph("Institui o Programa de Limpeza.")
    document.add_paragraph(
        "O Secretário de Estado de Justiça, no uso das atribuições que lhe "
        "confere o art. 71 da Constituição Estadual,"
    )
    document.add_paragraph("CONSIDERANDO a necessidade de padronizar a rotina;")
    document.add_paragraph("RESOLVE:")
    document.add_paragraph("Art. 1º Instituir o Programa de Limpeza nas unidades.")
    document.add_paragraph("§ 1º O programa abrange as unidades administrativas da SEJUS.")
    document.add_paragraph("I - padronizar os procedimentos diários;")
    document.add_paragraph("II - definir responsáveis por unidade;")
    document.add_paragraph("Esta Portaria entra em vigor na data de sua publicação.")
    document.add_paragraph("Cuiabá-MT, 8 de setembro de 2026.")
    document.add_paragraph("VITOR HUGO BRUZULATO TEIXEIRA")
    document.add_paragraph("Secretário de Estado de Justiça")

    model_path = tmp_path / "Modelo_Portaria_Teste.docx"
    document.save(str(model_path))

    perfil = PerfilModelo(
        name="PORTARIA_TESTE",
        file=str(model_path),
        act_types=modelos.PORTARIA.act_types,
        patterns=modelos.PORTARIA.patterns,
    )
    return perfil, model_path


def test_montagem_preserva_formatacao_e_monta_estrutura(modelo_portaria_tmp, tmp_path):
    perfil, _ = modelo_portaria_tmp
    from sejus_project.tools import minuta

    output_path = minuta.montar_docx(perfil, ESTRUTURA, tmp_path)

    assert output_path.is_file()
    document = Document(str(output_path))
    paragraphs = document.paragraphs
    texts = [p.text for p in paragraphs]

    assert "PORTARIA Nº 001/2026/GAB-SEJUS/MT" in texts
    assert "Dispõe sobre rotina de limpeza nas unidades." in texts
    assert "CONSIDERANDO a necessidade de padronizar a rotina de limpeza;" in texts
    assert "RESOLVE:" in texts
    assert "Art. 1º Instituir o Programa de Limpeza nas unidades da SEJUS." in texts
    assert "I - padronizar os procedimentos diários;" in texts
    assert "II - definir responsáveis por unidade;" in texts
    assert "Art. 2º Criar comissão de fiscalização da rotina de limpeza." in texts
    assert "Esta Portaria entra em vigor na data de sua publicação." in texts
    assert "Cuiabá-MT, 8 de setembro de 2026." in texts
    assert "VITOR HUGO BRUZULATO TEIXEIRA" in texts
    assert "Secretário de Estado de Justiça" in texts

    titulo = next(p for p in paragraphs if p.text.startswith("PORTARIA Nº 001/2026"))
    assert titulo.runs[0].bold is True
    assert titulo.runs[0].font.size == Pt(13)


def test_montagem_adiciona_vigencia_quando_faltam(fake_retrieval, modelo_portaria_tmp, tmp_path):
    from sejus_project.tools import minuta

    perfil, _ = modelo_portaria_tmp
    estrutura = ESTRUTURA.copy()
    estrutura["fechamento"] = []

    output_path = minuta.montar_docx(perfil, estrutura, tmp_path)
    document = Document(str(output_path))
    texts = [p.text for p in document.paragraphs]

    assert any("entra em vigor" in t.casefold() for t in texts)


def test_montagem_remove_lixo_de_diario(modelo_portaria_tmp, tmp_path):
    from sejus_project.tools import minuta

    perfil, _ = modelo_portaria_tmp
    output_path = minuta.montar_docx(perfil, ESTRUTURA, tmp_path)
    document = Document(str(output_path))
    text = "\n".join(p.text for p in document.paragraphs)

    assert "Diário Oficial" not in text
    assert "Protocolo" not in text
    assert "Página" not in text


def test_docx_placeholder_can_cross_runs(tmp_path):
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("Ementa: [EM")
    paragraph.add_run("ENTA]")
    source = tmp_path / "source.docx"
    document.save(source)

    loaded = Document(source)
    from sejus_project.tools.docx_templates import _replace_in_paragraph

    assert _replace_in_paragraph(loaded.paragraphs[0], {"[EMENTA]": "Limpeza"}) == 1
    assert loaded.paragraphs[0].text == "Ementa: Limpeza"


def test_docx_placeholder_in_table_is_replaced(monkeypatch, tmp_path):
    document = Document()
    cell = document.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = "Responsavel: [NOME]"
    source = tmp_path / "Template_Tabela.docx"
    document.save(source)

    monkeypatch.setattr(docx_templates, "TEMPLATES_DIR", tmp_path)
    monkeypatch.setattr(docx_templates, "OUTPUTS_DIR", tmp_path / "outputs")
    result = docx_templates.fill_template(
        source.name, {"[NOME]": "Equipe de limpeza"}
    )

    generated = Document(result["output_path"])
    assert generated.tables[0].cell(0, 0).text == "Responsavel: Equipe de limpeza"
    assert result["remaining_placeholders"] == []


def test_template_path_traversal_is_rejected():
    with pytest.raises(docx_templates.TemplateError):
        docx_templates.resolve_template("../Template_Portaria.docx")


# ---------------------------------------------------------------------------
# Modelo enviado pelo usuário (botão 'Modelo')
# ---------------------------------------------------------------------------


def _salvar_docx_portaria(tmp_path: Path, nome="Modelo_Usuario_Portaria.docx") -> Path:
    document = Document()
    titulo = document.add_paragraph()
    run = titulo.add_run("PORTARIA Nº 45/2026/GAB-SEJUS/MT")
    run.bold = True
    run.font.size = Pt(13)
    document.add_paragraph("Institui o Programa de Limpeza nas unidades.")
    document.add_paragraph(
        "O Secretário de Estado de Justiça, no uso das atribuições que lhe "
        "confere o art. 71 da Constituição Estadual,"
    )
    document.add_paragraph("CONSIDERANDO a necessidade de padronizar a rotina;")
    document.add_paragraph("RESOLVE:")
    document.add_paragraph("Art. 1º Instituir o Programa de Limpeza nas unidades.")
    document.add_paragraph("Esta Portaria entra em vigor na data de sua publicação.")
    document.add_paragraph("Cuiabá-MT, 8 de setembro de 2026.")
    document.add_paragraph("VITOR HUGO BRUZULATO TEIXEIRA")
    document.add_paragraph("Secretário de Estado de Justiça")
    caminho = tmp_path / nome
    document.save(str(caminho))
    return caminho


def test_set_modelo_usuario_detecta_tipo_e_constroi_perfil(tmp_path):
    generation._modelo_usuario = None
    caminho = _salvar_docx_portaria(tmp_path)

    resumo = generation.set_modelo_usuario(caminho.name, tmp_path)

    assert resumo["filename"] == caminho.name
    assert resumo["tipo_ato"] == "portaria"
    perfil = generation._modelo_usuario["perfil"]
    assert perfil.name == f"USUARIO_{caminho.stem}"
    assert perfil.act_types == modelos.PORTARIA.act_types
    assert perfil.patterns == modelos.PORTARIA.patterns
    assert Path(perfil.file).resolve() == caminho.resolve()
    generation._modelo_usuario = None


@pytest.mark.parametrize(
    "titulo,esperado",
    [
        ("PORTARIA Nº 45/2026/GAB-SEJUS/MT", "portaria"),
        ("DECRETO Nº 123/2026, de 5 de março", "decreto"),
        ("INSTRUÇÃO NORMATIVA Nº 26/2026/SEJUS", "instrução normativa"),
        ("PORTARIA CONJUNTA Nº 07/2026/GAB-SEJUS", "portaria conjunta"),
        ("RETIFICAÇÃO DE PORTARIA Nº 03/2026", "retificação"),
    ],
)
def test_detectar_tipo_ato_por_titulo(titulo, esperado):
    # O preâmbulo cita decreto/lei e confundiria a detecção por palavra solta;
    # a detecção precisa vir do título.
    corpo = (
        titulo
        + "\n\nDiário Oficial de Mato Grosso\n"
        + "O Secretário de Estado de Justiça, no uso das atribuições que lhe "
        + "conferem o Decreto nº 100/2025, a Lei Estadual e a Constituição,"
    )
    assert modelos.detectar_tipo_ato_por_titulo(corpo) == esperado


def test_modelo_usuario_detecta_tipo_pelo_titulo_ignorando_corpo(tmp_path):
    from sejus_project.tools import minuta

    document = Document()
    titulo = document.add_paragraph()
    titulo.add_run("PORTARIA Nº 45/2026/GAB-SEJUS/MT")
    document.add_paragraph(
        "O Secretário de Estado de Justiça, no uso das atribuições do "
        "Decreto nº 100/2025 e da Lei Estadual nº 11.000, RESOLVE:"
    )
    document.add_paragraph("Art. 1º Instituir rotina de limpeza.")
    document.add_paragraph("Cuiabá-MT, 8 de setembro de 2026.")
    caminho = tmp_path / "Modelo_Com_Decreto_No_Corpo.docx"
    document.save(str(caminho))

    texto = generation.extract_file_text(caminho)
    perfil = modelos.crear_perfil_de_arquivo(caminho, texto, "Com_Decreto_No_Corpo")

    assert perfil.act_types == modelos.PORTARIA.act_types
    refs = minuta._referencias(Document(str(caminho)), perfil)
    assert refs["titulo"] is not None
    assert refs["preambulo"] is not None
    assert refs["artigo"] is not None


def test_modelo_usuario_preserva_moldura_do_diario(tmp_path):
    """Capturas do Diário Oficial têm cabeçalho e rodapé no corpo; a montagem
    com modelo do usuário deve preservar essa moldura e reconstruir o miolo."""
    from sejus_project.tools import minuta

    document = Document()
    document.add_paragraph("18 de junho de 2025")
    document.add_paragraph("DiárioOficial")
    document.add_paragraph("Nº 29.013")
    document.add_paragraph("Página 69")
    titulo = document.add_paragraph()
    titulo.add_run("PORTARIA Nº 45/2025/GAB-SEJUS/MT")
    document.add_paragraph("O Secretário de Estado de Justiça, no uso das atribuições,")
    document.add_paragraph("RESOLVE:")
    document.add_paragraph("Art. 1º Instituir o Comitê de Proteção de Dados.")
    document.add_paragraph("Art. 2º Esta Portaria entra em vigor na data da publicação.")
    document.add_paragraph("CONTEÚDO DE OUTRO ATO QUE DEVE SUMIR")
    document.add_paragraph(
        "GOVERNO DO ESTADO DE MATO GROSSO Secretaria de Estado de Planejamento "
        "e Gestão - SEPLAG - Imprensa Oficial - IOMAT"
    )
    caminho = tmp_path / "Modelo_Diario.docx"
    document.save(str(caminho))

    texto = generation.extract_file_text(caminho)
    perfil = modelos.crear_perfil_de_arquivo(caminho, texto, "Modelo_Diario")

    output_path = minuta.montar_docx(perfil, ESTRUTURA, tmp_path)

    texts = [p.text for p in Document(str(output_path)).paragraphs]
    assert texts[0] == "18 de junho de 2025"
    assert "DiárioOficial" in texts
    assert texts[-1].startswith("GOVERNO DO ESTADO DE MATO GROSSO")
    assert "PORTARIA Nº 001/2026/GAB-SEJUS/MT" in texts
    assert "Art. 1º Instituir o Programa de Limpeza nas unidades da SEJUS." in texts
    assert "VITOR HUGO BRUZULATO TEIXEIRA" in texts
    assert not any("CONTEÚDO DE OUTRO ATO" in t for t in texts)
    assert not any("Comitê de Proteção de Dados" in t for t in texts)


def test_set_modelo_usuario_rejeita_arquivo_nao_docx(tmp_path):
    generation._modelo_usuario = None
    arquivo = tmp_path / "nota.txt"
    arquivo.write_text("apenas texto", encoding="utf-8")

    with pytest.raises(generation.UserFileError):
        generation.set_modelo_usuario(arquivo.name, tmp_path)
    generation._modelo_usuario = None


def test_modelo_usuario_priorizado_inclusive_tipo_divergente(fake_retrieval, fake_minuta, tmp_path):
    generation._modelo_usuario = None
    _salvar_docx_portaria(tmp_path)
    generation.set_modelo_usuario("Modelo_Usuario_Portaria.docx", tmp_path)

    resultado = json.loads(
        generation.gerar_documento_normativo(
            "Gere um decreto sobre limpeza das unidades.",
            values={"numero_ato": "PORTARIA Nº 99/2026/GAB-SEJUS/MT"},
        )
    )

    assert resultado["status"] == "generated"
    assert resultado["modelo"] == "USUARIO_Modelo_Usuario_Portaria"
    assert resultado["modelo_usuario"] == "Modelo_Usuario_Portaria.docx"
    assert generation.ultima_minuta()["modelo_usuario"] == "Modelo_Usuario_Portaria.docx"
    generation._modelo_usuario = None


def test_modelo_referencia_repassado_ao_llm(fake_retrieval, monkeypatch, tmp_path):
    generation._modelo_usuario = None
    _salvar_docx_portaria(tmp_path)
    generation.set_modelo_usuario("Modelo_Usuario_Portaria.docx", tmp_path)

    capturado = {}

    def gerar_estrutura_minuta(pedido, tipo, perfil, contexto, valores, modelo_referencia=None):
        capturado.update(
            {"tipo": tipo, "referencia": modelo_referencia, "perfil": perfil}
        )
        assert modelo_referencia and "PORTARIA" in modelo_referencia
        return ESTRUTURA.copy()

    def montar_docx(perfil, estrutura, output_dir):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        caminho = output_dir / "gen_modelo.docx"
        document = Document()
        document.add_paragraph("teste")
        document.save(str(caminho))
        return caminho

    monkeypatch.setattr(generation.minuta, "gerar_estrutura_minuta", gerar_estrutura_minuta)
    monkeypatch.setattr(generation.minuta, "montar_docx", montar_docx)

    json.loads(
        generation.gerar_documento_normativo(
            "Gere uma portaria sobre limpeza.",
            values={"numero_ato": "PORTARIA Nº 88/2026"},
        )
    )

    assert capturado["tipo"] == "portaria"
    assert capturado["perfil"].name == "USUARIO_Modelo_Usuario_Portaria"
    generation._modelo_usuario = None


def test_montagem_com_docx_de_usuario_preserva_formatacao(tmp_path):
    from sejus_project.tools import minuta

    generation._modelo_usuario = None
    model_path = _salvar_docx_portaria(tmp_path)
    texto = generation.extract_file_text(model_path)
    perfil = modelos.crear_perfil_de_arquivo(model_path, texto, "Portaria_Usuario")

    output_path = minuta.montar_docx(perfil, ESTRUTURA, tmp_path)

    assert output_path.is_file()
    document = Document(str(output_path))
    texts = [p.text for p in document.paragraphs]
    assert "PORTARIA Nº 001/2026/GAB-SEJUS/MT" in texts
    assert "RESOLVE:" in texts
    assert "Art. 1º Instituir o Programa de Limpeza nas unidades da SEJUS." in texts
    titulo = next(p for p in document.paragraphs if p.text.startswith("PORTARIA Nº 001/2026"))
    assert titulo.runs[0].bold is True
    assert titulo.runs[0].font.size == Pt(13)
    generation._modelo_usuario = None


def test_gerar_recusa_documento_generico_tabela(fake_retrieval, fake_minuta):
    """Agente nao pode gerar DOCX generico (tabela de resumo): a tool recusa."""
    generation._pending_document = None
    resultado = json.loads(
        generation.gerar_documento_normativo(
            "Crie um DOCX com a tabela resumo das portarias 45 e 46."
        )
    )
    assert resultado["status"] == "nao_normativo"
    assert generation.has_pending_document() is False
    generation._pending_document = None


def test_gerar_recusa_tabela_mesmo_mencionando_portaria(fake_retrieval, fake_minuta):
    """Mesmo citando 'portaria', um pedido de tabela/resumo nao e normativo."""
    generation._pending_document = None
    resultado = json.loads(
        generation.gerar_documento_normativo(
            "Gere um DOCX com a tabela resumo de uma portaria sobre limpeza."
        )
    )
    assert resultado["status"] == "nao_normativo"
    assert generation.has_pending_document() is False
    generation._pending_document = None


def test_guarda_nao_bloqueia_pedido_normativo(fake_retrieval, fake_minuta):
    generation._pending_document = None
    resultado = json.loads(
        generation.gerar_documento_normativo(
            "Gere uma portaria sobre limpeza das unidades administrativas."
        )
    )
    assert resultado["status"] == "awaiting_confirmation"
    assert generation.has_pending_document() is True
    generation._pending_document = None


def test_confirmacao_faca_isso_gera_pendente(fake_retrieval, fake_minuta):
    """'faca isso' confirma uma minuta pendente (e nao uma geracao nova)."""
    generation._pending_document = None
    first = json.loads(
        generation.gerar_documento_normativo("Gere uma portaria sobre limpeza.")
    )
    assert first["status"] == "awaiting_confirmation"

    resultado = json.loads(generation.gerar_documento_normativo("faça isso"))

    assert resultado["status"] == "generated"
    generation._pending_document = None