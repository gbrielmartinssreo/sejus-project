"""Geracao dinamica de minutas: LLM produz a estrutura do ato e o motor DOCX monta o arquivo.

Fluxo:
1. ``gerar_estrutura_minuta`` chama o LLM (function calling) com o pedido do
   usuario, o tipo de ato, o contexto recuperado do RAG e os campos que o
   usuario eventualmente informou. O LLM devolve uma estrutura JSON livre
   (numero, ementa, considerandos, articulacao com incisos/paragrafos,
   fechamento, local/data e assinaturas).
2. ``montar_docx`` duplica o modelo e reconstroi o corpo lancando mao dos
   paragrafos de referencia do modelo (docx_engine), preservando a formatacao.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from docx.oxml.ns import qn

from sejus_project.llm.ia import perguntar
from sejus_project.tools.docx_engine import (
    all_paragraphs,
    assinalar_insercao,
    build_paragraph,
    clear_body,
    find_reference,
    paragraph_text,
)
from sejus_project.tools.modelos import PerfilModelo
from sejus_project.web.render_html import minuta_para_texto

_SIMPLES = re.compile(r"\s+")

_VERBO_ATO = {
    "portaria": "Esta Portaria",
    "portaria conjunta": "Esta Portaria Conjunta",
    "instrução normativa": "Esta Instrução Normativa",
    "instrucao normativa": "Esta Instrução Normativa",
    "decreto": "Este Decreto",
    "retificação": "Esta Retificação",
    "retificacao": "Esta Retificação",
}

_DEFAULT_RESOLUTIVO = {
    "portaria": "RESOLVE:",
    "portaria conjunta": "R E S O L V E M:",
    "instrução normativa": "RESOLVE:",
    "instrucao normativa": "RESOLVE:",
    "decreto": "D E C R E T A:",
    "retificação": "RESOLVE:",
    "retificacao": "RESOLVE:",
}


# ---------------------------------------------------------------------------
# Schema do function calling usado para extrair a estrutura do ato
# ---------------------------------------------------------------------------

STRUTURA_DEFINITION = {
    "type": "function",
    "function": {
        "name": "apresentar_estrutura_minuta",
        "description": (
            "Apresenta a estrutura completa de uma minuta de ato normativo "
            "para montagem em DOCX. A estrutura deve refletir o pedido do "
            "usuario e os atos recuperados como fundamento, seguindo o estilo "
            "formal dos atos da SEJUS/MT."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "numero": {
                    "type": "string",
                    "description": (
                        "Linha de identificacao do ato, ex: "
                        "'PORTARIA Nº 001/2026/GAB-SEJUS/MT'."
                    ),
                },
                "ementa": {
                    "type": "string",
                    "description": "Ementa do ato, iniciando com 'Dispõe sobre ...'.",
                },
                "preambulo": {
                    "type": "string",
                    "description": (
                        "Preambulo, ex: 'O SECRETÁRIO DE ESTADO DE JUSTIÇA, no "
                        "uso das atribuições que lhe confere o art. 71 da "
                        "Constituição Estadual,'."
                    ),
                },
                "considerandos": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de considerandos, cada um iniciando com 'CONSIDERANDO'.",
                },
                "resolutivo": {
                    "type": "string",
                    "description": "Verbo resolutivo, ex: 'RESOLVE:'.",
                },
                "corpo": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rotulo": {"type": "string", "description": "ex: 'Art. 1º'."},
                            "texto": {"type": "string"},
                            "subitens": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "tipo": {
                                            "type": "string",
                                            "description": "'inciso' ou 'paragrafo'.",
                                        },
                                        "rotulo": {
                                            "type": "string",
                                            "description": "ex: 'I -' ou 'Parágrafo único.'.",
                                        },
                                        "texto": {"type": "string"},
                                    },
                                    "required": ["tipo", "texto"],
                                },
                            },
                        },
                        "required": ["rotulo", "texto"],
                    },
                    "description": "Articulacao do ato (artigos e itens).",
                },
                "fechamento": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rotulo": {"type": "string"},
                            "texto": {"type": "string"},
                        },
                        "required": ["rotulo", "texto"],
                    },
                    "description": "Artigos finais (vigencia e revogacao).",
                },
                "local_data": {
                    "type": "string",
                    "description": "Local e data, ex: 'Cuiabá-MT, 8 de setembro de 2026.'",
                },
                "assinaturas": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "nome": {"type": "string"},
                            "cargo": {"type": "string"},
                        },
                        "required": ["nome", "cargo"],
                    },
                    "description": "Nomes e cargos dos signatarios.",
                },
            },
            "required": ["numero", "ementa", "corpo"],
        },
    },
}


def _resumir_contexto(contexto: list[dict]) -> str:
    linhas = []
    for item in contexto or []:
        tipo = item.get("act_type") or "ATO"
        numero = item.get("act_number") or ""
        texto = (item.get("text") or "").strip()[:3000]
        prefixo = f"[tema: {item.get('tema')}] " if item.get("tema") else ""
        linhas.append(f"- {tipo} {numero}\n  {prefixo}{texto}")
    return "\n".join(linhas) if linhas else "(nenhum ato recuperado)"


def _sistema():
    return (
        "Voce e um redator experiente de atos normativos da Secretaria de "
        "Estado de Justiça de Mato Grosso (SEJUS/MT). Sua tarefa e redigir "
        "minutas completas, robustas e fieis ao estilo juridico dos atos "
        "publicados (Portarias, Instruções Normativas, Decretos, Portarias "
        "Conjuntas), com o mesmo nivel de detalhamento e sofisticacao dos "
        "documentos oficiais da SEJUS/MT.\n\n"
        "ORIENTACOES DE DETALHAMENTO:\n"
        "1. Artigos e subitens: produza uma articulacao rica e coerente. Para "
        "tema simples use ao menos 3 a 4 artigos; para tema medio 5 a 7 "
        "artigos; para tema complexo ou institucional 8 ou mais artigos. Sempre "
        "que o tema envolver competencias, atribuicoes, prazos, comissoes, "
        "fluxos, prazos de execucao ou objetos multiplos, desdobre os artigos "
        "em incisos e paragrafos (§) para esmiucar cada ponto.\n"
        "2. Considerandos: fundamente o ato com considerandos bem desenvolvidos "
        "(a partir de 'CONSIDERANDO'), extraindo do contexto RAG os atos, "
        "normas e fundamentos legais correlatos (referencias a Constituicao "
        "Estadual, leis, decretos, instrucoes normativas ou portarias "
        "anteriores quando disponiveis nos atos recuperados).\n"
        "3. Preambulo: redija conforme o padrao do modelo, citando as "
        "atribuicoes legais aplicaveis.\n"
        "4. Uso do contexto RAG: aproveite ao maximo o conteudo dos atos "
        "recuperados como fundamento. Incorpore prazos, procedimentos, "
        "obrigacoes, prazos, comissoes e condicoes que estejam presentes nos "
        "atos recuperados e que sejam pertinentes ao objeto pedido, adaptando "
        "o texto ao novo ato (nao copie literalmente bloco de outro ato, mas "
        "aproveite as regras e detalhes relevantes).\n"
        "5. Fechamento: inclua artigos finais sobre vigencia, revogacao de "
        "disposicoes em contrario e, quando cabivel, regulamentacao/execucao.\n"
        "6. Extensao: prefira minutas mais longas e detalhadas quando o tema "
        "for institucional (comissoes, grupos de trabalho, procedimentos, "
        "estruturas), evitando respostas excessivamente curtas ou rascunhos "
        "resumidos.\n"
        "Retorne apenas o JSON da funcao apresentar_estrutura_minuta."
    )


def _usuario(pedido, tipo_ato, perfil, contexto, valores, modelo_referencia=None):
    partes = [
        "Pedido do usuario:",
        pedido,
        "",
        f"Tipo de ato: {tipo_ato}",
        f"Modelo de referencia (formato): {perfil.name}",
        "",
        (
            "Atos recuperados como fundamento (RAG). Use esse conteudo como "
            "base para prazos, procedimentos, fundamentos legais e detalhes:"
        ),
        _resumir_contexto(contexto),
    ]
    if modelo_referencia:
        partes.extend(
            [
                "",
                (
                    "DOCUMENTO ENVIADO PELO USUARIO COMO MODELO: espelhe a "
                    "estrutura, as secoes e o estilo formal deste documento ao "
                    "redigir a nova minuta (artigos, considerandos, incisos, "
                    "paragrafos e nivel de detalhamento). Conteudo:"
                ),
                str(modelo_referencia)[:8000],
            ]
        )
    if valores:
        partes.extend(
            [
                "",
                "Campos informados pelo usuario (use-os, nao os invente):",
                json.dumps(valores, ensure_ascii=False, indent=2),
            ]
        )
    return "\n".join(partes)


def _limpar(texto: str) -> str:
    return _SIMPLES.sub(" ", (texto or "").strip())


def _padronizar(estrutura: dict, tipo_ato: str) -> dict:
    estrutura["numero"] = _limpar(estrutura.get("numero") or "")
    estrutura["ementa"] = _limpar(estrutura.get("ementa") or "")
    estrutura["preambulo"] = _limpar(estrutura.get("preambulo") or "")
    estrutura["resolutivo"] = _limpar(
        estrutura.get("resolutivo") or _DEFAULT_RESOLUTIVO.get(tipo_ato, "RESOLVE:")
    )
    estrutura["considerandos"] = [
        _limpar(c) for c in estrutura.get("considerandos") or [] if _limpar(c)
    ]
    estrutura["local_data"] = _limpar(estrutura.get("local_data") or "")

    corpo = []
    for item in estrutura.get("corpo") or []:
        if not isinstance(item, dict) or not _limpar(item.get("texto") or ""):
            continue
        tipo = _limpar(item.get("tipo") or "artigo").casefold()
        novo = {
            "tipo": tipo if tipo in ("artigo", "capitulo") else "artigo",
            "rotulo": _limpar(item.get("rotulo") or ""),
            "texto": _limpar(item["texto"]),
        }
        if novo["tipo"] == "capitulo":
            corpo.append(novo)
            continue
        subitens = []
        for sub in item.get("subitens") or []:
            if isinstance(sub, dict) and _limpar(sub.get("texto") or ""):
                subitens.append(
                    {
                        "tipo": _limpar(sub.get("tipo") or "inciso"),
                        "rotulo": _limpar(sub.get("rotulo") or ""),
                        "texto": _limpar(sub["texto"]),
                    }
                )
        novo["subitens"] = subitens
        corpo.append(novo)
    estrutura["corpo"] = corpo

    fechamento = []
    for item in estrutura.get("fechamento") or []:
        if isinstance(item, dict) and _limpar(item.get("texto") or ""):
            fechamento.append(
                {"rotulo": _limpar(item.get("rotulo") or ""), "texto": _limpar(item["texto"])}
            )

    # Regra suave: o ato precisa de artigo de vigencia (garantia tambem em
    # montar_docx; aqui apenas normalizamos o que veio do LLM).
    estrutura["fechamento"] = fechamento

    assinaturas = []
    for item in estrutura.get("assinaturas") or []:
        if isinstance(item, dict) and _limpar(item.get("nome") or ""):
            assinaturas.append(
                {"nome": _limpar(item["nome"]), "cargo": _limpar(item.get("cargo") or "")}
            )
    estrutura["assinaturas"] = assinaturas

    return estrutura


def _extrair_json_com_retry(
    mensagens,
    definition,
    max_tokens,
    preservar_completo: bool = False,
):
    """Chama o LLM (function calling) e devolve o argumento JSON já parseado.

    Se a resposta vier truncada (JSON incompleto por estouro do limite de
    tokens), reenvia a conversa com o dobro de ``max_tokens`` e instrução para
    o modelo devolver um JSON válido. Quando ``preservar_completo=True`` (fluxo
    de melhoria), o retry instrui o modelo a reproduzir TODO o conteúdo do
    original sem omitir nem resumir; caso contrário mantém o comportamento de
    permitir reduzir o campo ``corpo``. Falha com mensagem clara se a repetição
    também vier truncada.
    """
    if preservar_completo:
        instrucao_retry = (
            "JSON invalido ou truncado (resposta cortada no limite de tokens). "
            "Reproduza o documento COMPLETO, preservando TODOS os artigos, "
            "considerandos, titulos e trechos do original, sem omitir nem "
            "resumir. Apenas devolva um JSON valido, completo e encerrado."
        )
    else:
        instrucao_retry = (
            "JSON invalido ou truncado (resposta cortada no limite "
            "de tokens). Refaça a estrutura completa, reduzindo o "
            "tamanho do campo 'corpo' se precisar, e devolva um "
            "JSON valido e encerrado."
        )

    for tentativa in range(2):
        resposta = perguntar(mensagens, [definition], max_tokens=max_tokens)
        message = resposta.choices[0].message
        if not message.tool_calls:
            raise ValueError("O modelo nao devolveu uma estrutura de documento valida.")

        tool_call = message.tool_calls[0]
        argumentos = tool_call.function.arguments or "{}"
        try:
            return json.loads(argumentos)
        except json.JSONDecodeError:
            if tentativa == 1:
                raise ValueError(
                    "O modelo gerou uma resposta incompleta mesmo após a repetição. "
                    "O documento pode ser grande demais para gerar de uma vez; "
                    "tente novamente ou envie um arquivo mais curto."
                )
            max_tokens *= 2
            tool_call_id = getattr(tool_call, "id", "call_retry")
            mensagens.extend(
                [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": definition["function"]["name"],
                                    "arguments": argumentos,
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": instrucao_retry,
                    },
                ]
            )

    raise ValueError("Nao foi possivel gerar a estrutura do documento.")


def gerar_estrutura_minuta(
    pedido: str,
    tipo_ato: str,
    perfil: PerfilModelo,
    contexto: list[dict],
    valores: dict | None = None,
    modelo_referencia: str | None = None,
) -> dict:
    """Chama o LLM e devolve a estrutura estruturada da minuta."""
    mensagens = [
        {"role": "system", "content": _sistema()},
        {
            "role": "user",
            "content": _usuario(pedido, tipo_ato, perfil, contexto, valores, modelo_referencia),
        },
    ]

    estrutura = _extrair_json_com_retry(
        mensagens,
        STRUTURA_DEFINITION,
        max(4096, int(os.getenv("MINUTA_MAX_TOKENS", "4096"))),
    )
    return _padronizar(estrutura, tipo_ato)


# ---------------------------------------------------------------------------
# Melhoria e adequacao de um documento enviado pelo usuario
# ---------------------------------------------------------------------------

# Mesmo schema da minuta, acrescido da lista 'alteracoes' (comparacao
# antes/depois que a interface exibe ao lado do documento melhorado).
MELHORIA_DEFINITION = json.loads(json.dumps(STRUTURA_DEFINITION))
MELHORIA_DEFINITION["function"]["name"] = "apresentar_documento_melhorado"
MELHORIA_DEFINITION["function"]["description"] = (
    "Apresenta o documento normativo enviado pelo usuario reescrito com "
    "melhorias e adequacoes juridicas. Deve devolver: 'alteracoes' com as "
    "CORRECOES aplicadas ao texto existente; 'adicoes_estruturais' com os "
    "artigos NOVOS propostos para fechar lacunas de aplicabilidade (somente "
    "quando houver precedente no RAG); e 'lacunas_identificadas' com as "
    "lacunas pertinentes sem precedente no acervo. O ato deve permanecer o "
    "mesmo (numero, ementa, objeto e assinaturas preservados)."
)
MELHORIA_DEFINITION["function"]["parameters"]["properties"]["alteracoes"] = {
    "type": "array",
    "description": (
        "CORRECOES aplicadas ao texto EXISTENTE do documento, para a "
        "comparacao antes/depois. Use apenas 'alterado', 'corrigido' ou "
        "'removido'. Artigos novos NAO entram aqui -- vao em "
        "'adicoes_estruturais' com tipo 'adicionado'."
    ),
    "items": {
        "type": "object",
        "properties": {
            "tipo": {
                "type": "string",
                "description": (
                    "'alterado', 'removido' ou 'corrigido' (correcoes de texto "
                    "existente)."
                ),
            },
            "o_que": {
                "type": "string",
                "description": (
                    "Item alterado, ex.: 'Fundamento legal no preambulo', "
                    "'Numeracao dos incisos do art. 2º'."
                ),
            },
            "detalhe": {
                "type": "string",
                "description": "Explicacao curta da mudanca e do motivo.",
            },
        },
        "required": ["tipo", "o_que", "detalhe"],
    },
}
MELHORIA_DEFINITION["function"]["parameters"]["properties"]["adicoes_estruturais"] = {
    "type": "array",
    "description": (
        "ADICOES ESTRUTURAIS PROPOSTAS: artigos NOVOS acrescentados ao "
        "documento para fechar lacunas de aplicabilidade, somente quando a "
        "lacuna tiver precedente nos atos recuperados no RAG. Cada item e um "
        "artigo simples, com numero por sufixo quando inserido no MEIO da "
        "sequencia (LC 95/1998, art. 12, §§ 2o-3o): inserido apos o art. 6o, "
        "vira 'Art. 6o-A'; so continue a numeracao ('Art. 34') ao final do "
        "ato. O texto do artigo deve ser AUTONOMO (nao citar ato SEJUS "
        "lateral, de outro assunto, no corpo do dispositivo)."
    ),
    "items": {
        "type": "object",
        "properties": {
            "tipo": {
                "type": "string",
                "description": "'adicionado'.",
            },
            "o_que": {
                "type": "string",
                "description": (
                    "Identificacao do artigo novo, ex.: 'Art. 6o-A'. Igual ao "
                    "rotulo usado no 'corpo'."
                ),
            },
            "posicao": {
                "type": "string",
                "description": (
                    "Onde entra, ex.: 'apos o art. 6o, no Capitulo III' ou "
                    "'ao final do ato, apos o art. 33'."
                ),
            },
            "detalhe": {
                "type": "string",
                "description": "Motivo da adicao (qual lacuna fechada).",
            },
            "lastro": {
                "type": "string",
                "description": (
                    "Opcional. Atos do RAG usados como modelo de redacao, ex.: "
                    "'IN 07/2026, art. 13 (validade de 02 anos)'. Transparencia "
                    "de processo para o relatorio -- nao e citacao normativa no "
                    "texto do artigo."
                ),
            },
        },
        "required": ["o_que", "posicao", "detalhe"],
    },
}
MELHORIA_DEFINITION["function"]["parameters"]["properties"]["lacunas_identificadas"] = {
    "type": "array",
    "description": (
        "Lacunas de aplicabilidade que VOCE avaliou como pertinentes mas que "
        "NAO geraram artigo novo (nao havia precedente no RAG). Use os temas: "
        "'recurso_administrativo', 'prazo_validade', 'prestacao_contas', "
        "'revogacao', 'seguranca_epi', 'publicacao_vigencia'. A versao final "
        "lista no relatorio apenas as sem precedente no acervo."
    ),
    "items": {
        "type": "object",
        "properties": {
            "tema": {"type": "string"},
            "detalhe": {
                "type": "string",
                "description": "Descricao curta da omissao observada no ato.",
            },
        },
        "required": ["tema"],
    },
}
MELHORIA_DEFINITION["function"]["parameters"]["required"] = [
    "numero",
    "ementa",
    "corpo",
    "alteracoes",
]

# Títulos de capítulo (ex.: 'CAPÍTULO I' e o subtítulo 'DAS DISPOSIÇÕES
# GERAIS') entram como itens do próprio 'corpo', em ordem de aparecimento,
# cada linha como um item com tipo 'capitulo'. Documentos sem capítulos não
# usam esse campo — basta omitir.
_corpo_items = MELHORIA_DEFINITION["function"]["parameters"]["properties"]["corpo"]["items"]
_corpo_items["properties"]["tipo"] = {
    "type": "string",
    "description": (
        "'capitulo' para a linha de titulo de capitulo (ex.: 'CAPÍTULO I' ou o "
        "subtitulo 'DAS DISPOSIÇÕES GERAIS', cada um como item separado e em "
        "ordem no corpo). Omita para artigos comuns ('artigo', padrao)."
    ),
    "enum": ["artigo", "capitulo"],
}
_corpo_items["description"] = (
    "Articulacao do ato em ordem: artigos, incisos/paragrafos e, quando o "
    "original tiver, itens 'capitulo' (titulos de capitulo) na posicao correta."
)


def _sistema_melhoria():
    return (
        "Voce e um consultor juridico experiente da Secretaria de Estado de "
        "Justiça de Mato Grosso (SEJUS/MT). Sua tarefa e REESCREVER um "
        "documento normativo enviado pelo usuario com melhorias e adequacoes, "
        "mantendo o mesmo ato: mesmo numero, mesma ementa, mesmo objeto e "
        "mesmas assinaturas. Nao crie um ato novo nem mude o sentido do texto "
        "original.\n\n"
        "ORIENTACOES DE MELHORIA E ADEQUACAO:\n"
        "1. Preserve o esqueleto do documento: numero, ementa, estrutura de "
        "artigos, titulos de capitulo e assinaturas. Aprimore o texto onde ele "
        "estiver fragil.\n"
        "2. Capitulos: se o original tiver titulos de capitulo (ex.: 'CAPÍTULO "
        "I' seguido de 'DAS DISPOSIÇÕES GERAIS'), reproduza TODOS eles como "
        "itens 'capitulo' do 'corpo', na mesma posicao do original. Nao crie "
        "capitulos que o original nao tinha.\n"
        "3. Fundamentacao legal: confira e ajuste o preambulo e os considerandos "
        "usando as normas e fundamentos presentes nos atos recuperados no RAG "
        "(nao invente referencias que nao possa sustentar nos atos recuperados).\n"
        "5. NUMERACAO DE ARTIGOS NOVOS (LC 95/1998, art. 12, §§ 2o-3o): ao "
        "inserir um artigo no MEIO da sequencia, NUNCA renumerar os "
        "existentes. Use o numero do artigo que o precede acrescido de sufixo "
        "de letra: inserido apos o 'Art. 6o', vira 'Art. 6o-A'; depois "
        "'Art. 6o-B', e assim por diante. So usa numeracao continua "
        "('Art. 34', 'Art. 35') para artigos acrescentados apos o ULTIMO "
        "artigo do ato.\n"
        "6. Fechamento: garanta artigo de vigencia e, quando o original revoga "
        "algo, preserve a revogacao nos termos corretos.\n"
        "7. ANALISE DE APLICABILIDADE (LACUNAS): revise o ato como quem vai "
        "aplica-lo no dia a dia e avalie cada lacuna: "
        "(a) 'recurso_administrativo' -- recurso ou pedido de reconsideracao "
        "quando a norma der a uma autoridade poder de vedar/negar mediante "
        "decisao fundamentada; "
        "(b) 'prazo_validade' -- prazo de validade e renovacao de "
        "autorizacoes/suspensoes; "
        "(c) 'prestacao_contas' -- prestacao de contas e fiscalizacao, "
        "sobretudo quando houver insumo fornecido pelo Estado; "
        "(d) 'revogacao' -- revogacao de norma anterior sobre o mesmo objeto; "
        "(e) 'seguranca_epi' -- seguranca do trabalho/EPI quando a atividade "
        "envolver risco; "
        "(f) 'publicacao_vigencia' -- veiculo de publicacao e regime de "
        "vigencia. Se a lacuna existir E houver precedente no RAG rotulado "
        "com o MESMO tema, acrescente UM artigo simples (nao uma serie), no "
        "capitulo adequado, com numero por sufixo, e registre-o em "
        "'adicoes_estruturais' com posicao, motivo e lastro. Se a lacuna "
        "existir MAS nao houver precedente rotulado, NAO proponha artigo -- "
        "apenas registre o tema em 'lacunas_identificadas'. Nao encha o "
        "documento de artigos novos: so adicione o que fechar omissao real de "
        "aplicacao.\n"
        "8. TEXTOS NOVOS SAO AUTONOMOS: nao cite ato SEJUS lateral (de outro "
        "assunto) no corpo do artigo -- a base de estilo vai apenas no campo "
        "'lastro' do relatorio. Citacoes VERTICAIS ja embasadas no preambulo "
        "(ex.: LEP, Decreto 548/2016) e citacoes SUBSTANTIVAS (ex.: o ato "
        "concreto a ser revogado) podem entrar no texto.\n"
        "9. Separacao: toda mudanca em texto EXISTENTE vai em 'alteracoes' "
        "(alterado/corrigido/removido); todo artigo NOVO vai em "
        "'adicoes_estruturais' (adicionado). Mude apenas o necessario: se um "
        "trecho ja esta adequado, mantenha-o sem lista-lo.\n"
        "Retorne apenas o JSON da funcao apresentar_documento_melhorado."
    )


def _usuario_melhoria(
    conteudo: str,
    tipo_ato: str,
    perfil: PerfilModelo,
    contexto: list[dict],
    valores: dict | None = None,
) -> str:
    partes = [
        (
            "DOCUMENTO ORIGINAL ENVIADO PELO USUARIO (reescreva ESTE documento "
            "com melhorias, preservando numero, ementa, objeto e assinaturas):"
        ),
        str(conteudo)[:60_000],
        "",
        f"Tipo de ato: {tipo_ato}",
        f"Formato/modelo de referencia: {perfil.name}",
        "",
        (
            "Atos recuperados como fundamento (RAG). Use esse conteudo para "
            "adequar fundamentos, prazos, procedimentos e detalhes. Trechos "
            "rotulados com '[tema: ...]' indicam precedentes de lacuna "
            "(recurso_administrativo, prazo_validade, prestacao_contas, "
            "revogacao, seguranca_epi, publicacao_vigencia) e podem embasar "
            "artigos novos em 'adicoes_estruturais':"
        ),
        _resumir_contexto(contexto),
    ]
    if valores:
        partes.extend(
            [
                "",
                "Diretrizes/pedido do usuario para esta melhoria (siga-as):",
                json.dumps(valores, ensure_ascii=False, indent=2),
            ]
        )
    return "\n".join(partes)


# Completude mínima para aceitar uma melhoria sem re-tentar/errar: a melhoria
# deve reproduzir o ato (mesmo número, ementa, objeto) — encolher demais ou
# omitir artigos descaracteriza o documento.
_MELHORIA_MAX_TOKENS_DEFAULT = 16384
_COMPLETUDE_MIN_ARTIGOS = 0.8
_COMPLETUDE_MIN_RATIO = 0.55

_RE_ARTIGO_ORIGEM = re.compile(r"^\s*art\.?\s*\d", re.IGNORECASE)

_MENSAGEM_PRESERVAR = (
    "A versão gerada ficou INCOMPLETA: artigos, títulos de capítulo e trechos "
    "do documento original foram omitidos e o texto encolheu. Refaça "
    "preservando TODOS os artigos, capítulos, considerandos, títulos e o nível "
    "de detalhamento do original — não omita, não resuma e não renumere de "
    "forma que descaracterize o ato. Devolva o JSON completo e encerrado."
)


def _melhoria_incompleta(conteudo: str, estrutura: dict) -> bool:
    """Diz se a estrutura gerada omitiu conteúdo relevante do original.

    Confere (a) a proporção de artigos do original que foi reproduzida e (b) a
    razão entre o tamanho do texto gerado e o do original. Um ato deve usar os
    mesmos argumentos descritivos — encolher para uma fração indica conteúdo
    cortado."""
    linhas_origem = [linha.strip() for linha in (conteudo or "").splitlines()]
    artigos_origem = sum(1 for linha in linhas_origem if _RE_ARTIGO_ORIGEM.match(linha))
    artigos_gerados = len(estrutura.get("corpo") or []) + len(
        estrutura.get("fechamento") or []
    )
    if artigos_origem and artigos_gerados < artigos_origem * _COMPLETUDE_MIN_ARTIGOS:
        return True

    tamanho_depois = len(minuta_para_texto(estrutura))
    return tamanho_depois < max(1, len(conteudo or "")) * _COMPLETUDE_MIN_RATIO


def gerar_estrutura_melhoria(
    conteudo: str,
    tipo_ato: str,
    perfil: PerfilModelo,
    contexto: list[dict],
    valores: dict | None = None,
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """Chama o LLM e devolve (estrutura, correções, adições, lacunas).

    Além da estrutura melhorada e das ``alteracoes`` (correções em texto
    existente), devolve as ``adicoes_estruturais`` (artigos novos propostos
    para fechar lacunas) e as ``lacunas_identificadas`` pelo modelo.

    Ao contrário da minuta livre, a melhoria deve reproduzir o documento
    completo: usa um orçamento de tokens maior (``MELHORIA_MAX_TOKENS``) e uma
    guarda de completude que re-tenta (e depois erra com mensagem clara) caso o
    modelo omita artigos ou encolha o texto, em vez de entregar um ato cortado.
    """
    mensagens = [
        {"role": "system", "content": _sistema_melhoria()},
        {
            "role": "user",
            "content": _usuario_melhoria(conteudo, tipo_ato, perfil, contexto, valores),
        },
    ]

    max_tokens = max(
        _MELHORIA_MAX_TOKENS_DEFAULT,
        int(os.getenv("MELHORIA_MAX_TOKENS") or _MELHORIA_MAX_TOKENS_DEFAULT),
    )

    for tentativa in range(2):
        dados = _extrair_json_com_retry(
            mensagens,
            MELHORIA_DEFINITION,
            max_tokens,
            preservar_completo=True,
        )
        estrutura = _padronizar(
            {
                chave: valor
                for chave, valor in dados.items()
                if chave
                not in ("alteracoes", "adicoes_estruturais", "lacunas_identificadas")
            },
            tipo_ato,
        )
        alteracoes = [a for a in (dados.get("alteracoes") or []) if isinstance(a, dict)]
        adicoes = [
            a for a in (dados.get("adicoes_estruturais") or []) if isinstance(a, dict)
        ]
        lacunas = [
            l for l in (dados.get("lacunas_identificadas") or []) if isinstance(l, dict)
        ]

        if not _melhoria_incompleta(conteudo, estrutura):
            return estrutura, alteracoes, adicoes, lacunas

        if tentativa == 0:
            mensagens.append({"role": "user", "content": _MENSAGEM_PRESERVAR})
            max_tokens *= 2

    raise ValueError(
        "A melhoria ficou incompleta: artigos e trechos do documento foram "
        "omitidos e o texto encolheu mesmo após a repetição. O documento pode "
        "ser grande demais para melhorar de uma vez; peça ajustes pontuais ou "
        "envie uma versão mais curta."
    )


# ---------------------------------------------------------------------------
# Montagem do DOCX
# ---------------------------------------------------------------------------

_QUEDA_REFERENCIA = (
    "artigo",
    "resolutivo",
    "paragrafo",
)

# Autor das revisões de inserção (track changes) gravadas no DOCX melhorado.
_AUTOR_REVISAO = "editor"

_SIMPLIFICAR_ROTULO = re.compile(r"\s+")

# Linhas típicas do rodapé de imprensa do Diário Oficial. Usado para saber onde
# termina o corpo normativo e preservar o rodapé quando o modelo do usuário for
# uma captura do Diário (cabeçalho e rodapé ficam no corpo, e não na seção).
_RE_RODAPE_IMPRENSA = re.compile(
    r"(govern[oa] do estado de mato grosso|seplag|imprensa oficial|iomat)",
    re.IGNORECASE,
)


def _idx_rodape_imprensa(children: list) -> int | None:
    """Índice do último parágrafo do corpo que parece rodapé de imprensa."""
    idx = None
    for i, ch in enumerate(children):
        if ch.tag == qn("w:p") and _RE_RODAPE_IMPRENSA.search(paragraph_text(ch)):
            idx = i
    return idx


def _preparar_corpo(doc, refs: dict, preservar_moldura: bool):
    """Prepara o corpo para a reconstrução da minuta.

    Devolve o elemento-âncora: os novos parágrafos são inseridos imediatamente
    antes dele.

    * ``preservar_moldura=True`` (modelo do usuário — captura do Diário):
      mantém os parágrafos antes do título (cabeçalho do Diário Oficial) e o
      rodapé de imprensa no final, removendo apenas o miolo normativo.
    * Caso contrário (templates oficiais): limpa o corpo e insere antes do
      sectPr (comportamento original — cabeçalho/rodapé vivem na seção).
    """
    body = doc.element.body

    def _sect_pr_ancora():
        sect_pr = body.find(qn("w:sectPr"))
        return sect_pr if sect_pr is not None else body

    if not preservar_moldura:
        clear_body(doc)
        return _sect_pr_ancora()

    children = list(body)
    titulo_wp = refs.get("titulo")
    idx_titulo = next(
        (i for i, ch in enumerate(children) if ch is titulo_wp),
        None,
    )
    if titulo_wp is None or idx_titulo is None:
        clear_body(doc)
        return _sect_pr_ancora()

    idx_rodape = _idx_rodape_imprensa(children)
    if idx_rodape is not None and idx_rodape < idx_titulo:
        idx_rodape = None
    fim = idx_rodape if idx_rodape is not None else len(children)

    for i, ch in enumerate(children):
        if idx_titulo <= i < fim and ch.tag in (qn("w:p"), qn("w:tbl")):
            body.remove(ch)

    if idx_rodape is not None:
        return children[idx_rodape]
    return _sect_pr_ancora()


def _referencias(doc, perfil: PerfilModelo) -> dict:
    paragraphos = all_paragraphs(doc)
    refs = {}
    for papel, pattern in perfil.patterns.items():
        refs[papel] = find_reference(paragraphos, pattern)
    return refs


def _pedir_paragrafo(refs: dict, papel: str) -> bool:
    if refs.get(papel) is not None:
        return True
    for fallback in _QUEDA_REFERENCIA:
        if refs.get(fallback) is not None:
            return True
    return False


def _referencia_para(refs: dict, papel: str):
    ref = refs.get(papel)
    if ref is not None:
        return ref
    for fallback in _QUEDA_REFERENCIA:
        if refs.get(fallback) is not None:
            return refs[fallback]
    return None


def _adicionar_vigencia_faltante(estrutura: dict, tipo_ato: str) -> None:
    fechamento = [f for f in estrutura.get("fechamento") or [] if isinstance(f, dict)]
    if any("entra em vigor" in (f.get("texto") or "").casefold() for f in fechamento):
        return
    verbo = _VERBO_ATO.get(tipo_ato, "Este ato")
    fechamento.append(
        {
            "rotulo": "",
            "texto": f"{verbo} entra em vigor na data de sua publicação.",
        }
    )
    estrutura["fechamento"] = fechamento


def _chave_rotulo(rotulo: str) -> str:
    """Normaliza um rotulo ('Art. 6º-A', 'art. 6°a' ...) para comparacao."""
    chave = _SIMPLES.sub(" ", (rotulo or "").strip().casefold())
    return chave.rstrip(".")


def montar_docx(
    perfil: PerfilModelo,
    estrutura: dict,
    output_dir: Path,
    insercoes_rastreadas: set[str] | None = None,
) -> Path:
    """Duplica o modelo e monta a minuta preservando a formatacao.

    Artigos cujo rotulo esteja em ``insercoes_rastreadas`` (formato
    normalizado) sao gravados como revisao de insercao do Word (``<w:ins>``):
    o revisor precisa aceitar/rejeitar essas adicoes antes de publicar."""
    doc = _abrir_ou_criar(perfil.file)
    refs = _referencias(doc, perfil)
    _adicionar_vigencia_faltante(estrutura, perfil.act_types[0])

    ancora = _preparar_corpo(doc, refs, perfil.preservar_moldura)
    visados = {_chave_rotulo(r) for r in (insercoes_rastreadas or set())}
    n_insercoes = {"n": 0}

    def adicionar(papel: str, rotulo: str, texto: str, rastrear: bool = False) -> None:
        if not texto.strip():
            return
        if not _pedir_paragrafo(refs, papel):
            return
        ref = _referencia_para(refs, papel)
        w_p = build_paragraph(ref, rotulo, texto)
        if rastrear:
            n_insercoes["n"] += 1
            assinalar_insercao(w_p, n_insercoes["n"], _AUTOR_REVISAO)
        ancora.addprevious(w_p)

    adicionar("titulo", "", estrutura.get("numero", ""))
    adicionar("ementa", "", estrutura.get("ementa", ""))

    for considerando in estrutura.get("considerandos", []):
        adicionar("considerando", "", considerando)

    adicionar("preambulo", "", estrutura.get("preambulo", ""))
    adicionar("resolutivo", "", estrutura.get("resolutivo", ""))

    for artigo in estrutura.get("corpo", []):
        if artigo.get("tipo") == "capitulo":
            adicionar("capitulo", "", artigo.get("texto", ""))
            continue
        rastrear = _chave_rotulo(artigo.get("rotulo", "")) in visados
        adicionar("artigo", artigo.get("rotulo", ""), artigo["texto"], rastrear=rastrear)
        for sub in artigo.get("subitens", []):
            papel = "paragrafo" if sub.get("tipo") == "paragrafo" else "inciso"
            adicionar(papel, sub.get("rotulo", ""), sub["texto"])

    for fechamento in estrutura.get("fechamento", []):
        adicionar("artigo", fechamento.get("rotulo", ""), fechamento["texto"])

    adicionar("data", "", estrutura.get("local_data", ""))

    for assinatura in estrutura.get("assinaturas", []):
        adicionar("assinatura", "", assinatura.get("nome", ""))
        adicionar("assinatura", "", assinatura.get("cargo", ""))

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{perfil.name}_{uuid.uuid4().hex[:8]}.docx"
    doc.save(str(output_path))
    return output_path


def _abrir_ou_criar(path: str):
    """Abre o DOCX do modelo.

    O modelo pode estar num caminho relativo ou absoluto; caso nao exista,
    cria um documento vazio para nao quebrar a geracao (o LLM ja avisará
    a revisao). Na pratica os modelos existem no repositorio."""
    caminho = Path(path)
    if not caminho.exists():
        raise FileNotFoundError(f"Modelo de documento nao encontrado: {caminho}")
    from docx import Document

    return Document(str(caminho))