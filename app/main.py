from sejus_project.agent.agent import executar

from rich.console import Console
from rich.markdown import Markdown
from rich.rule import Rule
from rich.live import Live

console = Console()

cont = 0

while True:
    question = input("\nVocê: ")

    if question.lower() in ["sair", "exit", "quit"]:
        break

    cont += 1

    console.print("\n[dim]Agente está pensando...[/dim]")

    resposta = executar(question)

    texto = ""

    with Live(
        Markdown("**Resposta do Agente:**\n\n"),
        console=console,
        refresh_per_second=15,
    ) as live:

        for chunk in resposta:
            texto += chunk

            live.update(
                Markdown(
                    f"**Resposta do Agente:**\n\n{texto}"
                )
            )

    console.print(Rule(f"Resposta: {cont}", style="dim cyan"))
