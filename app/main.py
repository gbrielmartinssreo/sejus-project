from sejus_project.agent.agent import executar
from rich.console import Console
from rich.markdown import Markdown

console = Console()

question = input("Digite sua pergunta: ")

resposta = executar(question)

console.print(Markdown(f"**Resposta do Agente:**\n\n{resposta}"))