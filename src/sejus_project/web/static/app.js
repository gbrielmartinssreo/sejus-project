"use strict";

/* ---------------------------------------------------------------- */
/* Utilidades                                                         */
/* ---------------------------------------------------------------- */

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

/* Inline leve: negrito, italico e codigo */
function inline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

function splitRow(line) {
  return line.replace(/^\s*\||\|\s*$/g, "").split("|").map(function (c) {
    return c.trim();
  });
}

/* Markdown minimalista (sem CDN, sem dependencias) */
function renderMarkdown(md) {
  var lines = String(md).split("\n");
  var html = "";
  var i = 0;

  while (i < lines.length) {
    var l = lines[i];

    if (/^```/.test(l)) {
      var code = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) {
        code.push(lines[i]);
        i += 1;
      }
      i += 1;
      html += "<pre>" + escapeHtml(code.join("\n")) + "</pre>";
      continue;
    }

    if (/^\|/.test(l) && lines[i + 1] && /^\|[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
      var header = splitRow(l);
      i += 2;
      var rows = [];
      while (i < lines.length && /^\|/.test(lines[i])) {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      html += "<table><thead><tr>" +
        header.map(function (c) { return "<th>" + inline(c) + "</th>"; }).join("") +
        "</tr></thead><tbody>" +
        rows.map(function (r) {
          return "<tr>" + r.map(function (c) { return "<td>" + inline(c) + "</td>"; }).join("") + "</tr>";
        }).join("") +
        "</tbody></table>";
      continue;
    }

    var itemRe = /^(\s*[-*+]\s+|\s*\d+[.)]\s+)/;
    if (itemRe.test(l)) {
      var ordered = /^\s*\d+[.)]/.test(l);
      var re = ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*+]\s+/;
      var items = [];
      while (i < lines.length && re.test(lines[i])) {
        items.push(lines[i].replace(re, ""));
        i += 1;
      }
      var tag = ordered ? "ol" : "ul";
      html += "<" + tag + ">" + items.map(function (x) {
        return "<li>" + inline(x) + "</li>";
      }).join("") + "</" + tag + ">";
      continue;
    }

    var h = l.match(/^(#{1,4})\s+(.*)/);
    if (h) {
      var nivel = h[1].length;
      html += "<h" + nivel + ">" + inline(h[2]) + "</h" + nivel + ">";
      i += 1;
      continue;
    }

    if (/^\s*$/.test(l)) {
      i += 1;
      continue;
    }

    var par = [];
    while (i < lines.length && !/^\s*$/.test(lines[i])) {
      par.push(lines[i]);
      i += 1;
    }
    html += "<p>" + inline(par.join(" ")) + "</p>";
  }

  return html;
}

/* ---------------------------------------------------------------- */
/* DOM                                                                 */
/* ---------------------------------------------------------------- */

var $mensagens = document.getElementById("mensagens");
var $formulario = document.getElementById("formulario");
var $entrada = document.getElementById("entrada");
var $arquivo = document.getElementById("arquivo");
var $arquivoNome = document.getElementById("arquivo-nome");
var $arquivoModelo = document.getElementById("arquivo-modelo");
var $arquivoMelhorar = document.getElementById("arquivo-melhorar");
var $modeloStatus = document.getElementById("modelo-status");
var $overlayCampos = document.getElementById("overlay-campos");
var $formCampos = document.getElementById("form-campos");
var $camposLista = document.getElementById("campos-lista");
var $toast = document.getElementById("toast");
var $limpar = document.getElementById("limpar");

var DICIONARIO_CAMPOS = {
  numero_ato: "Número do ato",
  data_ato: "Data",
  local: "Local",
  signatario: "Signatário",
  cargo: "Cargo",
  ementa: "Ementa",
};

var aguardando = false;

function toast(mensagem) {
  $toast.textContent = mensagem;
  $toast.hidden = false;
  setTimeout(function () { $toast.hidden = true; }, 3500);
}

function atualizarModeloStatus(modelo) {
  if (modelo && modelo.filename) {
    $modeloStatus.textContent =
      "Modelo ativo: " + modelo.filename + (modelo.tipo_ato ? " (" + modelo.tipo_ato + ")" : "") +
      " — as próximas minutas seguirão este formato.";
    $modeloStatus.hidden = false;
  } else {
    $modeloStatus.hidden = true;
  }
}

function criarBubble(role, htmlOuTexto) {
  var div = document.createElement("div");
  div.className = "mensagem " + role;
  if (role === "agente") {
    var md = document.createElement("div");
    md.className = "markdown";
    md.innerHTML = renderMarkdown(htmlOuTexto);
    div.appendChild(md);
  } else {
    div.textContent = htmlOuTexto;
  }
  $mensagens.appendChild(div);
  $mensagens.scrollTop = $mensagens.scrollHeight;
  return div;
}

function bubblePensando() {
  var div = document.createElement("div");
  div.className = "mensagem agente pensando";
  div.textContent = "Analisando";
  $mensagens.appendChild(div);
  $mensagens.scrollTop = $mensagens.scrollHeight;
  return div;
}

function botaoAcao(texto, aoClicar) {
  var b = document.createElement("button");
  b.className = "botao";
  b.type = "button";
  b.textContent = texto;
  b.addEventListener("click", aoClicar);
  return b;
}

function anexarMinuta(bubble, dados) {
  var zona = document.createElement("div");
  zona.className = "zona-minuta";

  var card = document.createElement("div");
  card.className = "doc-card";

  var icone = document.createElement("div");
  icone.className = "doc-icone";
  icone.textContent = "\u{1F4C4}";

  var info = document.createElement("div");
  info.className = "doc-info";
  var nome = document.createElement("span");
  nome.className = "doc-nome";
  nome.textContent = dados.minuta_nome || "minuta.docx";
  var meta = document.createElement("span");
  meta.className = "doc-meta";
  meta.textContent = "Documento gerado";
  info.appendChild(nome);
  info.appendChild(meta);

  card.appendChild(icone);
  card.appendChild(info);
  zona.appendChild(card);

  var acoes = document.createElement("div");
  acoes.className = "acoes-minuta";
  if (dados.minuta_docx) {
    acoes.appendChild(botaoAcao("Baixar DOCX", function () {
      baixarArquivo(dados.minuta_docx);
    }));
  }
  if (dados.minuta_pdf) {
    acoes.appendChild(botaoAcao("Baixar PDF", function () {
      baixarArquivo(dados.minuta_pdf);
    }));
  }
  if (dados.minuta_texto) {
    acoes.appendChild(botaoAcao("Copiar texto", function () {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(dados.minuta_texto).then(function () { toast("Minuta copiada."); });
      } else {
        toast("Não foi possível copiar.");
      }
    }));
  }
  zona.appendChild(acoes);

  bubble.appendChild(zona);
}

function baixarArquivo(url) {
  var a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function anexarPendencia(bubble, campos) {
  var acoes = document.createElement("div");
  acoes.className = "acoes-minuta";
  acoes.appendChild(botaoAcao("Preencher automaticamente", function () {
    enviar("pode inventar");
  }));
  acoes.appendChild(botaoAcao("Informar campos", function () {
    abrirOverlayCampos(campos);
  }));
  bubble.appendChild(acoes);
}

function anexarArquivoEnviado(nome) {
  var bubble = criarBubble("usuario", "Arquivo enviado: " + nome);
  var acoes = document.createElement("div");
  acoes.className = "acoes-minuta";
  acoes.appendChild(botaoAcao("Analisar", function () {
    enviar("Analise o arquivo '" + nome + "'.");
  }));
  bubble.appendChild(acoes);
}

function anexarComparacao(bubble, dados) {
  var painel = document.createElement("div");
  painel.className = "painel-comparacao";

  var titulo = document.createElement("div");
  titulo.className = "comparacao-titulo";
  titulo.textContent = "Comparação antes/depois";
  painel.appendChild(titulo);

  if (dados.alteracoes && dados.alteracoes.length) {
    var lista = document.createElement("ul");
    lista.className = "lista-alteracoes";
    dados.alteracoes.forEach(function (alteracao) {
      var li = document.createElement("li");
      var tipo = document.createElement("span");
      tipo.className = "alteracao-tipo " + (alteracao.tipo || "alterado");
      tipo.textContent = alteracao.tipo || "alterado";
      li.appendChild(tipo);
      if (alteracao.o_que) {
        li.appendChild(document.createTextNode(" " + alteracao.o_que));
      }
      if (alteracao.detalhe) {
        var det = document.createElement("div");
        det.className = "alteracao-detalhe";
        det.textContent = alteracao.detalhe;
        li.appendChild(det);
      }
      lista.appendChild(li);
    });
    painel.appendChild(lista);
  }

  if (dados.url_original) {
    var acoes = document.createElement("div");
    acoes.className = "acoes-minuta";
    acoes.appendChild(botaoAcao("Baixar original", function () {
      baixarArquivo(dados.url_original);
    }));
    painel.appendChild(acoes);
  }

  bubble.appendChild(painel);
}

function abrirOverlayCampos(campos) {
  $camposLista.innerHTML = "";
  campos.forEach(function (chave) {
    var rotulo = DICIONARIO_CAMPOS[chave] || chave;
    var label = document.createElement("label");
    label.textContent = rotulo;
    var input = document.createElement("input");
    input.name = chave;
    label.appendChild(input);
    $camposLista.appendChild(label);
  });
  $overlayCampos.hidden = false;
  var primeiro = $camposLista.querySelector("input");
  if (primeiro) primeiro.focus();
}

function fecharOverlayCampos() {
  $overlayCampos.hidden = true;
}

/* ---------------------------------------------------------------- */
/* Rede                                                                */
/* ---------------------------------------------------------------- */

async function enviar(texto) {
  if (aguardando) return;
  texto = String(texto || "").trim();
  if (!texto) return;

  aguardando = true;
  $entrada.disabled = true;
  fecharOverlayCampos();
  criarBubble("usuario", texto);
  var pensando = bubblePensando();

  try {
    var resposta = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: texto }),
    });

    var dados;
    try {
      dados = await resposta.json();
    } catch (e) {
      throw new Error(
        resposta.ok
          ? "O servidor nao devolveu JSON valido."
          : "Servidor indisponivel (HTTP " + resposta.status + ")."
      );
    }

    pensando.remove();

    if (!resposta.ok) {
      criarBubble("agente", dados.reply || dados.detail || "Erro ao processar o pedido.");
      return;
    }

    var bubble = criarBubble("agente", dados.reply || "Sem resposta.");

    if (dados.modelo_usuario) {
      atualizarModeloStatus(dados.modelo_usuario);
    }

    if (dados.comparacao) {
      anexarComparacao(bubble, dados.comparacao);
    }
    if (dados.minuta_docx) {
      anexarMinuta(bubble, dados);
    } else if (dados.pendente) {
      anexarPendencia(bubble, dados.campos || []);
    }
  } catch (erro) {
    pensando.remove();
    criarBubble("agente", "Erro ao comunicar com o servidor: " + erro.message);
  } finally {
    aguardando = false;
    $entrada.disabled = false;
    $entrada.focus();
  }
}

function limparConversa() {
  if (aguardando) return;
  fetch("/api/conversa/limpar", { method: "POST" })
    .catch(function () { return null; })
    .then(function () {
      $mensagens.innerHTML = "";
      atualizarModeloStatus(null);
      criarBubble("agente",
        "Conversa reiniciada. Posso **gerar minutas** ou consultar o acervo " +
        "normativo da SEJUS. Ex.: *Gere uma portaria sobre limpeza das unidades*.");
    });
}

/* ---------------------------------------------------------------- */
/* Eventos                                                             */
/* ---------------------------------------------------------------- */

$formulario.addEventListener("submit", function (evento) {
  evento.preventDefault();
  var texto = $entrada.value;
  $entrada.value = "";
  $entrada.style.height = "auto";
  enviar(texto);
});

$entrada.addEventListener("input", function () {
  this.style.height = "auto";
  this.style.height = Math.min(this.scrollHeight, 160) + "px";
});

$entrada.addEventListener("keydown", function (evento) {
  if (evento.key === "Enter" && !evento.shiftKey) {
    evento.preventDefault();
    $formulario.requestSubmit();
  }
});

$arquivo.addEventListener("change", async function () {
  var arquivo = $arquivo.files[0];
  if (!arquivo) return;
  var form = new FormData();
  form.append("arquivo", arquivo);
  try {
    var resposta = await fetch("/api/upload", { method: "POST", body: form });
    var dados = await resposta.json();
    if (!resposta.ok) throw new Error(dados.detail || "falha no upload");
    anexarArquivoEnviado(dados.filename);
    toast("Arquivo '" + dados.filename + "' recebido.");
  } catch (erro) {
    toast("Upload falhou: " + erro.message);
  }
  $arquivo.value = "";
});

$arquivoMelhorar.addEventListener("change", async function () {
  var arquivo = $arquivoMelhorar.files[0];
  if (!arquivo) return;
  var form = new FormData();
  form.append("arquivo", arquivo);
  try {
    var resposta = await fetch("/api/upload", { method: "POST", body: form });
    var dados = await resposta.json();
    if (!resposta.ok) throw new Error(dados.detail || "falha no upload");
    criarBubble("usuario", "Arquivo enviado para melhoria: " + dados.filename);
    enviar("Melhore e compare o arquivo '" + dados.filename + "'");
  } catch (erro) {
    toast("Upload falhou: " + erro.message);
  }
  $arquivoMelhorar.value = "";
});

$arquivoModelo.addEventListener("change", async function () {
  var arquivo = $arquivoModelo.files[0];
  if (!arquivo) return;
  var form = new FormData();
  form.append("arquivo", arquivo);
  try {
    var resposta = await fetch("/api/modelo", { method: "POST", body: form });
    var dados = await resposta.json();
    if (!resposta.ok) throw new Error(dados.detail || "falha no upload do modelo");
    atualizarModeloStatus(dados);
    toast("Modelo '" + dados.filename + "' definido.");
  } catch (erro) {
    toast("Modelo falhou: " + erro.message);
  }
  $arquivoModelo.value = "";
});

$formCampos.addEventListener("submit", function (evento) {
  evento.preventDefault();
  var partes = [];
  var inputs = $camposLista.querySelectorAll("input");
  inputs.forEach(function (input) {
    var valor = input.value.trim();
    if (valor) partes.push(input.name + " " + valor);
  });
  fecharOverlayCampos();
  enviar("Informar campos: " + partes.join(", ") + ".");
});

document.getElementById("cancelar-campos").addEventListener("click", fecharOverlayCampos);
$limpar.addEventListener("click", limparConversa);

criarBubble("agente",
  "Olá! Sou o agente da SEJUS. Posso responder sobre os atos normativos " +
  "recuperados do acervo e **gerar minutas** (portarias, instruções normativas, " +
  "decretos etc.) — o documento aparece aqui como anexo, pronto para baixar em " +
  "DOCX ou PDF.\n\n" +
  "Você também pode enviar um ato para **análise** (📎) ou para **melhorar e " +
  "comparar** (botão ✨): o agente reescreve o mesmo documento com melhorias " +
  "e adequações, e mostra a comparação antes/depois.\n\n" +
  "O botão 📄 define um ato existente como **modelo** de formatação: a nova " +
  "minuta seguirá exatamente o formato do documento enviado.\n\n" +
  "Ex.: *Gere uma portaria sobre limpeza das unidades*.");