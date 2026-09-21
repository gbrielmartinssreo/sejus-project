"""Testes de integridade no fluxo de melhoria em modo patch.

No modo patch o LLM devolve apenas as mudanças (alteracoes/remocoes/adicoes)
ancoradas ao texto original, e o sistema monta o resultado copiando o ORIGINAL
e aplicando o patch — impossibilidade de truncamento por reescrita integral.
Estes testes fixam a sanidade do patch: âncoras e campos obrigatórios, o
emparelhamento pela chave de linha normalizada e a preservação do que não foi
citado (inclusive incisos/capítulos), além da normalização da estrutura.
"""
from __future__ import annotations

from pathlib import Path

from sejus_project.tools.document_infra import docx_builder
from sejus_project.tools.llm_tools import document_improvement as minuta
from sejus_project.tools.llm_tools.minuta_generation import _padronizar

_CAPITULO = "\nCAPÍTULO {n}\nSUB TITULO {n}\n"

_ART5 = (
    "Art. 5º A atividade artesanal poderá ser desenvolvida em oficina ou "
    "espaço produtivo destinado à atividade, ou em cela ou outro espaço de "
    "vivência, mediante autorização individual da Direção da unidade penal, "
    "observada a técnica desenvolvida, o local de execução e a possibilidade "
    "de acompanhamento e controle da atividade, dos materiais e da produção."
)

_ART28 = (
    "Art. 28 A área técnica responsável pela política de trabalho prisional "
    "no âmbito da Secretaria de Estado de Justiça realizará a orientação e o "
    "acompanhamento das atividades disciplinadas nesta Instrução Normativa, "
    "promovendo articulação com órgãos e entidades competentes para a "
    "qualificação, a comercialização e a geração de renda."
)


def _doc_com_capitulos_e_incisos() -> str:
    """Conteúdo espelhando o caso real: 8 capítulos e incisos nos arts.
    5º e 28 — conteúdo que a antiga reescrita integral deixava sumir."""
    partes = [
        "INSTRUÇÃO NORMATIVA Nº XX/XX/2026.",
        "Dispõe sobre a atividade artesanal.",
    ]
    for n in ("I", "II", "III", "IV", "V", "VI", "VII", "VIII"):
        partes.append(f"CAP\u00cdTULO {n}")
        partes.append(f"DAS DISPOSIÇÕES {n}")
    partes.append("Art. 5º A atividade artesanal poderá ser desenvolvida:")
    partes.append("I - em oficina ou espaço produtivo destinado à atividade; ou")
    partes.append(
        "II - em cela ou outro espaço de vivência, mediante autorização "
        "individual da Direção da unidade penal."
    )
    partes.append("§ 1º A autorização observará a técnica desenvolvida.")
    partes.append(_ART5.split(".", 1)[1].strip())
    partes.append(_ART28)
    partes.append("Art. 28 A área técnica realizará a orientação, podendo:")
    partes.append("I - orientar as unidades penais quanto aos procedimentos;")
    partes.append("II - consolidar informações sobre as atividades;")
    partes.append("III - propor medidas destinadas ao aperfeiçoamento;")
    partes.append("IV - promover articulação com órgãos e entidades.")
    partes.append("Parágrafo único. As unidades penais fornecerão as informações.")
    partes.append("Art. 33 Esta Instrução Normativa entra em vigor na data de sua publicação.")
    return "\n".join(partes)


def _patch_com_ancora_ok() -> dict:
    """Patch mínimo válido: altera um inciso e remove outro, sem tocar no resto."""
    return {
        "numero": "INSTRUÇÃO NORMATIVA Nº XX/XX/2026",
        "ementa": "Dispõe sobre a atividade artesanal nas unidades penais.",
        "alteracoes": [
            {
                "tipo": "corrigido",
                "rotulo": "II -",
                "trecho_original": (
                    "II - em cela ou outro espaço de vivência, mediante "
                    "autorização individual da Direção da unidade penal."
                ),
                "novo_texto": (
                    "II - em cela ou outro espaço de vivência, mediante "
                    "autorização individual da Direção da unidade penal, "
                    "observada a técnica desenvolvida."
                ),
                "detalhe": "Ajuste de texto.",
            }
        ],
        "remocoes": [
            {
                "rotulo": "Parágrafo único.",
                "trecho_original": "Parágrafo único. As unidades penais fornecerão as informações.",
                "detalhe": "Redundante com o caput.",
            }
        ],
    }


def _patch_com_ancora_fantasma() -> dict:
    """Âncoras que não existem no original (que a antiga guarda não pegava)."""
    return dict(
        _patch_com_ancora_ok(),
        alteracoes=[
            {
                "tipo": "corrigido",
                "rotulo": "Art. 999º",
                "trecho_original": "Art. 999º Dispositivo inexistente no original.",
                "novo_texto": "Art. 999º Dispositivo corrigido.",
                "detalhe": "Ancora fantasma.",
            }
        ],
    )


# ---------------------------------------------------------------------------
# Sanidade do patch
# ---------------------------------------------------------------------------


def test_problemas_do_patch_aceita_patch_saudavel():
    conteudo = _doc_com_capitulos_e_incisos()
    dado = _patch_com_ancora_ok()
    assert minuta._problemas_do_patch(conteudo, dado["alteracoes"], dado["remocoes"]) == []


def test_problemas_do_patch_detecta_ancora_fantasma():
    conteudo = _doc_com_capitulos_e_incisos()
    dado = _patch_com_ancora_fantasma()
    problemas = minuta._problemas_do_patch(conteudo, dado["alteracoes"], dado["remocoes"])
    assert any("ancora" in p for p in problemas)
    assert any("Art. 999º" in p for p in problemas)


def test_problemas_do_patch_detecta_remocao_sem_ancora():
    conteudo = _doc_com_capitulos_e_incisos()
    remocoes = [{"rotulo": "X", "trecho_original": "Parágrafo que não existe."}]
    problemas = minuta._problemas_do_patch(conteudo, [], remocoes)
    assert any("ancora" in p for p in problemas)


def test_problemas_do_patch_rejeita_tipo_errado_de_alteracao():
    conteudo = _doc_com_capitulos_e_incisos()
    alteracoes = [
        {
            "tipo": "remover",
            "rotulo": "Art. 5º",
            "trecho_original": "Art. 5º A atividade artesanal poderá ser desenvolvida:",
            "novo_texto": "x",
            "detalhe": "tipo invalido",
        }
    ]
    problemas = minuta._problemas_do_patch(conteudo, alteracoes, [])
    assert any("tipo invalido" in p for p in problemas)


# ---------------------------------------------------------------------------
# Estrutura montada a partir do ORIGINAL + patch
# ---------------------------------------------------------------------------


def test_construcao_preserva_conteudo_nao_citado():
    """Tudo o que o patch não cita permanece intacto na estrutura final
    (capítulos e incisos não somem, mesmo com mudanças em outros trechos)."""
    conteudo = _doc_com_capitulos_e_incisos()
    dado = _patch_com_ancora_ok()
    estrutura = minuta._construir_estrutura(
        conteudo, dado["alteracoes"], dado["remocoes"], dado["numero"], dado["ementa"]
    )
    texto = "\n".join(item["texto"] for item in estrutura["corpo"])

    assert "CAPÍTULO V" in texto
    assert "I - em oficina ou espaço produtivo destinado à atividade; ou" in texto
    assert "§ 1º A autorização observará a técnica desenvolvida." in texto
    assert "Art. 33 Esta Instrução Normativa entra em vigor" in texto
    # Alteração aplicada e remoção efetuada.
    assert "observada a técnica desenvolvida." in texto
    assert "As unidades penais fornecerão as informações." not in texto
    # Número/ementa vindos do modelo sobrescrevem.
    assert estrutura["numero"] == "INSTRUÇÃO NORMATIVA Nº XX/XX/2026"
    assert estrutura["ementa"].startswith("Dispõe sobre a atividade artesanal nas")


def test_ancoragem_ignora_diferencas_tipograficas():
    """Travessão por hífen e aspas curvas não quebram a âncora do patch."""
    conteudo = "Art. 1º Regulamento interno:\nI – padronizar os procedimentos;"
    alteracoes = [
        {
            "tipo": "corrigido",
            "rotulo": "I -",
            "trecho_original": "I - padronizar os procedimentos;",
            "novo_texto": "I - padronizar os procedimentos diários;",
            "detalhe": "Ajuste.",
        }
    ]
    estrutura = minuta._construir_estrutura(conteudo, alteracoes, [], "Num", "Em.")
    texto = "\n".join(item["texto"] for item in estrutura["corpo"])
    assert "I - padronizar os procedimentos diários;" in texto


def test_mensagem_retry_cita_problemas_do_patch():
    problemas = ["ancora nao encontrada no original: Art. 999º", "trecho_original ausente: Art. 3º"]
    mensagem = minuta._mensagem_retry_especifica(problemas)
    assert "Art. 999º" in mensagem
    assert "Art. 3º" in mensagem
    assert "trecho_original" in mensagem


# ---------------------------------------------------------------------------
# Normalização (etapa responsável pela transformação da estrutura)
# ---------------------------------------------------------------------------


def test_padronizar_anexa_incisos_soltos_ao_artigo_anterior():
    """Incisos devolvidos como itens soltos de 'corpo' (fora de 'subitens')
    devem ser vinculados ao artigo que os anuncia, não transformados em
    artigos novos nem descartados."""
    estrutura = {
        "corpo": [
            {
                "rotulo": "Art. 5º",
                "texto": "A atividade artesanal poderá ser desenvolvida:",
                "subitens": [],
            },
            {"tipo": "inciso", "rotulo": "I -", "texto": "em oficina; ou"},
            {"tipo": "inciso", "rotulo": "II -", "texto": "em cela."},
        ]
    }
    resultado = _padronizar(estrutura, "portaria")

    assert len(resultado["corpo"]) == 1
    art5 = resultado["corpo"][0]
    assert art5["rotulo"] == "Art. 5º"
    incisos = [s for s in art5["subitens"] if s.get("tipo") == "inciso"]
    assert [s["rotulo"] for s in incisos] == ["I -", "II -"]


def test_padronizar_remove_vigencia_duplicada_no_fechamento():
    """Se o art. de vigência já está na articulação e o LLM repetiu no
    'fechamento', a cópia duplicada deve ser removida."""
    estrutura = {
        "corpo": [
            {
                "rotulo": "Art. 33",
                "texto": "Esta Instrução Normativa entra em vigor na data de sua publicação.",
            }
        ],
        "fechamento": [
            {
                "rotulo": "",
                "texto": "Esta Instrução Normativa entra em vigor na data de sua publicação.",
            },
            {"rotulo": "", "texto": "Revogam-se as disposições em contrário."},
        ],
    }
    resultado = _padronizar(estrutura, "portaria")

    textos = [f["texto"] for f in resultado["fechamento"]]
    assert "Esta Instrução Normativa entra em vigor" not in " ".join(textos)
    assert any("Revogam-se" in t for t in textos)


def test_padronizar_remove_assinatura_duplicada():
    """Cargo repetido na lista de assinaturas deve ser deduplicado."""
    estrutura = {
        "corpo": [{"rotulo": "Art. 1º", "texto": "Texto."}],
        "assinaturas": [
            {"nome": "VITOR HUGO", "cargo": "Secretário de Estado de Justiça"},
            {"nome": "", "cargo": "Secretário Adjunto de Administração Penitenciária"},
            {"nome": "", "cargo": "Secretário Adjunto de Administração Penitenciária"},
        ],
    }
    resultado = _padronizar(estrutura, "portaria")

    cargos = [a["cargo"] for a in resultado["assinaturas"] if a["cargo"]]
    assert cargos.count("Secretário Adjunto de Administração Penitenciária") == 1


# ---------------------------------------------------------------------------
# Evidência no DOCX gerado (montar_docx do template usa a estrutura montada)
# ---------------------------------------------------------------------------


def test_montar_docx_preserva_capitulos_e_incisos_no_documento(tmp_path):
    """Integridade de ponta a ponta: a estrutura montada (original + patch),
    quando renderizada pelo template, mantém capítulos e incisos vinculados
    aos artigos no arquivo gerado."""
    from docx import Document

    body_qn = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"

    model_path = tmp_path / "Modelo.docx"
    document = Document()
    document.add_paragraph("CAPÍTULO I")
    document.add_paragraph("Art. 5º A atividade artesanal poderá ser desenvolvida:")
    document.add_paragraph("I - em oficina ou espaço produtivo destinado à atividade; ou")
    document.add_paragraph("II - em cela ou outro espaço de vivência.")
    document.add_paragraph("CAPÍTULO II")
    document.add_paragraph("Art. 28 A área técnica realizará a orientação, podendo:")
    document.add_paragraph("I - orientar as unidades penais;")
    document.add_paragraph("II - consolidar informações;")
    model_path = str(model_path)
    document.save(model_path)

    from sejus_project.tools.document_infra.docx_engine import paragraph_text
    from sejus_project.tools.document_infra.modelos import PORTARIA, PerfilModelo

    perfil = PerfilModelo(
        name="IN_TESTE",
        file=model_path,
        act_types=PORTARIA.act_types,
        patterns=PORTARIA.patterns,
        preservar_moldura=True,
    )
    # Estrutura montada por _construir_estrutura (linha a linha do original).
    conteudo = (
        "INSTRUÇÃO NORMATIVA Nº XX/XX/2026\n"
        "Dispõe sobre a atividade artesanal.\n"
        "CAPÍTULO I\n"
        "Art. 5º A atividade artesanal poderá ser desenvolvida:\n"
        "I - em oficina ou espaço produtivo destinado à atividade; ou\n"
        "II - em cela ou outro espaço de vivência.\n"
        "CAPÍTULO II\n"
        "Art. 28 A área técnica realizará a orientação, podendo:\n"
        "I - orientar as unidades penais;\n"
        "II - consolidar informações;\n"
    )
    estrutura = minuta._construir_estrutura(conteudo, [], [], "INSTRUÇÃO NORMATIVA Nº XX/XX/2026", "Dispõe sobre a atividade artesanal.")

    output_path = docx_builder.montar_docx(perfil, estrutura, tmp_path)

    doc = Document(str(output_path))
    textos = [
        paragraph_text(p).strip()
        for p in doc.element.body.iter(body_qn)
    ]
    conteudo = "\n".join(textos)
    assert "CAPÍTULO I" in conteudo
    assert "CAPÍTULO II" in conteudo
    art5 = next((t for t in textos if t.startswith("Art. 5º")), "")
    assert art5
    idx5 = textos.index(art5)
    assert "I - em oficina" in conteudo
    assert "II - em cela" in conteudo
    art28 = next((t for t in textos if t.startswith("Art. 28")), "")
    idx28 = textos.index(art28)
    assert idx28 > idx5
    assert "I - orientar" in conteudo
    assert "II - consolidar" in conteudo


# ---------------------------------------------------------------------------
# Regressão com os arquivos reais (quando disponíveis no projeto)
# ---------------------------------------------------------------------------

_DIR_PROJETO = Path(__file__).resolve().parents[1]
_ENTRADA_REAL = _DIR_PROJETO / "importacoes_usuario" / "INSTRUÇÃO NORMATIVA Nº XX ARTESÃO - ERIKA.docx"


def test_arquivo_real_aceita_patch_de_texto_do_proprio_arquivo():
    """Se o arquivo real estiver disponível, a âncora de um trecho do próprio
    texto do arquivo deve ser resolvida (a sanidade não rejeita a realidade).
    O patch vazio (sem mudanças) é sempre sadio."""
    import pytest

    from sejus_project.tools.llm_tools.user_files import extract_file_text

    if not _ENTRADA_REAL.is_file():
        pytest.skip("Arquivo real de entrada não disponível neste ambiente.")

    conteudo = extract_file_text(_ENTRADA_REAL)
    assert minuta._problemas_do_patch(conteudo, [], []) == []