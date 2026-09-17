"""Testes do fluxo de melhoria: orçamento de tokens, guarda de completude e
ausência de corte da entrada original."""
from types import SimpleNamespace

import pytest

from sejus_project.tools.document_infra import docx_builder
from sejus_project.tools.llm_tools import document_improvement as minuta
from sejus_project.tools.llm_tools.minuta_generation import (
    STRUTURA_DEFINITION,
    _padronizar,
)


def _perfil():
    return SimpleNamespace(name="USUARIO_Teste")


class _FakeExtrai:
    """Sequencia retornos de _extrair_json_com_retry e grava as chamadas."""

    def __init__(self, retornos):
        self.retornos = list(retornos)
        self.chamadas = []

    def __call__(self, mensagens, definition, max_tokens, preservar_completo=False):
        self.chamadas.append(
            {
                "max_tokens": max_tokens,
                "preservar_completo": preservar_completo,
                "n_mensagens": len(mensagens),
                "ultima_mensagem": mensagens[-1].get("content") if mensagens else None,
            }
        )
        return self.retornos.pop(0)


def _doc_completude():
    artigos = "\n".join(
        f"Art. {i}º Texto do artigo {i} com conteúdo suficiente e detalhado."
        for i in range(1, 11)
    )
    return f"PORTARIA Nº 1/2026\n{artigos}\nCuiabá-MT, 16 de setembro de 2026."


def _estrutura_completa():
    return {
        "numero": "PORTARIA Nº 1/2026",
        "ementa": "Dispõe sobre teste.",
        "corpo": [
            {"rotulo": f"Art. {i}º", "texto": f"Texto do artigo {i} melhorado."}
            for i in range(1, 11)
        ],
        "fechamento": [
            {
                "rotulo": "",
                "texto": "Esta Portaria entra em vigor na data de sua publicação.",
            }
        ],
        "alteracoes": [
            {"tipo": "corrigido", "o_que": "Redação", "detalhe": "Ajuste de texto."}
        ],
        "adicoes_estruturais": [
            {
                "o_que": "Art. 6º-A",
                "posicao": "após o art. 6º",
                "detalhe": "Recurso em caso de negativa.",
                "lastro": "IN 07/2026, art. 13.",
            }
        ],
        "lacunas_identificadas": [
            {"tema": "seguranca_epi", "detalhe": "Sem exigência de EPI."}
        ],
    }


def _estrutura_curta():
    return {
        "numero": "PORTARIA Nº 1/2026",
        "ementa": "Dispõe sobre teste.",
        "corpo": [{"rotulo": "Art. 1º", "texto": "Texto único reduzido."}],
    }


def test_melhoria_usa_orcamento_maior_e_preservar_completo(monkeypatch):
    fake = _FakeExtrai([_estrutura_completa()])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    estrutura, alteracoes, adicoes, lacunas = minuta.gerar_estrutura_melhoria(
        _doc_completude(), "portaria", _perfil(), [], None
    )

    assert fake.chamadas[0]["max_tokens"] >= minuta._MELHORIA_MAX_TOKENS_DEFAULT
    assert fake.chamadas[0]["preservar_completo"] is True
    assert len(estrutura["corpo"]) == 10
    assert len(alteracoes) == 1
    assert adicoes[0]["o_que"] == "Art. 6º-A"
    assert lacunas[0]["tema"] == "seguranca_epi"


def test_melhoria_respeita_melhoria_max_tokens(monkeypatch):
    monkeypatch.setenv("MELHORIA_MAX_TOKENS", "20000")
    fake = _FakeExtrai([_estrutura_completa()])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    minuta.gerar_estrutura_melhoria(_doc_completude(), "portaria", _perfil(), [], None)

    assert fake.chamadas[0]["max_tokens"] == 20000


def test_melhoria_incompleta_retenta_com_mensagem_de_preservar(monkeypatch):
    fake = _FakeExtrai([_estrutura_curta(), _estrutura_completa()])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    estrutura, _, _, _ = minuta.gerar_estrutura_melhoria(
        _doc_completude(), "portaria", _perfil(), [], None
    )

    assert len(fake.chamadas) == 2
    assert fake.chamadas[1]["n_mensagens"] > fake.chamadas[0]["n_mensagens"]
    assert fake.chamadas[1]["max_tokens"] == fake.chamadas[0]["max_tokens"] * 2
    # A mensagem de retry agora aponta exatamente o que está faltando
    # em vez de mensagem genérica de preservação
    assert "art." in fake.chamadas[1]["ultima_mensagem"].lower()
    assert len(estrutura["corpo"]) == 10


def test_melhoria_incompleta_apos_retry_erra(monkeypatch):
    fake = _FakeExtrai([_estrutura_curta(), _estrutura_curta()])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    with pytest.raises(ValueError, match="incompleta"):
        minuta.gerar_estrutura_melhoria(_doc_completude(), "portaria", _perfil(), [], None)

    assert len(fake.chamadas) == 2


def test_melhoria_incompleta_aceita_estrutura_completa(monkeypatch):
    fake = _FakeExtrai([_estrutura_completa()])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    estrutura, _, _, _ = minuta.gerar_estrutura_melhoria(
        _doc_completude(), "portaria", _perfil(), [], None
    )

    assert len(fake.chamadas) == 1
    assert len(estrutura["corpo"]) == 10


def test_melhoria_incompleta_devolve_falso_para_completo():
    assert minuta._melhoria_incompleta(_doc_completude(), _estrutura_completa()) is False


def test_melhoria_incompleta_devolve_true_para_encurtado():
    assert minuta._melhoria_incompleta(_doc_completude(), _estrutura_curta()) is True


def test_melhoria_nao_corta_entrada_em_20k():
    conteudo = ("CONSIDERANDO o disposto na legislação aplicável " * 900)  # ~40k chars
    assert len(conteudo) > 20_000

    resultado = minuta._usuario_melhoria(conteudo, "portaria", _perfil(), [])

    assert conteudo in resultado
    assert len(resultado) > 20_000


def test_melhoria_definition_admite_capitulo_no_corpo():
    props = minuta.MELHORIA_DEFINITION["function"]["parameters"]["properties"]
    itens = props["corpo"]["items"]
    assert itens["properties"]["tipo"]["enum"] == ["artigo", "capitulo"]

    props_minuta = STRUTURA_DEFINITION["function"]["parameters"]["properties"]
    assert "tipo" not in props_minuta["corpo"]["items"]["properties"]


def test_melhoria_definition_tem_adicoes_e_lacunas():
    props = minuta.MELHORIA_DEFINITION["function"]["parameters"]["properties"]
    adicoes = props["adicoes_estruturais"]["items"]["properties"]
    assert adicoes["o_que"]["description"]
    assert {"o_que", "posicao", "detalhe"} <= set(
        minuta.MELHORIA_DEFINITION["function"]["parameters"]["properties"][
            "adicoes_estruturais"
        ]["items"]["required"]
    )
    assert props["lacunas_identificadas"]["items"]["required"] == ["tema"]
    assert props["alteracoes"]["items"]["properties"]["tipo"]["description"].startswith(
        "'alterado'"
    )


def test_padronizar_mantem_itens_capitulo_em_ordem():
    estrutura = {
        "corpo": [
            {"tipo": "capitulo", "rotulo": "", "texto": "CAPÍTULO I"},
            {"tipo": "capitulo", "rotulo": "", "texto": "DAS DISPOSIÇÕES GERAIS"},
            {
                "rotulo": "Art. 1º",
                "texto": "Texto do artigo.",
                "subitens": [{"tipo": "inciso", "rotulo": "I -", "texto": "item.;"}],
            },
            {"tipo": "capitulo", "rotulo": "", "texto": "CAPÍTULO II"},
        ],
    }
    resultado = _padronizar(estrutura, "portaria")

    tipos = [item["tipo"] for item in resultado["corpo"]]
    assert tipos == ["capitulo", "capitulo", "artigo", "capitulo"]
    assert "subitens" not in resultado["corpo"][0]
    assert resultado["corpo"][2]["subitens"]


def test_render_inclui_titulos_de_capitulo():
    from sejus_project.web.render_html import minuta_para_html, minuta_para_texto

    estrutura = {
        "numero": "PORTARIA Nº 1/2026",
        "ementa": "Dispõe sobre teste.",
        "corpo": [
            {"tipo": "capitulo", "rotulo": "", "texto": "CAPÍTULO I"},
            {"tipo": "capitulo", "rotulo": "", "texto": "DAS DISPOSIÇÕES GERAIS"},
            {"rotulo": "Art. 1º", "texto": "Texto do artigo."},
        ],
    }

    texto = minuta_para_texto(estrutura)
    assert "CAPÍTULO I" in texto
    assert "DAS DISPOSIÇÕES GERAIS" in texto

    html = minuta_para_html(estrutura)
    assert 'class="minuta-capitulo">CAPÍTULO I' in html
    assert 'class="minuta-capitulo">DAS DISPOSIÇÕES GERAIS' in html


def test_chave_rotulo_normaliza_para_comparacao():
    assert docx_builder._chave_rotulo("Art. 6º-A") == "art. 6º-a"
    assert docx_builder._chave_rotulo(" art. 6°a ") == "art. 6°a"
    assert docx_builder._chave_rotulo("Art. 6º-A.") == "art. 6º-a"