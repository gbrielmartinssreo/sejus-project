"""Testes do fluxo de melhoria (modo patch): orçamento de tokens com teto,
guarda de sanidade do patch e ausência de corte da entrada original."""
from types import SimpleNamespace

from sejus_project.tools.document_infra import docx_builder
from sejus_project.tools.llm_tools import document_improvement as minuta
from sejus_project.tools.llm_tools.minuta_generation import (
    _TETO_TOKENS_MODELO,
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


def _patch_saudavel():
    """Patch válido: alterações/remoções ancoradas ao texto original."""
    return {
        "numero": "PORTARIA Nº 1/2026",
        "ementa": "Dispõe sobre teste.",
        "alteracoes": [
            {
                "tipo": "corrigido",
                "rotulo": "Art. 3º",
                "trecho_original": (
                    "Art. 3º Texto do artigo 3 com conteúdo suficiente e detalhado."
                ),
                "novo_texto": "Art. 3º Texto do artigo 3 melhorado e detalhado.",
                "detalhe": "Ajuste de texto.",
            }
        ],
        "remocoes": [],
        "adicoes_estruturais": [
            {
                "o_que": "Art. 6º-A",
                "texto": "Recurso em caso de negativa, com efeito suspensivo.",
                "posicao": "após o art. 6º",
                "detalhe": "Recurso em caso de negativa.",
                "lastro": "IN 07/2026, art. 13.",
            }
        ],
        "lacunas_identificadas": [
            {"tema": "seguranca_epi", "detalhe": "Sem exigência de EPI."}
        ],
    }


def _patch_com_problema():
    """Patch com âncora inexistente no original (a sanidade falha)."""
    return {
        "numero": "PORTARIA Nº 1/2026",
        "ementa": "Dispõe sobre teste.",
        "alteracoes": [
            {
                "tipo": "corrigido",
                "rotulo": "Art. 999º",
                "trecho_original": "Art. 999º Texto que não existe no documento.",
                "novo_texto": "Art. 999º Texto corrigido.",
                "detalhe": "Ajuste de texto.",
            }
        ],
        "remocoes": [],
        "lacunas_identificadas": [],
    }


def _texto_da_estrutura(estrutura):
    return "\n".join(item["texto"] for item in estrutura.get("corpo") or [])


def test_melhoria_usa_orcamento_maior_e_preservar_completo(monkeypatch):
    fake = _FakeExtrai([_patch_saudavel()])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    estrutura, alteracoes, remocoes, adicoes, lacunas = minuta.gerar_estrutura_melhoria(
        _doc_completude(), "portaria", _perfil(), [], None
    )

    assert fake.chamadas[0]["max_tokens"] == minuta._MELHORIA_MAX_TOKENS_DEFAULT
    assert fake.chamadas[0]["preservar_completo"] is True
    # Modo patch: a estrutura sai do ORIGINAL + patch (nada é reescrito).
    assert "Art. 3º Texto do artigo 3 melhorado e detalhado." in _texto_da_estrutura(estrutura)
    assert len(alteracoes) == 1
    assert alteracoes[0]["trecho_original"]
    assert remocoes == []
    assert adicoes[0]["o_que"] == "Art. 6º-A"
    assert lacunas[0]["tema"] == "seguranca_epi"


def test_melhoria_respeita_melhoria_max_tokens_abaixo_do_teto(monkeypatch):
    monkeypatch.setenv("MELHORIA_MAX_TOKENS", "12000")
    fake = _FakeExtrai([_patch_saudavel()])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    minuta.gerar_estrutura_melhoria(_doc_completude(), "portaria", _perfil(), [], None)

    assert fake.chamadas[0]["max_tokens"] == 12000


def test_melhoria_clampa_max_tokens_no_teto_do_modelo(monkeypatch):
    """Passar MELHORIA_MAX_TOKENS acima do teto não gera erro 400 na API."""
    monkeypatch.setenv("MELHORIA_MAX_TOKENS", "30000")
    fake = _FakeExtrai([_patch_saudavel()])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    minuta.gerar_estrutura_melhoria(_doc_completude(), "portaria", _perfil(), [], None)

    assert fake.chamadas[0]["max_tokens"] == _TETO_TOKENS_MODELO


def test_melhoria_patch_aceito_sem_retry(monkeypatch):
    fake = _FakeExtrai([_patch_saudavel()])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    estrutura, _, _, _, _ = minuta.gerar_estrutura_melhoria(
        _doc_completude(), "portaria", _perfil(), [], None
    )

    assert len(fake.chamadas) == 1
    assert "Art. 3º Texto do artigo 3 melhorado e detalhado." in _texto_da_estrutura(estrutura)


def test_melhoria_patch_problematico_retenta_com_mensagem_de_retry(monkeypatch):
    fake = _FakeExtrai([_patch_com_problema(), _patch_saudavel()])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    estrutura, _, _, _, _ = minuta.gerar_estrutura_melhoria(
        _doc_completude(), "portaria", _perfil(), [], None
    )

    assert len(fake.chamadas) == 2
    assert fake.chamadas[1]["n_mensagens"] > fake.chamadas[0]["n_mensagens"]
    # No modo patch o orçamento NÃO dobra entre tentativas (já é o teto/clamp).
    assert fake.chamadas[1]["max_tokens"] == fake.chamadas[0]["max_tokens"]
    # A mensagem de retry aponta exatamente o problema de âncora do patch.
    assert "ancora" in fake.chamadas[1]["ultima_mensagem"].lower()
    assert "Art. 3º Texto do artigo 3 melhorado e detalhado." in _texto_da_estrutura(estrutura)


def test_melhoria_patch_problematico_apos_retry_entrega_melhor_esforco(monkeypatch):
    fake = _FakeExtrai([_patch_com_problema(), _patch_com_problema()])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    estrutura, alteracoes, _, _, _ = minuta.gerar_estrutura_melhoria(
        _doc_completude(), "portaria", _perfil(), [], None
    )

    assert len(fake.chamadas) == 2
    # Melhor esforço: o patch mesmo problemático é devolvido (arquivo sempre sai).
    assert any("Art. 999º" in item["novo_texto"] for item in alteracoes)
    # A estrutura continua completa (origem + patch); conteúdo não citado intacto.
    assert "Art. 5º Texto do artigo 5 com conteúdo suficiente e detalhado." in _texto_da_estrutura(estrutura)


def test_problemas_do_patch_aceita_patch_saudavel():
    assert minuta._problemas_do_patch(_doc_completude(), _patch_saudavel()["alteracoes"], []) == []


def test_problemas_do_patch_detecta_ancora_invalida():
    problemas = minuta._problemas_do_patch(_doc_completude(), _patch_com_problema()["alteracoes"], [])
    assert problemas
    assert any("ancora" in p for p in problemas)


def test_problemas_do_patch_detecta_campos_ausentes():
    alteracoes = [{"tipo": "alterado", "rotulo": "Art. 1º"}]
    problemas = minuta._problemas_do_patch(_doc_completude(), alteracoes, [])
    assert any("trecho_original" in p for p in problemas)
    assert any("novo_texto" in p for p in problemas)


def _contexto_com_ato():
    return [
        {
            "source_file": "IN_07-2026.docx",
            "act_type": "IN",
            "act_number": "07/2026",
            "text": "Art. 13 A autorização terá validade de 02 (dois) anos.",
        },
        {
            "source_file": "PORTARIA_12-2026.docx",
            "act_type": "PORTARIA",
            "act_number": "12/2026",
            "text": "Outro ato do acervo.",
        },
    ]


def test_documentos_do_contexto_dedup_por_fonte():
    contexto = [
        {
            "source_file": "IN_07-2026.docx",
            "act_type": "IN",
            "act_number": "07/2026",
            "text": "...",
        },
        {
            "source_file": "IN_07-2026.docx",
            "act_type": "IN",
            "act_number": "07/2026",
            "text": "trecho repetido da mesma fonte",
        },
        {
            "source_file": "",
            "act_type": "PORTARIA",
            "act_number": "12/2026",
            "text": "...",
        },
    ]
    docs = minuta._documentos_do_contexto(contexto)
    assert len(docs) == 2
    assert docs[0]["source_file"] == "IN_07-2026.docx"
    assert docs[1]["act_number"] == "12/2026"


def test_identificar_lastro_casa_com_ato_do_contexto():
    docs = minuta._documentos_do_contexto(_contexto_com_ato())
    ato = minuta._identificar_lastro("IN 07/2026, art. 13 (validade de 02 anos).", docs)
    assert ato is not None
    assert ato["source_file"] == "IN_07-2026.docx"


def test_identificar_lastro_sem_numero_nao_afirma_documento():
    docs = minuta._documentos_do_contexto(_contexto_com_ato())
    assert minuta._identificar_lastro("conforme modelo do acervo", docs) is None


def test_identificar_lastro_numero_ambiguo_nao_afirma_documento():
    docs = minuta._documentos_do_contexto(
        [
            {"source_file": "A.docx", "act_type": "IN", "act_number": "07/2026", "text": "..."},
            {"source_file": "B.docx", "act_type": "PORTARIA", "act_number": "07/2026", "text": "..."},
        ]
    )
    assert minuta._identificar_lastro("art. 13, validade de 02 anos", docs) is None


def test_validar_lastros_anota_adicoes_sem_bloquear():
    adicoes = [
        {"o_que": "Art. 6º-A", "lastro": "IN 07/2026, art. 13."},
        {"o_que": "Art. 7º-A", "lastro": "PORTARIA fantasma 99/9999."},
        {"o_que": "Art. 8º-A"},  # sem lastro: nada a validar
    ]
    minuta._validar_lastros(adicoes, _contexto_com_ato())
    assert adicoes[0]["lastro_validado"] is True
    assert adicoes[0]["lastro_fonte"] == "IN_07-2026.docx"
    assert "lastro_aviso" not in adicoes[0]
    assert adicoes[1]["lastro_validado"] is False
    assert adicoes[1]["lastro_fonte"] == ""
    assert "lastro_aviso" in adicoes[1]
    assert "lastro_validado" not in adicoes[2]


def test_gerar_estrutura_melhoria_identifica_lastro_no_contexto(monkeypatch):
    fake = _FakeExtrai([_patch_saudavel()])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    _, _, _, adicoes, _ = minuta.gerar_estrutura_melhoria(
        _doc_completude(), "portaria", _perfil(), _contexto_com_ato(), None
    )

    assert adicoes[0]["lastro"] == "IN 07/2026, art. 13."
    assert adicoes[0]["lastro_validado"] is True
    assert adicoes[0]["lastro_fonte"] == "IN_07-2026.docx"


def test_gerar_estrutura_melhoria_sinaliza_lastro_fantasma(monkeypatch):
    fake = _FakeExtrai([_patch_saudavel()])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    _, _, _, adicoes, _ = minuta.gerar_estrutura_melhoria(
        _doc_completude(), "portaria", _perfil(), [], None
    )

    assert adicoes[0]["lastro_validado"] is False
    assert "lastro_aviso" in adicoes[0]


def test_melhoria_nao_corta_entrada_em_20k():
    conteudo = ("CONSIDERANDO o disposto na legislação aplicável " * 900)  # ~40k chars
    assert len(conteudo) > 20_000

    resultado = minuta._usuario_melhoria(conteudo, "portaria", _perfil(), [])

    assert conteudo in resultado
    assert len(resultado) > 20_000


def test_melhoria_definition_usa_patch_sem_corpo():
    props = minuta.MELHORIA_DEFINITION["function"]["parameters"]["properties"]
    assert "corpo" not in props
    assert "corpo" not in minuta.MELHORIA_DEFINITION["function"]["parameters"]["required"]
    assert minuta.MELHORIA_DEFINITION["function"]["parameters"]["required"] == [
        "numero",
        "ementa",
        "alteracoes",
        "remocoes",
    ]
    assert "remocoes" in props


def test_melhoria_definition_tem_remocoes_adicoes_e_lacunas():
    props = minuta.MELHORIA_DEFINITION["function"]["parameters"]["properties"]
    adicoes = props["adicoes_estruturais"]["items"]["properties"]
    assert adicoes["o_que"]["description"]
    assert {"o_que", "posicao", "detalhe"} <= set(
        props["adicoes_estruturais"]["items"]["required"]
    )
    assert props["lacunas_identificadas"]["items"]["required"] == ["tema"]
    assert props["alteracoes"]["items"]["properties"]["tipo"]["description"].startswith(
        "'alterado'"
    )
    assert props["remocoes"]["items"]["required"] == ["rotulo", "trecho_original", "detalhe"]


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


# ---------------------------------------------------------------------------
# Item 2/3: lastro estendido a alteracoes/remocoes + coerencia tematica
# ---------------------------------------------------------------------------


def test_schema_estende_lastro_e_requer_decisao_juridica():
    props = minuta.MELHORIA_DEFINITION["function"]["parameters"]["properties"]
    alteracoes = props["alteracoes"]["items"]["properties"]
    remocoes = props["remocoes"]["items"]["properties"]
    adicoes = props["adicoes_estruturais"]["items"]["properties"]
    assert "lastro" in alteracoes
    assert "requer_decisao_juridica" in alteracoes
    assert "lastro" in remocoes
    assert "requer_decisao_juridica" in adicoes
    assert "[PRAZO A DEFINIR PELA SECRETARIA]" in minuta._sistema_melhoria()


def test_validar_lastros_tambem_anota_alteracoes_e_remocoes():
    alteracoes = [{"rotulo": "Art. 3º", "lastro": "IN 07/2026, art. 13."}]
    remocoes = [{"rotulo": "Art. 9º", "lastro": "Decreto 2.541/2008"}]
    minuta._validar_lastros(alteracoes, _contexto_com_ato())
    minuta._validar_lastros(remocoes, _contexto_com_ato())
    assert alteracoes[0]["lastro_validado"] is True
    assert alteracoes[0]["lastro_fonte"] == "IN_07-2026.docx"
    # Remocao com lastro que nao casa -> aviso (sem bloquear).
    assert remocoes[0]["lastro_validado"] is False
    assert "lastro_aviso" in remocoes[0]


def test_checar_coerencia_lastros_marca_requer_sem_sobreposicao_tematica():
    """Lastro identificado, mas de outro assunto: item marcado para decisão
    jurídica (requer_decisao_juridica + coerencia_aviso)."""
    alteracoes = [
        {
            "rotulo": "Art. 3º",
            "trecho_original": "Art. 3º Cabe recurso em caso de negativa.",
            "novo_texto": "Art. 3º Cabe recurso em caso de negativa administrativa.",
            "lastro": "IN 07/2026, art. 13",
            "requer_decisao_juridica": False,
        }
    ]
    minuta._checar_coerencia_lastros(alteracoes, [], [], _contexto_com_ato())
    assert alteracoes[0]["requer_decisao_juridica"] is True
    assert "coerencia_aviso" in alteracoes[0]


def test_checar_coerencia_lastros_nao_marca_quando_temas_concordam():
    """Lastro do MESMO assunto (ambos falam de autorização/validade) não é
    sinalizado como incoerente."""
    contexto = [
        {
            "source_file": "IN_07-2026.docx",
            "act_type": "IN",
            "act_number": "07/2026",
            "text": "O registro terá validade de 02 anos, renovável por igual período.",
        }
    ]
    adicoes = [
        {
            "o_que": "Art. 4º-A",
            "texto": "Art. 4º-A A autorização terá validade de 02 anos e poderá ser renovada.",
            "lastro": "IN 07/2026, art. 13",
            "requer_decisao_juridica": False,
        }
    ]
    minuta._checar_coerencia_lastros([], [], adicoes, contexto)
    assert adicoes[0].get("requer_decisao_juridica") is False
    assert "coerencia_aviso" not in adicoes[0]


def test_gerar_estrutura_melhoria_define_padrao_de_requer_decisao(monkeypatch):
    patch = dict(_patch_saudavel())
    fake = _FakeExtrai([patch])
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    _, alteracoes, _remocoes, adicoes, _ = minuta.gerar_estrutura_melhoria(
        _doc_completude(), "portaria", _perfil(), [], None
    )

    # Schema obriga 'requer_decisao_juridica'; itens antigos ficam False.
    assert alteracoes[0]["requer_decisao_juridica"] is False
    assert adicoes[0]["requer_decisao_juridica"] is False
    # Sem contexto nenhum, o lastro da adicao vira aviso (phantom).
    assert adicoes[0]["lastro_validado"] is False


# ---------------------------------------------------------------------------
# Opcao A: documentos de 10+ paginas por janelas (sem corte em 60k)
# ---------------------------------------------------------------------------


def _doc_grande(n_artigos: int = 6000) -> str:
    linhas = [
        f"Art. {i}º Conteúdo do artigo {i} com detalhamento suficiente para o teste."
        for i in range(1, n_artigos + 1)
    ]
    return "PORTARIA Nº 1/2026\n" + "\n".join(linhas)


def test_janelas_conteudo_cobre_documento_inteiro():
    conteudo = _doc_grande()
    assert len(conteudo) > minuta._MELHORIA_JANELA_CHARS_DEFAULT

    janelas = minuta._janelas_conteudo(conteudo)

    assert len(janelas) >= 2
    # Nenhuma janela ultrapassa (muito) o limite configurado.
    assert max(len(j) for j in janelas) <= minuta._MELHORIA_JANELA_CHARS_DEFAULT + 500
    # O começo, o meio e o fim do documento aparecem em alguma janela.
    for trecho in (
        "Art. 1º Conteúdo",
        "Art. 3000º Conteúdo",
        "Art. 6000º Conteúdo",
    ):
        assert any(trecho in janela for janela in janelas)


def test_janelas_conteudo_pequeno_devolve_uma_janela():
    assert minuta._janelas_conteudo("Art. 1º Curto.") == ["Art. 1º Curto."]


def test_dedupe_mudancas_remove_repeticao_entre_janelas():
    itens = [
        {"rotulo": "Art. 3º", "trecho_original": "Art. 3º Texto."},
        {"rotulo": "Art. 3º", "trecho_original": "Art. 3º Texto."},
        {"rotulo": "Art. 4º", "trecho_original": "Art. 4º Outro."},
    ]
    unicos = minuta._dedupe_mudancas(itens)
    assert len(unicos) == 2


def test_usuario_melhoria_inclui_documento_maior_que_60k():
    conteudo = "CONSIDERANDO o disposto na legislação aplicável. " * 2000
    assert len(conteudo) > 60_000

    resultado = minuta._usuario_melhoria(conteudo, "portaria", _perfil(), [])

    assert conteudo in resultado


def test_gerar_estrutura_melhoria_processa_documento_grande_em_janelas(monkeypatch):
    """Documento maior que a janela e processado em blocos: o LLM e chamado uma
    vez por janela e as mudancas de todas sao acumuladas (o final do documento
    nao fica de fora, como acontecia no corte de 60k)."""
    conteudo = _doc_grande()
    janelas = minuta._janelas_conteudo(conteudo)
    assert len(janelas) >= 2

    retornos = []
    for janela in janelas:
        primeira = janela.splitlines()[0]
        retornos.append(
            {
                "numero": "PORTARIA Nº 1/2026",
                "ementa": "Dispõe sobre teste.",
                "alteracoes": [
                    {
                        "tipo": "corrigido",
                        "rotulo": primeira.split()[1],
                        "trecho_original": primeira,
                        "novo_texto": primeira + " [melhorado]",
                        "detalhe": "Ajuste.",
                    }
                ],
                "remocoes": [],
            }
        )

    fake = _FakeExtrai(retornos)
    monkeypatch.setattr(minuta, "_extrair_json_com_retry", fake)

    estrutura, alteracoes, remocoes, _, _ = minuta.gerar_estrutura_melhoria(
        conteudo, "portaria", _perfil(), [], None
    )

    assert len(fake.chamadas) == len(janelas)
    assert len(alteracoes) == len(janelas)
    assert remocoes == []
    texto = _texto_da_estrutura(estrutura)
    assert "[melhorado]" in texto
    # A cauda do documento continua presente e foi alcancada pelas janelas.
    assert "Art. 6000º" in texto
