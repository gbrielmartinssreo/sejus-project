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
    build_paragraph,
    clear_body,
    find_reference,
    paragraph_text,
)
from sejus_project.tools.modelos import PerfilModelo

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
        linhas.append(f"- {tipo} {numero}\n  {texto}")
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
        novo = {
            "rotulo": _limpar(item.get("rotulo") or ""),
            "texto": _limpar(item["texto"]),
        }
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

    resposta = perguntar(
        mensagens,
        [STRUTURA_DEFINITION],
        max_tokens=max(4096, int(os.getenv("MINUTA_MAX_TOKENS", "4096"))),
    )
    message = resposta.choices[0].message
    if not message.tool_calls:
        raise ValueError("O modelo nao devolveu uma estrutura de minuta valida.")

    argumentos = message.tool_calls[0].function.arguments or "{}"
    estrutura = json.loads(argumentos)
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
    "melhorias e adequacoes juridicas, mais a lista 'alteracoes' explicando "
    "o que mudou em relacao ao original e por que. O ato deve permanecer o "
    "mesmo (numero, ementa, objeto e assinaturas preservados)."
)
MELHORIA_DEFINITION["function"]["parameters"]["properties"]["alteracoes"] = {
    "type": "array",
    "description": (
        "Lista objetiva das alteracoes aplicadas ao documento original, para a "
        "comparacao antes/depois. Registre CADA mudanca com tipo e motivo."
    ),
    "items": {
        "type": "object",
        "properties": {
            "tipo": {
                "type": "string",
                "description": "'adicionado', 'alterado', 'removido' ou 'corrigido'.",
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
MELHORIA_DEFINITION["function"]["parameters"]["required"] = [
    "numero",
    "ementa",
    "corpo",
    "alteracoes",
]


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
        "artigos e assinaturas. Aprimore o texto onde ele estiver fragil.\n"
        "2. Fundamentacao legal: confira e ajuste o preambulo e os considerandos "
        "usando as normas e fundamentos presentes nos atos recuperados no RAG "
        "(nao invente referencias que nao possa sustentar nos atos recuperados).\n"
        "3. Rio de articulacao: corrija numeracao de artigos, incisos (romano "
        "maiusculo) e paragrafos (§ / Paragrafo unico), sem pular numeros.\n"
        "4. Redacao juridica: padronize siglas e termos, elimine ambiguidades, "
        "mantendo o tom impessoal e tecnico dos atos oficiais.\n"
        "5. Fechamento: garanta vigencia e, quando o original revoga algo, "
        "preserve a revogacao nos termos corretos.\n"
        "6. Mude apenas o necessario: toda alteracao deve constar em "
        "'alteracoes' com tipo, item e motivo. Se nada precisar mudar em um "
        "trecho, mantenha-o e nao o liste.\n"
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
        str(conteudo)[:20_000],
        "",
        f"Tipo de ato: {tipo_ato}",
        f"Formato/modelo de referencia: {perfil.name}",
        "",
        (
            "Atos recuperados como fundamento (RAG). Use esse conteudo para "
            "adequar fundamentos, prazos, procedimentos e detalhes:"
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


def gerar_estrutura_melhoria(
    conteudo: str,
    tipo_ato: str,
    perfil: PerfilModelo,
    contexto: list[dict],
    valores: dict | None = None,
) -> tuple[dict, list[dict]]:
    """Chama o LLM e devolve (estrutura melhorada, lista de alteracoes)."""
    mensagens = [
        {"role": "system", "content": _sistema_melhoria()},
        {
            "role": "user",
            "content": _usuario_melhoria(conteudo, tipo_ato, perfil, contexto, valores),
        },
    ]

    resposta = perguntar(
        mensagens,
        [MELHORIA_DEFINITION],
        max_tokens=max(4096, int(os.getenv("MINUTA_MAX_TOKENS", "4096"))),
    )
    message = resposta.choices[0].message
    if not message.tool_calls:
        raise ValueError("O modelo nao devolveu uma estrutura de documento valida.")

    argumentos = message.tool_calls[0].function.arguments or "{}"
    dados = json.loads(argumentos)
    estrutura = _padronizar(
        {chave: valor for chave, valor in dados.items() if chave != "alteracoes"},
        tipo_ato,
    )
    alteracoes = [a for a in (dados.get("alteracoes") or []) if isinstance(a, dict)]
    return estrutura, alteracoes


# ---------------------------------------------------------------------------
# Montagem do DOCX
# ---------------------------------------------------------------------------

_QUEDA_REFERENCIA = (
    "artigo",
    "resolutivo",
    "paragrafo",
)

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


def montar_docx(
    perfil: PerfilModelo,
    estrutura: dict,
    output_dir: Path,
) -> Path:
    """Duplica o modelo e monta a minuta preservando a formatacao."""
    doc = _abrir_ou_criar(perfil.file)
    refs = _referencias(doc, perfil)
    _adicionar_vigencia_faltante(estrutura, perfil.act_types[0])

    ancora = _preparar_corpo(doc, refs, perfil.preservar_moldura)

    def adicionar(papel: str, rotulo: str, texto: str) -> None:
        if not texto.strip():
            return
        if not _pedir_paragrafo(refs, papel):
            return
        ref = _referencia_para(refs, papel)
        w_p = build_paragraph(ref, rotulo, texto)
        ancora.addprevious(w_p)

    adicionar("titulo", "", estrutura.get("numero", ""))
    adicionar("ementa", "", estrutura.get("ementa", ""))

    for considerando in estrutura.get("considerandos", []):
        adicionar("considerando", "", considerando)

    adicionar("preambulo", "", estrutura.get("preambulo", ""))
    adicionar("resolutivo", "", estrutura.get("resolutivo", ""))

    for artigo in estrutura.get("corpo", []):
        adicionar("artigo", artigo.get("rotulo", ""), artigo["texto"])
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