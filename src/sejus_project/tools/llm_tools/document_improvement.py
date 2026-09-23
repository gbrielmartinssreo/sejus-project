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
    _ancora_rigorosa,
    _paragrafos_absorvidos,
    _proximos_subitens_texto,
)
from sejus_project.tools.document_infra.docx_builder import (
    _chave_linha as _chave_texto,
)
from sejus_project.tools.document_infra.analise_validacao import (
    detectar_correcoes_redacao,
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

# Origem de cada mudança do patch: diferencia a exigência de lastro.
#   - ORIGEM_ANALISE_APROVADA: o item executa um apontamento da análise que o
#     usuário aprovou pedindo a correção — ausência de lastro no acervo NÃO
#     bloqueia a aplicação nem gera 'requer_decisao_juridica' automático.
#   - ORIGEM_INICIATIVA_MODELO: o item não corresponde a nenhum apontamento —
#     iniciativa própria do modelo; mantém a exigência atual de lastro forte.
ORIGEM_ANALISE_APROVADA = "origem_analise_aprovada"
ORIGEM_INICIATIVA_MODELO = "iniciativa_modelo"

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
                    "sinaliza revisao da equipe juridica antes da publicacao. "
                    "Em itens de 'origem' = 'origem_analise_aprovada', a "
                    "ausencia de lastro no acervo NAO gera true (so criterio "
                    "juridico de fato: depende de decisao explicita, conflita "
                    "com norma superior ou cria despesa sem previsao)."
                ),
            },
            "origem": {
                "type": "string",
                "enum": [ORIGEM_ANALISE_APROVADA, ORIGEM_INICIATIVA_MODELO],
                "description": (
                    "Opcional (padrao 'iniciativa_modelo'). Origem da mudanca: "
                    "'origem_analise_aprovada' quando o item executa um "
                    "apontamento da ANALISE que o usuario aprovou pedindo a "
                    "correcao; 'iniciativa_modelo' quando nao corresponde a "
                    "nenhum apontamento (decisao propria do modelo ao gerar o "
                    "patch). Se ausente, o sistema trata como "
                    "'iniciativa_modelo'."
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
            "origem": {
                "type": "string",
                "enum": [ORIGEM_ANALISE_APROVADA, ORIGEM_INICIATIVA_MODELO],
                "description": (
                    "Opcional (padrao 'iniciativa_modelo'). Origem da mudanca: "
                    "'origem_analise_aprovada' quando o item executa um "
                    "apontamento da ANALISE que o usuario aprovou pedindo a "
                    "correcao; 'iniciativa_modelo' quando nao corresponde a "
                    "nenhum apontamento (decisao propria do modelo ao gerar o "
                    "patch). Se ausente, o sistema trata como "
                    "'iniciativa_modelo'."
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
                    "da publicacao. Em itens de 'origem' = "
                    "'origem_analise_aprovada', a ausencia de lastro no acervo "
                    "NAO gera true (so criterio juridico de fato: depende de "
                    "decisao explicita, conflita com norma superior ou cria "
                    "despesa sem previsao)."
                ),
            },
            "origem": {
                "type": "string",
                "enum": [ORIGEM_ANALISE_APROVADA, ORIGEM_INICIATIVA_MODELO],
                "description": (
                    "Opcional (padrao 'iniciativa_modelo'). Origem da mudanca: "
                    "'origem_analise_aprovada' quando o item executa um "
                    "apontamento da ANALISE que o usuario aprovou pedindo a "
                    "correcao; 'iniciativa_modelo' quando nao corresponde a "
                    "nenhum apontamento (decisao propria do modelo ao gerar o "
                    "patch). Se ausente, o sistema trata como "
                    "'iniciativa_modelo'."
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
MELHORIA_DEFINITION["function"]["parameters"]["properties"]["apontamentos_analise"] = {
    "type": "array",
    "description": (
        "Cobertura dos APONTAMENTOS DA ANALISE quando eles forem informados. "
        "Devolva UMA entrada por apontamento (usando o 'apontamento_id' "
        "recebido). 'status' = 'aplicado' somente quando a mudanca "
        "correspondente existir de fato em 'alteracoes'/'remocoes'/"
        "'adicoes_estruturais' e estiver ancorada no documento; 'nao_aplicado' "
        "apenas com um IMPEDIMENTO CONCRETO (nao use 'nao foi alterado'/"
        "'nao se aplica'). Em 'referencia', informe o rotulo (ou 'o_que') da "
        "mudanca que executou o apontamento. 'motivo' e obrigatorio no "
        "'nao_aplicado'. Nao invente mudancas apenas para cobrir apontamento: "
        "se a analise nao apontar alteracao, deixe as listas de mudancas "
        "vazias."
    ),
    "items": {
        "type": "object",
        "properties": {
            "apontamento_id": {
                "type": "string",
                "description": "ID recebido no bloco APONTAMENTOS DA ANALISE (ex.: 'ap-1a2b3c4d').",
            },
            "status": {
                "type": "string",
                "description": "'aplicado' ou 'nao_aplicado'.",
            },
            "referencia": {
                "type": "string",
                "description": (
                    "Rotulo/o_que da alteracao/remocao/adicao que executou o "
                    "apontamento (ex.: 'Art. 3º', 'Art. 6º-A')."
                ),
            },
            "motivo": {
                "type": "string",
                "description": (
                    "Obrigatorio quando status='nao_aplicado': impedimento "
                    "CONCRETO (ex.: depende de decisão jurídica; conflita com "
                    "a norma X; cria despesa sem previsão). Nao use 'nao foi "
                    "alterado'/'nao se aplica'."
                ),
            },
        },
        "required": ["apontamento_id", "status"],
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
        "redacionais nao precisam de lastro. Em itens de 'origem' = "
        "'origem_analise_aprovada', a falta de lastro no acervo NAO impede a "
        "aplicacao: execute a correcao do apontamento e registre a origem.\n"
        "12. REVOGACAO SO COM NORMA ESPECIFICA: proponha revogacao (em "
        "'remocoes' ou 'alteracoes') apenas quando indicar a norma concreta a "
        "ser revogada, citando nome, tipo, numero e ano (ex.: 'Decreto "
        "2.541/2008'). NUNCA proponha clausula generica do tipo 'ficam "
        "revogadas as disposicoes em contrario' ou revogacao implicita sem essa "
        "citacao. Essa regra vale para as duas origens -- inclusive em itens "
        "'origem_analise_aprovada'.\n"
        "13. REQUER_DECISAO_JURIDICA: marque 'requer_decisao_juridica' = true "
        "em qualquer alteracao ou adicao de 'origem' = 'iniciativa_modelo' que "
        "INOVE em relacao ao original (novo prazo, nova exigencia, novo "
        "percentual, ampliacao de alcance) ou quando o lastro nao estiver "
        "explicito no acervo; nesses casos o sistema destaca o paragrafo em "
        "amarelo e o sinaliza para validacao da equipe juridica antes da "
        "publicacao. Em itens de 'origem' = 'origem_analise_aprovada', a "
        "ausencia de lastro no acervo NAO gera 'requer_decisao_juridica': "
        "marque true somente por criterio juridico de fato (a mudanca depende "
        "de decisao juridica explicita, conflita com norma superior ou cria "
        "despesa sem previsao legal). Se o lastro cobrir integralmente o "
        "conteudo, marque false.\n"
        "14. APONTAMENTOS DA ANALISE (quando informados): trate cada apontamento "
        "como uma TAREFA a executar no documento original. Aplique a alteracao "
        "correspondente e registre-a em 'alteracoes'/'remocoes'/"
        "'adicoes_estruturais'. Nao faca uma revisao independente que ignore ou "
        "substitua esses apontamentos; nao invente correcoes alem deles e das "
        "diretrizes explicitas do usuario. Se a analise nao apontar nenhuma "
        "alteracao acionavel, devolva as listas de mudancas vazias. Para CADA "
        "apontamento informado, devolva uma entrada em 'apontamentos_analise' "
        "com o mesmo 'apontamento_id':\n"
        "   - 'status' = 'aplicado' SOMENTE quando a mudanca existir de fato em "
        "'alteracoes'/'remocoes'/'adicoes_estruturais' e estiver ancorada no "
        "documento, com 'referencia' = rótulo da mudança;\n"
        "   - 'status' = 'nao_aplicado' apenas com um IMPEDIMENTO CONCRETO no "
        "'motivo' (ex.: depende de decisao juridica; conflita com a norma X; "
        "cria despesa sem previsao legal; materia reservada a lei "
        "complementar). NUNCA use 'nao foi alterado', 'nao se aplica' ou "
        "'nao aplicavel' como justificativa;\n"
        "   - se a execucao falhou (trecho nao localizado, por exemplo), deixe "
        "claro o motivo tecnico no 'motivo' — o sistema marcara como falha.\n"
        "Aplicar outras melhorias NAO substitui cumprir os apontamentos "
        "anteriores.\n"
        "15. ORIGEM DE CADA MUDANCA: preencha 'origem' em todo item de "
        "'alteracoes'/'remocoes'/'adicoes_estruturais'. Use "
        "'origem_analise_aprovada' quando a mudanca executa um apontamento da "
        "ANALISE aprovado pelo usuario (pedido de correcao, ex.: 'isso, agora "
        "me de o documento com as correcoes') e 'iniciativa_modelo' quando a "
        "mudanca nao corresponde a nenhum apontamento (decisao propria sua ao "
        "gerar o patch). A origem define a exigencia de lastro: itens "
        "'origem_analise_aprovada' sao aplicados mesmo sem ato no acervo; "
        "itens 'iniciativa_modelo' exigem lastro valido.\n"
        "16. PRESERVE O OBJETIVO DO ACHADO: cada apontamento da analise tem "
        "um objetivo especifico (corrigir grafia, sanar lacuna, renumerar "
        "capitulos, revisar fundamento, etc.). Aplique a mudanca que CUMPRA "
        "esse objetivo no trecho apontado — nao troque o alvo (nao use um "
        "apontamento de renumeracao para inserir conteudo novo, nem um de "
        "redacao para mexer em numeros). Se o apontamento pedir AMPLIACAO de "
        "conteudo (novo prazo, nova exigencia, novo capitulo): proponha em "
        "'adicoes_estruturais' OU, quando nao houver como ancorar sem alterar "
        "a estrutura original, marque 'nao_aplicado' com impedimento concreto "
        "e justificado no 'motivo' (o sistema registra a proposta como "
        "pendente de decisao). Nao transforme a correcao de um tema em "
        "reescrita integral de outro trecho.\n"
        "Retorne apenas o JSON da funcao apresentar_documento_melhorado."
    )


def _usuario_melhoria(
    conteudo: str,
    tipo_ato: str,
    perfil: PerfilModelo,
    contexto: list[dict],
    valores: dict | None = None,
    rotulo_janela: str = "",
) -> str:
    if rotulo_janela:
        cabecalho = (
            "DOCUMENTO ORIGINAL ENVIADO PELO USUARIO. Voce esta vendo o "
            f"TRECHO {rotulo_janela} de um documento maior: liste apenas as "
            "mudancas contidas NESTE trecho (nao reescreva o documento, apenas "
            "aponte alteracoes, remocoes e adicoes, preservando numero, "
            "ementa, objeto e assinaturas). Use 'trecho_original' copiado "
            "EXATAMENTE deste trecho. O restante do documento sera tratado em "
            "outra chamada."
        )
    else:
        cabecalho = (
            "DOCUMENTO ORIGINAL ENVIADO PELO USUARIO (liste as mudancas contra "
            "ESTE texto: nao reescreva o documento, apenas aponte alteracoes, "
            "remocoes e adicoes, preservando numero, ementa, objeto e "
            "assinaturas):"
        )
    partes = [
        cabecalho,
        str(conteudo),
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
        apontamentos = valores.get("apontamentos") or []
        analise_completa = valores.get("analise_completa") or ""
        diretrizes = valores.get("diretrizes") or ""

        if apontamentos:
            partes.extend(
                [
                    "",
                    (
                        "APONTAMENTOS DA ANALISE (TAREFAS a executar no "
                        "documento original; cada item tem ID e deve constar em "
                        "'apontamentos_analise' com resultado ou impedimento "
                        "concreto):"
                    ),
                ]
            )
            for apontamento in apontamentos:
                partes.append(
                    f"- [{apontamento.get('id')}] {apontamento.get('texto')}"
                )
        elif analise_completa:
            partes.extend(
                [
                    "",
                    (
                        "ANALISE ANTERIOR DO PROPRIO DOCUMENTO (contexto; se nao "
                        "houver apontamento acionavel, nao crie mudancas apenas "
                        "para 'atende-la'):"
                    ),
                    str(analise_completa)[:20_000],
                ]
            )

        if diretrizes:
            partes.extend(
                [
                    "",
                    "Diretrizes/pedido do usuario para esta melhoria (siga-as):",
                    str(diretrizes),
                ]
            )
    return "\n".join(partes)


# Orçamento de tokens da melhoria. Como o fluxo usa patch (só as mudanças são
# devolvidas pelo modelo), a resposta é pequena; mesmo assim o orçamento NUNCA
# sobe acima do teto do modelo (evita o erro 400 da API OpenAI em modelos com
# saída limitada, ex.: gpt-4o-mini). O padrão fica abaixo do teto para que
# MELHORIA_MAX_TOKENS ainda tenha efeito em documentos grandes.
_MELHORIA_MAX_TOKENS_DEFAULT = 8192

# Tamanho da janela de documento enviada ao modelo em CADA chamada de patch.
# Antes o documento era cortado em 60k caracteres (o que deixava de fora o
# final de atos de 10+ páginas). Agora o documento é processado em janelas com
# sobreposição, e todas as janelas cobrem o ato inteiro, acumulando as mudanças.
_MELHORIA_JANELA_CHARS_DEFAULT = 60_000
_MELHORIA_JANELA_OVERLAP_CHARS = 2_000

# Tentativas de geração do patch por janela. Cada tentativa pode reprocessar o
# JSON quando a sanidade do patch ou a cobertura dos apontamentos fica
# incompleta/vaga (IDs de apontamento precisam ser ecoados com fidelidade).
_MAX_TENTATIVAS_PATCH = 2


def _tamanho_janela() -> int:
    valor = os.getenv("MELHORIA_JANELA_CHARS")
    try:
        tamanho = int(valor) if valor else _MELHORIA_JANELA_CHARS_DEFAULT
    except ValueError:
        tamanho = _MELHORIA_JANELA_CHARS_DEFAULT
    return max(10_000, tamanho)


def _janelas_conteudo(conteudo: str) -> list[str]:
    """Divide o documento em janelas (por linhas) com sobreposição.

    Cada janela cabe em ``_MELHORIA_JANELA_CHARS_DEFAULT`` (ou no valor de
    ``MELHORIA_JANELA_CHARS``). Não corta no meio de um parágrafo, para manter
    as âncoras ('trecho_original') intactas. Documentos pequenos devolvem uma
    única janela (comportamento de chamada única preservado)."""
    conteudo = conteudo or ""
    tamanho = _tamanho_janela()
    if len(conteudo) <= tamanho:
        return [conteudo]

    janelas: list[str] = []
    atual: list[str] = []
    tam_atual = 0

    for linha in conteudo.splitlines():
        comp = len(linha) + 1
        if atual and tam_atual + comp > tamanho:
            janelas.append("\n".join(atual))
            retidas: list[str] = []
            tam_ret = 0
            for anterior in reversed(atual):
                comp_ant = len(anterior) + 1
                if tam_ret + comp_ant > _MELHORIA_JANELA_OVERLAP_CHARS:
                    break
                retidas.insert(0, anterior)
                tam_ret += comp_ant
            atual = retidas
            tam_atual = tam_ret
        atual.append(linha)
        tam_atual += comp

    if atual:
        janelas.append("\n".join(atual))
    return janelas


def _dedupe_mudancas(itens: list[dict]) -> list[dict]:
    """Remove mudanças repetidas entre janelas sobrepostas, preservando a 1ª."""
    vistos: set[str] = set()
    unicos: list[dict] = []
    for item in itens:
        if not isinstance(item, dict):
            continue
        chave = _chave_linha(
            item.get("o_que") or item.get("trecho_original") or item.get("rotulo") or ""
        )
        if not chave:
            unicos.append(item)
            continue
        if chave in vistos:
            continue
        vistos.add(chave)
        unicos.append(item)
    return unicos


def _dedupe_declarada(declarada: list[dict]) -> list[dict]:
    """Consolida as declarações de cobertura por apontamento_id entre janelas,
    preferindo a declaração 'aplicado' quando houver."""
    por_id: dict[str, dict] = {}
    for item in declarada:
        if not isinstance(item, dict):
            continue
        identificador = str(item.get("apontamento_id") or "").strip()
        atual = por_id.get(identificador)
        if atual is None:
            por_id[identificador] = item
            continue
        status = (item.get("status") or "").strip().casefold()
        if status == "aplicado" and (
            (atual.get("status") or "").strip().casefold() != "aplicado"
        ):
            por_id[identificador] = item
    return list(por_id.values())


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

    for a in alteracoes:
        if not isinstance(a, dict):
            continue
        i = _ancora_rigorosa(linhas, a, set())
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
        i = _ancora_rigorosa(linhas, r, set())
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


def _duplicacoes_detalhado(
    conteudo: str,
    alteracoes: list[dict],
    remocoes: list[dict],
    adicoes: list[dict],
) -> tuple[list[str], dict[int, list[tuple[str, int]]]]:
    """Versão estruturada de ``_duplicacoes_do_patch``.

    Além das mensagens, devolve, para cada parágrafo original que ficaria
    duplicado, a lista de ``(grupo, indice)`` dos itens do patch responsáveis
    por introduzir a linha duplicada — permite descartar apenas os culpados
    em vez de derrubar o patch inteiro."""
    linhas: list[str | None] = list((conteudo or "").splitlines())

    afetados: set[int] = set()
    for a in alteracoes:
        if not isinstance(a, dict):
            continue
        i = _ancora_rigorosa(linhas, a, afetados)
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
        i = _ancora_rigorosa(linhas, r, afetados)
        if i is None:
            continue
        linhas[i] = None
        afetados.add(i)

    fontes: dict[str, list[tuple[str, int]]] = {}
    for nome_grupo, grupo in (
        ("alteracoes", alteracoes),
        ("adicoes_estruturais", adicoes),
    ):
        for indice, item in enumerate(grupo):
            if not isinstance(item, dict):
                continue
            for linha in _linhas_novas_de(item):
                chave = _chave_linha(linha)
                if chave:
                    fontes.setdefault(chave, []).append((nome_grupo, indice))

    duplicados: list[str] = []
    fontes_por_linha: dict[int, list[tuple[str, int]]] = {}
    for i, linha in enumerate(linhas):
        if i in afetados or not linha:
            continue
        chave = _chave_linha(linha)
        if chave and chave in fontes:
            trecho = next((l.strip() for l in linha.splitlines() if l.strip()), "")
            duplicados.append(f"parágrafo '{trecho[:90]}' duplicado após aplicar a alteração")
            fontes_por_linha[i] = fontes[chave]
    return duplicados, fontes_por_linha


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
    duplicados, _ = _duplicacoes_detalhado(conteudo, alteracoes, remocoes, adicoes)
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


def _item_tem_ancora(conteudo: str, item: dict) -> bool:
    """Diz se o 'trecho_original'/'rotulo' de um item ancora no texto ORIGINAL.

    Ancoragem ESTRITA: ``trecho_original`` precisa identificar UM parágrafo
    (único parágrafo ou par título+subtítulo); rótulo genérico (ex.:
    'CONSIDERANDO', 'CAPÍTULO III' — que existem mais de uma vez) é tratado
    como AMBÍGUO e não ancora, para a mudança não atingir parágrafo errado.
    Usado pela sanidade do patch e pela validação da cobertura dos apontamentos:
    mudança sem âncora inequívoca não é aplicada de fato."""
    linhas = (conteudo or "").splitlines()
    return _ancora_rigorosa(linhas, item, set()) is not None


# ---------------------------------------------------------------------------
# Renumeração canônica de capítulos (sequência I..N pela ordem do documento)
# ---------------------------------------------------------------------------

_ARABICOS_POR_ROMANO = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6,
    "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12,
    "xiii": 13, "xiv": 14, "xv": 15, "xvi": 16, "xvii": 17,
    "xviii": 18, "xix": 19, "xx": 20,
}

_ROMANO_POR_ARABICO = {
    valor: romano
    for romano, valor in _ARABICOS_POR_ROMANO.items()
}


def _numeral_capitulo(texto: str) -> str | None:
    """Numeral romano de um cabeçalho 'CAPÍTULO N' (com/sem acento) ou None."""
    sem_acento = _remover_acentos(texto or "")
    m = re.match(r"^\s*capitulo\s+([ivxl]+)\b", sem_acento, re.IGNORECASE)
    return m.group(1).casefold() if m else None


def _romano_para_int(romano: str) -> int | None:
    return _ARABICOS_POR_ROMANO.get((romano or "").casefold())


def _int_para_romano(num: int) -> str:
    romano = _ROMANO_POR_ARABICO.get(num)
    if romano:
        return romano
    return "XX"


def _subtitulo_de_capitulo(linhas: list[str], idx: int) -> str:
    """Primeira linha não-vazia após um título de capítulo (o subtítulo 'DA
    ...') sem atravessar outro cabeçalho. Vazio quando não houver subtítulo."""
    for j in range(idx + 1, len(linhas)):
        linha = (linhas[j] or "").strip()
        if not linha:
            continue
        if _numeral_capitulo(linha):
            return ""
        return linha
    return ""


def _renumeracao_canonica_capitulos(conteudo: str) -> list[dict]:
    """Alterações que reordenam a numeração dos capítulos na forma CANÔNICA.

    Validade pela ORDEM dos cabeçalhos no documento: o k-ésimo capítulo recebe
    o numeral k (I..N), mantendo o PRIMEIRO de cada numeração repetida — no
    caso de dois 'CAPÍTULO III', o primeiro permanece III e os seguintes são
    renumerados. Devolve vazio quando a sequência já é contígua I..N. Cada item
    carrega o subtítulo no ``trecho_original``/``novo_texto`` (âncora única).
    """
    linhas = (conteudo or "").splitlines()
    indices = [
        i
        for i, linha in enumerate(linhas)
        if _romano_para_int(_numeral_capitulo(linha))
    ]
    planos: list[dict] = []
    for pos, idx in enumerate(indices):
        romano_atual = _numeral_capitulo(linhas[idx])
        alvo = pos + 1
        if _romano_para_int(romano_atual) == alvo:
            continue
        sub = _subtitulo_de_capitulo(linhas, idx)
        trecho = linhas[idx].strip()
        novo = f"CAPÍTULO {_int_para_romano(alvo).upper()}"
        if sub:
            trecho = f"{trecho}\n{sub}"
            novo = f"{novo}\n{sub}"
        planos.append(
            {
                "tipo": "corrigido",
                "rotulo": f"CAPÍTULO {romano_atual.upper()}",
                "trecho_original": trecho,
                "novo_texto": novo,
                "detalhe": (
                    "Renumeração automática do sistema para manter a sequência "
                    "numérica dos capítulos (I..N) pela ordem do documento."
                ),
                "renumeracao_engine": True,
            }
        )
    return planos


def _romano_ancorado(conteudo: str, item: dict) -> str | None:
    """Numeral do cabeçalho QUE a mudança alcança no original (via âncora
    estrita), para saber se o item muda o número do capítulo."""
    linhas = (conteudo or "").splitlines()
    i = _ancora_rigorosa(linhas, item, set())
    if i is None:
        return None
    return _numeral_capitulo(linhas[i])


def _substituir_renumeracao_do_modelo(
    conteudo: str,
    alteracoes: list[dict],
    canonicas: list[dict],
) -> tuple[list[dict], list[str]]:
    """Troca/restringe a numeração de capítulos propostas pelo modelo.

    A numeração de capítulos passa a ser garantida pelo SISTEMA: itens do
    modelo que mudam o numeral de um cabeçalho são descartados e substituídos
    pelas alterações canônicas (I..N). Itens que alteram APENAS o subtítulo
    (mesmo numeral) são mantidos. Devolve ``(alteracoes, descartados)``."""
    mantidos: list[dict] = []
    descartados: list[str] = []
    for a in alteracoes:
        if not isinstance(a, dict):
            continue
        num_atual = _romano_ancorado(conteudo, a)
        num_novo = (
            _numeral_capitulo(a.get("novo_texto") or "")
            or _numeral_capitulo(a.get("trecho_original") or "")
        )
        if num_atual and num_novo and num_atual != num_novo:
            descartados.append(a.get("rotulo") or "?")
            continue
        mantidos.append(a)
    mantidos.extend(canonicas)
    return mantidos, descartados


# Marcadores de subdispositivo (§/Parágrafo único/inciso/alínea) no início de
# uma linha. Usado para detectar quando uma alteração removeria subitens sem
# que eles constem do novo texto nem de remoção explícita.
_RE_MARCA_SUBITEM = re.compile(
    r"^\s*(§\s*\d+[ºo°]?|par[áa]grafo\s+[úu]nico|[ivxl]{1,4}\s*[-–—]|[a-z]\))",
    re.IGNORECASE | re.MULTILINE,
)


def _marcas_subitens(texto: str) -> set[str]:
    return {_chave_texto(m.group(1)) for m in _RE_MARCA_SUBITEM.finditer(texto or "")}


def _subitens_perdidos(item: dict) -> list[str]:
    """Subitens citados em ``trecho_original`` que sumiriam da versão ativa.

    Uma alteração só pode descartar §/inciso se a exclusão for declarada no
    patch (``subitens_removidos``/``excluir_subitens``). Sem isso, a perda é
    involuntária — a alteração é rejeitada para não apagar dispositivo vigente.
    Remoções (``tipo == 'removido'``) são exclusões explícitas por definição."""
    if item.get("tipo") == "removido":
        return []
    if item.get("excluir_subitens") or item.get("subitens_removidos"):
        return []
    trecho = item.get("trecho_original") or ""
    novo = item.get("novo_texto") or item.get("texto") or ""
    return sorted(_marcas_subitens(trecho) - _marcas_subitens(novo))


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
    problemas: list[str] = []

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
        elif not _item_tem_ancora(conteudo, a):
            problemas.append(
                f"ancora nao encontrada no original: {a.get('rotulo') or '?'}"
            )
        perdidos = _subitens_perdidos(a)
        if perdidos:
            problemas.append(
                f"alteracao '{a.get('rotulo') or '?'}' descarta subitem(ns) "
                f"({', '.join(perdidos)}) sem declaracao explicita de exclusao"
            )
    for r in remocoes:
        if not isinstance(r, dict):
            problemas.append("remocoes possui item invalido")
            continue
        if not (r.get("rotulo") or "").strip() or not (r.get("trecho_original") or "").strip():
            problemas.append("remocao sem rotulo/trecho_original")
        elif not _item_tem_ancora(conteudo, r):
            problemas.append(
                f"ancora nao encontrada para remocao: {r.get('rotulo') or '?'}"
            )
    return problemas


def _filtrar_patch_valido(
    conteudo: str,
    alteracoes: list[dict],
    remocoes: list[dict],
    adicoes: list[dict],
) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """Descarta mudanças inválidas para NÃO aplicar patch quebrado.

    Remove itens sem âncora/campos mínimos e itens que causariam duplicação no
    texto final. Se não for possível resolver as duplicações, descarta TODAS as
    mudanças (entrega a cópia intacta em vez de um patch inválido). Devolve
    ``(alteracoes, remocoes, adicoes, descartados)`` com os rótulos descartados.
    """

    def _ok_alt(item) -> bool:
        return (
            isinstance(item, dict)
            and item.get("tipo") in ("alterado", "corrigido")
            and bool((item.get("novo_texto") or "").strip())
            and _item_tem_ancora(conteudo, item)
            and not _subitens_perdidos(item)
        )

    def _ok_rem(item) -> bool:
        return (
            isinstance(item, dict)
            and bool((item.get("rotulo") or "").strip())
            and _item_tem_ancora(conteudo, item)
        )

    def _ok_add(item) -> bool:
        return isinstance(item, dict) and bool((item.get("texto") or "").strip())

    def _rotulo(item: dict) -> str:
        return item.get("rotulo") or item.get("o_que") or item.get("tipo") or "?"

    alt = [a for a in alteracoes if _ok_alt(a)]
    rem = [r for r in remocoes if _ok_rem(r)]
    adic = [a for a in adicoes if _ok_add(a)]

    def _rotulo_descarte(a: dict) -> str:
        perdidos = _subitens_perdidos(a)
        if perdidos:
            return (
                f"{_rotulo(a)} (perda involuntaria de subitens: "
                f"{', '.join(perdidos)})"
            )
        return _rotulo(a)

    descartados = (
        [_rotulo_descarte(a) for a in alteracoes if not _ok_alt(a)]
        + [_rotulo(r) for r in remocoes if not _ok_rem(r)]
        + [_rotulo(a) for a in adicoes if not _ok_add(a)]
    )

    while True:
        duplicados, fontes_por_linha = _duplicacoes_detalhado(
            conteudo, alt, rem, adic
        )
        if not duplicados:
            break
        resolvido = False
        for lista_nome in ("alt", "adic", "rem"):
            lista = {"alt": alt, "adic": adic, "rem": rem}[lista_nome]
            for i in range(len(lista)):
                tentativa = lista[:i] + lista[i + 1 :]
                if lista_nome == "alt":
                    restante = _duplicacoes_do_patch(conteudo, tentativa, rem, adic)
                elif lista_nome == "adic":
                    restante = _duplicacoes_do_patch(conteudo, alt, rem, tentativa)
                else:
                    restante = _duplicacoes_do_patch(conteudo, alt, tentativa, adic)
                if not restante:
                    descartados.append(_rotulo(lista[i]))
                    if lista_nome == "alt":
                        alt = tentativa
                    elif lista_nome == "adic":
                        adic = tentativa
                    else:
                        rem = tentativa
                    resolvido = True
                    break
            if resolvido:
                break
        if resolvido:
            continue
        # Nenhuma remoção única resolve: descarta apenas os itens que
        # INTRODUZEM as linhas duplicadas, mantendo o restante do patch
        # aplicável (aplicação parcial em vez de derrubar tudo).
        culpaveis: set[tuple[str, int]] = set()
        for fontes in fontes_por_linha.values():
            culpaveis.update(fontes)
        if not culpaveis:
            # Sem culpado identificável: não aplica patch inválido (entrega a
            # cópia intacta em vez de texto duplicado).
            descartados.extend(_rotulo(a) for a in alt)
            descartados.extend(_rotulo(r) for r in rem)
            descartados.extend(_rotulo(a) for a in adic)
            return [], [], [], descartados
        por_grupo: dict[str, list[int]] = {}
        _MAP_GRUPO = {
            "alteracoes": "alt",
            "remocoes": "rem",
            "adicoes_estruturais": "adic",
        }
        for nome_grupo, indice in culpaveis:
            lista_nome = _MAP_GRUPO.get(nome_grupo)
            if lista_nome is None:
                continue
            por_grupo.setdefault(lista_nome, []).append(indice)
        removidos = 0
        for lista_nome, indices in por_grupo.items():
            lista = {"alt": alt, "adic": adic, "rem": rem}[lista_nome]
            for indice in sorted(indices, reverse=True):
                if 0 <= indice < len(lista):
                    descartados.append(_rotulo(lista[indice]))
                    del lista[indice]
                    removidos += 1
        if removidos == 0:
            # Indices fora do alcance: evita loop infinito (desiste do patch).
            descartados.extend(_rotulo(a) for a in alt)
            descartados.extend(_rotulo(r) for r in rem)
            descartados.extend(_rotulo(a) for a in adic)
            return [], [], [], descartados

    return alt, rem, adic, descartados


# ---------------------------------------------------------------------------
# Cobertura dos apontamentos da análise
# ---------------------------------------------------------------------------


# Status possíveis de um apontamento da análise na cobertura.
STATUS_APLICADO = "aplicado"
STATUS_PENDENTE = "pendente"
STATUS_NAO_APLICADO = "nao_aplicado"
STATUS_FALHOU = "falhou"
# A mudança chegou a ser proposta/executada, mas foi removida depois
# (validação pós-geração, rede de duplicação) — não é falha de execução do
# modelo e não deve ser reportada como "não encaminhado".
STATUS_DESCARTADO = "descartado"

# Motivo genérico NÃO é impedimento: "não foi alterado" não justifica nada.
_RE_MOTIVO_VAGO = re.compile(
    r"^\s*(?:n[ãa]o\s+(?:foi|foram|é|seria|se|houve|p[ôo]de|deu|"
    r"alterad|aplic|realizad|poss[íi]vel)|sem\s+(?:necessidade|altera|"
    r"aplica|previs|efeito|pertin)|imposs[íi]vel|desnecess|"
    r"nada\s+a\s+fazer|n[ãa]o\s+coube|n[ãa]o\s+h[áa]\s+como)",
    re.IGNORECASE,
)
_RE_IMPEDIMENTO_CONCRETO = re.compile(
    r"(decis[ãa]o\s+jur[íi]dica|aprova[çc][ãa]o|compet[êe]ncia|"
    r"lei\s+complementar|or[çc]ament|depende\s+de|requer\s+|necessita\s+de|"
    r"aguarda|pendente\s+de|conflit|incompat|vedad|contradiz|prejudicad|"
    r"falta\s+de\s+base|sem\s+lastro|prazo\s+a\s+definir|norma\s+superior|"
    r"ato\s+superior|revoga[çc]|vig[êe]ncia|indefer|inconstitucional|"
    r"redund|sobrepos|duplic|j[áa]\s+prev|j[áa]\s+disciplin|"
    r"mat[ée]ria\s+reservada|iniciativa|cria\s+despesa)",
    re.IGNORECASE,
)


def _motivo_concreto(motivo: str) -> bool:
    """Diz se o motivo é um impedimento concreto (não uma justificativa vaga).

    Exige um motivo minimamente descritivo e que não seja apenas 'não foi
    alterado'/'não se aplica'. Se apontar uma causa concreta, aceita."""
    texto = (motivo or "").strip()
    if len(texto) < 20:
        return False
    if _RE_IMPEDIMENTO_CONCRETO.search(texto):
        return True
    # Motivo descritivo, porém sem palavra-chave conhecida: só vale se não for
    # claramente vago.
    return len(texto) >= 40 and not _RE_MOTIVO_VAGO.search(texto)


def _mudanca_aplicada(conteudo: str, tipo: str, item: dict) -> tuple[str, str]:
    """Diz se a mudança foi de fato aplicada e, se não, o status/motivo.

    A declaração do modelo não basta: a mudança precisa existir no patch, estar
    ancorada no original e, quando tiver lastro, ter passado na validação de
    lastro/coerência. Falha de âncora é FALHA de execução; lastro divergente é
    PENDÊNCIA de decisão jurídica. Exceção: item de ``origem_analise_aprovada``
    (apontamento aprovado pelo usuário) não é rebaixado a pendente pela simples
    ausência de lastro no acervo — a ausência só bloqueia itens de iniciativa do
    modelo."""
    if (item.get("origem") != ORIGEM_ANALISE_APROVADA) and (
        (item.get("lastro_validado") is False) or item.get("coerencia_aviso")
    ):
        return (
            STATUS_PENDENTE,
            (
                "a mudança foi inserida, mas o lastro não foi validado no "
                "acervo (requer decisão da equipe jurídica antes da publicação)"
            ),
        )
    if tipo == "adicao":
        if not (item.get("texto") or "").strip():
            return STATUS_FALHOU, "a adição não tem texto para inserir"
        return STATUS_APLICADO, ""
    if not _item_tem_ancora(conteudo, item):
        return (
            STATUS_FALHOU,
            (
                "o trecho original não foi localizado no documento "
                "(falha de execução da alteração)"
            ),
        )
    return STATUS_APLICADO, ""


def _localizar_mudanca(
    referencia: str,
    alteracoes: list[dict],
    remocoes: list[dict],
    adicoes: list[dict],
) -> tuple[str, dict, str] | None:
    """Encontra a mudança do patch referida pelo modelo.

    Compara a referência (normalizada) com o rótulo das alterações/remoções e
    o ``o_que`` das adições. Devolve ``(tipo, item, rotulo)`` ou ``None``."""
    alvo = _chave_linha(referencia or "")
    if not alvo:
        return None
    candidatos: list[tuple[str, dict, str]] = []
    for item in alteracoes or []:
        if isinstance(item, dict):
            candidatos.append(("alteracao", item, (item.get("rotulo") or "").strip()))
    for item in remocoes or []:
        if isinstance(item, dict):
            candidatos.append(("remocao", item, (item.get("rotulo") or "").strip()))
    for item in adicoes or []:
        if isinstance(item, dict):
            candidatos.append(("adicao", item, (item.get("o_que") or "").strip()))

    melhor: tuple[str, dict, str] | None = None
    for tipo, item, rotulo in candidatos:
        chave = _chave_linha(rotulo)
        if not chave:
            continue
        casou = (
            chave == alvo
            or chave.startswith(alvo)
            or alvo.startswith(chave)
        )
        if casou:
            if chave == alvo:
                return (tipo, item, rotulo)
            melhor = melhor or (tipo, item, rotulo)
    return melhor


def _marcar_origem(
    alteracoes: list[dict],
    remocoes: list[dict],
    adicoes: list[dict],
    declarada: list[dict],
    apontamentos: list[dict],
) -> None:
    """Atribui deterministicamente a 'origem' de cada mudanca do patch.

    A declaração do modelo não basta: um item só é ``origem_analise_aprovada``
    quando a cobertura (``apontamentos_analise``, status 'aplicado') vincula a
    mudança a um apontamento REAL da análise que o usuário aprovou. Tudo o mais
    vira ``iniciativa_modelo`` — impede que o modelo fuja da exigência de lastro
    rotulando uma mudança por conta própria como 'aprovado pelo usuário'."""
    ids_apontamentos = {
        str(a.get("id")).strip()
        for a in apontamentos
        if isinstance(a, dict)
    }
    vinculadas: set[str] = set()
    for d in declarada or []:
        if not isinstance(d, dict):
            continue
        if (d.get("status") or "").strip().casefold() != "aplicado":
            continue
        identificador = str(d.get("apontamento_id") or "").strip()
        if identificador not in ids_apontamentos:
            continue
        localizada = _localizar_mudanca(
            d.get("referencia") or "", alteracoes, remocoes, adicoes
        )
        if localizada is not None:
            vinculadas.add(_chave_linha(localizada[2]))
    for grupo in (alteracoes, remocoes, adicoes):
        for item in grupo:
            if not isinstance(item, dict):
                continue
            rotulo = _chave_linha(item.get("rotulo") or item.get("o_que") or "")
            item["origem"] = (
                ORIGEM_ANALISE_APROVADA
                if rotulo and rotulo in vinculadas
                else ORIGEM_INICIATIVA_MODELO
            )


def _normalizar_apontamento_id(valor) -> str:
    """Normaliza um ID de apontamento para casamento tolerante.

    O modelo pode devolver o ID com espaços, caixa diferente ou pequenos
    desvios; comparamos a versão alfanumérica em caixa baixa."""
    return re.sub(r"[^a-z0-9]", "", str(valor or "").casefold())


def _procurar_declaracao(
    por_id: dict[str, dict],
    por_id_norm: dict[str, dict],
    identificador,
) -> dict | None:
    """Localiza a declaração do modelo para o ID, tolerando variações."""
    chave = str(identificador or "").strip()
    if chave in por_id:
        return por_id[chave]
    normalizado = _normalizar_apontamento_id(chave)
    if not normalizado:
        return None
    if normalizado in por_id_norm:
        return por_id_norm[normalizado]
    # Desvio parcial: o ID declarado contém/é contido no real (prefixo).
    if len(normalizado) >= 5:
        for chave_real, declaracao in por_id_norm.items():
            if chave_real.startswith(normalizado) or normalizado.startswith(chave_real):
                return declaracao
    return None


_RE_ART_NUM_COBERTURA = re.compile(r"art(?:igo)?\.?\s*(\d{1,3})", re.IGNORECASE)


def _localizar_mudanca_por_apontamento(
    apontamento_texto: str,
    alteracoes: list[dict],
    remocoes: list[dict],
    adicoes: list[dict],
) -> tuple[str, dict, str] | None:
    """Vincula um apontamento a uma mudança de MESMO teor (última tentativa).

    Usado quando a declaração de cobertura veio ausente ou com referência
    divergente: em vez de acusar falha de execução, procura no patch uma
    mudança que trate do mesmo assunto. É conservador — exige sinal forte:
    número de artigo em comum, termo citado entre aspas, ou tokens relevantes
    compartilhados (>= 3, ou >= 2 com ao menos um termo longo de domínio)."""
    texto = apontamento_texto or ""
    tokens_ap = _tokens_relevantes_apontamento(texto)
    artigos_ap = set(_RE_ART_NUM_COBERTURA.findall(_remover_acentos(texto)))
    citados = {
        _chave_texto(t)
        for t in re.findall(r"['\u201c\u201d\"]([^'\u201c\u201d\"]{3,60})['\u201c\u201d\"]", texto)
    }
    for tipo, grupo in (("alteracao", alteracoes), ("remocao", remocoes), ("adicao", adicoes)):
        for item in grupo or []:
            if not isinstance(item, dict):
                continue
            # Correções automáticas do sistema têm reconciliação própria
            # (_reconciliar_cobertura_automatica), com motivo específico.
            if _categoria_item(item):
                continue
            rotulo = item.get("rotulo") or item.get("o_que") or ""
            corpo = " ".join(
                str(item.get(k) or "")
                for k in ("rotulo", "o_que", "trecho_original", "novo_texto", "texto")
            )
            if artigos_ap and artigos_ap & set(
                _RE_ART_NUM_COBERTURA.findall(_remover_acentos(corpo))
            ):
                return (tipo, item, rotulo)
            corpo_chave = _chave_texto(corpo)
            if citados and any(c in corpo_chave for c in citados if c):
                return (tipo, item, rotulo)
            comuns = _tokens_relevantes_apontamento(corpo) & tokens_ap
            if len(comuns) >= 3 or (
                len(comuns) >= 2 and any(len(p) >= 8 for p in comuns)
            ):
                return (tipo, item, rotulo)
    return None


def _tokens_relevantes_apontamento(texto: str) -> set[str]:
    palavras = re.findall(
        r"[a-z0-9]{4,}", _remover_acentos((texto or "").casefold())
    )
    return {p for p in palavras if p not in _STOPWORDS_TEMA}


def _apontamentos_relevantes_janela(
    apontamentos: list[dict], conteudo_janela: str
) -> list[dict]:
    """Apontamentos cujo teor toca o trecho da janela.

    Em documentos multi-janela, cada bloco deve ser cobrado apenas pelos
    apontamentos cujo assunto aparece nele — exigir a cobertura dos demais
    forçaria o modelo a 'não aplicar' o que está em outro trecho."""
    tokens_janela = _tokens_relevantes_apontamento(conteudo_janela)
    relevantes: list[dict] = []
    for apontamento in apontamentos or []:
        if not isinstance(apontamento, dict):
            continue
        tokens_ap = _tokens_relevantes_apontamento(apontamento.get("texto") or "")
        if not tokens_ap or (tokens_ap & tokens_janela):
            relevantes.append(apontamento)
    return relevantes


def _validar_cobertura(
    conteudo: str,
    alteracoes: list[dict],
    remocoes: list[dict],
    adicoes: list[dict],
    apontamentos: list[dict],
    declarada: list[dict] | None,
    patch_descartado: bool = False,
) -> list[dict]:
    """Reconstrói a cobertura a partir dos IDs e das mudanças EFETIVAS.

    Parte dos apontamentos (IDs estáveis) e verifica cada declaração do modelo
    contra o patch realmente montado. O painel sai SEMPRE desta lista original:
    o modelo não pode omitir apontamentos nem trocá-los por outras sugestões.

    - ``aplicado``: mudança existe, está ancorada e sem pendência de lastro;
    - ``pendente``: mudança inserida, mas requer decisão jurídica (lastro) —
      só para itens de ``iniciativa_modelo``; apontamento aprovado não cai em
      pendente apenas por ausência de lastro no acervo;
    - ``descartado``: a mudança foi proposta, mas removida depois (validação
      pós-geração/rede de duplicação) — ``patch_descartado`` ativa esse status;
    - ``falhou``: apontamento não encaminhado, referência inexistente, âncora
      não localizada ou justificativa vaga ('não foi alterado' não justifica);
    - ``nao_aplicado``: impedimento CONCRETO informado pela melhoria.
    """
    por_id = {
        str(d.get("apontamento_id") or "").strip(): d
        for d in (declarada or [])
        if isinstance(d, dict)
    }
    por_id_norm = {
        _normalizar_apontamento_id(d.get("apontamento_id")): d
        for d in (declarada or [])
        if isinstance(d, dict) and _normalizar_apontamento_id(d.get("apontamento_id"))
    }
    cobertura: list[dict] = []
    for apontamento in apontamentos or []:
        if not isinstance(apontamento, dict):
            continue
        identificador = apontamento.get("id")
        entrada = {
            "apontamento_id": identificador,
            "apontamento": apontamento.get("texto") or "",
            "origem": apontamento.get("origem") or "",
            "status": STATUS_FALHOU,
            "referencia": "",
            "motivo": "",
        }
        declaracao = _procurar_declaracao(por_id, por_id_norm, identificador)
        if declaracao is None:
            # Sem declaração: antes de acusar falha, tenta vincular por teor a
            # uma mudança efetiva de mesmo assunto (evita falso 'não encaminhado').
            achado = _localizar_mudanca_por_apontamento(
                entrada["apontamento"], alteracoes, remocoes, adicoes
            )
            if achado is not None:
                tipo, item, rotulo = achado
                status, motivo_tecnico = _mudanca_aplicada(conteudo, tipo, item)
                entrada["referencia"] = rotulo
                entrada["status"] = status
                entrada["motivo"] = (
                    "vinculado por teor à mudança do patch; declaração de "
                    "cobertura ausente na melhoria"
                    + (f" ({motivo_tecnico})" if motivo_tecnico else "")
                )
                cobertura.append(entrada)
                continue
            entrada["motivo"] = (
                "o apontamento não foi encaminhado pela melhoria "
                "(falha de execução)"
            )
            cobertura.append(entrada)
            continue

        referencia = (declaracao.get("referencia") or "").strip()
        motivo = (declaracao.get("motivo") or "").strip()
        status_modelo = (declaracao.get("status") or "").strip().casefold()

        if status_modelo != "aplicado":
            entrada["referencia"] = referencia
            if _motivo_concreto(motivo):
                entrada["status"] = STATUS_NAO_APLICADO
                entrada["motivo"] = motivo
            elif patch_descartado:
                entrada["status"] = STATUS_DESCARTADO
                entrada["motivo"] = (
                    "a mudança declarada foi removida na validação pós-geração "
                    "para preservar a integridade do documento"
                )
            else:
                entrada["status"] = STATUS_FALHOU
                entrada["motivo"] = (
                    "não aplicado sem impedimento concreto"
                    + (f": '{motivo}'" if motivo else "")
                    + " (falha de execução)"
                )
            cobertura.append(entrada)
            continue

        localizada = _localizar_mudanca(referencia, alteracoes, remocoes, adicoes)
        if localizada is None:
            localizada = _localizar_mudanca_por_apontamento(
                entrada["apontamento"], alteracoes, remocoes, adicoes
            )
        if localizada is None:
            entrada["referencia"] = referencia
            if patch_descartado:
                entrada["status"] = STATUS_DESCARTADO
                entrada["motivo"] = (
                    "a mudança declarada foi removida na validação pós-geração "
                    "para preservar a integridade do documento"
                )
            else:
                entrada["status"] = STATUS_FALHOU
                entrada["motivo"] = (
                    "a referência informada não corresponde a nenhuma mudança "
                    "aplicada no patch (falha de execução)"
                )
            cobertura.append(entrada)
            continue

        tipo, item, rotulo = localizada
        status, motivo_tecnico = _mudanca_aplicada(conteudo, tipo, item)
        entrada["referencia"] = rotulo or referencia
        entrada["status"] = status
        entrada["motivo"] = motivo_tecnico
        cobertura.append(entrada)
    return cobertura


# ===========================================================================
# Reconciliar cobertura com correções automáticas (numeração de capítulos e
# redação) na MESMA rastreabilidade dos patches do modelo.
# ===========================================================================

_RE_AP_RENUMERACAO = re.compile(
    r"renumer|cascat\w*|sequ[eê]nci\w* de cap|duplic\w* de cap|"
    r"cap[ií]tulo (?:repetid\w*|duplicad\w*)|numera\w* de cap|"
    r"ordem dos cap[ií]tulos",
    re.IGNORECASE,
)

_RE_AP_REDACAO = re.compile(
    r"ortograf|grafia|parafins|jun[cç][aã]\w*|reda[cç][aã]\w*|"
    r"escrit\w* (?:de|do|incorret)|acentua[cç][aã]\w*",
    re.IGNORECASE,
)


def _categoria_apontamento(apontamento: str) -> str:
    if _RE_AP_RENUMERACAO.search(apontamento or ""):
        return "renumeracao"
    if _RE_AP_REDACAO.search(apontamento or ""):
        return "redacao"
    return ""


def _categoria_item(item: dict) -> str:
    if item.get("renumeracao_engine"):
        return "renumeracao"
    if item.get("automatica"):
        return "redacao"
    return ""


def _reconciliar_cobertura_automatica(
    cobertura: list[dict],
    alteracoes: list[dict],
    apontamentos: list[dict],
) -> list[dict]:
    """Reconcilia a cobertura com as correções automáticas do sistema.

    (1) Apontamentos marcados como ``nao_aplicado``/``falhou`` que são em
    realidade resolvidos por uma correção automática (renumeração canônica de
    capítulos ou correção de redação) passam ao status PELO RESULTADO EFETIVO
    (``aplicado``), justificado com a referência à correção — capítulos I..N
    não podem terminar 'nao_aplicado'. (2) As correções automáticas sem
    apontamento correspondente ganham linha própria de rastreabilidade
    (``auto-*``), com a mesma estrutura do painel."""
    automaticas = [
        a for a in alteracoes
        if isinstance(a, dict) and _categoria_item(a) != ""
    ]
    if not automaticas:
        return cobertura

    usadas: set[int] = set()
    for entrada in cobertura or []:
        if not isinstance(entrada, dict):
            continue
        if (entrada.get("status") or "").strip().casefold() not in (
            STATUS_NAO_APLICADO,
            STATUS_FALHOU,
        ):
            continue
        categoria = _categoria_apontamento(str(entrada.get("apontamento") or ""))
        if not categoria:
            continue
        for i, item in enumerate(automaticas):
            if i in usadas or _categoria_item(item) != categoria:
                continue
            usadas.add(i)
            entrada["status"] = STATUS_APLICADO
            entrada["referencia"] = (
                item.get("rotulo") or entrada.get("referencia") or ""
            )
            entrada["motivo"] = (
                "resolvido por correção automática do sistema: "
                + (item.get("detalhe") or item.get("motivo") or "")
            )
            item["origem_apontamento"] = True
            item["achado_id"] = (
                str(entrada.get("apontamento_id") or "")
                or str(item.get("achado_id") or "")
                or None
            )
            break

    rotulos_ja = {
        _chave_texto(str(c.get("referencia") or ""))
        for c in cobertura
        if isinstance(c, dict)
    }
    ajustes = list(alteracoes)
    for i, item in enumerate(automaticas):
        if i in usadas:
            continue
        rotulo = item.get("rotulo") or ""
        if _chave_texto(rotulo) in rotulos_ja:
            continue
        fonte = "renumeração de capítulos" if _categoria_item(item) == "renumeracao" \
            else "correção automática de redação"
        item2 = dict(item)
        rotulo_num = _numeral_capitulo(item2.get("novo_texto") or "") \
            or _numeral_capitulo(item2.get("trecho_original") or "")
        if rotulo_num and _categoria_item(item) == "renumeracao":
            rotulo = f"CAPÍTULO {rotulo_num.upper()}"
        cobertura.append({
            "apontamento_id": f"auto-{_slug(item, ajustes)}",
            "apontamento": f"Correção automática do sistema: {fonte}.",
            "origem": "sistema (correção automática)",
            "status": STATUS_APLICADO,
            "referencia": rotulo,
            "motivo": item.get("detalhe") or "correção automática aplicada",
        })
        rotulos_ja.add(_chave_texto(rotulo))
    return cobertura


def _slug(item: dict, alteracoes: list[dict]) -> str:
    """Identificador estável (8 hex) para a linha de rastreabilidade auto-*."""
    import hashlib

    chave = (
        str(item.get("rotulo") or "")
        + "|" + str(item.get("buscar") or "")
        + "|" + str(item.get("trecho_original") or "")
        + "|" + str(len(alteracoes))
    )
    return hashlib.sha1(chave.encode("utf-8")).hexdigest()[:8]


def _marcar_origem_apontamento(
    alteracoes: list[dict],
    remocoes: list[dict],
    adicoes: list[dict],
    cobertura: list[dict] | None,
) -> None:
    """Anota ``origem_apontamento=True`` nas mudanças que executaram um
    apontamento aprovado da análise (status 'aplicado' na cobertura).

    O comentário nativo do .docx usa essa marca quando a mudança não tem lastro:
    ela veio de um apontamento da análise aprovado pela correção do usuário —
    não é invenção do modelo."""
    for entrada in cobertura or []:
        if not isinstance(entrada, dict):
            continue
        if (entrada.get("status") or "").strip().casefold() != STATUS_APLICADO:
            continue
        referencia = (entrada.get("referencia") or "").strip()
        if not referencia:
            continue
        localizada = _localizar_mudanca(referencia, alteracoes, remocoes, adicoes)
        if localizada is None:
            continue
        item = localizada[1]
        if isinstance(item, dict):
            item["origem_apontamento"] = True
            item["achado_id"] = (entrada.get("apontamento_id") or "") or None


def _problemas_cobertura(
    apontamentos: list[dict],
    declarada: list[dict] | None,
) -> list[str]:
    """Problemas que disparam retry: cobertura ausente/incompleta ou vaga."""
    if not apontamentos:
        return []
    ids = {str(a.get("id")) for a in apontamentos if isinstance(a, dict)}
    declarada = [d for d in (declarada or []) if isinstance(d, dict)]
    if not declarada:
        return ["cobertura dos apontamentos da analise ausente em 'apontamentos_analise'"]

    problemas: list[str] = []
    ids_declarados = {
        str(d.get("apontamento_id") or "").strip() for d in declarada
    }
    faltantes = sorted(ids - ids_declarados)
    if faltantes:
        problemas.append(
            "apontamento(s) sem entrada em 'apontamentos_analise': "
            + ", ".join(faltantes)
        )
    for d in declarada:
        status = (d.get("status") or "").strip().casefold()
        if status not in ("aplicado", "nao_aplicado"):
            problemas.append(
                f"status invalido para {d.get('apontamento_id')!r}: use "
                "'aplicado' ou 'nao_aplicado'"
            )
            continue
        if status == "nao_aplicado":
            motivo = (d.get("motivo") or "").strip()
            if not motivo:
                problemas.append(
                    f"apontamento {d.get('apontamento_id')!r} 'nao_aplicado' "
                    "sem motivo"
                )
            elif not _motivo_concreto(motivo):
                problemas.append(
                    f"apontamento {d.get('apontamento_id')!r} com motivo vago "
                    f"({motivo!r}): informe um impedimento concreto"
                )
    return problemas


def _mensagem_retry_cobertura(problemas: list[str]) -> str:
    detalhes = "; ".join(problemas)
    return (
        "A cobertura dos apontamentos da analise esta incompleta ou vaga. "
        f"Problemas: {detalhes}. Reenvie o JSON com 'apontamentos_analise' "
        "contendo UMA entrada por apontamento_id recebido. Para cada item: "
        "EXECUTE a alteracao e marque 'aplicado' com 'referencia' = rótulo da "
        "mudança; ou informe 'nao_aplicado' com um impedimento CONCRETO (ex.: "
        "depende de decisão jurídica, conflita com a norma X, cria despesa sem "
        "previsão). NUNCA use 'não foi alterado'/'não se aplica' como "
        "justificativa. Devolva o JSON valido e encerrado."
    )


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
      - ``lastro_aviso``: mensagem legivel quando o lastro nao casa. Para itens
        de ``origem_analise_aprovada`` (apontamento aprovado), em vez do aviso
        de 'lastro inventado' registra a origem, pois a ausência de lastro não
        bloqueia a aplicação nesse caso.
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
            if item.get("origem") == ORIGEM_ANALISE_APROVADA:
                item["lastro_aviso"] = (
                    "Origem: apontamento da análise, aprovado pelo usuário."
                )
            else:
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

    Itens de ``origem_analise_aprovada`` (apontamento aprovado pelo usuario)
    sao EXCLUIDOS desta checagem: para eles a ausencia de lastro no acervo nao
    gera 'requer_decisao_juridica' automatico nem pendencia -- a origem e que
    vai para o comentario.
    """
    documentos = _documentos_do_contexto(contexto)
    textos = _textos_dos_documentos(contexto)
    for item in (*alteracoes, *remocoes, *adicoes):
        if not isinstance(item, dict):
            continue
        if item.get("origem") == ORIGEM_ANALISE_APROVADA:
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

    Documentos maiores que a janela (``_MELHORIA_JANELA_CHARS_DEFAULT``) são
    processados em blocos com sobreposição: o ato inteiro é coberto e as
    mudanças de todas as janelas são acumuladas/consolidadas, em vez de o final
    do documento ficar de fora como no antigo corte de 60k caracteres.
    """
    valor_env = os.getenv("MELHORIA_MAX_TOKENS")
    max_tokens = max(
        _MELHORIA_MAX_TOKENS_DEFAULT,
        int(valor_env) if valor_env else _MELHORIA_MAX_TOKENS_DEFAULT,
    )
    max_tokens = min(max_tokens, _TETO_TOKENS_MODELO)

    apontamentos = [
        a for a in ((valores or {}).get("apontamentos") or []) if isinstance(a, dict)
    ]

    janelas = _janelas_conteudo(conteudo)

    if len(janelas) == 1:
        dados, alteracoes, remocoes, adicoes, lacunas, declarada = _gerar_patch_janela(
            janelas[0], tipo_ato, perfil, contexto, valores, apontamentos, max_tokens
        )
        numero = dados.get("numero") or ""
        ementa = dados.get("ementa") or ""
    else:
        # Documento maior que a janela: processa em blocos com sobreposição e
        # acumula as mudanças. Cada bloco é ancorado no próprio trecho, então as
        # âncoras ('trecho_original') continuam válidas no documento inteiro.
        numero = ""
        ementa = ""
        alteracoes = []
        remocoes = []
        adicoes = []
        lacunas = []
        declarada = []
        total = len(janelas)
        for indice, janela in enumerate(janelas, start=1):
            dados, alts, rems, adds, lacs, decl = _gerar_patch_janela(
                janela,
                tipo_ato,
                perfil,
                contexto,
                valores,
                apontamentos,
                max_tokens,
                rotulo_janela=f"{indice}/{total}",
            )
            if not numero:
                numero = dados.get("numero") or ""
            if not ementa:
                ementa = dados.get("ementa") or ""
            alteracoes.extend(alts)
            remocoes.extend(rems)
            adicoes.extend(adds)
            lacunas.extend(lacs)
            declarada.extend(decl)
        alteracoes = _dedupe_mudancas(alteracoes)
        remocoes = _dedupe_mudancas(remocoes)
        adicoes = _dedupe_mudancas(adicoes)

    # Correções automáticas de redação verificáveis sobre o ORIGINAL (ex.:
    # 'parágrafo único do 20' -> 'parágrafo único do art. 20', 'parafins' ->
    # 'para fins') entram no MESMO patch e na MESMA rastreabilidade dos itens
    # do modelo. São ancoradas por parágrafo inteiro e não alteram o conteúdo —
    # só a grafia/forma, preservando o objetivo de cada achado.
    alteracoes = _dedupe_mudancas(
        detectar_correcoes_redacao(conteudo) + alteracoes
    )

    # Numeração de capítulos é responsabilidade do SISTEMA: renumeração
    # proposta pelo modelo (que tende a deslocar TODA a cascata e errar o
    # primeiro capítulo duplicado) é substituída pela forma canônica —
    # sequência I..N pela ordem dos documentos, preservando os títulos.
    canonicas = _renumeracao_canonica_capitulos(conteudo)
    alteracoes, descartados_renumeracao = _substituir_renumeracao_do_modelo(
        conteudo, alteracoes, canonicas
    )

    # Não aplica patch inválido: descarta itens sem âncora ou que causariam
    # duplicação. Se nada sobrar, a entrega será a cópia intacta do original.
    alteracoes, remocoes, adicoes, descartados = _filtrar_patch_valido(
        conteudo, alteracoes, remocoes, adicoes
    )
    descartados = descartados_renumeracao + descartados

    # Cobertura dos apontamentos validada UMA vez sobre o patch consolidado.
    declarada_consolidada = _dedupe_declarada(declarada)
    cobertura = _validar_cobertura(
        conteudo,
        alteracoes,
        remocoes,
        adicoes,
        apontamentos,
        declarada_consolidada,
    )
    cobertura = _reconciliar_cobertura_automatica(
        cobertura, alteracoes, apontamentos
    )
    estrutura = _construir_estrutura(conteudo, alteracoes, remocoes, numero, ementa)
    estrutura["_cobertura_analise"] = cobertura
    estrutura["_declarada"] = declarada_consolidada
    estrutura["_descartados"] = descartados
    return estrutura, alteracoes, remocoes, adicoes, lacunas


def _gerar_patch_janela(
    conteudo: str,
    tipo_ato: str,
    perfil: PerfilModelo,
    contexto: list[dict],
    valores: dict | None,
    apontamentos: list[dict],
    max_tokens: int,
    rotulo_janela: str = "",
) -> tuple[dict, list[dict], list[dict], list[dict], list[dict], list[dict]]:
    """Executa o LLM em modo patch para UMA janela do documento.

    Devolve (dados, alteracoes, remocoes, adicoes, lacunas, declarada). A
    sanidade do patch (``_problemas_do_patch``) é validada contra o texto da
    janela e, em caso de falha, re-tenta com a mensagem direcionada. A cobertura
    dos apontamentos é exigida em janela única; em documentos multi-janela é
    cobrada apenas pelos apontamentos cujo assunto aparece na janela (e o
    consolidado ainda é validado em ``gerar_estrutura_melhoria``).
    """
    apontamentos_janela = (
        _apontamentos_relevantes_janela(apontamentos, conteudo)
        if rotulo_janela
        else apontamentos
    )
    valores_janela = valores
    if valores is not None and (valores.get("apontamentos") is not None):
        valores_janela = dict(valores)
        valores_janela["apontamentos"] = apontamentos_janela
    mensagens = [
        {"role": "system", "content": _sistema_melhoria()},
        {
            "role": "user",
            "content": _usuario_melhoria(
                conteudo, tipo_ato, perfil, contexto, valores_janela, rotulo_janela
            ),
        },
    ]

    dados: dict = {}
    alteracoes: list[dict] = []
    remocoes: list[dict] = []
    adicoes: list[dict] = []
    lacunas: list[dict] = []
    declarada: list[dict] = []

    for tentativa in range(_MAX_TENTATIVAS_PATCH):
        dados = _extrair_json_com_retry(
            mensagens,
            MELHORIA_DEFINITION,
            max_tokens,
            preservar_completo=True,
        )
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
        lacunas = [
            l for l in (dados.get("lacunas_identificadas") or []) if isinstance(l, dict)
        ]
        declarada = [
            d for d in (dados.get("apontamentos_analise") or []) if isinstance(d, dict)
        ]
        # Origem deterministica por item: muda o tratamento do lastro. Itens que
        # a cobertura vincula a um apontamento da analise sao 'aprovados' e nao
        # sao bloqueados pela ausencia de lastro; o restante e 'iniciativa do
        # modelo' e mantem a exigencia atual de lastro forte. A vinculacao usa a
        # lista COMPLETA de apontamentos (não apenas os da janela).
        _marcar_origem(alteracoes, remocoes, adicoes, declarada, apontamentos)
        # Identifica o documento especifico do RAG referenciado pelo 'lastro'
        # de cada mudanca (sinaliza divergencias sem bloquear a melhoria) e
        # verifica a coerencia TEMATICA entre o dispositivo e a fonte citada
        # (lastro de outro assunto -> requer_decisao_juridica; exclui itens
        # 'origem_analise_aprovada').
        for grupo in (alteracoes, remocoes, adicoes):
            _validar_lastros(grupo, contexto)
        _checar_coerencia_lastros(alteracoes, remocoes, adicoes, contexto)

        problemas = _problemas_do_patch(conteudo, alteracoes, remocoes)
        problemas_cob = _problemas_cobertura(apontamentos_janela, declarada)
        if not problemas and not problemas_cob:
            break

        if tentativa < _MAX_TENTATIVAS_PATCH - 1:
            mensagens_retry = []
            if problemas:
                mensagens_retry.append(_mensagem_retry_especifica(problemas))
            if problemas_cob:
                mensagens_retry.append(_mensagem_retry_cobertura(problemas_cob))
            if mensagens_retry:
                mensagens.append(
                    {"role": "user", "content": "\n".join(mensagens_retry)}
                )

    # As tentativas podem falhar: entrega o melhor esforço mesmo incompleto,
    # para que o arquivo sempre seja gerado e entregue ao usuário.
    return dados, alteracoes, remocoes, adicoes, lacunas, declarada