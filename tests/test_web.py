from fastapi.testclient import TestClient

from sejus_project.agent import agent
from sejus_project.tools import document_generation as generation
from sejus_project.web.render_html import minuta_para_html, minuta_para_texto
from sejus_project.web.server import app

ESTRUTURA = {
    "numero": "PORTARIA Nº 001/2026/GAB-SEJUS/MT",
    "ementa": "Dispõe sobre rotina de limpeza.",
    "preambulo": "O SECRETÁRIO DE ESTADO DE JUSTIÇA, no uso das atribuições,",
    "considerandos": ["CONSIDERANDO a necessidade de padronizar a rotina;"],
    "resolutivo": "RESOLVE:",
    "corpo": [
        {
            "rotulo": "Art. 1º",
            "texto": "Instituir o Programa de Limpeza.",
            "subitens": [
                {"tipo": "inciso", "rotulo": "I -", "texto": "padronizar os procedimentos;"},
            ],
        }
    ],
    "fechamento": [{"rotulo": "", "texto": "Esta Portaria entra em vigor na data de sua publicação."}],
    "local_data": "Cuiabá-MT, 8 de setembro de 2026.",
    "assinaturas": [{"nome": "VITOR HUGO BRUZULATO TEIXEIRA", "cargo": "Secretário de Estado de Justiça"}],
}


def test_minuta_para_html_escapa_texto():
    estrutura = {
        "numero": 'PORTARIA <Nº> 1 & 2 "x"',
        "ementa": "Dispõe sobre & <tag>.",
        "corpo": [
            {
                "rotulo": "Art. 1º",
                "texto": "Item com &, < e >.",
                "subitens": [{"tipo": "inciso", "rotulo": "I -", "texto": "A < B"}],
            }
        ],
        "fechamento": [{"rotulo": "", "texto": "Entra em vigor."}],
        "local_data": "Cuiabá-MT.",
        "assinaturas": [{"nome": "FULANO", "cargo": "Secretário"}],
    }

    html = minuta_para_html(estrutura)

    assert "&lt;" in html and "&amp;" in html and "&quot;" in html
    assert "<script>" not in html
    assert 'class="minuta-documento"' in html
    assert 'class="minuta-numero"' in html
    assert "Art. 1º" in html
    assert "I -" in html


def test_minuta_para_texto():
    texto = minuta_para_texto(ESTRUTURA)

    assert "PORTARIA Nº 001/2026/GAB-SEJUS/MT" in texto
    assert "Art. 1º" in texto
    assert "Cuiabá-MT, 8 de setembro de 2026." in texto


def test_index_entrega_pagina():
    client = TestClient(app)
    resp = client.get("/")

    assert resp.status_code == 200
    assert "SEJUS" in resp.text
    assert "/static/app.js" in resp.text


def test_chat_devolve_reply_e_minuta(monkeypatch):
    monkeypatch.setattr(agent, "executar", lambda msg: "Resposta de teste.")
    monkeypatch.setattr(
        generation,
        "ultima_minuta",
        lambda: {"estructura": ESTRUTURA, "modelo": "PORTARIA", "output_path": "outputs/x.docx"},
    )
    monkeypatch.setattr(generation, "has_pending_document", lambda: False)

    client = TestClient(app)
    resp = client.post("/api/chat", json={"message": "gere uma portaria"})

    assert resp.status_code == 200
    dados = resp.json()
    assert dados["reply"] == "Resposta de teste."
    assert "minuta-documento" in dados["minuta_html"]
    assert "Dispõe sobre rotina de limpeza." in dados["minuta_html"]
    assert dados["minuta_texto"]
    assert dados["pendente"] is False


def test_chat_sem_minuta_fica_pendente(monkeypatch):
    monkeypatch.setattr(agent, "executar", lambda msg: "Deseja informar os campos?")
    monkeypatch.setattr(generation, "ultima_minuta", lambda: None)
    monkeypatch.setattr(generation, "has_pending_document", lambda: True)

    client = TestClient(app)
    dados = client.post("/api/chat", json={"message": "gere uma portaria"}).json()

    assert dados["minuta_html"] is None
    assert dados["pendente"] is True
    assert generation.CAMPOS_BASE


def test_upload_salva_arquivo(monkeypatch, tmp_path):
    from sejus_project.web import server

    monkeypatch.setattr(server, "IMPORTACOES_DIR", tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/api/upload",
        files={"arquivo": ("minuta.txt", b"conteudo do arquivo", "text/plain")},
    )

    assert resp.status_code == 200
    assert resp.json()["filename"] == "minuta.txt"
    assert (tmp_path / "minuta.txt").read_text() == "conteudo do arquivo"


def test_upload_rejeita_formato_invalido(monkeypatch, tmp_path):
    from sejus_project.web import server

    monkeypatch.setattr(server, "IMPORTACOES_DIR", tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/api/upload",
        files={"arquivo": ("malicioso.sh", b"echo oi", "text/plain")},
    )

    assert resp.status_code == 400