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

var ICONE_ARQUIVO =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/></svg>';
var ICONE_CHECK =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M20 6L9 17l-5-5"/></svg>';
var ICONE_DOWNLOAD =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 21h16"/></svg>';
var ICONE_COPIAR =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M4 16V4a2 2 0 012-2h10"/></svg>';

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

function botaoAcao(texto, aoClicar, classe) {
  var b = document.createElement("button");
  b.className = "botao" + (classe ? " " + classe : "");
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
  icone.innerHTML = ICONE_ARQUIVO;

  var info = document.createElement("div");
  info.className = "doc-info";

  var status = document.createElement("div");
  status.className = "doc-status";
  status.innerHTML = ICONE_CHECK + "<span>" + escapeHtml("Documento gerado") + "</span>";

  var nome = document.createElement("span");
  nome.className = "doc-nome";
  nome.textContent = dados.minuta_nome || "minuta.docx";

  info.appendChild(status);
  info.appendChild(nome);

  card.appendChild(icone);
  card.appendChild(info);
  zona.appendChild(card);

  var acoes = document.createElement("div");
  acoes.className = "acoes-minuta";
  if (dados.minuta_docx) {
    acoes.appendChild(botaoAcaoChild(ICONE_DOWNLOAD, "Baixar DOCX", function () {
      baixarArquivo(dados.minuta_docx);
    }, "primary"));
  }
  if (dados.minuta_pdf) {
    acoes.appendChild(botaoAcaoChild(ICONE_DOWNLOAD, "Baixar PDF", function () {
      baixarArquivo(dados.minuta_pdf);
    }, "secondary"));
  }
  if (dados.minuta_texto) {
    acoes.appendChild(botaoAcaoChild(ICONE_COPIAR, "Copiar texto", function () {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(dados.minuta_texto).then(function () { toast("Minuta copiada."); });
      } else {
        toast("Não foi possível copiar.");
      }
    }, "secondary"));
  }
  info.appendChild(acoes);

  bubble.appendChild(zona);
}

function botaoAcaoChild(svg, texto, aoClicar, classe) {
  var b = botaoAcao("", aoClicar, classe);
  var el = document.createElement("span");
  el.textContent = texto;
  b.insertAdjacentHTML("afterbegin", svg);
  b.appendChild(el);
  return b;
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

function criarBubbleArquivo(label, nome) {
  var div = document.createElement("div");
  div.className = "mensagem usuario upload";
  var lbl = document.createElement("span");
  lbl.className = "label";
  lbl.textContent = label;
  var fn = document.createElement("span");
  fn.className = "filename";
  fn.textContent = nome;
  div.appendChild(lbl);
  div.appendChild(fn);
  $mensagens.appendChild(div);
  $mensagens.scrollTop = $mensagens.scrollHeight;
  return div;
}

function anexarArquivoEnviado(nome) {
  criarBubbleArquivo("Arquivo enviado", nome);
}

function anexarComparacao(bubble, dados) {
  var painel = document.createElement("div");
  painel.className = "painel-comparacao";

  var titulo = document.createElement("div");
  titulo.className = "comparacao-titulo";
  titulo.textContent = "Comparação antes / depois";
  var contagem = document.createElement("span");
  contagem.className = "count";
  contagem.textContent = (dados.alteracoes ? dados.alteracoes.length : 0) + " alterações";
  titulo.appendChild(contagem);
  painel.appendChild(titulo);

  if (dados.alteracoes && dados.alteracoes.length) {
    var lista = document.createElement("ul");
    lista.className = "lista-alteracoes";
    dados.alteracoes.forEach(function (alteracao) {
      var li = document.createElement("li");
      li.className = "alteracao-item";
      var tipo = document.createElement("span");
      tipo.className = "alteracao-tipo " + (alteracao.tipo || "alterado");
      tipo.textContent = alteracao.tipo || "alterado";
      li.appendChild(tipo);
      var corpo = document.createElement("div");
      corpo.className = "item-body";
      if (alteracao.o_que) {
        var h = document.createElement("h4");
        h.textContent = alteracao.o_que;
        corpo.appendChild(h);
      }
      if (alteracao.detalhe) {
        var det = document.createElement("p");
        det.className = "alteracao-detalhe";
        det.textContent = alteracao.detalhe;
        corpo.appendChild(det);
      }
      li.appendChild(corpo);
      lista.appendChild(li);
    });
    painel.appendChild(lista);
  }

  if (dados.url_original) {
    var acoes = document.createElement("div");
    acoes.className = "acoes-minuta";
    acoes.appendChild(botaoAcao("Baixar versão original", function () {
      baixarArquivo(dados.url_original);
    }, "ghost"));
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
    criarBubbleArquivo("Arquivo enviado para melhoria", dados.filename);
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

/* ---------- Tema ---------- */

var $btnEscuro = document.getElementById("btn-escuro");
var $btnClaro = document.getElementById("btn-claro");

function aplicarTema(tema) {
  document.body.setAttribute("data-theme", tema);
  $btnEscuro.classList.toggle("is-active", tema === "dark");
  $btnClaro.classList.toggle("is-active", tema === "light");
  try { localStorage.setItem("sejus_tema", tema); } catch (e) { /* ignore */ }
}

var temaSalvo = "dark";
try { temaSalvo = localStorage.getItem("sejus_tema") || "dark"; } catch (e) { /* ignore */ }
aplicarTema(temaSalvo);

$btnEscuro.addEventListener("click", function () { aplicarTema("dark"); });
$btnClaro.addEventListener("click", function () { aplicarTema("light"); });

criarBubble("agente",
  "Olá! Sou o agente da SEJUS. Posso responder sobre os atos normativos " +
  "recuperados do acervo e **gerar minutas** (portarias, instruções normativas, " +
  "decretos etc.) — o documento aparece aqui como anexo, pronto para baixar em " +
  "DOCX ou PDF. Você também pode enviar um ato para **análise** ou para " +
  "**melhorar e comparar**: eu reescrevo o mesmo documento com melhorias e " +
  "adequações, e mostro a comparação antes/depois. Definir um ato existente " +
  "como **modelo** faz a nova minuta seguir exatamente o formato enviado.\n\n" +
  "Ex.: *Gere uma portaria sobre limpeza das unidades.*");