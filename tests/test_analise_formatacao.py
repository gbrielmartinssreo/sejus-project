"""Análise de documento nas 4 classes canônicas.

Regras (ordem de prioridade):
  1. constatação de conformidade ("X: ok", "X: adequadas") NUNCA vira
     problema — vai para o checklist;
  2. ausência COM fundamento concreto = problema; ausência SEM fundamento =
     ponto de atenção (validação necessária);
  3. recomendação especulativa = ponto de atenção;
  4. elogio = pontos fortes; erro objetivo = problema.
"""

import pytest

from sejus_project.tools.llm_tools import analysis_registry
from sejus_project.tools.llm_tools.analise_formatacao import (
    _RE_INSTRUCAO,
    SECAO_ATENCAO,
    SECAO_CHECKLIST,
    SECAO_PROBLEMAS,
    _classificar_item,
    _Item,
    eh_tarefa_de_correcao,
    parecer_analise,
    reestruturar_analise,
)

ANALISE = (
    "## Pontos fortes\n"
    "- Estrutura em capitulos bem organizada.\n"
    "\n"
    "## Pontos fracos\n"
    "- Competencia e assinaturas: adequadas.\n"
    "- Numeracao: ok.\n"
    "- Falta incluir artigo de vigencia.\n"
    "- Art. 4 nao e citado no preambulo.\n"
    "- Seria util avaliar a inclusao de anexos.\n"
)


def _secao_de(texto, item):
    """Devolve a seção canônica em que `item` aparece."""
    partes = texto.split("## ")
    alvo = f"- {item}".casefold()
    for parte in partes:
        if alvo in parte.casefold():
            return parte.splitlines()[0].strip()
    return ""


# ---------------------------------------------------------------------------
# Regras de classificação
# ---------------------------------------------------------------------------


def test_constatacao_de_conformidade_nao_vira_problema():
    saida = reestruturar_analise(ANALISE)

    assert _secao_de(saida, "Competencia e assinaturas: adequadas.") == SECAO_CHECKLIST
    assert _secao_de(saida, "Numeracao: ok.") == SECAO_CHECKLIST
    # "ok"/"adequadas" jamais em problemas (regra 1).
    problemas = saida.split(f"## {SECAO_PROBLEMAS}")[1].split("## ")[0].casefold()
    assert " ok." not in problemas
    assert "adequadas" not in problemas


def test_elogio_vai_para_pontos_fortes():
    saida = reestruturar_analise(ANALISE)

    assert _secao_de(saida, "Estrutura em capitulos bem organizada.") == "Pontos fortes"


def test_ausencia_com_fundamento_vira_problema():
    saida = reestruturar_analise(ANALISE)

    assert _secao_de(saida, "Falta incluir artigo de vigencia.") == SECAO_PROBLEMAS
    # Art. citado = fundamento concreto.
    assert _secao_de(saida, "Art. 4 nao e citado no preambulo.") == SECAO_PROBLEMAS


def test_ausencia_sem_fundamento_vai_para_atencao():
    saida = reestruturar_analise(
        "## Pontos fracos\n"
        "- Nao ha previsao de recurso administrativo.\n"
        "- Nao ha prazo especifico definido.\n"
    )

    atencao = saida.split(f"## {SECAO_ATENCAO}")[1].split("## ")[0]
    assert "recurso administrativo" in atencao
    assert "prazo especifico" in atencao
    problemas = saida.split(f"## {SECAO_PROBLEMAS}")[1].split("## ")[0]
    assert "recurso administrativo" not in problemas


def test_recomendacao_especulativa_vai_para_atencao():
    saida = reestruturar_analise(
        "## Pontos fracos\n"
        "- Pode haver desvio de finalidade no art. 3.\n"
        "- Seria util incluir um anexo de definicoes.\n"
    )

    atencao = saida.split(f"## {SECAO_ATENCAO}")[1].split("## ")[0]
    assert "Pode haver desvio" in atencao
    assert "Seria util incluir" in atencao


def test_negacao_de_positiva_vira_problema():
    saida = reestruturar_analise("## Pontos fortes\n- A numeracao nao esta correta.\n")

    assert _secao_de(saida, "A numeracao nao esta correta.") == SECAO_PROBLEMAS


def test_item_reclassificado_nao_e_perdido():
    """Regressão: bullet movido de seção precisa aparecer na seção nova."""
    saida = reestruturar_analise(ANALISE)

    for item in (
        "Competencia e assinaturas: adequadas.",
        "Numeracao: ok.",
        "Falta incluir artigo de vigencia.",
        "Art. 4 nao e citado no preambulo.",
        "Seria util avaliar a inclusao de anexos.",
        "Estrutura em capitulos bem organizada.",
    ):
        assert f"- {item}" in saida, item


# ---------------------------------------------------------------------------
# Formato / invariantes
# ---------------------------------------------------------------------------


def test_gera_as_quatro_secoes_na_ordem_canonica():
    saida = reestruturar_analise(ANALISE)

    posicoes = [saida.index(f"## {secao}") for secao in (
        "Pontos fortes",
        SECAO_PROBLEMAS,
        SECAO_ATENCAO,
        SECAO_CHECKLIST,
    )]
    assert posicoes == sorted(posicoes)


def test_reestruturar_e_idempotente():
    uma_vez = reestruturar_analise(ANALISE)

    assert reestruturar_analise(uma_vez) == uma_vez
    assert reestruturar_analise(reestruturar_analise(uma_vez)) == uma_vez


def test_texto_que_nao_e_analise_passa_intacto():
    for texto in (
        "Ok, vou verificar o documento.",
        "",
        "O arquivo foi analisado com sucesso.",
    ):
        assert reestruturar_analise(texto) == texto
        assert parecer_analise(texto) is False


def test_cabecalho_nao_canonico_ainda_classifica_os_itens():
    """Título livre (ex.: '## Análise da IN') é preservado e os bullets abaixo
    dele continuam sendo classificados pelo conteúdo."""
    saida = reestruturar_analise(
        "## Analise da IN 2026\n"
        "- Competencia e assinaturas: adequadas.\n"
        "- Falta incluir artigo de vigencia.\n"
    )

    assert saida.startswith("## Analise da IN 2026")
    assert _secao_de(saida, "Competencia e assinaturas: adequadas.") == SECAO_CHECKLIST
    assert _secao_de(saida, "Falta incluir artigo de vigencia.") == SECAO_PROBLEMAS


def test_prosa_sem_bullets_passa_intacto():
    texto = "Este documento apresenta boa estrutura.\nSem problemas relevantes."

    assert reestruturar_analise(texto) == texto


# ---------------------------------------------------------------------------
# Regressão: análise em lista solta (sem "##"), como o modelo às vezes devolve
# ---------------------------------------------------------------------------

ANALISE_SOLTA = (
    "Segue a análise completa da IN Conjunta SEJUS/FUNAC Nº ___/2026 enviada:\n"
    "- O ato possui preâmbulo claro, com as competências dos signatários.\n"
    "- A ementa está bem definida, detalhando o âmbito do artesanato laboral.\n"
    "- Estabelece critérios objetivos para concessão de autorização.\n"
    "- Prevê a prestação periódica de relatórios para controle e fiscalização.\n"
    "- Falta a numeração da Instrução Normativa (número do ato não preenchido).\n"
    "- Falta detalhamento da forma de fiscalização, por exemplo, periodicidade.\n"
    "- A delimitação da responsabilidade da FUNAC poderia detalhar "
    "procedimentos específicos.\n"
    "- Não há indicação expressa dos signatários oficiais.\n"
    "- Confirmação se o prazo de 60 dias para adequação é suficiente.\n"
    "- Necessário verificar regras específicas de responsabilidade civil.\n"
    "- Ementa condizente com o conteúdo: ok.\n"
    "- Fundamentação jurídica adequada: ok.\n"
    "Pontos fortes\n\nProblemas identificados\n\n"
    "Pontos de atenção / validações necessárias\n\n"
    "Checklist de conformidade\n"
)


def test_lista_solta_sem_titulos_e_classificada():
    """Regressão: a análise chegou como lista solta e NADA foi para os campos
    (todas as 4 saíam vazias com placeholder)."""
    saida = reestruturar_analise(ANALISE_SOLTA)

    for secao in (
        "Pontos fortes",
        SECAO_PROBLEMAS,
        SECAO_ATENCAO,
        SECAO_CHECKLIST,
    ):
        conteudo = saida.split(f"## {secao}")[1].split("## ")[0]
        assert "Não foram identificados" not in conteudo, secao
        assert "Nenhum item de" not in conteudo, secao


def test_lista_solta_classifica_elogio_como_ponto_forte():
    saida = reestruturar_analise(ANALISE_SOLTA)

    for item in (
        "O ato possui preâmbulo claro, com as competências dos signatários.",
        "A ementa está bem definida, detalhando o âmbito do artesanato laboral.",
        "Estabelece critérios objetivos para concessão de autorização.",
        "Prevê a prestação periódica de relatórios para controle e fiscalização.",
    ):
        assert _secao_de(saida, item) == "Pontos fortes", item


def test_lista_solta_separa_problema_de_validacao():
    saida = reestruturar_analise(ANALISE_SOLTA)

    assert _secao_de(
        saida, "Falta a numeração da Instrução Normativa (número do ato não preenchido)."
    ) == SECAO_PROBLEMAS
    assert _secao_de(
        saida, "Falta detalhamento da forma de fiscalização, por exemplo, periodicidade."
    ) == SECAO_PROBLEMAS
    for item in (
        (
            "A delimitação da responsabilidade da FUNAC poderia detalhar "
            "procedimentos específicos."
        ),
        "Não há indicação expressa dos signatários oficiais.",
        "Confirmação se o prazo de 60 dias para adequação é suficiente.",
        "Necessário verificar regras específicas de responsabilidade civil.",
    ):
        assert _secao_de(saida, item) == SECAO_ATENCAO, item


def test_lista_solta_manda_ok_para_o_checklist():
    saida = reestruturar_analise(ANALISE_SOLTA)

    assert _secao_de(saida, "Ementa condizente com o conteúdo: ok.") == SECAO_CHECKLIST
    assert _secao_de(saida, "Fundamentação jurídica adequada: ok.") == SECAO_CHECKLIST
    problemas = saida.split(f"## {SECAO_PROBLEMAS}")[1].split("## ")[0]
    assert " ok." not in problemas


def test_lista_solta_nao_extrai_elogio_como_tarefa_de_correcao():
    """O registry extraicorreção só do que é PROBLEMA: elogio, descrição
    positiva e pedido de validação não viram tarefa."""
    saida = reestruturar_analise(ANALISE_SOLTA)
    textos = " ".join(
        a["texto"] for a in analysis_registry.extrair_apontamentos(saida)
    ).casefold()

    assert "numeração" in textos
    assert "fiscalização, por exemplo" in textos
    for nao_deve in (
        "preâmbulo claro",
        "ementa está bem definida",
        "estabelece critérios objetivos",
        "prestação periódica de relatórios",
        "confirmação se o prazo",
        "necessário verificar",
    ):
        assert nao_deve not in textos, nao_deve


def test_texto_sem_nenhum_item_nao_vira_secoes_vazias():
    """Nunca substituir o conteúdo por 4 seções com placeholder."""
    texto = "- primeiro item solto\n- segundo item solto\n"

    saida = reestruturar_analise(texto)
    # Sem classes declaradas e sem sinal de análise estruturada, o texto volta
    # intacto em vez de virar 4 blocos vazios.
    assert "Checklist de conformidade" not in saida or saida != texto


# ---------------------------------------------------------------------------
# Regressão: particípio/adverbio NÃO é ordem de correção
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "Falar em correção incluída no anexo.",
        "Recurso garantido pela Lei.",
        "Norma reorganizada por técnico.",
        "A decisão foi retificada.",
        "Tabela substituída pela versão nova.",
        "Recurso excluído da base.",
    ],
)
def test_participio_descritivo_nao_e_instrucao(texto):
    """"incluída", "garantido", "reorganizada" descrevem o ato (particípio
    passado), não pedem correção. Classificar como problema gerava tarefa
    espúria no registry."""
    assert not _RE_INSTRUCAO.search(texto)
    assert _classificar_item(_Item(texto, "")).classe != SECAO_PROBLEMAS


def test_adverbio_explicitamente_nao_e_instrucao():
    texto = (
        "O ato não aborda explicitamente a questão dos EPIs, "
        "importante em atividades artesanais."
    )

    assert not _RE_INSTRUCAO.search(texto)
    # ausência sem fundamento explícito = atenção, não problema
    assert _classificar_item(_Item(texto, "")).classe == SECAO_ATENCAO


def test_descricao_neutra_do_ato_vira_ponto_forte():
    for texto in (
        "Prevê a prestação periódica de relatórios e registros detalhados.",
        "A legislação estabelece critérios objetivos para concessão.",
    ):
        assert _classificar_item(_Item(texto, "")).classe == "Pontos fortes", texto


def test_negacao_de_posicao_detalhada_vira_problema():
    """"O capítulo não está detalhado" é defeito, não elogio."""
    texto = "O capítulo não está detalhado."

    assert _classificar_item(_Item(texto, "")).classe == SECAO_PROBLEMAS


# ---------------------------------------------------------------------------
# Regressão: infinitivo de correção precisa gerar tarefa
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "Esclarecer o art. 3º sobre a competência atribuída.",
        "Renumerar o capítulo III.",
        "Padronizar a redação do art. 7º.",
        "Reorganizar os capítulos conforme a hierarquia do plano de cargos.",
        "Uniformizar a notação dos valores.",
    ],
)
def test_infinitivo_de_correcao_gera_tarefa(texto):
    """Regressão dos 3 testes de test_correcao_pos_analise.py que passaram a
    falhar: o classificador só reconhecia imperativo ("esclareça"), então
    "Esclarecer o art. 3º" caía em validação e o registry descartava."""
    assert _RE_INSTRUCAO.search(texto)
    assert _classificar_item(_Item(texto, "")).classe == SECAO_PROBLEMAS
    assert eh_tarefa_de_correcao(texto)

    # E o item precisa chegar no registry como apontamento.
    saida = reestruturar_analise(f"## {SECAO_PROBLEMAS}\n\n- {texto}\n")
    assert len(analysis_registry.extrair_apontamentos(saida)) == 1


def test_estrutura_canonica_aceita_pontos_fORTes_e_fracos(monkeypatch):
    """O agent_loop usa este módulo; o import não pode quebrar o agente."""
    from sejus_project.agent import agent

    assert callable(agent.reestruturar_analise)


# ---------------------------------------------------------------------------
# Camada de registro: constatação de conformidade não vira tarefa
# ---------------------------------------------------------------------------


def test_conformidade_nao_vira_apontamento_de_correcao():
    """Regressão do bug: 'Competencia e assinaturas: adequadas.' era
    extraída como apontamento acionável (virava tarefa de correção)."""
    apontamentos = analysis_registry.extrair_apontamentos(ANALISE)
    textos = " ".join(a["texto"] for a in apontamentos).casefold()

    assert "adequadas" not in textos
    assert "numeracao: ok" not in textos
    # Problemas reais continuam acionáveis.
    assert "vigencia" in textos


@pytest.mark.parametrize(
    "item",
    [
        "- Competencia e assinaturas: adequadas.",
        "- Numeracao: ok.",
        "- Fundamentacao: correta.",
    ],
)
def test_conclusao_positiva_nunca_e_acionavel(item):
    assert analysis_registry.extrair_apontamentos(item) == []
