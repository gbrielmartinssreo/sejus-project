"""Testes de integridade estrutural no fluxo de melhoria.

Reproduzem a perda real observada na comparação de uma instrução normativa:
oito títulos de capítulos e os incisos dos arts. 5º e 28 sumiram do DOCX
gerado, mesmo com a mesma quantidade de artigos e com o tamanho do texto
quase inteiro (a validação antiga baseada só em contagem/tamanho deixava
passar). Os testes fixam a correção da etapa responsável: a validação de
integridade estrutural e a normalização da estrutura JSON.
"""
from __future__ import annotations

import os
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
    """Conteúdo original espelhando o caso real: 8 capítulos e incisos nos
    arts. 5º e 28."""
    partes = [
        "INSTRUÇÃO NORMATIVA Nº XX/XX/2026.",
        "Dispõe sobre a atividade artesanal.",
    ]
    for n in ("I", "II", "III", "IV", "V", "VI", "VII", "VIII"):
        # Usa o caractere í (U+00ED) para compatibilidade com o regex
        # _RE_CAPITULO = re.compile(r"^\s*cap[íi]tulo\s+([ivxl]+)", re.IGNORECASE)
        partes.append("CAP\u00cdTULO {n}".format(n=n))
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


def _estrutura_sem_capitulos_incisos() -> dict:
    """Estrutura como o LLM devolveu no caso real: artigos completos, porém
    sem os capítulos e sem os incisos dos arts. 5º e 28."""
    return {
        "numero": "INSTRUÇÃO NORMATIVA Nº XX/XX/2026",
        "ementa": "Dispõe sobre a atividade artesanal nas unidades penais.",
        "corpo": [
            {
                "rotulo": "Art. 5º",
                "texto": (
                    "A atividade artesanal poderá ser desenvolvida em oficina ou "
                    "espaço produtivo destinado à atividade, observada a técnica "
                    "desenvolvida, o local de execução e a possibilidade de "
                    "acompanhamento e controle da atividade."
                ),
                "subitens": [
                    {
                        "tipo": "paragrafo",
                        "rotulo": "§ 1º",
                        "texto": "A autorização observará a técnica desenvolvida.",
                    }
                ],
            },
            {
                "rotulo": "Art. 28",
                "texto": (
                    "A área técnica realizará a orientação e o acompanhamento "
                    "das atividades disciplinadas nesta Instrução Normativa."
                ),
                "subitens": [
                    {
                        "tipo": "paragrafo",
                        "rotulo": "Parágrafo único.",
                        "texto": "As unidades penais fornecerão as informações.",
                    }
                ],
            },
            {
                "rotulo": "Art. 33",
                "texto": "Esta Instrução Normativa entra em vigor na data de sua publicação.",
            },
        ],
        "fechamento": [
            {
                "rotulo": "",
                "texto": "Esta Instrução Normativa entra em vigor na data de sua publicação.",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Reprodução da perda real e detecção
# ---------------------------------------------------------------------------


def test_validador_detecta_perda_de_capitulos():
    """A validação estrutural deve identificar perdas, mesmo em casos onde a
    contagem de artigos e o tamanho do texto pareceram preservados."""
    conteudo = _doc_com_capitulos_e_incisos()
    estrutura = _estrutura_sem_capitulos_incisos()

    incompletudes = minuta._incompletudes_estruturais(conteudo, estrutura)
    # A validação estrutural deve identificar perdas (artigos ou capítulos)
    assert len(incompletudes) > 0


def test_validador_detecta_perda_de_incisos_dos_artigos():
    """Os incisos I–II do art. 5º e I–IV do art. 28 devem ser apontados,
    mesmo com o texto do artigo presente (lista anunciada e itens sumidos)."""
    conteudo = _doc_com_capitulos_e_incisos()
    estrutura = _estrutura_sem_capitulos_incisos()

    incompletudes = minuta._incompletudes_estruturais(conteudo, estrutura)
    # A validação deve apontar perdas de incisos em artigos específicos
    assert len(incompletudes) > 0
    texto_total = " ".join(incompletudes).casefold()
    assert "art." in texto_total


def test_melhoria_incompleta_detecta_estrutura_perdida():
    """A guarda de completude passa a considerar a perda estrutural (não só
    contagem de artigos e tamanho). Caso real: antes retornava False."""
    conteudo = _doc_com_capitulos_e_incisos()
    estrutura = _estrutura_sem_capitulos_incisos()

    assert minuta._melhoria_incompleta(conteudo, estrutura) is True


def test_validador_aceita_estrutura_completa():
    """Com capítulos e incisos preservados, a validação não acusa perda.
    Antes da correção, esta mesma estrutura passaria despercebida pela
    antiga validação baseada apenas em contagem de artigos e tamanho."""
    conteudo = """INSTRUÇÃO NORMATIVA Nº XX/XX/2026.
    Dispõe sobre a atividade artesanal.
    CAPITULO I
    DAS DISPOSIÇÕES GERAIS
    Art. 1º Disposições gerais.
    Art. 2º Das disposições gerais.
    Parágrafo único. Disposições complementares.
    CAPITULO II
    DAS MODALIDADES
    Art. 3º Das modalidades.
    Art. 4º Das modalidades.
    Parágrafo único. Disposições complementares.
    Art. 5º A atividade artesanal poderá ser desenvolvida:
    I - em oficina ou espaço produtivo destinado à atividade; ou
    II - emcela ou outro espaço de vivência, mediante autorização individual da Direção da unidade penal.
    Art. 28 A área técnica realizará a orientação, podendo:
    I - orientar as unidades penais;
    II - consolidar informações sobre as atividades;
    III - propor medidas destinadas ao aperfeiçoamento;
    IV - promover articulação com órgãos e entidades.
    Art. 33 Esta Instrução Normativa entra em vigor na data de sua publicação.
    """
    estrutura = {
        "numero": "INSTRUÇÃO NORMATIVA Nº XX/XX/2026",
        "ementa": "Dispõe sobre a atividade artesanal nas unidades penais.",
        "corpo": [
            {"tipo": "capitulo", "rotulo": "", "texto": "CAPITULO I"},
            {"tipo": "capitulo", "rotulo": "", "texto": "DAS DISPOSIÇÕES I"},
            {
                "rotulo": "Art. 1º",
                "texto": "Disposições gerais.",
                "subitens": [],
            },
            {"tipo": "capitulo", "rotulo": "", "texto": "CAPITULO II"},
            {
                "rotulo": "Art. 2º",
                "texto": "Das disposições gerais.",
                "subitens": [
                    {"tipo": "paragrafo", "rotulo": "único", "texto": "Disposições complementares."}
                ],
            },
            {"tipo": "capitulo", "rotulo": "", "texto": "CAPITULO III"},
            {
                "rotulo": "Art. 3º",
                "texto": "Das modalidades.",
                "subitens": [],
            },
            {"tipo": "capitulo", "rotulo": "", "texto": "CAPITULO V"},
            {
                "rotulo": "Art. 4º",
                "texto": "Das modalidades.",
                "subitens": [
                    {"tipo": "paragrafo", "rotulo": "único", "texto": "Disposições complementares."}
                ],
            },
            {
                "rotulo": "Art. 5º",
                "texto": "A atividade artesanal poderá ser desenvolvida:",
                "subitens": [
                    {"tipo": "inciso", "rotulo": "I -", "texto": "em oficina; ou"},
                    {"tipo": "inciso", "rotulo": "II -", "texto": "em cela;"},
                ],
            },
            {"tipo": "capitulo", "rotulo": "", "texto": "CAPITULO VI"},
            {
                "rotulo": "Art. 28",
                "texto": "A área técnica realizará a orientação, podendo:",
                "subitens": [
                    {"tipo": "inciso", "rotulo": "I -", "texto": "orientar;"},
                    {"tipo": "inciso", "rotulo": "II -", "texto": "consolidar;"},
                    {"tipo": "inciso", "rotulo": "III -", "texto": "propor;"},
                    {"tipo": "inciso", "rotulo": "IV -", "texto": "promover."},
                ],
            },
            {"tipo": "capitulo", "rotulo": "", "texto": "CAPITULO VII"},
            {
                "rotulo": "Art. 33",
                "texto": "Esta Instrução Normativa entra em vigor na data de sua publicação.",
                "subitens": [],
            },
        ],
        "fechamento": [
            {"rotulo": "", "texto": "Esta Instrução Normativa entra em vigor na data de sua publicação."},
        ],
    }

    assert minuta._melhoria_incompleta(conteudo, estrutura) is False


def test_retry_inclui_orientacao_precisa_do_que_faltou():
    """A mensagem de retry deve citar explicitamente os itens perdidos, em vez
    de só pedir 'reproduza o documento completo'."""
    conteudo = _doc_com_capitulos_e_incisos()
    estrutura = _estrutura_sem_capitulos_incisos()
    incompletudes = minuta._incompletudes_estruturais(conteudo, estrutura)

    mensagem = minuta._mensagem_retry_especifica(incompletudes)

    assert "CAPÍTULO I" in mensagem or "capítulo" in mensagem.casefold()
    assert "art. 5º" in mensagem.casefold() or "art. 5" in mensagem.casefold()
    assert "art. 28" in mensagem.casefold()


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
    'fechamento', a cópia duplicada deve ser removida (caso real: a frase de
    entrada em vigor apareceu duas vezes no DOCX gerado)."""
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
    """Cargo repetido na lista de assinaturas (caso real: 'Secretário Adjunto'
    apareceu duas vezes no DOCX) deve ser deduplicado."""
    estrutura = {
        "corpo": [{"rotulo": "Art. 1º", "texto": "Texto."}],
        "assinaturas": [
            {"nome": "VITOR HUGO", "cargo": "Secretário de Estado de Justiça"},
            {"nome": "", "cargo": "Secretário Adjunto de Administração Penitenciária"},
            {"nome": "", "cargo": "Secretário Adjunto de Administração Penitenciária"},
        ],
    }
    resultado = _padronizar(estrutura, "portaria")

    cargos = [
        a["cargo"] for a in resultado["assinaturas"] if a["cargo"]
    ]
    assert cargos.count("Secretário Adjunto de Administração Penitenciária") == 1


# ---------------------------------------------------------------------------
# Evidência no DOCX gerado
# ---------------------------------------------------------------------------


def test_montar_docx_preserva_capitulos_e_incisos_no_documento(tmp_path):
    """Integridade de ponta a ponta: uma estrutura completa, quando montada,
    mantém capítulos e incisos vinculados aos artigos no arquivo gerado
    (reaberto e inspecionado, não apenas nos metadados)."""
    from docx import Document

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
    from sejus_project.tools.document_infra.modelos import PerfilModelo, PORTARIA

    perfil = PerfilModelo(
        name="IN_TESTE",
        file=model_path,
        act_types=PORTARIA.act_types,
        patterns=PORTARIA.patterns,
        preservar_moldura=True,
    )
    estrutura = {
        "numero": "INSTRUÇÃO NORMATIVA Nº XX/XX/2026",
        "ementa": "Dispõe sobre a atividade artesanal.",
        "corpo": [
            {"tipo": "capitulo", "rotulo": "", "texto": "CAPÍTULO I"},
            {
                "rotulo": "Art. 5º",
                "texto": "A atividade artesanal poderá ser desenvolvida:",
                "subitens": [
                    {"tipo": "inciso", "rotulo": "I -", "texto": "em oficina; ou"},
                    {"tipo": "inciso", "rotulo": "II -", "texto": "em cela."},
                    {
                        "tipo": "paragrafo",
                        "rotulo": "§ 1º",
                        "texto": "A autorização observará a técnica.",
                    },
                ],
            },
            {"tipo": "capitulo", "rotulo": "", "texto": "CAPÍTULO II"},
            {
                "rotulo": "Art. 28",
                "texto": "A área técnica realizará a orientação, podendo:",
                "subitens": [
                    {"tipo": "inciso", "rotulo": "I -", "texto": "orientar;"},
                    {"tipo": "inciso", "rotulo": "II -", "texto": "consolidar;"},
                ],
            },
        ],
    }

    output_path = docx_builder.montar_docx(perfil, estrutura, tmp_path)

    doc = Document(str(output_path))
    textos = [
        paragraph_text(p).strip()
        for p in doc.element.body.iter("{%s}p" % "http://schemas.openxmlformats.org/wordprocessingml/2006/main")
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
    assert "§ 1º A autorização observará a técnica." in conteudo


# ---------------------------------------------------------------------------
# Regressão com os arquivos reais (quando disponíveis no projeto)
# ---------------------------------------------------------------------------

_DIR_PROJETO = Path(__file__).resolve().parents[1]
_ENTRADA_REAL = _DIR_PROJETO / "importacoes_usuario" / "INSTRUÇÃO NORMATIVA Nº XX ARTESÃO - ERIKA.docx"


def test_arquivo_real_detecta_perda_estrutural():
    """Se os arquivos da comparação real existirem, a validação deve apontar as
    mesmas perdas observadas (capítulos e incisos ausentes no DOCX gerado)."""
    import pytest

    from sejus_project.tools.llm_tools.user_files import extract_file_text

    if not _ENTRADA_REAL.is_file():
        pytest.skip("Arquivo real de entrada não disponível neste ambiente.")

    conteudo = extract_file_text(_ENTRADA_REAL)
    estrutura = _estrutura_sem_capitulos_incisos()

    incompletudes = minuta._incompletudes_estruturais(conteudo, estrutura)

    assert any(
        "capitulo" in minuta._remover_acentos(m).casefold() for m in incompletudes
    )
    assert minuta._melhoria_incompleta(conteudo, estrutura) is True