import grimp
import networkx as nx

PACKAGE = "sejus_project"

graph = grimp.build_graph(
    PACKAGE,
    include_external_packages=False,
)

G = nx.DiGraph()

for module in graph.modules:
    G.add_node(module)

for importer in graph.modules:
    for imported in graph.find_modules_directly_imported_by(importer):
        G.add_edge(importer, imported)

nx.write_graphml(G, "architecture.graphml")

print(f"Gerado architecture.graphml")
print(f"Módulos: {G.number_of_nodes()}")
print(f"Dependências: {G.number_of_edges()}")