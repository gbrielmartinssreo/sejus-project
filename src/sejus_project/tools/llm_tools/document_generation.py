"""Tool de function calling para gerar atos a partir de modelos DOCX reais."""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import traceback
import uuid
from pathlib import Path

from sejus_project.tools.document_infra import analise_validacao, docx_builder, docx_validacao, modelos
from sejus_project.tools.document_infra.docx_templates import OUTPUTS_DIR
from sejus_project.tools.llm_tools import (
    analysis_registry,
    document_improvement,
    minuta_generation,
)
from sejus_project.tools.llm_tools.retrieval import retrieve
from sejus_project.tools.llm_tools.user_files import (
    UserFileError,
    _list_available_files,
    _resolve_file,
    _ultimo_arquivo_importado,
    extract_file_text,
    limpar_upload_sessao,
)
from sejus_project.web.render_html import minuta_para_texto

_pending_document: dict | None = None
_ultima_minuta: dict | None = None
_modelo_usuario: dict | None = None
_gerada_no_turno: bool = False
_pendencia_mudou_no_turno: bool = False
_melhoria_no_turno: bool = False
_ultima_comparacao: dict | None = None

# Persistência de propostas (aceites/rejeitadas/ aplicadas)
# Armazenado em arquivo JSON para sobrevivência entre reinicializações do servidor.
# Cada proposta tem ID próprio (UUID), associado a (doc_hash, rotulo, versao, localizacao).
PROPOSTAS_ARQUIVO = Path(__file__).parent / "propostas_estado.json"

# Estado de uma proposta individual
ESTADO_PENDENTE = "pendente"
ESTADO_ACEITA = "aceita"
ESTADO_REJEITADA = "rejeitada"
ESTADO_APLICADA = "aplicada"

# Cache em memória (válido para a conversa atual)
_propostas_cache: dict | None = None


def _caminho_arquivo_propostas() -> Path:
    """Retorna o caminho do arquivo de persistência de propostas."""
    return PROPOSTAS_ARQUIVO


def _carregar_propostas_disc() -> dict:
    """Carrega dicionário de propostas do arquivo JSON na disco."""
    if _caminho_arquivo_propostas().is_file():
        try:
            with open(_caminho_arquivo_propostas(), "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _salvar_propostas_disc(propostas: dict):
    """Salva dicionário de propostas no arquivo JSON na disco."""
    with open(_caminho_arquivo_propostas(), "w", encoding="utf-8") as f:
        json.dump(propostas, f, ensure_ascii=False, indent=2)


def _hash_documento(caminho: str) -> str:
    """Calcula o hash SHA-1 do conteúdo do documento."""
    from sejus_project.tools.llm_tools.user_files import extract_file_text
    try:
        texto = extract_file_text(Path(caminho))
        return hashlib.sha1(texto.encode("utf-8", "ignore")).hexdigest()
    except Exception:
        return ""


def _gerar_id_proposta(doc_hash: str, rotulo: str, versao: str, localizacao: str) -> str:
    """Gera um ID estável para a proposta usando os campos identificadores."""
    chave = f"{doc_hash}|{rotulo.strip().lower()}|{versao.strip().lower()}|{localizacao.strip().lower()}"
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()


def proposicao_para_dict(proposal_id: str, doc_hash: str, rotulo: str,
                         versao: str, localizacao: str, estado: str,
                         proposta_texto: str, justificativa: str = "",
                         fonte: str = "") -> dict:
    """Converte os dados da proposta dicionário para armazenamento."""
    return {
        "proposal_id": proposal_id,
        "doc_hash": doc_hash,
        "rotulo": rotulo,
        "versao": versao,
        "localizacao": localizacao,
        "estado": estado,
        "proposta_texto": proposta_texto,
        "justificativa": justificativa,
        "fonte": fonte,
    }


def dict_para_proposta(dado: dict) -> dict:
    """Converte dicionário de volta para estrutura de proposta."""
    return {
        "proposal_id": dado.get("proposal_id", ""),
        "doc_hash": dado.get("doc_hash", ""),
        "rotulo": dado.get("rotulo", ""),
        "versao": dado.get("versao", ""),
        "localizacao": dado.get("localizacao", ""),
        "estado": dado.get("estado", ESTADO_PENDENTE),
        "proposta_texto": dado.get("proposta_texto", ""),
        "justificativa": dado.get("justificativa", ""),
        "fonte": dado.get("fonte", ""),
    }


def iniciar_conversa_propostas():
    """Inicializa o cache de propostas para a nova conversa."""
    global _propostas_cache
    _propostas_cache = _carregar_propostas_disc()


def finalizar_conversa_propostas():
    """Salva o cache de volta ao disco ao encerrar a conversa."""
    global _propostas_cache
    if _propostas_cache is not None:
        _salvar_propostas_disc(_propostas_cache)
    _propostas_cache = None


def proposicao_id_para_chave(doc_hash: str, rotulo: str, versao: str, localizacao: str) -> str:
    """Retorna a chave de lookup no dicionário de propostas."""
    return _gerar_id_proposta(doc_hash, rotulo, versao, localizacao)


def procurar_proposta(doc_hash: str, rotulo: str, versao: str, localizacao: str) -> dict | None:
    """Busca uma proposta pelos seus identificadores associados."""
    global _propostas_cache
    if _propostas_cache is None:
        iniciar_conversa_propostas()
    chave = proposicao_id_para_chave(doc_hash, rotulo, versao, localizacao)
    return _propostas_cache.get(chave)


def aceitar_proposta(documento_caminho: str, rotulo: str, versao: str, localizacao: str,
                     proposta_texto: str, justificativa: str = "", fonte: str = "") -> str:
    """Marca uma proposta como aceita. Se já existir com mesmo ID, atualiza.
    Retorna o proposal_id."""
    global _propostas_cache
    if _propostas_cache is None:
        iniciar_conversa_propostas()

    doc_hash = _hash_documento(documento_caminho)
    chave = proposicao_id_para_chave(doc_hash, rotulo, versao, localizacao)
    proposta_id = chave

    proposta_atual = _propostas_cache.get(chave)
    if proposta_atual and proposta_atual.get("estado") == ESTADO_ACEITA:
        # Já estava aceita; apenas atualiza o texto se mudou
        proposta_atual["proposta_texto"] = proposta_texto
        proposta_atual["justificativa"] = justificativa
        proposta_atual["fonte"] = fonte
        _propostas_cache[chave] = proposta_atual
        _salvar_propostas_disc(_propostas_cache)
        return proposta_id

    # Cria ou atualiza proposta com estado aceita
    proposta_id = chave
    nova_proposta = proposicao_para_dict(
        proposta_id, doc_hash, rotulo, versao, localizacao,
        ESTADO_ACEITA, proposta_texto, justificativa, fonte)
    _propostas_cache[chave] = nova_proposta
    _salvar_propostas_disc(_propostas_cache)
    return proposta_id


def rejeitar_proposta(documento_caminho: str, rotulo: str, versao: str, localizacao: str) -> str:
    """Marca uma proposta como rejeitada. Retorna o proposal_id."""
    global _propostas_cache
    if _propostas_cache is None:
        iniciar_conversa_propostas()

    doc_hash = _hash_documento(documento_caminho)
    chave = proposicao_id_para_chave(doc_hash, rotulo, versao, localizacao)

    # Remove do cache e do disco se existir
    if chave in _propostas_cache:
        del _propostas_cache[chave]
    _salvar_propostas_disc(_propostas_cache)  # estoque limpo (remove a entrada)

    # Também remove do arquivo se estiver lá
    todas = _carregar_propostas_disc()
    if chave in todas:
        del todas[chave]
    _salvar_propostas_disc(todas)

    return chave


def listar_propostas(filtro_estado: str | None = None) -> list[dict]:
    """Lista propostas, opcionalmente filtradas por estado."""
    global _propostas_cache
    if _propostas_cache is None:
        iniciar_conversa_propostas()

    resultados = list(_propostas_cache.values())
    if filtro_estado:
        resultados = [p for p in resultados if p.get("estado") == filtro_estado]
    return resultados


def aplicar_alteracoes_selecionadas(filename: str) -> dict:
    """Aplica apenas as propostas marcadas como 'aceita'.

    Retorna um dicionário com o status e caminhos dos arquivos resultantes.
    Apenas alteracoes com estado 'aceita' sao gravadas no documento.
    """
    global _propostas_cache
    if _propostas_cache is None:
        iniciar_conversa_propostas()

    aceitas = listar_propostas(ESTADO_ACEITA)

    if not aceitas:
        return {
            "status": "nenhuma_aceita",
            "mensagem": "Nenhuma proposta foi aceita. Use 'aceitar_proposta' para marcar alteracoes.",
            "output_path": None,
        }

    # Carrega o documento original e estrutura
    from sejus_project.tools.document_infra import docx_builder
    from sejus_project.tools.llm_tools import document_improvement as minuta
    from sejus_project.tools.llm_tools.user_files import extract_file_text

    conteudo = extract_file_text(Path(filename))
    tipo_ato = modelos.detectar_tipo_ato(conteudo)
    perfil = modelos.crear_perfil_de_arquivo(Path(filename), conteudo, Path(filename).stem)

    # Gera nova estrutura considering only aceitas
    # Regenerar o patch com apenas as alteracoes aceitas
    _estrutura, alt, rem, adicoo, _lac = minuta.gerar_estrutura_melhoria(
        conteudo, tipo_ato, perfil, [], {"diretrizes": "Aplicar apenas alteracoes aceitas"}
    )

    # Marcar alteracoes aceitas no documento
    for a in alt:
        if a.get("estado") == ESTADO_ACEITA:
            a["aplicada"] = True

    output_path = docx_builder.montar_docx_revisado(
        perfil, alt, rem, adicoo, OUTPUTS_DIR
    )

    return {
        "status": "aplicado",
        "mensagem": f"{len(alt)} alteracoes, {len(rem)} remocoes e {len(adicoo)} adicoes estruturais aplicadas.",
        "output_path": str(output_path),
        "total_aceitas": len(alt),
        "total_remocoes": len(rem),
        "total_adicoes": len(adicoo),
    }


def proposta_para_texto(proposta: dict) -> str:
    """Formata uma proposta para exibicao no chat."""
    partes = []
    if proposta.get("rotulo"):
        partes.append(f"**{proposta['rotulo']}**")
    if proposta.get("proposta_texto"):
        partes.append(proposta["proposta_texto"][:500] + ("..." if len(proposta["proposta_texto"]) > 500 else ""))
    if proposta.get("justificativa"):
        partes.append(f"*Justificativa: {proposta['justificativa'][:200]}*")
    if proposta.get("fonte"):
        partes.append(f"*Fonte: {proposta['fonte']}*")
    return "  \n".join(partes)

# Teto de tamanho do pedido para nao estourar contexto indefinidamente.
MAX_REQUEST_CHARS = 40_000

# Campos que o usuario pode informar antes da geracao.
CAMPOS_BASE = ["numero_ato", "data_ato", "local", "signatario", "cargo", "ementa"]


def limpar_estado():
    """Reseta o estado interno (usado pelo botao 'Limpar conversa')."""
    global _pending_document, _ultima_minuta, _modelo_usuario, _gerada_no_turno
    global _pendencia_mudou_no_turno, _melhoria_no_turno, _ultima_comparacao
    _pending_document = None
    _ultima_minuta = None
    _modelo_usuario = None
    _gerada_no_turno = False
    _pendencia_mudou_no_turno = False
    _melhoria_no_turno = False
    _ultima_comparacao = None
    # Nova sessão: as análises da conversa anterior não se aplicam à nova
    # e o upload registrado volta a ser descoberto por mtime, se ainda existir.
    analysis_registry.nova_sessao()
    limpar_upload_sessao()


# ---------------------------------------------------------------------------
# Análise do documento enviado (contexto para a correção posterior)
# ---------------------------------------------------------------------------

# Intenção de correção/entrega do documento já analisado (não é geração nova).
# Verbos fortes de correção bastam; 'ajustar' é ambíguo e exige referência ao
# documento para não capturar um pedido de ato novo ("ajuste o fluxo...").
_RE_CORRECAO = re.compile(
    r"(corri[gj]|corre[çc][ãõ]e?s?|consert|arrum|refa[çz]|retific)",
    re.IGNORECASE,
)
_RE_CORRECAO_CONTEXTO = re.compile(
    r"(arquivo|documento|texto|minuta|ato|norma|instru[çc][ãa]o|portaria|"
    r"decreto|anexo|cl[áa]usula|artigo|dispositivo|reda[çc][ãa]o)",
    re.IGNORECASE,
)
_RE_GERACAO = re.compile(
    r"\b(gere|gerar|crie|criar|elabore|elaborar|redija|redigir|produza|"
    r"produzir|monte|nova|novo)\b",
    re.IGNORECASE,
)


def pedido_de_correcao(request: str) -> bool:
    """Diz se o pedido é de corrigir/entregar o documento já analisado."""
    texto = request or ""
    if _RE_CORRECAO.search(texto):
        return True
    return bool(
        re.search(r"ajust", texto, re.IGNORECASE)
        and _RE_CORRECAO_CONTEXTO.search(texto)
        and not _RE_GERACAO.search(texto)
    )


def registrar_analise(
    filename: str, analise_completa: str, origem: str | None = None
) -> dict | None:
    """Guarda a análise do documento na sessão corrente (com a versão).

    Vincula os apontamentos ao arquivo REALMENTE analisado e à sua versão
    (hash do conteúdo), acumulando por ID em análises de vários turnos."""
    if not filename:
        return None
    try:
        caminho = _resolve_file(filename)
        texto = extract_file_text(caminho)
    except (UserFileError, OSError):
        return None
    return analysis_registry.registrar(
        analysis_registry.sessao_atual(),
        caminho.name,
        analysis_registry.hash_conteudo(texto),
        analise_completa or "",
        origem=origem,
    )


def documento_em_analise() -> str | None:
    """Nome do documento cuja análise foi registrada mais recentemente.

    Usado para vincular o APROFUNDAMENTO (turno sem nova leitura) ao mesmo
    documento já analisado, mantendo os apontamentos atualizados."""
    entrada = analysis_registry.mais_recente(analysis_registry.sessao_atual())
    if not entrada:
        return None
    return entrada.get("arquivo") or None


def documento_para_analise(
    analise_completa: str, arquivo_lido: str | None = None
) -> str | None:
    """Documento ao qual o turno de análise se refere.

    Prioriza o arquivo lido no turno; depois o documento já em análise. Se o
    agente produziu apontamentos acionáveis sem (re)ler o arquivo, vincula à
    importação mais recente — assim a análise não se perde quando o modelo
    responde a partir do histórico."""
    if arquivo_lido:
        return arquivo_lido
    em_analise = documento_em_analise()
    if em_analise:
        return em_analise
    if analysis_registry.extrair_apontamentos(analise_completa or ""):
        return _ultimo_arquivo_importado()
    return None


def arquivo_para_correcao_sem_analise(request: str) -> str | None:
    """Arquivo a corrigir quando NÃO há análise registrada.

    Ainda usa o motor de melhoria (não a geração de ato novo) para pedidos de
    correção do arquivo enviado que não sejam de um ato normativo novo, como
    "me gera o arquivo com as correções"."""
    if not pedido_de_correcao(request):
        return None
    if _intencao_normativa(request):
        return None
    return _ultimo_arquivo_importado()


def analise_para_correcao(filename: str | None = None) -> dict | None:
    """Análise registrada do documento a corrigir (ou da análise mais recente).

    Sem nome, usa a análise registrada mais recentemente na sessão (não a
    importação mais recente), para corrigir o documento que foi de fato
    analisado. Só devolve quando a versão em disco casa com a analisada."""
    if not filename:
        recente = analysis_registry.mais_recente(analysis_registry.sessao_atual())
        if recente is None:
            return None
        filename = recente.get("arquivo")
    if not filename:
        return None
    try:
        caminho = _resolve_file(filename)
        texto = extract_file_text(caminho)
    except (UserFileError, OSError):
        return None
    entrada = analysis_registry.obter(
        analysis_registry.sessao_atual(),
        caminho.name,
        analysis_registry.hash_conteudo(texto),
    )
    if entrada is None:
        return None
    return {
        "filename": entrada.get("arquivo") or caminho.name,
        "apontamentos": entrada.get("apontamentos") or [],
        "analise_completa": entrada.get("analise_completa") or "",
    }


def _pendencia_de_correcao(pendente: dict | None) -> dict | None:
    """Análise do documento a corrigir quando a geração pendente é, na verdade,
    a entrega da correção de um ato já analisado (e não a geração de um ato novo).

    A confirmação da pendência ("sim"/"pode gerar") após uma análise registrada
    do arquivo enviado só faz sentido como aplicação dos apontamentos ao
    original. Com análise registrada na sessão, a pendência é desviada para a
    melhoria — desvio deliberado para não entregar um ato novo criado a partir
    de template no lugar do documento corrigido. Sem análise, a geração segue."""
    if not pendente:
        return None
    return analise_para_correcao()


def set_modelo_usuario(filename: str, importacoes_dir: Path) -> dict:
    """Define um DOCX enviado pelo usuário como modelo de formatação ativo.

    O arquivo vira um ``PerfilModelo`` dinâmico (formato + padrões do tipo
    detectado) e passa a ser a base de montagem das próximas minutas da
    conversa, até ``limpar_conversa``. Só aceita ``.docx`` -- formatos de
    leitura (pdf/txt/md) não carregam formatação clonável.

    Devolve um resumo com o nome do modelo e o tipo detectado."""
    global _modelo_usuario

    nome = Path(filename).name
    destino = (Path(importacoes_dir) / nome).resolve()
    base = Path(importacoes_dir).resolve()

    if base not in destino.parents and destino != base:
        raise UserFileError("Caminho inválido para o modelo.")

    if destino.suffix.lower() != ".docx":
        raise UserFileError(
            "Somente arquivos .docx podem ser usados como modelo de "
            "formatação (pdf, txt e md não preservam o layout)."
        )
    if not destino.is_file():
        raise UserFileError(f"Arquivo '{nome}' não encontrado em {importacoes_dir}/{nome}.")

    texto = extract_file_text(destino)
    perfil = modelos.crear_perfil_de_arquivo(destino, texto, Path(nome).stem)
    _modelo_usuario = {
        "perfil": perfil,
        "texto": texto,
        "filename": nome,
    }
    return {
        "filename": nome,
        "modelo": perfil.name,
        "tipo_ato": perfil.act_types[0],
        "n_chars": len(texto),
    }


def modelo_usuario_ativo() -> dict | None:
    """Devolve um resumo do modelo do usuário ativo (ou None)."""
    if not _modelo_usuario:
        return None
    return {
        "filename": _modelo_usuario["filename"],
        "modelo": _modelo_usuario["perfil"].name,
        "tipo_ato": _modelo_usuario["perfil"].act_types[0],
    }


def _context_for_request(request: str, perfil: modelos.PerfilModelo) -> list[dict]:
    tipo = modelos.detectar_tipo_ato(request)
    act_type = modelos.ACT_TYPE_FILTER.get(tipo)
    return retrieve(
        f"{request}\nTipo de ato: {perfil.name}",
        limit=16,
        act_type=act_type,
    )


definition = {
    "type": "function",
    "function": {
        "name": "gerar_documento_normativo",
        "description": (
            "Gera um ATO NORMATIVO (minuta oficial): seleciona "
            "automaticamente um modelo DOCX real da SEJUS conforme o "
            "tipo de ato pedido, consulta atos normativos relacionados no RAG e "
            "gera uma copia preenchida em outputs/. Use SOMENTE quando o "
            "usuario pedir explicitamente um ato normativo (portaria, "
            "instrucao normativa, portaria conjunta, decreto, retificacao). "
            "NAO use para documentos genericos: tabelas de resumo, "
            "relatorios, atas, oficios, planilhas ou qualquer outro DOCX que "
            "nao seja um ato normativo — a ferramenta nao sabe montar esses "
            "documentos e retorna status 'nao_normativo'. Um pedido vago como "
            "'faca isso' so faz sentido quando ja existe uma minuta pendente "
            "esperando confirmacao. Se o usuario tiver enviado "
            "um documento como modelo (botao 'Modelo' com .docx), este modelo "
            "do usuario e usado como base de formatacao e estilo no lugar do "
            "template interno. Na primeira chamada, informe "
            "request e, opcionalmente, template_name, sem values, para obter os "
            "campos e o contexto. Depois pergunte ao usuario se ele deseja "
            "informar os campos (numero, data, signatario, ementa etc.) ou se "
            "prefere que a minuta seja preenchida automaticamente com dados "
            "plausiveis para revisao. Se o usuario autorizar inventar ou disser "
            "para gerar o arquivo, chame novamente sem values (ou com values "
            "parciais) para finalizar. Nao responda apenas com texto quando o "
            "usuario pediu um arquivo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "Pedido do usuario e objeto pretendido do ATO normativo (ex.: 'Gere uma portaria sobre limpeza das unidades').",
                },
                "template_name": {
                    "type": "string",
                    "description": (
                        "Opcional. Nome do modelo a usar (ex.: "
                        "'IN_FUNCAO_ARMADA', 'PORTARIA', 'PORTARIA_CONJUNTA', "
                        "'RETIFICACAO', 'DECRETO_LEGADO'). Se omitido, o modelo "
                        "e escolhido automaticamente pelo tipo de ato."
                    ),
                },
                "values": {
                    "type": "object",
                    "description": (
                        "Opcional. Campos informados pelo usuario, por exemplo "
                        "{\"numero_ato\": \"PORTARIA Nº 12/2026/GAB-SEJUS/MT\", "
                        "\"signatario\": \"Vitor Hugo Bruzulato Teixeira\", "
                        "\"data_ato\": \"08/09/2026\"}."
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["request"],
        },
    },
}


def _source_summary(results: list[dict]) -> list[dict]:
    return [
        {
            "source_file": result.get("source_file"),
            "act_type": result.get("act_type"),
            "act_number": result.get("act_number"),
            "score": result.get("score"),
            "text": result.get("text", ""),
        }
        for result in results
    ]


_RE_ROTULO_TRECHO = re.compile(
    r"^(?:art\.?\s*\d+(?:º|°)?(?:\s*[-–—].*)?$|"
    r"§\s*\d+|cap[íi]tulo\s+[ivxl]+.*$|anexo\s+\w+.*$)",
    re.IGNORECASE,
)


def _rotulo_do_trecho(linhas: list[str], inicio: int) -> str:
    """Busca o rótulo (artigo/§/capítulo/anexo) mais próximo acima do trecho."""
    for i in range(max(inicio, 0), -1, -1):
        texto = linhas[i].strip()
        if texto and _RE_ROTULO_TRECHO.match(texto):
            return texto[:120]
    return "Trecho alterado"


# Respeita o limite de contexto: no máximo alguns trechos reais, cada um curto.
MAX_TRECHOS_COMPARACAO = 12
MAX_TRECHO_CHARS = 500


def _textos_antes_depois(antes: str, depois: str) -> list[dict]:
    """Extrai trechos reais alterados (antes/depois) via diff linha a linha."""
    linhas_antes = antes.splitlines()
    linhas_depois = depois.splitlines()
    matcher = difflib.SequenceMatcher(
        None, linhas_antes, linhas_depois, autojunk=False
    )
    textos: list[dict] = []
    for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
        if opcode == "equal":
            continue
        trecho_antes = "\n".join(linhas_antes[i1:i2]).strip()
        trecho_depois = "\n".join(linhas_depois[j1:j2]).strip()
        if not trecho_antes and not trecho_depois:
            continue
        if len(textos) >= MAX_TRECHOS_COMPARACAO:
            break
        if trecho_antes:
            rotulo = _rotulo_do_trecho(linhas_antes, i1 - 1)
        else:
            rotulo = _rotulo_do_trecho(linhas_depois, j1 - 1)
        textos.append(
            {
                "parte": rotulo,
                "antes": trecho_antes[:MAX_TRECHO_CHARS],
                "depois": trecho_depois[:MAX_TRECHO_CHARS],
            }
        )
    return textos


def has_pending_document() -> bool:
    return _pending_document is not None


def consumir_pendencia_do_turno() -> bool:
    """Diz se a confirmação de campos foi pedida no turno atual e reseta o sinal.

    A web usa isso para mostrar os botões 'Preencher automaticamente' /
    'Informar campos' apenas na mensagem em que eles foram propostos — não em
    todas as respostas seguintes."""
    global _pendencia_mudou_no_turno
    mudou = _pendencia_mudou_no_turno
    _pendencia_mudou_no_turno = False
    return mudou


def cancelar_pendencia():
    """Descarta o pedido de documento em andamento (usuario desistiu)."""
    global _pending_document, _pendencia_mudou_no_turno
    _pending_document = None
    _pendencia_mudou_no_turno = False


def ultima_minuta() -> dict | None:
    """Devolve a estrutura da ultima minuta gerada (para renderizacao web)."""
    return _ultima_minuta


def consumir_geracao_do_turno() -> bool:
    """Diz se um documento foi gerado no turno atual e reseta o sinal.

    A web usa isso para anexar o cartão de download apenas na mensagem em que
    o arquivo foi realmente produzido — não em todas as respostas seguintes."""
    global _gerada_no_turno
    gerou = _gerada_no_turno
    _gerada_no_turno = False
    return gerou


def consumir_melhoria_do_turno() -> bool:
    """Diz se uma melhoria de documento foi feita no turno atual e reseta o
    sinal (analoga a consumir_geracao_do_turno, para o fluxo antes/depois)."""
    global _melhoria_no_turno
    fez = _melhoria_no_turno
    _melhoria_no_turno = False
    return fez


def ultima_comparacao() -> dict | None:
    """Devolve os dados da comparacao antes/depois da ultima melhoria."""
    return _ultima_comparacao


def _is_generation_confirmation(request: str) -> bool:
    normalized = request.casefold().strip()
    phrases = (
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
        "pode usar o banco",
    )
    return any(
        normalized == phrase or normalized.startswith(f"{phrase} ")
        or normalized.endswith(f" {phrase}")
        or f" {phrase} " in f" {normalized} "
        for phrase in phrases
    )


# Palavras que indicam intencao de gerar um ATO normativo (minuta oficial).
_RE_INTENCAO_NORMATIVA = re.compile(
    r"(portaria|instru[çc][aã]o\s+normativa|decreto|retifica[çc][aã]o|"
    r"ato\s+normativo|minuta)",
    re.IGNORECASE,
)

# Conteudo que NAO e um ato normativo (a tool so monta minutas oficiais).
_RE_CONTEUDO_NAO_NORMATIVO = re.compile(
    r"(tabela|resumo|relat[óo]rio|planilha|lista|s[íi]ntese|ata|of[íi]cio|"
    r"convite|gr[áa]fico|certid[aã]o|memorando)",
    re.IGNORECASE,
)


def _intencao_normativa(request: str) -> bool:
    """Diz se o pedido aponta para gerar um ato normativo (e nao falar de um
    documento generico como tabela de resumo, relatorio ou ata).

    A tool so gera minutas oficiais a partir dos templates DOCX da SEJUS;
    pedidos de DOCX/PDF genericos sao recusados com status ``nao_normativo``
    para que o agente nao invente documentos que nao sabe montar."""
    texto = request.casefold()
    if not _RE_INTENCAO_NORMATIVA.search(texto):
        return False
    return not _RE_CONTEUDO_NAO_NORMATIVO.search(texto)


def _resolver_perfil(request: str, template_name: str | None) -> modelos.PerfilModelo:
    if _modelo_usuario:
        referencia = _modelo_usuario
        alvos = {
            "modelo_usuario",
            referencia["filename"].casefold(),
            referencia["perfil"].name.casefold(),
        }
        if not template_name or template_name.casefold() in alvos:
            return referencia["perfil"]

    if template_name:
        perfil = modelos.buscar_perfil(template_name)
        if perfil is None:
            raise ValueError(f"Modelo '{template_name}' não encontrado.")
        return perfil
    tipo = modelos.detectar_tipo_ato(request)
    return modelos.selecionar_modelo(tipo)


def _gerar_e_relatar(request, perfil, contexto, values):
    global _ultima_minuta, _gerada_no_turno
    tipo = perfil.act_types[0]
    modelo_referencia = (_modelo_usuario or {}).get("texto")
    estrutura = minuta_generation.gerar_estrutura_minuta(
        request,
        tipo,
        perfil,
        contexto,
        values,
        modelo_referencia=modelo_referencia,
    )
    output_path = docx_builder.montar_docx(perfil, estrutura, OUTPUTS_DIR)
    _ultima_minuta = {
        "estructura": estrutura,
        "modelo": perfil.name,
        "output_path": str(output_path),
        "modelo_usuario": (_modelo_usuario or {}).get("filename"),
    }
    _gerada_no_turno = True
    return json.dumps(
        {
            "status": "generated",
            "request": request,
            "modelo": perfil.name,
            "output_path": str(output_path),
            "estructura": estrutura,
            "sources": _source_summary(contexto),
            "review_required": True,
            "auto_filled": True,
            "modelo_usuario": (_modelo_usuario or {}).get("filename"),
        },
        ensure_ascii=False,
    )


def gerar_documento_normativo(
    request: str,
    template_name: str | None = None,
    values: dict[str, str] | None = None,
) -> str:
    """Seleciona o modelo, recupera contexto e gera/encaminha a minuta."""
    global _pending_document, _pendencia_mudou_no_turno

    if len(request or "") > MAX_REQUEST_CHARS:
        return json.dumps(
            {
                "status": "error",
                "error": (
                    f"O pedido é muito grande "
                    f"(máximo de {MAX_REQUEST_CHARS} caracteres)."
                ),
            },
            ensure_ascii=False,
        )

    # Correção de um documento enviado NÃO é geração de ato novo: sinaliza
    # para o agente encaminhar a melhoria do documento com os apontamentos.
    # Vale também sem análise registrada (arquivo importado) — nunca entregar
    # a "correção" como um ato novo criado a partir de template.
    if pedido_de_correcao(request):
        analise = analise_para_correcao()
        filename = (
            analise["filename"]
            if analise
            else arquivo_para_correcao_sem_analise(request)
        )
        if analise or filename:
            return json.dumps(
                {
                    "status": "melhoria_necessaria",
                    "filename": filename,
                    "apontamentos": (analise or {}).get("apontamentos") or [],
                    "message": (
                        "O pedido é uma correção do documento enviado. "
                        "Use melhorar_documento_usuario com este arquivo e os "
                        "apontamentos da análise — não gere um ato novo."
                    ),
                },
                ensure_ascii=False,
            )

    try:
        if not values and _pending_document and _is_generation_confirmation(request):
            pendente = _pending_document
            # A confirmação pode estar completando a CORREÇÃO de um documento
            # analisado (ex.: o LLM criou a pendência com "gere a IN sobre..."
            # e o usuário respondeu "sim"). Nesse caso NÃO completa um ato novo
            # a partir de template: devolve o mesmo despacho de
            # "melhoria_necessaria" que o loop do agente reencaminha.
            despacho = _pendencia_de_correcao(pendente)
            _pending_document = None
            if despacho:
                return json.dumps(
                    {
                        "status": "melhoria_necessaria",
                        "filename": despacho["filename"],
                        "apontamentos": despacho["apontamentos"],
                        "message": (
                            "A confirmação completa a correção de um documento "
                            "já analisado. Use melhorar_documento_usuario com "
                            "este arquivo e os apontamentos da análise — não "
                            "gere um ato novo."
                        ),
                    },
                    ensure_ascii=False,
                )
            return _gerar_e_relatar(
                pendente["request"],
                pendente["perfil"],
                pendente["contexto"],
                values,
            )

        if not _intencao_normativa(request):
            return json.dumps(
                {
                    "status": "nao_normativo",
                    "error": (
                        "O pedido não é de um ato normativo. Esta ferramenta "
                        "só gera minutas oficiais (portaria, instrução "
                        "normativa, portaria conjunta, decreto, retificação) — "
                        "não documentos genéricos como tabelas de resumo, "
                        "relatórios ou atas."
                    ),
                },
                ensure_ascii=False,
            )

        perfil = _resolver_perfil(request, template_name)
        contexto = _context_for_request(request, perfil)

        if not values:
            _pending_document = {
                "request": request,
                "perfil": perfil,
                "contexto": contexto,
            }
            _pendencia_mudou_no_turno = True
            return json.dumps(
                {
                    "status": "awaiting_confirmation",
                    "modelo": perfil.name,
                    "modelo_usuario": (_modelo_usuario or {}).get("filename"),
                    "template": perfil.file,
                    "available_models": [m.name for m in modelos.MODELOS],
                    "campos": CAMPOS_BASE,
                    "contexto": _source_summary(contexto),
                    "message": (
                        "Deseja informar os campos deste ato (número, data, "
                        "signatário, cargo etc.) ou prefere que eu preencha "
                        "automaticamente com dados plausíveis para revisão? "
                        "Responda 'informar campos' com os dados, ou 'pode "
                        "inventar' / 'gere o arquivo' para gerar agora."
                    ),
                },
                ensure_ascii=False,
            )

        resultado = _gerar_e_relatar(request, perfil, contexto, values)
        _pending_document = None
        return resultado

    except (ValueError, OSError) as error:
        return json.dumps(
            {"status": "error", "error": str(error)}, ensure_ascii=False
        )
    except Exception as error:  # noqa: BLE001 - falha vira resultado de tool
        return json.dumps(
            {
                "status": "error",
                "error": "Falha ao gerar o documento normativo.",
                "detail": str(error),
            },
            ensure_ascii=False,
        )


# ---------------------------------------------------------------------------
# Melhoria e adequacao de um documento enviado pelo usuario (antes/depois)
# ---------------------------------------------------------------------------

melhoria_definition = {
    "type": "function",
    "function": {
        "name": "melhorar_documento_usuario",
        "description": (
            "Reescreve um arquivo enviado pelo usuario (na pasta "
            "importacoes_usuario/) com melhorias e adequacoes juridicas, "
            "consultando o acervo normativo no RAG, e gera uma copia "
            "melhorada em outputs/. O documento continua o mesmo ato "
            "(numero, ementa, objeto e assinaturas preservados). Se o "
            "arquivo for .docx, o layout dele e usado como formatacao; "
            "pdf/txt/md usam o template oficial do tipo de ato. Use quando "
            "o usuario pedir para melhorar, adequejar, atualizar, revisar e "
            "comparar um arquivo que ele enviou (antes/depois)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": (
                        "Nome do arquivo dentro da pasta importacoes_usuario/ "
                        "(ex.: 'minuta_contrato.pdf'). OPCIONAL: se o usuario "
                        "acabou de importar/enviar o arquivo e nao informou o "
                        "nome, NÃO preencha — a tool usa a importacao mais "
                        "recente e lista as alternativas."
                    ),
                },
                "diretrizes": {
                    "type": "string",
                    "description": (
                        "Opcional. Diretrizes/pedido do usuario para a "
                        "melhoria (ex.: 'mantenha a mesma numeracao dos "
                        "artigos', 'adeque ao novo organograma')."
                    ),
                },
                "apontamentos": {
                    "type": "string",
                    "description": (
                        "Opcional. Apontamentos de uma analise anterior deste "
                        "documento, quando o usuario pedir para corrigir o "
                        "arquivo apos uma analise. Se omitido, a tool usa "
                        "automaticamente a analise registrada para o arquivo."
                    ),
                },
            },
        },
    },
}


comparacao_definition = {
    "type": "function",
    "function": {
        "name": "obter_textos_comparacao",
        "description": (
            "Recupera os dados da ultima melhoria antes/depois ja feita na "
            "conversa: textos REAIS alterados (campos 'antes' e 'depois'), a "
            "lista de correcoes, as adicoes estruturais propostas (artigos "
            "novos, com posicao e lastro), as lacunas sem precedente e o nome "
            "do arquivo original. Use somente em "
            "perguntas de acompanhamento sobre uma melhoria ja feita (ex.: "
            "'o que exatamente foi alterado', 'mostre antes e depois', 'mostre "
            "os textos alterados', 'monte uma tabela do antes/depois'). NAO gere "
            "nem reescreva o arquivo nessas situacoes — apenas copie os textos "
            "reais devolvidos por esta ferramenta."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


# ---------------------------------------------------------------------------
# Trava de precedente: lacuna so vira artigo novo se houver ato analogo no
# acervo. o 'chaves' ancora o tema num trecho recuperado -- sem a palavra-
# chave no texto, o score da busca nao basta para gerar artigo.
# ---------------------------------------------------------------------------

PRECEDENTE_MIN_SCORE = 0.45
PRECEDENTE_TOP = 6
CONTEXTO_MELHORIA_MAX = 24

LACUNAS_ESTRUTURAIS = [
    {
        "tema": "recurso_administrativo",
        "rotulo": "Recurso administrativo / reconsideração",
        "query": (
            "recurso administrativo pedido de reconsideração contra decisão "
            "fundamentada de autoridade que vedar negar técnica material"
        ),
        "chaves": re.compile(r"recurso|reconsidera", re.IGNORECASE),
    },
    {
        "tema": "prazo_validade",
        "rotulo": "Prazo de validade e renovação",
        "query": (
            "prazo de validade da autorização registro suspensão renovação cadastro"
        ),
        "chaves": re.compile(r"validade|renova|revalid", re.IGNORECASE),
    },
    {
        "tema": "prestacao_contas",
        "rotulo": "Prestação de contas e fiscalização",
        "query": (
            "prestação de contas fiscalização obrigações do fiscal insumo "
            "fornecido recurso público"
        ),
        "chaves": re.compile(r"presta[çc][aã]o|fiscaliz", re.IGNORECASE),
    },
    {
        "tema": "revogacao",
        "rotulo": "Revogação de norma anterior",
        "query": (
            "revogação de disposições em contrário normas anteriores sobre o mesmo objeto"
        ),
        "chaves": re.compile(r"revog", re.IGNORECASE),
    },
    {
        "tema": "seguranca_epi",
        "rotulo": "Segurança do trabalho / EPI",
        "query": (
            "equipamento de proteção individual segurança do trabalho atividade de risco"
        ),
        "chaves": re.compile(
            r"prote[çc][aã]o individual|\bepi\b|equipamento de prote",
            re.IGNORECASE,
        ),
    },
    {
        "tema": "publicacao_vigencia",
        "rotulo": "Publicação e vigência",
        "query": "entrada em vigor publicação diário oficial regime de vigência",
        "chaves": re.compile(
            r"entra em vigor|publique|di[áa]rio oficial|vig[êe]ncia", re.IGNORECASE
        ),
    },
]

_SIMPLES_TEXTO = re.compile(r"\s+")

_STOP = {"de", "da", "do", "das", "dos", "em", "no", "na", "a", "o", "e"}


def _tema_por_nome(nome: str) -> str | None:
    """Mapeia o nome de tema escrito pelo modelo para a chave canônica."""
    texto = _SIMPLES_TEXTO.sub(
        " ", (nome or "").strip().casefold().replace("_", " ").replace("-", " ")
    )
    if not texto:
        return None
    palavras = set(texto.split())
    for lacuna in LACUNAS_ESTRUTURAIS:
        chave = lacuna["tema"].replace("_", " ")
        if texto == chave:
            return lacuna["tema"]
        nucleo = {p for p in chave.split() if p not in _STOP}
        if nucleo and nucleo <= palavras:
            return lacuna["tema"]
        if chave in texto or texto in chave:
            return lacuna["tema"]
    return None


def _precedente_das_lacunas(conteudo: str, tipo_ato: str) -> dict:
    """Avalia, por lacuna, se o acervo tem precedente analogo.

    Devolve por tema um dict com ``rotulo``, ``tem_precedente`` e ``chunks``
    (trechos de suporte com a palavra-chave e score acima do limiar). Usa
    somente a leitura do indice Qdrant (nao chama LLM)."""
    resultado = {}
    for lacuna in LACUNAS_ESTRUTURAIS:
        chunks = retrieve(lacuna["query"], limit=PRECEDENTE_TOP)
        suporte = [
            {**c, "tema": lacuna["tema"]}
            for c in chunks
            if c.get("score", 0) >= PRECEDENTE_MIN_SCORE
            and lacuna["chaves"].search(c.get("text") or "")
        ]
        resultado[lacuna["tema"]] = {
            "rotulo": lacuna["rotulo"],
            "tem_precedente": bool(suporte),
            "chunks": suporte,
        }
    return resultado


def _assunto_do_documento(conteudo: str) -> str:
    """Pequena base textual do documento (ementa) para a query de assunto."""
    for linha in (conteudo or "").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        if linha.casefold().startswith("disp"):
            return linha[:300]
        return linha[:300]
    return ""


def _contexto_para_melhoria(
    conteudo: str,
    tipo_ato: str,
    filename: str,
    precedente: dict,
) -> list[dict]:
    """Monta o contexto RAG da melhoria: temas com precedente + assunto.

    Consulta o acervo pelo assunto do documento e reaproveita os chunks de
    suporte dos temas aprovados pela trava (ja rotulados por tema), sem
    duplicar fontes (dedupe por ``source_file``). Os chunks rotulados entram
    primeiro: quando a mesma fonte aparece no assunto, a etiqueta de tema
    precisa prevalecer para embasar a adicao."""
    chunks: list[dict] = []
    vistos: set[str] = set()

    for info in precedente.values():
        if not info.get("tem_precedente"):
            continue
        for chunk in info.get("chunks", []):
            fonte = chunk.get("source_file")
            if fonte in vistos:
                continue
            vistos.add(fonte)
            chunks.append(chunk)
            if len(chunks) >= CONTEXTO_MELHORIA_MAX:
                return chunks

    assunto = _assunto_do_documento(conteudo)
    if assunto:
        for chunk in retrieve(
            f"{assunto}\nTipo de ato: {tipo_ato}", limit=8
        ):
            fonte = chunk.get("source_file")
            if fonte in vistos:
                continue
            vistos.add(fonte)
            chunks.append(chunk)
            if len(chunks) >= CONTEXTO_MELHORIA_MAX:
                return chunks
    return chunks


def _filtrar_lacunas_sem_precedente(lacunas_do_modelo: list[dict], precedente: dict) -> list[dict]:
    """Mantem apenas as lacunas que o modelo viu como pertinentes e que nao
    passaram na trava de precedente (mostradas no relatorio como informacao)."""
    sem = []
    for lacuna in lacunas_do_modelo:
        tema = _tema_por_nome(lacuna.get("tema"))
        if tema and not precedente.get(tema, {}).get("tem_precedente"):
            sem.append(
                {
                    "tema": tema,
                    "detalhe": (lacuna.get("detalhe") or "").strip()[:400],
                }
            )
    return sem


def obter_textos_comparacao() -> str:
    """Devolve os textos reais (antes/depois) da ultima melhoria da conversa.

    Permite ao agente responder perguntas de acompanhamento ('o que mudou',
    'mostre os textos alterados', tabelas antes/depois) com fidelidade, sem
    precisar re-executar a melhoria nem inventar trechos."""
    dados = _ultima_comparacao
    if not dados:
        return json.dumps(
            {
                "status": "sem_comparacao",
                "detail": (
                    "Nenhuma melhoria antes/depois foi feita ainda nesta "
                    "conversa. Envie um arquivo pelo botão ✨ para eu comparar."
                ),
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "status": "ok",
            "arquivo_original": dados.get("arquivo_original"),
            "alteracoes": dados.get("alteracoes") or [],
            "adicoes_estruturais": dados.get("adicoes_estruturais") or [],
            "lacunas": dados.get("lacunas") or [],
            "textos": dados.get("textos") or [],
            "antes": (dados.get("antes") or "")[:12_000],
            "depois": (dados.get("depois") or "")[:12_000],
        },
        ensure_ascii=False,
    )


def _numero_base_rotulo(rotulo: str) -> str | None:
    """Extrai o número-base do rótulo de um artigo ('Art. 6º-A' -> '6')."""
    m = re.match(r"^\s*art\.?\s*(\d+)", rotulo or "", re.IGNORECASE)
    return m.group(1) if m else None


def _indice_insercao_artigo(corpo: list, rotulo: str, posicao: str) -> int:
    """Índice do `corpo` onde inserir um artigo novo.

    Primeiro tenta a âncora pelo número-base do rótulo (sufixo LC 95/1998:
    'Art. 6º-A' vai logo após o último 'Art. 6º'). Sem âncora, tenta o número
    citado em ``posicao``; se nada casar, anexa ao fim do corpo.
    """
    base = _numero_base_rotulo(rotulo)
    if base:
        for i in range(len(corpo) - 1, -1, -1):
            item = corpo[i]
            if (item.get("tipo") or "artigo") == "capitulo":
                continue
            if _numero_base_rotulo(item.get("rotulo") or "") == base:
                return i + 1
    for num in re.findall(r"\d+", posicao or ""):
        for i in range(len(corpo) - 1, -1, -1):
            item = corpo[i]
            if (item.get("tipo") or "artigo") == "capitulo":
                continue
            if _numero_base_rotulo(item.get("rotulo") or "") == num:
                return i + 1
    return len(corpo)


def _integrar_adicoes_estruturais(estrutura: dict, adicoes: list) -> set[str]:
    """Mescla ``adicoes_estruturais`` dentro de ``estrutura['corpo']``.

    Para cada adição com rótulo e texto, insere um artigo novo no corpo na
    posição correta (após o artigo-base; fallback segue o número citado em
    ``posicao``; senão, no fim). Adições cujo rótulo já existe no corpo (o
    modelo já as incluiu) ou sem texto completo são ignoradas. Devolve os
    rótulos normalizados efetivamente inseridos.
    """
    inseridos: set[str] = set()
    corpo = estrutura.setdefault("corpo", [])
    presentes = {
        docx_builder._chave_rotulo(item.get("rotulo") or "")
        for item in corpo
        if (item.get("tipo") or "artigo") != "capitulo"
        and (item.get("rotulo") or "").strip()
    }
    for a in adicoes:
        if not isinstance(a, dict):
            continue
        rotulo = (a.get("o_que") or "").strip()
        if not rotulo:
            continue
        chave = docx_builder._chave_rotulo(rotulo)
        if chave in presentes:
            continue
        texto = (a.get("texto") or "").strip()
        if not texto:
            continue
        artigo = {
            "tipo": "artigo",
            "rotulo": rotulo,
            "texto": texto,
            "subitens": [],
        }
        corpo.insert(_indice_insercao_artigo(corpo, rotulo, a.get("posicao") or ""), artigo)
        presentes.add(chave)
        inseridos.add(chave)
    return inseridos


def _descartar_itens_por_rotulo(
    alteracoes: list[dict],
    remocoes: list[dict],
    adicoes: list[dict],
    rotulos: set[str],
    descartados: list[str],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Remove itens cujo rótulo está no conjunto; rótulos removidos entram em
    ``descartados`` para constar no relatório da entrega."""
    descartados_novos = list(descartados)

    def _filtrar(grupo: list[dict], chave_main: str, chave_alt: str | None):
        novos: list[dict] = []
        for a in grupo:
            label = a.get(chave_main) or (a.get(chave_alt) if chave_alt else None) or "?"
            if label in rotulos:
                descartados_novos.append(label)
            else:
                novos.append(a)
        return novos

    alteracoes = _filtrar(alteracoes, "rotulo", "o_que")
    remocoes = _filtrar(remocoes, "rotulo", None)
    adicoes = _filtrar(adicoes, "o_que", "rotulo")
    return alteracoes, remocoes, adicoes


class _SemSaidaDeValidacao(Exception):
    """Validação pós-geração sem saída válida após descartar responsáveis.

    Faz a entrega recair na cópia intacta (fallback), nao em erro — o arquivo
    sempre deve ser entregue."""


def _melhorar_e_relatar(
    filename: str,
    destino,
    conteudo: str,
    perfil: modelos.PerfilModelo,
    tipo_ato: str,
    contexto: list[dict],
    diretrizes: str | None,
    outros: list[str] | None = None,
    precedente: dict | None = None,
    apontamentos: list[dict] | None = None,
    analise_completa: str | None = None,
) -> str:
    global _ultima_minuta, _melhoria_no_turno, _ultima_comparacao, _gerada_no_turno
    # Passagens de ANÁLISE (distintas das pós-geração): ortografia, estrutura,
    # clareza, fundamentação e conferência dos achados — cada uma registra a
    # chamada, o resultado e a duração. A conferência valida cada achado contra
    # o ORIGINAL (trecho/localização verificáveis) e descarta o que o contradiz
    # ANTES da melhoria; só os achados confirmados vão ao patch, ao painel de
    # cobertura e à lista de tarefas (elogios/duplicatas ficam de fora).
    resultado_analise = analise_validacao.rodar_passagens_analise(
        conteudo, apontamentos or []
    )
    passagens_analise = resultado_analise["passagens"]
    achados_descartados = resultado_analise["achados_descartados"]
    apontamentos = resultado_analise["apontamentos_validos"] or None
    valores: dict = {}
    if diretrizes:
        valores["diretrizes"] = diretrizes
    if apontamentos:
        valores["apontamentos"] = apontamentos
    if analise_completa:
        valores["analise_completa"] = analise_completa
    valores = valores or None
    # Uma falha de geração/validação do patch NÃO pode barrar a entrega: cai no
    # fallback (cópia intacta) com os apontamentos marcados como falha.
    try:
        estrutura, alteracoes, remocoes, adicoes, lacunas = (
            document_improvement.gerar_estrutura_melhoria(
                conteudo,
                tipo_ato,
                perfil,
                contexto,
                valores,
            )
        )
        # Cobertura dos apontamentos da análise (validada contra o patch efetivo).
        cobertura = estrutura.pop("_cobertura_analise", None) or []
        descartados = estrutura.pop("_descartados", None) or []
        # Marca as mudanças que vieram de um apontamento aprovado da análise,
        # para o comentário do .docx indicar a origem quando não houver lastro.
        document_improvement._marcar_origem_apontamento(
            alteracoes, remocoes, adicoes, cobertura
        )
    except Exception:  # noqa: BLE001 - entrega a cópia intacta em vez de falhar
        traceback.print_exc()
        estrutura = document_improvement._estruturar_original(conteudo)
        alteracoes, remocoes, adicoes, lacunas, descartados = [], [], [], [], []
        cobertura = document_improvement._validar_cobertura(
            conteudo, [], [], [], apontamentos or [], []
        )
    # Identifica o documento especifico do RAG referenciado pelo 'lastro' de
    # cada mudanca e sinaliza divergencias no relatorio (sem bloquear); em
    # seguida, verifica a coerencia TEMATICA (item 3): lastro de outro assunto
    # marca 'requer_decisao_juridica' nas alteracoes/adicoes.
    for grupo in (alteracoes, remocoes, adicoes):
        document_improvement._validar_lastros(grupo, contexto)
    document_improvement._checar_coerencia_lastros(alteracoes, remocoes, adicoes, contexto)
    # Rede de seguranca: o filtro do patch ja removeu itens que duplicariam
    # texto; se ainda houver, NAO aplica o patch invalido (entrega a copia).
    if document_improvement._duplicacoes_do_patch(
        conteudo, alteracoes, remocoes, adicoes
    ):
        descartados = list(descartados) + [
            a.get("rotulo") or a.get("o_que") or "?"
            for grupo in (alteracoes, remocoes, adicoes)
            for a in grupo
        ]
        alteracoes, remocoes, adicoes = [], [], []
        cobertura = document_improvement._validar_cobertura(
            conteudo, [], [], [], apontamentos or [], []
        )
    # Persistir propostas com estado pendente
    doc_hash = _hash_documento(filename)
    for a in alteracoes:
        if a.get("estado") == ESTADO_PENDENTE and a.get("rotulo") and a.get("localizacao"):
            procurar_proposta(doc_hash, a["rotulo"], a.get("versao", ""), a["localizacao"])
            # Garante que a proposta está salva com estado pendente
            chave = proposicao_id_para_chave(doc_hash, a["rotulo"], a.get("versao", ""), a["localizacao"])
            if chave not in _propostas_cache:
                proposta_texto = minuta_para_texto(estrutura) if minuta_para_texto else ""
                nova_proposta = proposicao_para_dict(
                    chave, doc_hash, a["rotulo"], a.get("versao", ""), a["localizacao"],
                    ESTADO_PENDENTE, proposta_texto,
                    justificativa=f"Melhoria estrutural - {a.get('rotulo')}",
                    fonte="llm_mejora")
                _propostas_cache[chave] = nova_proposta
    _salvar_propostas_disc(_propostas_cache)
    insercoes = {
        docx_builder._chave_rotulo(a.get("o_que") or "")
        for a in adicoes
        if a.get("o_que")
    }
    # Adições estruturais viram artigos de verdade no corpo (posição correta),
    # tanto para o arquivo quanto para a prévia antes/depois.
    insercoes |= _integrar_adicoes_estruturais(estrutura, adicoes)
    # Entrega SEMPRE um arquivo: com correções, parcial ou a cópia intacta.
    sem_correcao = not (alteracoes or remocoes or adicoes)
    fallback = False
    is_docx = destino.suffix.lower() == ".docx"
    passagens: list[dict] | None = None

    def _copia_intacta():
        if is_docx:
            return docx_builder.copiar_docx(perfil, OUTPUTS_DIR)
        return docx_builder.montar_docx(perfil, estrutura, OUTPUTS_DIR)

    if sem_correcao:
        output_path = _copia_intacta()
        fallback = True
        passagens = docx_validacao.validar_docx_gerado(
            conteudo, output_path, [], [], [], aplicado=False
        )
    else:
        try:
            if is_docx:
                # Cópia do DOCX original com as mudanças marcadas
                # (tachado/verde). Parágrafos não citados permanecem intactos.
                output_path = docx_builder.montar_docx_revisado(
                    perfil, alteracoes, remocoes, adicoes, OUTPUTS_DIR
                )
                # Validação pós-geração (5 passagens) do arquivo recém-feito.
                # Passagem com falha descarta os itens RESPONSÁVEIS e remonta
                # (aplicação parcial); sem responsáveis identificados ou após
                # algumas tentativas, recai na cópia intacta.
                tentativas = 0
                while tentativas < 3:
                    passagens = docx_validacao.validar_docx_gerado(
                        conteudo, output_path, alteracoes, remocoes, adicoes
                    )
                    if all(p.get("ok") for p in passagens):
                        break
                    rotulos = docx_validacao.responsaveis(passagens)
                    if not rotulos or not (alteracoes or remocoes or adicoes):
                        break
                    alteracoes, remocoes, adicoes = _descartar_itens_por_rotulo(
                        alteracoes, remocoes, adicoes, set(rotulos), descartados
                    )
                    if not (alteracoes or remocoes or adicoes):
                        break
                    cobertura = document_improvement._validar_cobertura(
                        conteudo, alteracoes, remocoes, adicoes,
                        apontamentos or [], descartados,
                    )
                    output_path = docx_builder.montar_docx_revisado(
                        perfil, alteracoes, remocoes, adicoes, OUTPUTS_DIR
                    )
                    tentativas += 1
                if passagens and not all(p.get("ok") for p in passagens):
                    passagens = None
                    raise _SemSaidaDeValidacao()
            else:
                output_path = docx_builder.montar_docx(
                    perfil, estrutura, OUTPUTS_DIR, insercoes_rastreadas=insercoes
                )
        except _SemSaidaDeValidacao:
            # Validação sem saída: entrega a cópia intacta (fallback), nunca um
            # arquivo com alterações não confirmadas.
            output_path = _copia_intacta()
            fallback = True
            descartados = list(descartados) + [
                a.get("rotulo") or a.get("o_que") or "?"
                for grupo in (alteracoes, remocoes, adicoes)
                for a in grupo
            ]
            alteracoes, remocoes, adicoes = [], [], []
            cobertura = document_improvement._validar_cobertura(
                conteudo, [], [], [], apontamentos or [], []
            )
            passagens = docx_validacao.validar_docx_gerado(
                conteudo, output_path, [], [], [], aplicado=False
            )
        except Exception:  # noqa: BLE001 - falha de montagem não pode barrar a entrega
            traceback.print_exc()
            output_path = _copia_intacta()
            fallback = True
            descartados = list(descartados) + [
                a.get("rotulo") or a.get("o_que") or "?"
                for grupo in (alteracoes, remocoes, adicoes)
                for a in grupo
            ]
            alteracoes, remocoes, adicoes = [], [], []
            cobertura = document_improvement._validar_cobertura(
                conteudo, [], [], [], apontamentos or [], []
            )
            passagens = docx_validacao.validar_docx_gerado(
                conteudo, output_path, [], [], [], aplicado=False
            )
    depois = minuta_para_texto(estrutura)
    textos = _textos_antes_depois(conteudo, depois)
    lacunas_sem = _filtrar_lacunas_sem_precedente(lacunas, precedente or {})
    _ultima_minuta = {
        "estructura": estrutura,
        "modelo": perfil.name,
        "output_path": str(output_path),
        "modelo_usuario": (_modelo_usuario or {}).get("filename"),
    }
    _melhoria_no_turno = True
    _gerada_no_turno = True
    _ultima_comparacao = {
        "arquivo_original": filename,
        "antes": conteudo[:40_000],
        "depois": depois[:40_000],
        "alteracoes": alteracoes,
        "adicoes_estruturais": adicoes,
        "lacunas": lacunas_sem,
        "textos": textos,
        "apontamentos_analise": cobertura,
        "fallback": fallback,
        "descartados": descartados,
        "passagens": passagens,
        "passagens_analise": passagens_analise,
        "achados_descartados": achados_descartados,
        "sha1": hashlib.sha1(conteudo.encode("utf-8", "ignore")).hexdigest(),
    }
    resposta = {
        "status": "improved",
        "filename": filename,
        "modelo": perfil.name,
        "output_path": str(output_path),
        "alteracoes": alteracoes,
        "remocoes": remocoes,
        "adicoes_estruturais": adicoes,
        "lacunas": lacunas_sem,
        "textos": textos,
        "apontamentos_analise": cobertura,
        "fallback": fallback,
        "descartados": descartados,
        "passagens": passagens,
        "passagens_analise": passagens_analise,
        "achados_descartados": achados_descartados,
        "outros": outros or [],
        "sources": _source_summary(contexto),
    }
    if fallback and sem_correcao:
        resposta["mensagem"] = (
            "Não foi possível aplicar as correções. "
            "Este arquivo preserva o conteúdo original."
        )
    avisos: list[str] = []
    nao_aplicados = [
        c
        for c in cobertura
        if c.get("status") in ("nao_aplicado", "falhou", "pendente")
    ]
    if apontamentos and nao_aplicados:
        avisos.append(
            f"{len(nao_aplicados)} apontamento(s) da análise não foram "
            "aplicados ao documento — confira o status e o motivo na cobertura "
            "dos apontamentos."
        )
    if descartados:
        avisos.append(
            "Mudanças inválidas foram descartadas para não corromper o "
            "documento: " + ", ".join(dict.fromkeys(descartados)) + "."
        )
    if fallback:
        avisos.append(
            "Não foi possível aplicar as correções; o arquivo entregue é a "
            "cópia do documento original."
        )
    if document_improvement._problemas_do_patch(conteudo, alteracoes, remocoes):
        avisos.append(
            "O arquivo foi gerado e entregue, mas o patch de melhoria ficou com "
            "itens sem âncora no documento original (alguns trechos podem não "
            "ter sido alterados como pedido). Revise o arquivo gerado."
        )
    lastros_divergentes = [
        a
        for grupo in (alteracoes, remocoes, adicoes)
        for a in grupo
        if (
            (a.get("lastro") or "").strip()
            and a.get("lastro_validado") is not True
            and a.get("origem") != document_improvement.ORIGEM_ANALISE_APROVADA
        )
    ]
    if lastros_divergentes:
        labels = []
        for a in lastros_divergentes:
            labels.append(a.get("o_que") or a.get("rotulo") or "?")
        avisos.append(
            "Nenhum ato recuperado no acervo foi identificado para o 'lastro' "
            "de " + ", ".join(labels) + " — a referência pode ter sido "
            "inventada. Revise antes de incluir."
        )
    incoerentes = [
        a
        for grupo in (alteracoes, adicoes)
        for a in grupo
        if a.get("coerencia_aviso")
        and a.get("origem") != document_improvement.ORIGEM_ANALISE_APROVADA
    ]
    if incoerentes:
        labels = ", ".join(
            a.get("o_que") or a.get("rotulo") or "?" for a in incoerentes
        )
        avisos.append(
            "O 'lastro' de " + labels + " trata de tema diferente do "
            "dispositivo tocado pela mudança — marcado como pendente de "
            "decisão jurídica no arquivo gerado."
        )
    if avisos:
        resposta["aviso"] = " ".join(avisos)
    return json.dumps(resposta, ensure_ascii=False)


def melhorar_documento_usuario(
    filename: str | None = None,
    diretrizes: str | None = None,
    apontamentos: list[dict] | str | None = None,
) -> str:
    """Reescreve um arquivo enviado com melhorias e devolve o resultado (JSON).

    Se ``filename`` não for informado, usa a importação mais recente da pasta
    ``importacoes_usuario/`` (útil quando o usuário acabou de enviar o arquivo
    e não sabe o nome). O resultado inclui a lista de outros arquivos
    disponíveis para o agente sugerir alternativas.

    Quando existir uma análise registrada para o mesmo arquivo e a mesma versão,
    os apontamentos acionáveis dessa análise são injetados automaticamente (o
    parâmetro ``apontamentos`` só sobrescreve se informado).
    """
    if isinstance(apontamentos, str):
        diretrizes = (
            f"{diretrizes}\n{apontamentos}" if diretrizes else apontamentos
        )
        apontamentos = None

    disponiveis: list[str] = []
    destino: Path | None = None
    conteudo = ""
    perfil: modelos.PerfilModelo | None = None
    try:
        disponiveis = _list_available_files()
        if not filename:
            filename = _ultimo_arquivo_importado()
            if not filename:
                return json.dumps(
                    {
                        "status": "error",
                        "error": (
                            "Nenhum arquivo importado ainda. Envie um documento "
                            "pelos botões da interface (📎 análise / ✨ melhoria)."
                        ),
                        "available_files": [],
                    },
                    ensure_ascii=False,
                )
        destino = _resolve_file(filename)
        conteudo = extract_file_text(destino)
        sha1_conteudo = hashlib.sha1(
            conteudo.encode("utf-8", "ignore")
        ).hexdigest()

        # Análise registrada deste documento/versão: contexto da correção.
        analise = analysis_registry.obter(
            analysis_registry.sessao_atual(), destino.name, sha1_conteudo
        )
        analise_completa = (analise or {}).get("analise_completa") or ""
        if apontamentos is None and analise:
            apontamentos = analise.get("apontamentos") or []

        # Perguntas de acompanhamento sobre a melhoria já feita não devem gerar
        # um arquivo novo: se o mesmo arquivo, com o mesmo conteúdo, for
        # pedido de novo sem novas diretrizes/apontamentos, reaproveita.
        if (
            not diretrizes
            and not apontamentos
            and _ultima_comparacao
            and _ultima_comparacao.get("arquivo_original") == filename
            and _ultima_comparacao.get("sha1") == sha1_conteudo
        ):
            return json.dumps(
                {
                    "status": "already_improved",
                    "arquivo_original": filename,
                    "alteracoes": _ultima_comparacao.get("alteracoes") or [],
                    "adicoes_estruturais": _ultima_comparacao.get(
                        "adicoes_estruturais"
                    )
                    or [],
                    "lacunas": _ultima_comparacao.get("lacunas") or [],
                    "textos": _ultima_comparacao.get("textos") or [],
                    "apontamentos_analise": _ultima_comparacao.get(
                        "apontamentos_analise"
                    )
                    or [],
                    "outros": [f for f in disponiveis if f != filename],
                },
                ensure_ascii=False,
            )

        tipo_ato = modelos.detectar_tipo_ato(conteudo)
        if destino.suffix.lower() == ".docx":
            perfil = modelos.crear_perfil_de_arquivo(
                destino, conteudo, Path(filename).stem
            )
        else:
            perfil = modelos.selecionar_modelo(tipo_ato)

        precedente = _precedente_das_lacunas(conteudo, tipo_ato)
        contexto = _contexto_para_melhoria(conteudo, tipo_ato, filename, precedente)
        return _melhorar_e_relatar(
            filename,
            destino,
            conteudo,
            perfil,
            tipo_ato,
            contexto,
            diretrizes,
            outros=[f for f in disponiveis if f != filename],
            precedente=precedente,
            apontamentos=apontamentos,
            analise_completa=analise_completa,
        )
    except UserFileError as error:
        return json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False)
    except document_improvement.PatchIntegrityError as error:
        return json.dumps(
            {
                "status": "error",
                "error": (
                    "A sugestão de melhoria resultaria em parágrafos duplicados "
                    "no documento"
                ),
                "detail": str(error),
                "hint": (
                    "Revise as alterações que fundem caput com parágrafo/inciso "
                    "e que acrescentam texto novo."
                ),
            },
            ensure_ascii=False,
        )
    except Exception as error:  # noqa: BLE001 - falha inesperada nao vira "limitacao tecnica"
        traceback.print_exc()
        # Falha inesperada NAO pode virar a mensagem genérica de erro técnico:
        # entrega a cópia intacta do original sempre que possível (apontamentos
        # marcados como falha), só devolvendo status de erro se nem a cópia der.
        try:
            cobertura_falha = document_improvement._validar_cobertura(
                conteudo, [], [], [], apontamentos or [], []
            )
        except Exception:  # noqa: BLE001
            cobertura_falha = []
        try:
            saida: Path | None = None
            if (
                perfil is not None
                and destino is not None
                and destino.suffix.lower() == ".docx"
            ):
                saida = docx_builder.copiar_docx(perfil, OUTPUTS_DIR)
            elif destino is not None and destino.is_file():
                OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
                saida = OUTPUTS_DIR / (
                    f"{Path(destino).stem}_{uuid.uuid4().hex[:8]}"
                    f"{Path(destino).suffix}"
                )
                shutil.copyfile(Path(destino), saida)
            if saida is None:
                raise ValueError("origens insuficientes para a copia intacta")
            return json.dumps(
                {
                    "status": "improved",
                    "filename": filename,
                    "output_path": str(saida),
                    "alteracoes": [],
                    "remocoes": [],
                    "adicoes_estruturais": [],
                    "lacunas": [],
                    "apontamentos_analise": cobertura_falha,
                    "textos": [],
                    "fallback": True,
                    "mensagem": (
                        "Não foi possível aplicar as correções; o arquivo "
                        "original foi preservado para você não ficar sem o "
                        "documento."
                    ),
                    "outros": [f for f in disponiveis if f != filename],
                },
                ensure_ascii=False,
            )
        except Exception:  # noqa: BLE001 - ultimo recurso: devolve o erro real
            return json.dumps(
                {
                    "status": "error",
                    "error": "Falha ao melhorar o documento.",
                    "detail": str(error),
                },
                ensure_ascii=False,
            )