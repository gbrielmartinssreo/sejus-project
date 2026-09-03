"""Descoberta e carregamento seletivo das skills do agente SEJUS."""
from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent
TEMPLATE_SKILL_PATH = SKILLS_DIR / "preenchimento_template" / "SKILL.md"

SKILL_RULES = {
    "preenchimento_template": {
        "path": TEMPLATE_SKILL_PATH,
        "keywords": (
            "portaria", "decreto", "instrução normativa", "instrucao normativa",
            "retificação", "retificacao", "minuta", "docx", "documento",
        ),
    },
    "consulta_normativa": {
        "path": SKILLS_DIR / "consulta_normativa" / "SKILL.md",
        "keywords": (
            "consult", "vigente", "art.", "artigo", "ato normativo", "lei",
            "decreto", "portaria", "instrução normativa", "instrucao normativa",
            "valor", "prazo", "requisito",
        ),
    },
    "estrutura_ato_normativo": {
        "path": SKILLS_DIR / "estrutura_ato_normativo" / "SKILL.md",
        "keywords": (
            "estrutura", "metadado", "extrair", "o que é esse documento",
            "do que trata", "resumir", "resumo", "decreto", "portaria",
            "instrução normativa", "instrucao normativa", "diário oficial",
        ),
    },
    "extracao_tabela_anexo": {
        "path": SKILLS_DIR / "extracao_tabela_anexo" / "SKILL.md",
        "keywords": (
            "tabela", "anexo", "lotacionograma", "cargo", "função", "funcao",
            "dga", "quantitativo", "linha específica", "linha especifica",
        ),
    },
    "comparacao_retificacao": {
        "path": SKILLS_DIR / "comparacao_retificacao" / "SKILL.md",
        "keywords": (
            "retificação", "retificacao", "republica-se", "republicado",
            "o que mudou", "diferença entre versões", "diferenca entre versoes",
        ),
    },
    "revisao_documento": {
        "path": SKILLS_DIR / "revisao_documento" / "SKILL.md",
        "keywords": (
            "revisar", "revisão", "revisao", "auditar", "conformidade",
            "inconsistência", "inconsistencia", "antes da publicação",
            "antes da publicacao", "minuta",
        ),
    },
}

_loaded_skills: dict[str, str] = {}


def load_skill(skill_name: str) -> str:
    """Carrega uma skill uma vez; arquivo ausente vira conteúdo vazio."""
    if skill_name not in _loaded_skills:
        path = SKILL_RULES[skill_name]["path"]
        try:
            _loaded_skills[skill_name] = path.read_text(encoding="utf-8")
        except OSError:
            _loaded_skills[skill_name] = ""
    return _loaded_skills[skill_name]


def skills_for_messages(conversation_messages) -> list[str]:
    """Seleciona skills pelas palavras-chave das mensagens do usuário."""
    user_text = "\n".join(
        (message.get("content") or "").casefold()
        for message in conversation_messages
        if message.get("role") == "user"
    )
    return [
        skill_name
        for skill_name, rule in SKILL_RULES.items()
        if any(keyword in user_text for keyword in rule["keywords"])
    ]


def build_system_message(base_instructions: str, conversation_messages) -> dict:
    """Monta a mensagem de sistema com as skills relevantes da conversa."""
    content = base_instructions
    for skill_name in skills_for_messages(conversation_messages):
        skill = load_skill(skill_name)
        if skill:
            content += (
                f"\n\n## Skill carregada: {skill_name}\n"
                "Siga as instrucoes especificas desta skill. Se ela mencionar "
                "uma capacidade que nao existe como tool, trate-a como criterio "
                "de analise e informe a limitacao:\n\n"
                f"{skill}"
            )
    return {"role": "system", "content": content}
