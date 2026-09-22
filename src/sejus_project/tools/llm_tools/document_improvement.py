"""Melhoria/adequacao de um documento enviado pelo usuario, via LLM.

Responsabilidade: schema e prompts de melhoria e a chamada ao LLM em modo
``patch``: o modelo NAO reescreve o documento inteiro -- devolve apenas as
mudancas (``alteracoes``/``remocoes``/``adicoes_estruturais``) ancoradas ao
texto original. A estrutura final e montada em ``_construir_estrutura``
(original + patch), eliminando o truncamento por reescrita integral. Reaproveita
o schema, o parsing com retry e a normalizacao de ``minuta_generation``. Nao
manipula DOCX: a montagem fisica do arquivo fica em
``document_infra.docx_builder``.
"""
from __future__ import annotations

import json
import os
import re

from sejus_project.tools.document_infra.docx_builder import (
    _chave_linha as _chave_texto,
)
from sejus_project.tools.document_infra.docx_builder import (
    _paragrafos_absorvidos,
    _proximos_subitens_texto,
)
from sejus_project.tools.document_infra.modelos import PerfilModelo
from sejus_project.tools.llm_tools.minuta_generation import (
    _TETO_TOKENS_MODELO,
    STRUTURA_DEFINITION,
    _extrair_json_com_retry,
    _resumir_contexto,
)

# Estados possíveis para uma proposta de alteração ou adição estrutural.
# Independente do tipo (alterado/incluído/excluído/movimentado), o estado
#tracka o ciclo de vida da proposta.
ESTADO_PENDENTE = "pendente"
ESTADO_ACEITA = "aceita"
ESTADO_REJEITADA = "rejeitada"
ESTADO_APLICADA = "aplicada"

# Estados antigos para compatibilidade (pode remover depois)
_ESTADO_ANTIGOS = (ESTADO_PENDENTE, ESTADO_ACEITA, ESTADO_REJEITADA, ESTADO_APLICADA)

# Schema de melhoria em modo patch: o LLM NAO reescreve o documento -- devolve
# apenas as mudancas (alteracoes/remocoes/adicoes) ancoradas ao texto original.
# A estrutura final e montada por _construir_estrutura (original + patch), o que
# elimina o truncamento por limite de tokens na reescrita integral.
MELHORIA_DEFINITION = json.loads(json.dumps(STRUTURA_DEFINITION))
melhoria_definition = MELHORIA_DEFINITION
MELHORIA_DEFINITION["function"]["name"] = "apresentar_documento_melhorado"
MELHORIA_DEFINITION["function"]["description"] = (
    "Registra as mudancas de melhoria/adequacao juridica de um documento "
    "normativo enviado pelo usuario, SEM reescrever o texto nao alterado. "
    "Deve devolver: 'alteracoes' com as CORRECOES aplicadas ao texto "
    "EXISTENTE (cada item com 'rotulo', 'trecho_original' copiado fielmente "
    "do original e 'novo_texto' completo); 'remocoes' com os paragrafos "
    "EXISTENTES que devem ser excluidos; 'adicoes_estruturais' com os artigos "
    "NOVOS propostos para fechar lacunas de aplicabilidade (somente quando "
    "houver precedente no RAG); e 'lacunas_identificadas' com as lacunas "
    "pertinentes sem precedente no acervo. O ato deve permanecer o mesmo "
    "(numero, ementa, objeto e assinaturas preservados)."
)
MELHORIA_DEFINITION["function"]["parameters"]["properties"]["alteracoes"] = {
    "type": "array",
    "description": (
        "CORRECOES aplicadas a paragrafos EXISTENTES do documento, para a "
        "comparacao antes/depois. Use apenas 'alterado' ou 'corrigido'. "
        "Cada item substitui UM paragrafo completo: 'rotulo' identifica o "
        "dispositivo (ex.: 'Art. 3º', '§ 1º do art. 5º'), 'trecho_original' e "
        "copiado EXATAMENTE do texto original (para a mudanca ser localizada) "
        "e 'novo_texto' e o paragrafo inteiro na versao corrigida, incluindo o "
        "rotulo como no original. Nao use para artigos NOVOS -- vao em "
        "'adicoes_estruturais'."
    ),
    "items": {
        "type": "object",
        "properties": {
            "tipo": {
                "type": "string",
                "description": "'alterado' ou 'corrigido' (correcoes de texto "
                "existente).",
            },
            "rotulo": {
                "type": "string",
                "description": (
                    "Dispositivo alterado, ex.: 'Art. 3º', '§ 1º do art. 5º', "
                    "'Fundamento legal no preambulo'."
                ),
            },
            "trecho_original": {
                "type": "string",
                "description": (
                    "Trecho EXATO do texto original que sera substituido -- "
                    "copie fielmente do documento recebido (mesma grafia)."
                ),
            },
            "novo_texto": {
                "type": "string",
                "description": (
                    "Novo paragrafo COMPLETO que substitui o trecho, incluindo "
                    "o rotulo como no original (ex.: 'Art. 3º O prazo ...')."
                ),
            },
            "detalhe": {
                "type": "string",
                "description": "Explicacao curta da mudanca e do motivo.",
            },
            "lastro": {
                "type": "string",
                "description": (
                    "Opcional. Ato recuperado no RAG que embasa a mudanca, ex.: "
                    "'IN 07/2026, art. 13'. Obrigatorio quando a alteracao "
                    "alinha o texto a uma norma do acervo ou corrige "
                    "prazo/percentual; obrigatorio em toda alteracao que "
                    "introduzir exigencia nova. Ausente em correcoes puramente "
                    "redacionais. Nao e citacao no corpo do dispositivo."
                ),
            },
            "requer_decisao_juridica": {
                "type": "boolean",
                "description": (
                    "Obrigatorio. True quando a mudanca altera exigencia, "
                    "prazo, percentual ou alcance inovando em relacao ao "
                    "original e o lastro nao esta garantido pelo sistema -- "
                    "sinaliza revisao da equipe juridica antes da publicacao."
                ),
            },
        },
        "required": ["tipo", "rotulo", "trecho_original", "novo_texto", "detalhe", "requer_decisao_juridica"],
    },
}
MELHORIA_DEFINITION["function"]["parameters"]["properties"]["remocoes"] = {
    "type": "array",
    "description": (
        "PARAGRAFOS EXISTENTES que devem ser EXCLUIDOS do documento (texto "
        "inteiro removido). Use somente quando a exclusao for realmente "
        "necessaria. Cada item e ancorado por 'rotulo' e 'trecho_original' "
        "copiado EXATAMENTE do original."
    ),
    "items": {
        "type": "object",
        "properties": {
            "rotulo": {
                "type": "string",
                "description": "Dispositivo removido, ex.: 'Art. 9º'.",
            },
            "trecho_original": {
                "type": "string",
                "description": "Trecho EXATO do texto original a remover.",
            },
            "detalhe": {
                "type": "string",
                "description": "Motivo da remocao.",
            },
            "lastro": {
                "type": "string",
                "description": (
                    "Opcional. Ato recuperado no RAG que sustenta a exclusao, "
                    "ex.: 'Decreto 2.541/2008, art. 16' (norma posterior que "
                    "revogou o dispositivo)."
                ),
            },
        },
        "required": ["rotulo", "trecho_original", "detalhe"],
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
                    "Rotulo do artigo novo, ex.: 'Art. 6o-A'. Tambem usado "
                    "como ancora (apos o artigo-base)."
                ),
            },
            "texto": {
                "type": "string",
                "description": "Texto completo e AUTONOMO do artigo novo.",
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
            "requer_decisao_juridica": {
                "type": "boolean",
                "description": (
                    "Obrigatorio. True quando a adicao amplia exigencia, prazo, "
                    "percentual ou alcance alem do que o lastro garante "
                    "textualmente -- sinaliza revisao da equipe juridica antes "
                    "da publicacao."
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
    "alteracoes",
    "remocoes",
]
# Modo patch: o modelo NAO reescreve o corpo -- ele só devolve as mudanças.
MELHORIA_DEFINITION["function"]["parameters"]["properties"].pop("corpo", None)


def _sistema_melhoria():
    return (
        "Voce e um consultor juridico experiente da Secretaria de Estado de "
        "Justiça de Mato Grosso (SEJUS/MT). Sua tarefa e PROPOR MELHORIAS E "
        "ADEQUACOES a um documento normativo enviado pelo usuario, mantendo o "
        "mesmo ato: mesmo numero, mesma ementa, mesmo objeto e mesmas "
        "assinaturas. Nao crie um ato novo nem mude o sentido do texto "
        "original.\n\n"
        "MODO DE TRABALHO (PATCH): voce NAO reescreve o documento inteiro. O "
        "sistema parte do texto original e aplica sua lista de mudancas. "
        "Devolva apenas:\n"
        "- 'alteracoes': paragrafos EXISTENTES corrigidos (tipo 'alterado' ou "
        "'corrigido'), cada um com 'rotulo', 'trecho_original' copiado "
        "EXATAMENTE do texto recebido e 'novo_texto' com o paragrafo inteiro "
        "ja corrigido (incluindo o rotulo, como no original).\n"
        "- 'remocoes': paragrafos EXISTENTES que devem sair (rotulo + "
        "trecho_original exato).\n"
        "- 'adicoes_estruturais': artigos NOVOS propostos para fechar lacunas "
        "de aplicabilidade (com 'o_que', 'texto' completo e autonomo, "
        "'posicao' e 'motivo').\n"
        "ORIENTACOES DE MELHORIA E ADEQUACAO:\n"
        "1. Preserve o esqueleto do documento: numero, ementa, estrutura de "
        "artigos, titulos de capitulo e assinaturas. Aprimore o texto onde ele "
        "estiver fragil.\n"
        "2. Capitulos: nao crie capitulos que o original nao tinha.\n"
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
        "algo, preserve a revogacao nos termos corretos (via 'alteracoes' ou "
        "'remocoes').\n"
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
        "capitulo adequado, com numero por sufixo, e registre-o SOMENTE em "
        "'adicoes_estruturais' com 'o_que' (rotulo), 'texto' (texto completo e "
        "autonomo), 'posicao' (onde entra), motivo e lastro. Se a lacuna "
        "existir MAS nao houver precedente rotulado, NAO proponha artigo -- "
        "apenas registre o tema em 'lacunas_identificadas'. Nao encha o "
        "documento de artigos novos: so adicione o que fechar omissao real de "
        "aplicacao.\n"
        "8. TEXTOS NOVOS SAO AUTONOMOS: nao cite ato SEJUS lateral (de outro "
        "assunto) no corpo do artigo -- a base de estilo vai apenas no campo "
        "'lastro' do relatorio. Citacoes VERTICAIS ja embasadas no preambulo "
        "(ex.: LEP, Decreto 548/2016) e citacoes SUBSTANTIVAS (ex.: o ato "
        "concreto a ser revogado) podem entrar no texto.\n"
        "9. Mude apenas o necessario: se um trecho ja esta adequado, NAO o "
        "liste em lugar algum (o sistema mantem o original intacto). Nao altere "
        "apenas tipografia (travessao por hifen, aspas, espacos). 'trecho_original' "
        "DEVE casar com o texto do documento recebido -- copie fielmente, sem "
        "reescrever, sem encurtar alem do paragrafo exato.\n"
        "10. SEM SUGESTOES VAGAS OU COSMETICAS: nao proponha mudancas por "
        "estilo, sinonimo ou preferencia pessoal; nao troque acentuacao, "
        "pontuacao ou termo equivalente quando o sentido nao muda. Se a "
        "'detalhe' nao aponta beneficio normativo concreto (clareza de "
        "exigencia, prazo, competencia, alcance), DESCARTE a sugestao.\n"
        "11. LASTRO EM TODA MUDANCA QUE INOVE: toda alteracao, remocao ou "
        "adiccao que introduz prazo, percentual, exigencia ou penalidade nova "
        "deve informar em 'lastro' o ato do RAG que embasa (ex.: 'IN 07/2026, "
        "art. 13'). Quando o acervo nao sustentar um numero concreto, use o "
        "marcador literal [PRAZO A DEFINIR PELA SECRETARIA] no lugar do valor "
        "no 'novo_texto'/'texto' e NUNCA invente o numero. Correcoes puramente "
        "redacionais nao precisam de lastro.\n"
        "12. REVOGACAO SO COM NORMA ESPECIFICA: proponha revogacao (em "
        "'remocoes' ou 'alteracoes') apenas quando indicar a norma concreta a "
        "ser revogada, citando nome, tipo, numero e ano (ex.: 'Decreto "
        "2.541/2008'). NUNCA proponha clausula generica do tipo 'ficam "
        "revogadas as disposicoes em contrario' ou revogacao implicita sem essa "
        "citacao.\n"
        "13. REQUER_DECISAO_JURIDICA: marque 'requer_decisao_juridica' = true "
        "em qualquer alteracao ou adicao que INOVE em relacao ao original (novo "
        "prazo, nova exigencia, novo percentual, ampliacao de alcance) ou "
        "quando o lastro nao estiver explicito no acervo; nesses casos o "
        "sistema destaca o paragrafo em amarelo e o sinaliza para validacao da "
        "equipe juridica antes da publicacao. Se o lastro cobrir integralmente "
        "o conteudo, marque false.\n"
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
            "DOCUMENTO ORIGINAL ENVIADO PELO USUARIO (liste as mudancas contra "
            "ESTE texto: nao reescreva o documento, apenas aponte alteracoes, "
            "remocoes e adicoes, preservando numero, ementa, objeto e "
            "assinaturas):"
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


# Orçamento de tokens da melhoria. Como o fluxo usa patch (só as mudanças são
# devolvidas pelo modelo), a resposta é pequena; mesmo assim o orçamento NUNCA
# sobe acima do teto do modelo (evita o erro 400 da API OpenAI em modelos com
# saída limitada, ex.: gpt-4o-mini). O padrão fica abaixo do teto para que
# MELHORIA_MAX_TOKENS ainda tenha efeito em documentos grandes.
_MELHORIA_MAX_TOKENS_DEFAULT = 8192


def _remover_acentos(texto: str) -> str:
    """Remove acentos caracteristicos do português para permitar comparação
   regex case-insensitive com variantes acentuadas."""
    substituicoes = [
        ('á', 'a'), ('Á', 'A'), ('à', 'a'), ('À', 'A'),
        ('é', 'e'), ('É', 'E'), ('ê', 'e'), ('Ê', 'E'),
        ('í', 'i'), ('Í', 'I'), ('ó', 'o'), ('Ó', 'O'),
        ('ú', 'u'), ('Ú', 'U'), ('ç', 'c'), ('Ç', 'C'),
    ]
    for acento, replace in substituicoes:
        texto = texto.replace(acento, replace)
    return texto


_RE_CAPITULO = re.compile(r"^\s*capitulo\s+([ivxl]+)", re.IGNORECASE)


def _tem_capitulo(linha: str) -> str | bool:
    """Verifica se uma linha corresponde a um título de capítulo,
    aceitando variantes com ou sem acento (CAPÍTULO, CAPITULO, etc.).
    Retorna o numeral romano capturado ou False se não for capítulo."""
    m = _RE_CAPITULO.match(linha)
    if m:
        return m.group(1).casefold()
    m2 = _RE_CAPITULO.match(_remover_acentos(linha))
    if m2:
        return m2.group(1).casefold()
    return False


def _chave_linha(texto: str) -> str:
    """Chave normalizada de um trecho para ancorar mudanças no original."""
    return _chave_texto(texto)


def _aplicar_patch_no_texto(
    conteudo: str,
    alteracoes: list[dict],
    remocoes: list[dict],
) -> str:
    """Aplica as mudanças (patch) ao texto original e devolve o 'depois'.

    Cada mudança é ancorada ao primeiro parágrafo do original cuja chave casing
    com 'trecho_original' ou 'rotulo'. Alterações substituem o parágrafo por
    'novo_texto' (e "engolem" os parágrafos-subitem absorvidos, marcando-os
    como removidos); remoções apagam o parágrafo. Tudo o que não foi citado
    permanece intacto — o original nunca é encolhido por omissão do modelo.
    """
    linhas: list[str | None] = list((conteudo or "").splitlines())

    def _encontrar(campos: dict) -> int | None:
        alvos = [
            _chave_linha(campos.get(nome) or "")
            for nome in ("trecho_original", "rotulo")
        ]
        alvos = [a for a in alvos if a]
        for i, linha in enumerate(linhas):
            if linha is None:
                continue
            chave = _chave_linha(linha)
            if any(chave == a or chave.startswith(a) for a in alvos):
                return i
        return None

    for a in alteracoes:
        if not isinstance(a, dict):
            continue
        i = _encontrar(a)
        if i is not None:
            novo_texto = (a.get("novo_texto") or "").strip()
            linhas[i] = novo_texto
            # Alterações que fundem caput + parágrafo/inciso num único bloco
            # também "engolir" os parágrafos originais cobertos: eles precisam
            # sair do 'depois', senão o conteúdo fica duplicado no resultado.
            itens = _proximos_subitens_texto(linhas, i, limite=8)
            for j in _paragrafos_absorvidos(novo_texto, itens):
                linhas[j] = None
    for r in remocoes:
        if not isinstance(r, dict):
            continue
        i = _encontrar(r)
        if i is not None:
            linhas[i] = None
    return "\n".join(linha for linha in linhas if linha is not None)


class PatchIntegrityError(Exception):
    """Erro de integridade do patch: aplicar as mudanças devolveria um texto
    com parágrafos duplicados (conteúdo novo repetindo trecho que continua no
    original). Em vez de emitir um .docx inconsistente, o fluxo falha com
    antecedência para o usuário corrigir a sugestão."""


def _linhas_novas_de(item: dict) -> list[str]:
    textos: list[str] = []
    for campo in ("novo_texto", "texto"):
        valor = (item.get(campo) or "").strip()
        if valor:
            textos.extend(l for l in valor.splitlines() if l.strip())
    return textos


def _duplicacoes_do_patch(
    conteudo: str,
    alteracoes: list[dict],
    remocoes: list[dict],
    adicoes: list[dict],
) -> list[str]:
    """Detecta parágrafos que ficariam DUPLICADOS no texto final.

    Rede de segurança além da marcação de parágrafos absorvidos: simula o patch
    ("depois" com aceitação vs. rejeição) e compara. Se um parágrafo original
    não afetado pelas mudanças reaparecer (chave normalizada) entre os textos
    novos — do novo_texto de uma alteração ou do texto de uma adição — o patch
    está inconsistente. Devolve mensagens legíveis (integra a ``PatchIntegrityError``)."""
    linhas: list[str | None] = list((conteudo or "").splitlines())

    def _encontrar(item: dict, usados: set[int]) -> int | None:
        alvos = [
            _chave_linha(item.get(nome) or "")
            for nome in ("trecho_original", "rotulo")
        ]
        alvos = [a for a in alvos if a]
        for i, linha in enumerate(linhas):
            if i in usados or not linha:
                continue
            chave = _chave_linha(linha)
            if any(chave == a or chave.startswith(a) for a in alvos):
                return i
        return None

    afetados: set[int] = set()
    for a in alteracoes:
        if not isinstance(a, dict):
            continue
        i = _encontrar(a, afetados)
        if i is None:
            continue
        novo_texto = (a.get("novo_texto") or "").strip()
        linhas[i] = novo_texto
        afetados.add(i)
        for j in _paragrafos_absorvidos(
            novo_texto, _proximos_subitens_texto(linhas, i, limite=8)
        ):
            linhas[j] = None
            afetados.add(j)
    for r in remocoes:
        if not isinstance(r, dict):
            continue
        i = _encontrar(r, afetados)
        if i is None:
            continue
        linhas[i] = None
        afetados.add(i)

    novos: set[str] = set()
    for grupo in (alteracoes, adicoes):
        for item in grupo:
            if not isinstance(item, dict):
                continue
            for linha in _linhas_novas_de(item):
                chave = _chave_linha(linha)
                if chave:
                    novos.add(chave)

    duplicados: list[str] = []
    for i, linha in enumerate(linhas):
        if i in afetados or not linha:
            continue
        chave = _chave_linha(linha)
        if chave and chave in novos:
            trecho = next((l.strip() for l in linha.splitlines() if l.strip()), "")
            duplicados.append(f"parágrafo '{trecho[:90]}' duplicado após aplicar a alteração")
    return duplicados


def _estruturar_original(conteudo: str) -> dict:
    """Estrutura mínima a partir do texto original: cada linha vira um item de
    'corpo' (linhas de capítulo ganham tipo 'capitulo'); a primeira linha é o
    'numero'. Suficiente para renderizar a prévia/texto ('Copiar') e para o
    'montar_docx' (versões não-docx) sem heurística frágil de seções."""
    linhas = [linha.strip() for linha in (conteudo or "").splitlines() if linha.strip()]
    corpo: list[dict] = []
    for i, linha in enumerate(linhas):
        if i == 0:
            continue  # vira 'numero'
        corpo.append(
            {
                "tipo": "capitulo" if _tem_capitulo(linha) else "artigo",
                "rotulo": "",
                "texto": linha,
                "subitens": [],
            }
        )
    return {
        "numero": linhas[0] if linhas else "",
        "ementa": "",
        "considerandos": [],
        "preambulo": "",
        "resolutivo": "",
        "corpo": corpo,
        "fechamento": [],
        "local_data": "",
        "assinaturas": [],
    }


def _construir_estrutura(
    conteudo: str,
    alteracoes: list[dict],
    remocoes: list[dict],
    numero: str = "",
    ementa: str = "",
) -> dict:
    """Monta a estrutura final: o original do usuário com o patch aplicado.

    O número/ementa devolvidos pelo modelo (campos preservados do ato, que ele
    pode corrigir) sobrescrevem os originais; o corpo vem sempre do texto
    original + mudanças."""
    depois = _aplicar_patch_no_texto(conteudo, alteracoes, remocoes)
    estrutura = _estruturar_original(depois)
    if (numero or "").strip():
        estrutura["numero"] = numero
    if (ementa or "").strip():
        estrutura["ementa"] = ementa
    return estrutura


def _problemas_do_patch(
    conteudo: str,
    alteracoes: list[dict],
    remocoes: list[dict],
) -> list[str]:
    """Valida a sanidade do patch (substitui a antiga guarda de completude).

    Toda alteração/remoção precisa de campos mínimos e de âncora
    ('trecho_original' ou 'rotulo') resolvível no texto ORIGINAL — sem âncora a
    mudança seria silenciosamente ignorada. Devolve a lista de problemas (vazia
    = patch saudável)."""
    linhas = [linha for linha in (conteudo or "").splitlines() if linha.strip()]
    chaves = [_chave_linha(linha) for linha in linhas]
    problemas: list[str] = []

    def _ancora_ok(item: dict) -> bool:
        alvos = [
            _chave_linha(item.get(nome) or "")
            for nome in ("trecho_original", "rotulo")
        ]
        alvos = [a for a in alvos if a]
        if not alvos:
            return False
        return any(
            chave == a or chave.startswith(a)
            for chave in chaves
            for a in alvos
        )

    for a in alteracoes:
        if not isinstance(a, dict):
            problemas.append("alteracoes possui item invalido")
            continue
        if a.get("tipo") not in ("alterado", "corrigido"):
            problemas.append(
                f"tipo invalido em alteracao: {a.get('tipo')!r} "
                "(use 'alterado' ou 'corrigido'; remocoes ficam em 'remocoes')"
            )
        if not (a.get("novo_texto") or "").strip():
            problemas.append(f"novo_texto ausente: {a.get('rotulo') or '?'}")
        if not (a.get("trecho_original") or "").strip():
            problemas.append(f"trecho_original ausente: {a.get('rotulo') or '?'}")
        elif not _ancora_ok(a):
            problemas.append(
                f"ancora nao encontrada no original: {a.get('rotulo') or '?'}"
            )
    for r in remocoes:
        if not isinstance(r, dict):
            problemas.append("remocoes possui item invalido")
            continue
        if not (r.get("rotulo") or "").strip() or not (r.get("trecho_original") or "").strip():
            problemas.append("remocao sem rotulo/trecho_original")
        elif not _ancora_ok(r):
            problemas.append(
                f"ancora nao encontrada para remocao: {r.get('rotulo') or '?'}"
            )
    return problemas


def _normalizar_referencia(texto: str) -> str:
    """Normaliza um identificador de ato (act_type/act_number/fonte) para a
    comparacao de lastro case e acento-insensivel."""
    return _remover_acentos((texto or "").strip().casefold())


def _documentos_do_contexto(contexto: list[dict]) -> list[dict]:
    """Reune os atos reais recuperados no RAG (item 1: extracao de metadados).

    Cada item do contexto carrega ``source_file``, ``act_type`` e
    ``act_number`` (os metadados do chunk no indice). Deduplica por fonte e
    normaliza os identificadores para a validacao do ``lastro``."""
    vistos: set[str] = set()
    documentos: list[dict] = []
    for item in contexto or []:
        fonte = (item.get("source_file") or "").strip()
        chave = _normalizar_referencia(fonte)
        if not chave:
            chave = _normalizar_referencia(
                f"{item.get('act_type') or ''} {item.get('act_number') or ''}"
            )
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        documentos.append(
            {
                "source_file": fonte,
                "act_type": item.get("act_type") or "",
                "act_number": item.get("act_number") or "",
            }
        )
    return documentos


def _identificar_lastro(lastro: str, documentos: list[dict]) -> dict | None:
    """Tenta identificar o documento especifico do RAG citado no ``lastro``.

    O ``lastro`` e texto livre gerado pelo modelo (ex.: 'IN 07/2026, art. 13
    (validade de 02 anos)'). Considera casado quando o numero do ato aparece
    no lastro E (o tipo do ato OU a fonte) tambem aparece. Se apenas o numero
    casar, aceita somente quando for unico no contexto (evita afirmar um
    documento errado quando ha numeros repetidos)."""
    texto = _normalizar_referencia(lastro)
    if not texto:
        return None
    candidatos = []
    for doc in documentos:
        numero = _normalizar_referencia(doc["act_number"])
        if not numero or numero not in texto:
            continue
        tipo = _normalizar_referencia(doc["act_type"])
        fonte = _normalizar_referencia(doc["source_file"])
        if (tipo and tipo in texto) or (fonte and fonte in texto):
            return doc
        candidatos.append(doc)
    if len(candidatos) == 1:
        return candidatos[0]
    return None


def _validar_lastros(itens: list[dict], contexto: list[dict]) -> None:
    """Valida o ``lastro`` de cada mudança contra os atos do RAG (item 2).

    Nao bloqueia a melhoria: apenas anota cada item para sinalizacao no
    relatorio quando o lastro nao identificar nenhum documento real.
      - ``lastro_validado``: True quando casa com um ato do contexto.
      - ``lastro_fonte``: source_file do ato identificado (vazio se nao casou).
      - ``lastro_aviso``: mensagem legivel quando o lastro nao casa.
    """
    documentos = _documentos_do_contexto(contexto)
    for item in itens:
        if not isinstance(item, dict):
            continue
        lastro = (item.get("lastro") or "").strip()
        if not lastro:
            continue
        ato = _identificar_lastro(lastro, documentos)
        if ato:
            item["lastro_validado"] = True
            item["lastro_fonte"] = ato["source_file"]
        else:
            item["lastro_validado"] = False
            item["lastro_fonte"] = ""
            item["lastro_aviso"] = (
                f"Lastro '{lastro}' nao identifica nenhum ato recuperado no "
                "acervo (a referencia pode ter sido inventada)."
            )


_STOPWORDS_TEMA = {
    "para",
    "tambem",
    "mediante",
    "disposicoes",
    "normativos",
    "quando",
    "sendo",
    "sobre",
    "todas",
    "todos",
    "toda",
    "todo",
    "aos",
    "das",
    "dos",
    "nas",
    "nos",
    "pelas",
    "pelos",
    "qualquer",
    "entre",
    "apos",
    "antes",
    "durante",
    "esta",
    "este",
    "essa",
    "esse",
    "pode",
    "podem",
    "podera",
    "ser",
    "sera",
    "seja",
    "faca",
    "fazer",
    "tendo",
    "devera",
    "deverao",
}


def _tema_do_texto(texto: str) -> set[str]:
    """Conjunto de termos tematicos (nao-gramaticais) de um texto.

    Alimenta a checagem de coerencia tematica: tokens de 3+ caracteres, sem
    acento, sem palavras gramaticais/vazias. Palavras como 'prazo', 'validade',
    'autorização', 'fiscalização' ficam e sao exatamente o que queremos
    comparar entre o dispositivo e a fonte do lastro."""
    palavras = re.findall(r"[a-z0-9]{3,}", _remover_acentos(texto or ""))
    return {p for p in palavras if p not in _STOPWORDS_TEMA}


def _textos_dos_documentos(contexto: list[dict]) -> dict[str, str]:
    """Concatena o texto dos chunks do RAG por ``source_file``."""
    textos: dict[str, str] = {}
    for item in contexto or []:
        fonte = (item.get("source_file") or "").strip()
        if not fonte:
            continue
        trecho = (item.get("text") or "").strip()
        if trecho:
            textos[fonte] = f"{textos.get(fonte, '')}\n{trecho}".strip()
    return textos


def _checar_coerencia_lastros(
    alteracoes: list[dict],
    remocoes: list[dict],
    adicoes: list[dict],
    contexto: list[dict],
) -> None:
    """Checagem de coerencia TEMATICA do lastro (item 3).

    Alem de a referencia existir no acervo (``_validar_lastros``), o conteudo
    do dispositivo alterado/adicionado precisa ser do mesmo assunto do ato
    citado como lastro. Compara os termos tematicos (keywords nao-gramaticais)
    do texto-alvo com o texto completo do ato de origem no RAG. Se nao houver
    termo compartilhado, o lastro e tematicamente divergente:
      - ``requer_decisao_juridica`` = True (o generative DOCX vai sombrear em
        amarelo e nao publicar sem validacao da equipe juridica);
      - ``coerencia_aviso`` legivel, que tambem vai no comentario/docx.
    """
    documentos = _documentos_do_contexto(contexto)
    textos = _textos_dos_documentos(contexto)
    for item in (*alteracoes, *remocoes, *adicoes):
        if not isinstance(item, dict):
            continue
        lastro = (item.get("lastro") or "").strip()
        if not lastro:
            continue
        ato = _identificar_lastro(lastro, documentos)
        if not ato:
            continue
        texto_fonte = textos.get(ato["source_file"])
        if not texto_fonte:
            continue
        alvo = (
            item.get("novo_texto")
            or item.get("texto")
            or item.get("trecho_original")
            or ""
        )
        tema_alvo = _tema_do_texto(alvo)
        if len(tema_alvo) < 3:
            continue
        if tema_alvo & _tema_do_texto(texto_fonte):
            continue
        item["requer_decisao_juridica"] = True
        item["coerencia_aviso"] = (
            f"Coerência temática fraca: o lastro '{lastro}' não compartilha "
            "termos temáticos com o dispositivo tocado pela mudança — requer "
            "confirmação jurídica da equipe antes da publicação."
        )


def _mensagem_retry_especifica(problemas: list[str]) -> str:
    """Mensagem de retry apontando exatamente os problemas de sanidade do patch."""
    detalhes = "; ".join(problemas)
    return (
        "A lista de mudanças está incompleta ou com âncoras erradas. Problemas "
        f"detectados: {detalhes}. "
        "Reenvie o JSON com cada item de 'alteracoes' e 'remocoes' apontando "
        "'trecho_original' copiado EXATAMENTE do texto original (mesma grafia, "
        "sem resumir) e 'novo_texto' completo para as alteracoes. Devolva o "
        "JSON valido e encerrado."
    )


def gerar_estrutura_melhoria(
    conteudo: str,
    tipo_ato: str,
    perfil: PerfilModelo,
    contexto: list[dict],
    valores: dict | None = None,
) -> tuple[dict, list[dict], list[dict], list[dict], list[dict]]:
    """Chama o LLM e devolve (estrutura, alteracoes, remocoes, adicoes, lacunas).

    Modo patch: o modelo devolve apenas as mudanças ancoradas ao texto original;
    a ``estrutura`` final é montada em ``_construir_estrutura`` (original +
    patch), impossibilitando o truncamento por reescrita integral. A guarda vira
    a sanidade do patch (``_problemas_do_patch``): campos mínimos + âncora
    resolvível. Se falhar, re-tenta uma vez com mensagem direcionada; se mesmo
    assim persistir, o melhor esforço é devolvido para que o arquivo sempre seja
    entregue.
    """
    mensagens = [
        {"role": "system", "content": _sistema_melhoria()},
        {
            "role": "user",
            "content": _usuario_melhoria(conteudo, tipo_ato, perfil, contexto, valores),
        },
    ]

    valor_env = os.getenv("MELHORIA_MAX_TOKENS")
    max_tokens = max(
        _MELHORIA_MAX_TOKENS_DEFAULT,
        int(valor_env) if valor_env else _MELHORIA_MAX_TOKENS_DEFAULT,
    )
    max_tokens = min(max_tokens, _TETO_TOKENS_MODELO)

    estrutura: dict = _estruturar_original(conteudo)
    alteracoes: list[dict] = []
    remocoes: list[dict] = []
    adicoes: list[dict] = []
    lacunas: list[dict] = []

    for tentativa in range(2):
        dados = _extrair_json_com_retry(
            mensagens,
            MELHORIA_DEFINITION,
            max_tokens,
            preservar_completo=True,
        )
        numero = dados.get("numero") or ""
        ementa = dados.get("ementa") or ""
        alteracoes = [a for a in (dados.get("alteracoes") or []) if isinstance(a, dict)]
        for a in alteracoes:
            a.setdefault("estado", ESTADO_PENDENTE)
            a.setdefault("requer_decisao_juridica", False)
        remocoes = [r for r in (dados.get("remocoes") or []) if isinstance(r, dict)]
        for r in remocoes:
            r.setdefault("estado", ESTADO_PENDENTE)
        adicoes = [a for a in (dados.get("adicoes_estruturais") or []) if isinstance(a, dict)]
        for a in adicoes:
            a.setdefault("estado", ESTADO_PENDENTE)
            a.setdefault("requer_decisao_juridica", False)
        # Identifica o documento especifico do RAG referenciado pelo 'lastro'
        # de cada mudanca (sinaliza divergencias sem bloquear a melhoria) e
        # verifica a coerencia TEMATICA entre o dispositivo e a fonte citada
        # (lastro de outro assunto -> requer_decisao_juridica).
        for grupo in (alteracoes, remocoes, adicoes):
            _validar_lastros(grupo, contexto)
        _checar_coerencia_lastros(alteracoes, remocoes, adicoes, contexto)
        lacunas = [
            l for l in (dados.get("lacunas_identificadas") or []) if isinstance(l, dict)
        ]

        problemas = _problemas_do_patch(conteudo, alteracoes, remocoes)
        estrutura = _construir_estrutura(conteudo, alteracoes, remocoes, numero, ementa)
        if not problemas:
            return estrutura, alteracoes, remocoes, adicoes, lacunas

        if tentativa == 0:
            mensagens.append(
                {"role": "user", "content": _mensagem_retry_especifica(problemas)}
            )

    # As duas tentativas falharam: entrega o melhor esforço mesmo incompleto,
    # para que o arquivo sempre seja gerado e entregue ao usuário. O conteúdo
    # afetado é sinalizado à parte (aviso) quando possível.
    return estrutura, alteracoes, remocoes, adicoes, lacunas