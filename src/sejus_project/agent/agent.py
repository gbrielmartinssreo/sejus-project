import inspect
import json
import re

from sejus_project.agent.skills.loader import (
    build_system_message,
)
from sejus_project.llm.ia import perguntar
from sejus_project.tools.llm_tools import analysis_registry
from sejus_project.tools.llm_tools.document_generation import (
    aceitar_proposta,
    analise_para_correcao,
    aplicar_alteracoes_selecionadas,
    arquivo_para_correcao_sem_analise,
    cancelar_pendencia,
    comparacao_definition,
    documento_para_analise,
    gerar_documento_normativo,
    has_pending_document,
    limpar_estado,
    listar_propostas,
    melhorar_documento_usuario,
    obter_textos_comparacao,
    pedido_de_correcao,
    registrar_analise,
    rejeitar_proposta,
)
from sejus_project.tools.llm_tools.document_generation import (
    definition as document_generation_definition,
)
from sejus_project.tools.llm_tools.document_improvement import (
    MELHORIA_DEFINITION as melhoria_definition,
)
from sejus_project.tools.llm_tools.more import definition as more_definition
from sejus_project.tools.llm_tools.more import more_epic
from sejus_project.tools.llm_tools.retrieval import consultar_atos_sejus
from sejus_project.tools.llm_tools.retrieval import definition as retrieval_definition
from sejus_project.tools.llm_tools.user_files import (
    _list_available_files,
    analisar_arquivo_usuario,
    upload_sessao,
)
from sejus_project.tools.llm_tools.user_files import definition as user_files_definition

# ============================================================================
# TOOLS
# ============================================================================

TOOLS = [
    more_definition,
    retrieval_definition,
    user_files_definition,
    document_generation_definition,
    melhoria_definition,
    comparacao_definition,
]


FUNCTIONS = {
    "more_epic": more_epic,
    "consultar_atos_sejus": consultar_atos_sejus,
    "analisar_arquivo_usuario": analisar_arquivo_usuario,
    "gerar_documento_normativo": gerar_documento_normativo,
    "melhorar_documento_usuario": melhorar_documento_usuario,
    "obter_textos_comparacao": obter_textos_comparacao,
    "aceitar_proposta": aceitar_proposta,
    "rejeitar_proposta": rejeitar_proposta,
    "listar_propostas": listar_propostas,
    "aplicar_alteracoes_selecionadas": aplicar_alteracoes_selecionadas,
}


# ============================================================================
# CONFIGURAÇÃO DO AGENTE
# ============================================================================

messages = []


SYSTEM_INSTRUCTIONS = (
    "Voce e o agente da SEJUS. Responda em portugues. "
    "Quando o usuario pedir um arquivo DOCX, use a ferramenta "
    "gerar_documento_normativo. Se a ferramenta retornar campos pendentes, "
    "pergunte ao usuario se ele quer informar os campos (numero, data, "
    "signatario etc.) ou se prefere que a minuta seja preenchida "
    "automaticamente. Se o usuario autorizar inventar com base no RAG "
    "ou disser para gerar o arquivo, faca uma nova chamada da ferramenta "
    "sem values (ou com values parciais), marque claramente que a minuta "
    "exige revisao. Nao responda somente com uma minuta em texto quando o "
    "usuario pediu o arquivo.\n"
    "Se o usuario enviou um documento como modelo (botao 'Modelo', .docx), "
    "a geracao usa automaticamente esse documento como base de formatacao e "
    "estilo. Nesse caso, nao informe template_name na chamada da ferramenta "
    "e avise o usuario que a minuta seguira o formato do documento enviado.\n"
    "Se o usuario desistir ou cancelar a geracao, apenas confirme em texto e "
    "responda normalmente — nao recrie o pedido de campos nem chame a "
    "ferramenta de geracao de novo.\n"
    "Quando o usuario pedir para melhorar, adequar, atualizar ou comparar "
    "antes/depois um arquivo que ele enviou, use a ferramenta "
    "melhorar_documento_usuario (sem pedir aprovacao previa); ela devolve o "
    "arquivo melhorado e a lista de alteracoes. O documento continua o mesmo "
    "ato — numero, ementa, objeto e assinaturas preservados. Se o usuario "
    "acabou de importar/enviar e NAO informou o nome do arquivo, chame a "
    "ferramenta sem o argumento filename: ela usa a importacao mais recente "
    "e devolve a lista de outras arquivos importados para voce sugerir. "
    "O mesmo vale para analisar_arquivo_usuario: sem filename ela le a "
    "importacao mais recente, e o upload fica registrado na sessao — "
    "mesmo que o filename informado nao exista, a ferramenta usa o ultimo "
    "arquivo enviado e devolve o campo 'aviso' com o nome real. Confie no "
    "'filename' retornado e siga a analise; NAO peca ao usuario para repetir "
    "o nome do arquivo. A leitura do arquivo e paginada: se a tool "
    "devolver 'has_more': true, chame analisar_arquivo_usuario novamente com "
    "'offset' = 'next_offset' e continue ate ler o documento inteiro antes de "
    "concluir a analise; nao resuma nem ignore o restante do arquivo.\n"
    "ANALISE DE DOCUMENTO ENVIADO: quando o usuario pedir a analise de um "
    "documento/minuta que ele enviou, responda com a analise COMPLETA e "
    "densa, nunca com um panorama generico. Estruture assim: (1) bloco curto "
    "de 'Pontos fortes' (3-5 bullets de uma linha); (2) corpo principal de "
    "'Pontos fracos / apontamentos de correcao' - um problema por bullet, "
    "anunciado direto ('Falta...', 'Nao ha...', 'Ausente...'), com por que e "
    "ruim, referencia (Art./Anexo) e sugestao de correcao; errado vira "
    "apontamento acionavel (analysis_registry). Percorra todo o checklist "
    "normativo (numeracao, ementa x corpo, fundamentacao, revogacoes/"
    "vigencia, assinaturas/competencia, consistencia de nomes) e lacunas de "
    "seguranca juridica (prazo de validade, recurso administrativo, "
    "monitoramento/prestacao de contas, terminologia). Se um item do "
    "checklist esta ok, diga em uma linha; nao o omita.\n"
    "CORRECAO APOS ANALISE: se o usuario ja enviou um documento, voce o "
    "analisou nesta conversa e agora ele pede para corrigir/ajustar/entregar "
    "o arquivo corrigido (ex.: 'consegue fazer a correcao?', 'me de o arquivo "
    "corrigido'), use a ferramenta melhorar_documento_usuario — NAO use "
    "gerar_documento_normativo e NAO abra formulario de campos de um ato novo. "
    "A melhoria recebe automaticamente os apontamentos acionaveis da analise "
    "registrada para aquele documento: aplique-os ao original e, para cada "
    "apontamento, garanta desfecho explicito (aplicado ou justificativa de "
    "por que nao pode ser aplicado). Nao faca uma revisao independente que "
    "descarte a analise nem invente correcoes que ela nao apontou.\n"
    "Se gerar_documento_normativo retornar status 'melhoria_necessaria', "
    "encaminhe para melhorar_documento_usuario com o arquivo e os "
    "apontamentos indicados; nunca insista na geracao.\n"
    "Voce SOMENTE gera minutas normativas oficiais (portaria, instrucao "
    "normativa, portaria conjunta, decreto, retificacao) usando "
    "gerar_documento_normativo. Voce NAO gera documentos genericos — tabelas "
    "de resumo, relatorios, atas, oficios, planilhas ou qualquer outro DOCX "
    "que nao seja um ato normativo. Se o usuario pedir algo fora desse "
    "escopo (ex.: 'DOCX com a tabela resumo'), recuse educadamente e nao "
    "invente que o arquivo foi criado. Nunca afirme que gerou um arquivo se "
    "a ferramenta nao retornou geracao. Ao responder, nao mencione caminhos "
    "internos de arquivos (ex.: 'outputs/...'); diga que o documento gerado "
    "esta disponivel no cartao de download. Nunca descreva o arquivo gerado "
    "como contendo algo que o ato nao contem (ex.: nao diga que uma minuta "
    "normativa e 'so a tabela'). Se gerar_documento_normativo retornar "
    "status 'nao_normativo', admita o limite e responda em texto, sem "
    "insistir nem gerar o arquivo.\n"
    "Em perguntas de acompanhamento sobre uma melhoria JA feita (por ex.: "
    "'o que exatamente foi alterado', 'mostre antes e depois', "
    "'mostre os textos alterados', 'monte uma tabela do antes/depois', "
    "'quais trechos mudaram'), use a ferramenta obter_textos_comparacao "
    "para recuperar os TEXTOS REAIS da ultima comparacao. Copie fielmente "
    "os trechos dos campos 'antes' e 'depois' e NAO invente textos, "
    "resumos ou cotejamentos. Nessas perguntas NAO chame "
    "melhorar_documento_usuario nem gere ou reescreva o arquivo novamente — "
    "se ela retornar status 'already_improved' ou 'sem_comparacao', apenas "
    "responda em texto com base no que houver e informe que arquivos novos "
    "nao serao criados para evitar duplicacoes."
)


def _contexto_upload_sessao() -> str | None:
    """Anota para o LLM que um arquivo foi enviado na sessão.

    O upload ocorre fora do chat (endpoint /api/upload): sem essa anotação o
    LLM não tem como saber que existe um arquivo disponível e acaba pedindo
    para o usuário "enviar o documento" de novo, mesmo com o upload já feito."""
    nome = upload_sessao()
    if not nome:
        return None
    return (
        "Contexto: o usuario acaba de enviar/importar o arquivo "
        f"'{nome}', ja gravado na pasta de importacoes desta sessao. "
        "Se o pedido atual envolver analisar/revisar/verificar um documento, "
        "chame analisar_arquivo_usuario usando esse arquivo (filename OPCIONAL "
        "-- sem ele a tool usa este upload). Nao peca para o usuario reenviar "
        "o arquivo nem informar o nome de novo; se precisar confirmar qual "
        "arquivo usar, confirme pelo nome em uma frase."
    )


def _messages_for_llm() -> list[dict]:
    sistema = build_system_message(SYSTEM_INSTRUCTIONS, messages)
    contexto = _contexto_upload_sessao()
    return [
        sistema,
        *([{"role": "system", "content": contexto}] if contexto else []),
        *messages,
    ]


# ============================================================================
# PODA DO HISTÓRICO
# ============================================================================

MANTER_TOOL_RESULTS_COMPLETOS = 4
TRUNCAR_TOOL_RESULT_PARA = 300

MANTER_ASSISTANT_COMPLETOS = 3
TRUNCAR_ASSISTANT_PARA = 1500


def _podar_tool_results_antigos():
    """Trunca resultados antigos de ferramentas."""

    indices_tool = [
        i
        for i, m in enumerate(messages)
        if m.get("role") == "tool"
    ]

    if len(indices_tool) <= MANTER_TOOL_RESULTS_COMPLETOS:
        return

    indices_para_podar = indices_tool[:-MANTER_TOOL_RESULTS_COMPLETOS]

    for i in indices_para_podar:
        conteudo = messages[i].get("content", "") or ""

        if (
            isinstance(conteudo, str)
            and len(conteudo) > TRUNCAR_TOOL_RESULT_PARA
            and not conteudo.startswith("[resultado truncado")
        ):
            messages[i]["content"] = (
                conteudo[:TRUNCAR_TOOL_RESULT_PARA]
                + f"... [resultado truncado — "
                f"{len(conteudo)} caracteres originais]"
            )


def _podar_assistant_antigos():
    """Trunca respostas antigas do assistant."""

    indices_assistant = [
        i
        for i, m in enumerate(messages)
        if (
            m.get("role") == "assistant"
            and isinstance(m.get("content"), str)
        )
    ]

    if len(indices_assistant) <= MANTER_ASSISTANT_COMPLETOS:
        return

    indices_para_podar = indices_assistant[:-MANTER_ASSISTANT_COMPLETOS]

    for i in indices_para_podar:
        conteudo = messages[i].get("content", "") or ""

        if (
            len(conteudo) > TRUNCAR_ASSISTANT_PARA
            and not conteudo.startswith("[resposta anterior truncada")
        ):
            messages[i]["content"] = (
                conteudo[:TRUNCAR_ASSISTANT_PARA]
                + f"... [resposta anterior truncada — "
                f"{len(conteudo)} caracteres originais]"
            )


# ============================================================================
# EXECUÇÃO DE TOOLS
# ============================================================================

def _executar_tool(tool_call):
    """Executa uma ferramenta solicitada pelo LLM."""

    function_name = tool_call.function.name

    function = FUNCTIONS.get(function_name)

    if not function:
        return f"Ferramenta desconhecida: {function_name}"

    try:
        arguments = json.loads(
            tool_call.function.arguments or "{}"
        )

        signature = inspect.signature(function)

        if not signature.parameters:
            result = function()
        else:
            result = function(**arguments)

    except Exception as error:  # noqa: BLE001
        return json.dumps(
            {
                "status": "error",
                "error": f"A ferramenta {function_name} falhou.",
                "detail": str(error),
            },
            ensure_ascii=False,
        )

    return (
        result
        if isinstance(result, str)
        else json.dumps(result, ensure_ascii=False)
    )


# ============================================================================
# RESPOSTA DE MELHORIA
# ============================================================================

def _cobertura_para_texto(cobertura) -> str:
    """Formata a cobertura dos apontamentos da análise por status."""
    if not cobertura:
        return ""
    grupos = (
        ("aplicado", "Aplicados"),
        ("pendente", "Pendentes de decisão jurídica"),
        ("nao_aplicado", "Não aplicados (impedimento)"),
        ("falhou", "Falhas de execução"),
    )
    linhas = ["Cobertura dos apontamentos da análise:"]
    for status, rotulo in grupos:
        itens = [c for c in cobertura if c.get("status") == status]
        if not itens:
            continue
        linhas.append(f"{rotulo}:")
        for item in itens[:15]:
            texto = str(item.get("apontamento") or "")[:160]
            referencia = item.get("referencia")
            motivo = item.get("motivo")
            sufixo = ""
            if status == "aplicado" and referencia:
                sufixo = f" (mudança: {referencia})"
            elif status != "aplicado" and motivo:
                sufixo = f" — {motivo}"
            linhas.append(f"- {texto}{sufixo}")
    return "\n".join(linhas)


def _resposta_melhoria(result: dict) -> str:
    """Transforma o resultado da tool de melhoria em texto."""

    if result.get("status") == "already_improved":
        alteracoes = result.get("alteracoes") or []

        linhas = [
            f"- ({a.get('tipo', 'alterado')}) "
            f"{a.get('o_que', '')}: "
            f"{a.get('detalhe', '')}"
            for a in alteracoes[:15]
        ]

        resumo = (
            "\n".join(linhas)
            if linhas
            else "Nenhuma alteração significativa."
        )

        resposta = (
            f"A comparação para "
            f"**{result.get('arquivo_original', '')}** "
            "já está disponível nesta conversa — não gerei "
            "um arquivo novo para não duplicar.\n\n"
            "O que mudou:\n"
            f"{resumo}"
        )

        cobertura = _cobertura_para_texto(result.get("apontamentos_analise"))
        if cobertura:
            resposta += f"\n\n{cobertura}"

        if result.get("textos"):
            resposta += (
                "\n\nSe quiser, posso detalhar os trechos alterados "
                "(antes/depois) com os textos reais."
            )

        return resposta

    if result.get("status") != "improved":
        return result.get(
            "error",
            "Não foi possível melhorar o documento.",
        )

    filename = result.get("filename") or ""
    alteracoes = result.get("alteracoes") or []
    fallback = bool(result.get("fallback"))
    mensagem = result.get("mensagem")

    if fallback and not alteracoes:
        resposta = (
            f"{mensagem or 'Não foi possível aplicar as correções. Este arquivo preserva o conteúdo original.'}\n"
            f"Arquivo: **{filename}** — disponível para download no cartão "
            "abaixo."
        )
        cobertura = _cobertura_para_texto(result.get("apontamentos_analise"))
        if cobertura:
            resposta += f"\n\n{cobertura}"
        outros = result.get("outros") or []
        if outros:
            resposta += (
                "\n\nOutros arquivos importados disponíveis:\n"
                + "\n".join(f"- {nome}" for nome in outros)
            )
        return resposta

    linhas = [
        f"- ({a.get('tipo', 'alterado')}) "
        f"{a.get('o_que', '')}: "
        f"{a.get('detalhe', '')}"
        for a in alteracoes[:15]
    ]

    resumo = (
        "\n".join(linhas)
        if linhas
        else "Nenhuma alteração significativa."
    )

    resposta = (
        f"Documento melhorado e comparado! "
        f"Arquivo: **{filename}**.\n"
        "O arquivo original e a nova versão estão disponíveis "
        "nos downloads e na comparação ao lado.\n\n"
        "O que mudou:\n"
        f"{resumo}"
    )

    cobertura = _cobertura_para_texto(result.get("apontamentos_analise"))
    if cobertura:
        resposta += f"\n\n{cobertura}"

    if result.get("descartados"):
        resposta += (
            "\n\nMudanças inválidas descartadas: "
            + ", ".join(dict.fromkeys(result.get("descartados")))
            + "."
        )

    outros = result.get("outros") or []

    if outros:
        resposta += (
            "\n\nOutros arquivos importados disponíveis:\n"
            + "\n".join(f"- {nome}" for nome in outros)
        )

    return resposta


# ============================================================================
# FLUXOS ESPECIAIS
# ============================================================================

def _tratar_geracao_pendente(question):
    """Finaliza uma geração de documento que estava aguardando confirmação."""

    normalized_question = question.casefold()

    generation_phrases = (
        "gere o arquivo",
        "gerar o arquivo",
        "pode gerar",
        "pode preencher",
        "pode inventar",
        "prossiga",
        "faça isso",
        "faca isso",
        "pode fazer",
        "sim",
        "ok",
        "okay",
        "concordo",
        "confirmo",
        "confirma",
        "continua",
        "prossegue",
        "pode seguir",
    )

    if not has_pending_document():
        return None

    if not any(
        phrase in normalized_question
        for phrase in generation_phrases
    ):
        return None

    result = json.loads(
        gerar_documento_normativo(question)
    )

    if result.get("status") != "generated":
        return None

    resposta = (
        "Documento gerado com sucesso. O arquivo já está disponível "
        "no cartão de download desta conversa.\n\n"
        "A minuta foi preenchida automaticamente e precisa ser revisada."
    )

    messages.append({
        "role": "assistant",
        "content": resposta,
    })

    return resposta


def _tratar_cancelamento(question):
    """Cancela uma geração de documento pendente."""

    if not has_pending_document():
        return

    normalized_question = question.casefold()

    cancel_phrases = (
        "cancele",
        "cancelar",
        "cancela",
        "cancelando",
    )

    if any(
        phrase in normalized_question
        for phrase in cancel_phrases
    ):
        cancelar_pendencia()


def _executar_melhoria(filename=None, diretrizes=None, apontamentos=None):
    """Executa a melhoria de um documento."""

    argumentos = {}
    if filename:
        argumentos["filename"] = filename
    if diretrizes:
        argumentos["diretrizes"] = diretrizes
    if apontamentos:
        argumentos["apontamentos"] = apontamentos

    result = melhorar_documento_usuario(**argumentos)

    resposta = _resposta_melhoria(
        json.loads(result)
    )

    messages.append({
        "role": "assistant",
        "content": resposta,
    })

    return resposta


def _tratar_melhoria_direta(question):
    """Detecta pedidos especiais de melhoria de documento."""

    normalized_question = question.casefold()

    match_melhoria = re.match(
        r"^melhore e compare o arquivo\s+['\"]?(.+?)['\"]?\s*$",
        normalized_question,
    )

    if match_melhoria:
        return _executar_melhoria(
            match_melhoria.group(1)
        )

    match_melhoria_sem_nome = re.match(
        r"^melhore(?: e compare)?"
        r"(?: o| este| esse| aquele| um)?"
        r"(?: (?:arquivo|documento|texto|conteudo))?"
        r"(?: que (?:eu\s+)?(?:mandei|enviei|importei))?\s*$",
        normalized_question,
    )

    if match_melhoria_sem_nome:
        return _executar_melhoria()

    return None


def _tratar_correcao_direta(question):
    """Encaminha pedido de correção de um documento JÁ ANALISADO para a melhoria.

    Diferente de ``_tratar_melhoria_direta`` (que é acionado por pedidos
    explícitos de melhoria), aqui o gatilho é uma intenção de corrigir/entregar
    o arquivo corrigido depois que o agente já o analisou. Passa os apontamentos
    acionáveis da análise registrada para que a melhoria os aplique ao original
    — sem abrir o formulário de geração de um ato novo."""

    if not pedido_de_correcao(question):
        return None

    analise = analise_para_correcao()
    if analise:
        return _executar_melhoria(
            filename=analise.get("filename"),
            diretrizes=question,
            apontamentos=analise.get("apontamentos"),
        )

    # Sem análise registrada: se há arquivo importado e o pedido é de correção
    # do arquivo (não de um ato novo), ainda usa o motor de melhoria.
    filename = arquivo_para_correcao_sem_analise(question)
    if filename:
        return _executar_melhoria(filename=filename, diretrizes=question)

    return None


# ============================================================================
# CONFIRMAÇÃO DO ARQUIVO ENVIADO (encerra o loop de "envie o arquivo de novo")
# ============================================================================

# Intenção de analisar/revisar/verificar um documento (palavras-gatilho).
_RE_PEDIDO_ANALISE = re.compile(
    r"\ban[áa]l\w*|audit\w*|conformidade|consist[eê]nc\w*|verific\w*|avali\w*|"
    r"confer\w*|inspecion\w*|revis\w*|cotej\w*|adequa[çc][ãa]o|vigente|"
    r"jur[ií]dic\w*|ortogr[áa]fic\w*|sistem[áa]tic\w*",
    re.IGNORECASE,
)
_RE_REF_DOCUMENTO = re.compile(
    r"\b(arquivo|documento|texto|minuta|ato|norma|instru[çc][ãa]o|\bin\b|"
    r"portaria|decreto|anexo|conte[uú]do)\b",
    re.IGNORECASE,
)


def _arquivo_ja_analisado(nome: str) -> bool:
    """True se o arquivo já tem análise registrada nesta sessão."""
    try:
        return (
            analysis_registry.obter(
                analysis_registry.sessao_atual(), nome
            )
            is not None
        )
    except Exception:  # noqa: BLE001
        return False


def _mensagem_confirmacao(nome: str) -> str:
    """Pergunta de confirmação citando o arquivo encontrado no envio."""
    outros = [
        nome_outro
        for nome_outro in _list_available_files()
        if nome_outro.casefold() != nome.casefold()
    ]
    texto = (
        f"Encontrei o arquivo **'{nome}'** no envio recente.\n\n"
        "É este o arquivo que você deseja que eu analise?"
    )
    if outros:
        texto += (
            "\n\nTambém identifiquei outros arquivos importados: "
            + ", ".join(f"'{item}'" for item in outros[:5])
            + ". Se preferir analisar outro, informe o nome."
        )
    return texto


def _primeiro_pedido_analise_sem_nome(question: str) -> str | None:
    """Intercepta o primeiro pedido de análise de um documento enviado SEM
    citar o nome: em vez de deixar o LLM responder "envie o arquivo" (loop de
    reenvio), confirma o upload registrado na sessão perguntando se é o
    correto. Só atua uma vez por arquivo (análise ainda não registrada)."""
    if not (
        _RE_PEDIDO_ANALISE.search(question)
        and _RE_REF_DOCUMENTO.search(question)
    ):
        return None

    nome = upload_sessao()
    if not nome:
        return None
    if nome.casefold() in question.casefold():
        return None
    if _arquivo_ja_analisado(nome):
        return None

    resposta = _mensagem_confirmacao(nome)
    messages.append({"role": "assistant", "content": resposta})
    return resposta


# Respostas do LLM que pedem o arquivo de novo (gato escaldado: acontece quando
# o LLM responde em texto sem chamar a tool). Nesse caso trocamos pela mesma
# confirmação do upload registrado, para nunca pedir reenvio.
_RE_PEDE_ENVIO = re.compile(
    r"(?:por\s+favor\s*,\s*)?(?:envie|enviar|mande|manda|encaminhe|"
    r"forne[çc]a|disponibilize|compartilhe|anexe)\b.{0,80}"
    r"\b(?:arquivo|documento|instru[çc][ãa]o|\bin\b|minuta|ato|norma|texto)\b"
    r"|\binform(?:e|ar)?\b.{0,40}?\bnome\s+do\s+arquivo\b"
    r"|\bqual\s+(?:[ée]|eh)?\s*(?:o\s+)?nome\s+do\s+arquivo\b"
    r"|\bpara\s+que\s+eu\s+poss(?:a|o)\s+analisar\b",
    re.IGNORECASE,
)


def _resposta_reenvio_para_confirmacao(conteudo: str) -> str | None:
    """Se o LLM respondeu pedindo o arquivo de novo, mas há upload registrado
    na sessão, devolve a pergunta de confirmação no lugar daquela resposta."""
    if not _RE_PEDE_ENVIO.search(conteudo or ""):
        return None
    nome = upload_sessao()
    if not nome:
        return None
    return _mensagem_confirmacao(nome)


# ============================================================================
# LOOP PRINCIPAL DO AGENTE
# ============================================================================

def _filename_do_resultado(resultado: str) -> str | None:
    """Nome do arquivo lido por analisar_arquivo_usuario (ou None)."""
    try:
        dados = json.loads(resultado)
    except (TypeError, ValueError):
        return None
    if isinstance(dados, dict):
        nome = dados.get("filename")
        if isinstance(nome, str) and nome.strip():
            return nome
    return None


def _resultado_melhoria_necessaria(resultado: str) -> dict | None:
    """Interpreta o retorno da geração que pede encaminhamento para a melhoria."""
    try:
        dados = json.loads(resultado)
    except (TypeError, ValueError):
        return None
    if isinstance(dados, dict) and dados.get("status") == "melhoria_necessaria":
        return dados
    return None


def _executar_loop_agente(question: str = ""):
    """
    Executa o ciclo:

        LLM → tool call → tool → tool result → LLM

    até o LLM produzir uma resposta final.
    """

    ultimo_resultado_tool = None
    arquivo_analisado = None
    fez_edicao = False

    # Teto de iterações do loop. Foi ampliado (5 -> 10) para acomodar a leitura
    # PAGINADA de documentos grandes: cada janela de `analisar_arquivo_usuario`
    # consome uma iteração, e o agente precisa ler o arquivo inteiro antes de
    # concluir a análise.
    for _ in range(10):

        _podar_tool_results_antigos()
        _podar_assistant_antigos()

        try:
            response = perguntar(
                _messages_for_llm(),
                TOOLS,
            )

        except Exception as error:  # noqa: BLE001
            mensagem_erro = (
                f"Não foi possível consultar o modelo de linguagem: "
                f"{error}"
            )

            messages.append({
                "role": "assistant",
                "content": mensagem_erro,
            })

            return mensagem_erro

        message = response.choices[0].message

        # O LLM respondeu normalmente.
        if not message.tool_calls:

            conteudo = message.content

            # Se o LLM pediu o arquivo de novo (sem usar a tool), converte na
            # confirmação do upload registrado — nunca pedir reenvio ao usuário.
            troca = _resposta_reenvio_para_confirmacao(conteudo)
            if troca is not None:
                conteudo = troca

            messages.append({
                "role": "assistant",
                "content": conteudo,
            })

            # Consolida os apontamentos do turno no documento em análise. Um
            # APROFUNDAMENTO (ex.: "e o que está ruim?") não chama de novo a
            # leitura, mas ainda é vinculado ao mesmo documento. Turnos de
            # edição (melhoria/geração) não viram apontamento.
            alvo = documento_para_analise(conteudo, arquivo_analisado)
            if alvo and (conteudo or "").strip() and not fez_edicao:
                origem = "aprofundamento" if not arquivo_analisado else "análise"
                registrar_analise(alvo, conteudo, origem)

            return (
                conteudo
                or "Não foi possível gerar uma resposta."
            )

        # O LLM pediu uma ou mais ferramentas.
        messages.append(
            message.model_dump(exclude_none=True)
        )

        for tool_call in message.tool_calls:

            nome_tool = tool_call.function.name

            resultado = _executar_tool(tool_call)

            ultimo_resultado_tool = resultado

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": resultado,
            })

            if nome_tool == "analisar_arquivo_usuario":
                nome = _filename_do_resultado(resultado)
                if nome:
                    arquivo_analisado = nome

            if nome_tool in (
                "melhorar_documento_usuario",
                "gerar_documento_normativo",
            ):
                fez_edicao = True

            # A geração recusou gerar um ato novo por se tratar de correção de
            # documento analisado: encaminha para a melhoria sem novo formulário.
            despacho = _resultado_melhoria_necessaria(resultado)
            if despacho is not None:
                return _executar_melhoria(
                    filename=despacho.get("filename"),
                    diretrizes=question,
                    apontamentos=despacho.get("apontamentos"),
                )

    if ultimo_resultado_tool:
        return (
            "A ferramenta não concluiu a operação. "
            f"Último estado retornado: {ultimo_resultado_tool}"
        )

    return "Não foi possível concluir a consulta."


# ============================================================================
# ENTRADA PRINCIPAL
# ============================================================================

def executar(question):
    """
    Executa o agente mantendo o histórico da conversa.

    A função apenas coordena os diferentes fluxos.
    """

    # 1. Adiciona a pergunta ao histórico.
    messages.append({
        "role": "user",
        "content": question,
    })

    # 2. Trata geração de documento pendente.
    resposta = _tratar_geracao_pendente(question)

    if resposta is not None:
        return resposta

    # 3. Trata cancelamento.
    _tratar_cancelamento(question)

    # 4. Trata melhoria direta de documentos.
    resposta = _tratar_melhoria_direta(question)

    if resposta is not None:
        return resposta

    # 5. Trata correção de documento já analisado (vai direto para a melhoria).
    resposta = _tratar_correcao_direta(question)

    if resposta is not None:
        return resposta

    # 6. Primeiro pedido de análise de documento enviado sem citar o nome:
    #    confirma o arquivo encontrado na sessão em vez de pedir reenvio.
    resposta = _primeiro_pedido_analise_sem_nome(question)

    if resposta is not None:
        return resposta

    # 7. Caso nenhum fluxo especial tenha sido acionado,
    #    executa o agente normalmente.
    return _executar_loop_agente(question)


# ============================================================================
# LIMPEZA
# ============================================================================

def limpar_conversa():
    """Apaga o histórico da conversa e o estado de geração pendente."""

    messages.clear()
    limpar_estado()
