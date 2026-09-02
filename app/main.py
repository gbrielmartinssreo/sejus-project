from sejus_project.agent.agent import executar

from rich.console import Console
from rich.markdown import Markdown
from rich.rule import Rule


console = Console()

cont=0;

while True:

    question = input("\nVocê: ")

    if question.lower() in ["sair", "exit", "quit"]:
        break

    resposta = executar(question)

    console.print(
        Markdown(
            f"**Resposta do Agente:**\n\n{resposta}"
        )
    )
    cont+=1
    console.print(Rule(f"Resposta: {cont}", style="dim cyan"))