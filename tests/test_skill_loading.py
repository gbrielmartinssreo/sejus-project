from sejus_project.agent import agent
from sejus_project.agent.skills import loader


def test_template_skill_is_loaded_for_document_request(monkeypatch, tmp_path):
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("INSTRUCOES DA SKILL", encoding="utf-8")
    monkeypatch.setitem(loader.SKILL_RULES, "preenchimento_template", {
        "path": skill_path,
        "keywords": ("portaria",),
    })
    loader._loaded_skills.clear()
    monkeypatch.setattr(
        agent,
        "messages",
        [{"role": "user", "content": "gere uma portaria"}],
    )

    request_messages = agent._messages_for_llm()

    assert request_messages[0]["role"] == "system"
    assert "INSTRUCOES DA SKILL" in request_messages[0]["content"]
    assert "preenchimento_template" in request_messages[0]["content"]


def test_template_skill_is_not_loaded_for_regular_question(monkeypatch):
    loader._loaded_skills.clear()
    loader._loaded_skills["preenchimento_template"] = "NAO DEVERIA APARECER"
    monkeypatch.setattr(
        agent,
        "messages",
        [{"role": "user", "content": "qual o prazo?"}],
    )

    request_messages = agent._messages_for_llm()

    assert "NAO DEVERIA APARECER" not in request_messages[0]["content"]


def test_document_skills_are_selected_by_request(monkeypatch):
    loader._loaded_skills.clear()
    monkeypatch.setattr(
        agent,
        "messages",
        [{
            "role": "user",
            "content": (
                "Compare o que mudou na retificacao do ato, extraia a tabela "
                "do anexo e revise a minuta conforme a estrutura normativa."
            ),
        }],
    )

    selected = loader.skills_for_messages(agent.messages)

    assert "comparacao_retificacao" in selected
    assert "extracao_tabela_anexo" in selected
    assert "estrutura_ato_normativo" in selected
    assert "revisao_documento" in selected


def test_skill_loader_is_outside_agent_module():
    assert not hasattr(agent, "SKILL_RULES")
    assert not hasattr(agent, "_load_skill")
    assert len(loader.SKILL_RULES) == 6
