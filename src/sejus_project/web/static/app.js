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
var $overlayCampos = document.getElementById("overlay-campos");
var $formCampos = document.getElementById("form-campos");
var $camposLista = document.getElementById("campos-lista");
var $toast = document.getElementById("toast");

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

function anexarMinuta(bubble, minutaHtml, minutaTexto) {
  var zona = document.createElement("div");
  zona.innerHTML = minutaHtml;

  var acoes = document.createElement("div");
  acoes.className = "acoes-minuta";
  acoes.appendChild(botaoAcao("Copiar texto", function () {
    if (navigator.clipboard && minutaTexto) {
      navigator.clipboard.writeText(minutaTexto).then(function () { toast("Minuta copiada."); });
    } else {
      toast("Não foi possível copiar.");
    }
  }));
  acoes.appendChild(botaoAcao("Imprimir / PDF", function () {
    imprimirMinuta(zona);
  }));

  zona.appendChild(acoes);
  bubble.appendChild(zona);
}

function imprimirMinuta(zona) {
  var impressao = document.createElement("div");
  impressao.id = "impressao";
  impressao.innerHTML = zona.querySelector(".minuta-documento").outerHTML;
  document.body.appendChild(impressao);
  window.print();
  document.body.removeChild(impressao);
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
    var dados = await resposta.json();
    pensando.remove();

    var bubble = criarBubble("agente", dados.reply || "Sem resposta.");

    if (dados.minuta_html) {
      anexarMinuta(bubble, dados.minuta_html, dados.minuta_texto);
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

/* ---------------------------------------------------------------- */
/* Eventos                                                             */
/* ---------------------------------------------------------------- */

$formulario.addEventListener("submit", function (evento) {
  evento.preventDefault();
  var texto = $entrada.value;
  $entrada.value = "";
  enviar(texto);
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
    toast("Arquivo '" + dados.filename + "' recebido. Mencione-o na conversa.");
  } catch (erro) {
    toast("Upload falhou: " + erro.message);
  }
  $arquivo.value = "";
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

criarBubble("agente",
  "Olá! Sou o agente da SEJUS. Posso responder sobre os atos normativos " +
  "recuperados do acervo e **gerar minutas** (portarias, instruções normativas, " +
  "decretos etc.) — a minuta aparece aqui na página, pronta para copiar ou " +
  "imprimir em PDF.\n\nEx.: *Gere uma portaria sobre limpeza das unidades*.");