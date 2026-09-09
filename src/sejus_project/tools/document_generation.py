"""Tool de function calling para gerar atos a partir de modelos DOCX reais."""
from __future__ import annotations

import json
from pathlib import Path

from sejus_project.tools import minuta, modelos
from sejus_project.tools.docx_templates import OUTPUTS_DIR
from sejus_project.tools.retrieval import retrieve
from sejus_project.tools.user_files import (
    UserFileError,
    _list_available_files,
    _resolve_file,
    _ultimo_arquivo_importado,
    extract_file_text,
)

_pending_document: dict | None = None
_ultima_minuta: dict | None = None
_modelo_usuario: dict | None = None
_gerada_no_turno: bool = False
_pendencia_mudou_no_turno: bool = False
_melhoria_no_turno: bool = False
_ultima_comparacao: dict | None = None

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
            "Seleciona automaticamente um modelo DOCX real da SEJUS conforme o "
            "tipo de ato pedido, consulta atos normativos relacionados no RAG e "
            "gera uma copia preenchida em outputs/. Se o usuario tiver enviado "
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
                    "description": "Pedido do usuario e objeto pretendido para o ato.",
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
    estrutura = minuta.gerar_estrutura_minuta(
        request,
        tipo,
        perfil,
        contexto,
        values,
        modelo_referencia=modelo_referencia,
    )
    output_path = minuta.montar_docx(perfil, estrutura, OUTPUTS_DIR)
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


def _melhorar_e_relatar(
    filename: str,
    destino,
    conteudo: str,
    perfil: modelos.PerfilModelo,
    tipo_ato: str,
    contexto: list[dict],
    diretrizes: str | None,
    outros: list[str] | None = None,
) -> str:
    global _ultima_minuta, _melhoria_no_turno, _ultima_comparacao, _gerada_no_turno
    valores = {"diretrizes": diretrizes} if diretrizes else None
    estrutura, alteracoes = minuta.gerar_estrutura_melhoria(
        conteudo,
        tipo_ato,
        perfil,
        contexto,
        valores,
    )
    output_path = minuta.montar_docx(perfil, estrutura, OUTPUTS_DIR)
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
        "alteracoes": alteracoes,
    }
    return json.dumps(
        {
            "status": "improved",
            "filename": filename,
            "modelo": perfil.name,
            "output_path": str(output_path),
            "alteracoes": alteracoes,
            "outros": outros or [],
            "sources": _source_summary(contexto),
        },
        ensure_ascii=False,
    )


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

        tipo_ato = modelos.detectar_tipo_ato(conteudo)
        if destino.suffix.lower() == ".docx":
            perfil = modelos.crear_perfil_de_arquivo(
                destino, conteudo, Path(filename).stem
            )
        else:
            perfil = modelos.selecionar_modelo(tipo_ato)

        contexto = retrieve(
            f"Melhoria e adequacao do documento: {filename}\nTipo de ato: {tipo_ato}",
            limit=16,
            act_type=modelos.ACT_TYPE_FILTER.get(tipo_ato),
        )
        return _melhorar_e_relatar(
            filename,
            destino,
            conteudo,
            perfil,
            tipo_ato,
            contexto,
            diretrizes,
            outros=[f for f in disponiveis if f != filename],
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