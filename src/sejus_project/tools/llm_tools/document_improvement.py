"""Melhoria/adequacao de um documento enviado pelo usuario, via LLM.

Responsabilidade: schema e prompts de melhoria, chamada ao LLM e guarda de
completude da reescrita (nao pode omitir artigos nem encolher o texto do
original). Reaproveita o schema, o parsing com retry e a normalizacao de
``minuta_generation``. Nao manipula DOCX: a montagem fisica do arquivo fica em
``document_infra.docx_builder``.
"""
from __future__ import annotations

import json
import os
import re

from sejus_project.tools.document_infra.modelos import PerfilModelo
from sejus_project.tools.llm_tools.minuta_generation import (
    STRUTURA_DEFINITION,
    _extrair_json_com_retry,
    _padronizar,
    _resumir_contexto,
)
from sejus_project.web.render_html import minuta_para_texto

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