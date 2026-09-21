"""Tool de function calling para gerar atos a partir de modelos DOCX reais."""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path

from sejus_project.tools.document_infra import docx_builder, modelos
from sejus_project.tools.document_infra.docx_templates import OUTPUTS_DIR
from sejus_project.tools.llm_tools import document_improvement, minuta_generation
from sejus_project.tools.llm_tools.retrieval import retrieve
from sejus_project.tools.llm_tools.user_files import (
    UserFileError,
    _list_available_files,
    _resolve_file,
    _ultimo_arquivo_importado,
    extract_file_text,
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

    try:
        if not values and _pending_document and _is_generation_confirmation(request):
            pendente = _pending_document
            _pending_document = None
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
) -> str:
    global _ultima_minuta, _melhoria_no_turno, _ultima_comparacao, _gerada_no_turno
    valores = {"diretrizes": diretrizes} if diretrizes else None
    estrutura, alteracoes, remocoes, adicoes, lacunas = document_improvement.gerar_estrutura_melhoria(
        conteudo,
        tipo_ato,
        perfil,
        contexto,
        valores,
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
    if destino.suffix.lower() == ".docx":
        # Nova abordagem: o resultado é uma cópia do DOCX original com as
        # mudanças (patch) marcadas (alterado/removido tachado, adicionado e
        # novo texto em verde). Parágrafos não citados permanecem intactos.
        output_path = docx_builder.montar_docx_revisado(
            perfil,
            alteracoes,
            remocoes,
            adicoes,
            OUTPUTS_DIR,
        )
    else:
        output_path = docx_builder.montar_docx(
            perfil, estrutura, OUTPUTS_DIR, insercoes_rastreadas=insercoes
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
        "outros": outros or [],
        "sources": _source_summary(contexto),
    }
    if document_improvement._problemas_do_patch(conteudo, alteracoes, remocoes):
        resposta["aviso"] = (
            "O arquivo foi gerado e entregue, mas o patch de melhoria ficou com "
            "itens sem âncora no documento original (alguns trechos podem não "
            "ter sido alterados como pedido). Revise o arquivo gerado."
        )
    return json.dumps(resposta, ensure_ascii=False)


def melhorar_documento_usuario(filename: str | None = None, diretrizes: str | None = None) -> str:
    """Reescreve um arquivo enviado com melhorias e devolve o resultado (JSON).

    Se ``filename`` não for informado, usa a importação mais recente da pasta
    ``importacoes_usuario/`` (útil quando o usuário acabou de enviar o arquivo
    e não sabe o nome). O resultado inclui a lista de outros arquivos
    disponíveis para o agente sugerir alternativas.
    """
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

    try:
        destino = _resolve_file(filename)
        conteudo = extract_file_text(destino)

        # Perguntas de acompanhamento sobre a melhoria já feita não devem gerar
        # um arquivo novo: se o mesmo arquivo, com o mesmo conteúdo, for
        # pedido de novo sem novas diretrizes, reaproveita a comparação.
        if (
            not diretrizes
            and _ultima_comparacao
            and _ultima_comparacao.get("arquivo_original") == filename
            and _ultima_comparacao.get("sha1")
            == hashlib.sha1(conteudo.encode("utf-8", "ignore")).hexdigest()
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
        )
    except UserFileError as error:
        return json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False)
    except (ValueError, OSError) as error:
        return json.dumps(
            {"status": "error", "error": str(error)}, ensure_ascii=False
        )
    except Exception as error:  # noqa: BLE001 - falha vira resultado de tool
        return json.dumps(
            {
                "status": "error",
                "error": "Falha ao melhorar o documento.",
                "detail": str(error),
            },
            ensure_ascii=False,
        )