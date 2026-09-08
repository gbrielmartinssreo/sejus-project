import json
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
    def gerar_estrutura_minuta(pedido, tipo, perfil, contexto, valores):
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