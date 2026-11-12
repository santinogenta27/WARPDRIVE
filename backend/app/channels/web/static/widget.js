/* Enterprise Data AI — widget de chat (decisión 031).
   Uso: <script src="…/widget.js" data-chat="ENLACE" async></script>
   Opcionales: data-theme="auto|light|dark", data-position="right|left",
   data-mode="float|page|inline" con data-target="#selector", data-note="texto al pie".
   Un solo archivo, sin dependencias. Vive en un Shadow DOM: no toca el CSS del sitio. */
(function () {
  "use strict";
  var script = document.currentScript;
  if (!script || !window.fetch || !document.body.attachShadow) return;
  var attr = function (name, fallback) { return script.getAttribute("data-" + name) || fallback; };
  var slug = attr("chat", "");
  var base = new URL(script.src, location.href).origin;
  var mode = attr("mode", "float");
  var embedded = mode !== "float";
  var KEY = "edai-chat-" + (slug || "demo");
  var canal = slug ? "?canal=" + encodeURIComponent(slug) : "";

  var CSS = `
:host{all:initial}
*{box-sizing:border-box}
.root{--surface:255,255,255;--ink:#12151a;--muted:#5d6470;--line:rgba(18,21,26,.09);
--soft:rgba(18,21,26,.055);--warn-bg:rgba(180,83,9,.1);--warn:#8a4a06;--glass:.86;
--shadow:0 24px 64px -16px rgba(15,23,42,.28),0 2px 10px rgba(15,23,42,.06);
font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
color:var(--ink);-webkit-font-smoothing:antialiased;text-align:left}
.root[data-theme=dark]{--surface:23,25,30;--ink:#f1f2f4;--muted:#a4aab4;
--line:rgba(255,255,255,.1);--soft:rgba(255,255,255,.08);--warn-bg:rgba(251,191,36,.12);
--warn:#f5c56b;--glass:.84;--shadow:0 24px 64px -16px rgba(0,0,0,.7),0 2px 10px rgba(0,0,0,.3)}
button,textarea{font:inherit;color:inherit}
button{cursor:pointer;-webkit-tap-highlight-color:transparent}
:focus{outline:none}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
[hidden]{display:none!important}

.launcher{position:fixed;bottom:max(20px,env(safe-area-inset-bottom));right:20px;z-index:2147483000;
width:56px;height:56px;border-radius:50%;border:0;background:var(--accent);color:var(--on-accent);
display:grid;place-items:center;box-shadow:0 10px 28px -6px rgba(15,23,42,.35);
transition:transform .35s cubic-bezier(.2,1.3,.4,1),opacity .25s;opacity:0;transform:scale(.6)}
.root[data-side=left] .launcher{right:auto;left:20px}
.root[data-ready] .launcher{opacity:1;transform:none}
.root[data-ready] .launcher:hover{transform:scale(1.06)}
.root[data-ready] .launcher:active{transform:scale(.96)}
.launcher svg{position:absolute;width:26px;height:26px;transition:transform .3s cubic-bezier(.2,1.2,.4,1),opacity .2s}
.launcher .i-close{opacity:0;transform:rotate(-70deg) scale(.6)}
.root[data-open] .launcher .i-chat{opacity:0;transform:rotate(70deg) scale(.6)}
.root[data-open] .launcher .i-close{opacity:1;transform:none}
.badge{position:absolute;top:-3px;right:-3px;min-width:20px;height:20px;padding:0 6px;border-radius:10px;
background:#d92d20;color:#fff;font-size:12px;font-weight:650;line-height:20px;text-align:center;
box-shadow:0 0 0 2px rgb(var(--surface))}

.panel{display:flex;flex-direction:column;overflow:hidden;background:rgba(var(--surface),.985);
border:1px solid var(--line);border-radius:24px;box-shadow:var(--shadow)}
@supports ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){
.panel{background:rgba(var(--surface),var(--glass));
-webkit-backdrop-filter:blur(26px) saturate(1.7);backdrop-filter:blur(26px) saturate(1.7)}}
.root[data-mode=float] .panel{position:fixed;z-index:2147483001;right:20px;
bottom:calc(max(20px,env(safe-area-inset-bottom)) + 70px);width:384px;max-width:calc(100vw - 40px);
height:min(640px,calc(100vh - 120px));height:min(640px,calc(100dvh - 120px));
transform-origin:calc(100% - 28px) calc(100% + 40px);opacity:0;visibility:hidden;
transform:translateY(14px) scale(.94);
transition:transform .22s ease-in,opacity .18s ease-in,visibility 0s .22s}
.root[data-mode=float][data-side=left] .panel{right:auto;left:20px;transform-origin:28px calc(100% + 40px)}
.root[data-mode=float][data-open] .panel{opacity:1;visibility:visible;transform:none;
transition:transform .46s cubic-bezier(.16,1.22,.3,1),opacity .2s ease-out,visibility 0s}
.root:not([data-mode=float]){height:100%}
.root:not([data-mode=float]) .panel{width:100%;height:100%}

header{display:flex;align-items:center;gap:12px;padding:16px 14px 14px 18px;border-bottom:1px solid var(--line)}
.logo{flex:none;width:38px;height:38px;border-radius:12px;background:var(--accent);color:var(--on-accent);
display:grid;place-items:center;font-weight:650;font-size:17px;overflow:hidden}
.logo img{width:100%;height:100%;object-fit:cover;display:block}
.title{flex:1;min-width:0}
.title b{display:block;font-size:15.5px;font-weight:650;letter-spacing:-.01em;white-space:nowrap;
overflow:hidden;text-overflow:ellipsis}
.title span{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--muted)}
.title i{width:7px;height:7px;border-radius:50%;background:#12a150}
.root[data-offline] .title i{background:#b4690e}
.icon{flex:none;width:36px;height:36px;border-radius:12px;border:0;background:transparent;color:var(--muted);
display:grid;place-items:center;transition:background .15s,color .15s}
.icon:hover{background:var(--soft);color:var(--ink)}
.icon svg{width:18px;height:18px}

.bar{padding:9px 18px;font-size:13px;background:var(--warn-bg);color:var(--warn);
display:flex;align-items:center;gap:10px}
.bar span{flex:1}
.link{border:0;background:none;padding:2px 4px;border-radius:6px;font-weight:600;font-size:13px;
text-decoration:underline;text-underline-offset:3px}

.log{flex:1;overflow-y:auto;overscroll-behavior:contain;padding:20px 16px 10px;display:flex;
flex-direction:column;gap:10px;scrollbar-width:thin;scrollbar-color:var(--line) transparent}
.log:focus-visible{outline-offset:-5px;border-radius:14px}
.row{display:flex;flex-direction:column;max-width:84%;align-items:flex-start;
animation:in .32s cubic-bezier(.2,.9,.3,1) both}
.row.user{align-self:flex-end;align-items:flex-end}
.bubble{padding:10px 14px;border-radius:20px;border-bottom-left-radius:7px;background:var(--soft);
overflow-wrap:anywhere}
.user .bubble{background:var(--accent);color:var(--on-accent);border-radius:20px;border-bottom-right-radius:7px}
.user.failed .bubble{opacity:.55}
.who{font-size:11.5px;font-weight:600;color:var(--muted);margin:0 0 4px 12px}
.meta{font-size:12px;color:var(--warn);margin:5px 4px 0}
.note{align-self:center;max-width:92%;padding:9px 14px;border-radius:14px;background:var(--warn-bg);
color:var(--warn);font-size:13px;text-align:center;animation:in .32s both}
.typing{display:flex;gap:5px;padding:15px 16px}
.typing i{width:7px;height:7px;border-radius:50%;background:var(--muted);animation:dot 1.25s infinite ease-in-out}
.typing i:nth-child(2){animation-delay:.16s}.typing i:nth-child(3){animation-delay:.32s}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px;animation:in .4s .08s both}
.chip{border:1px solid var(--line);background:transparent;border-radius:999px;padding:8px 14px;
font-size:14px;text-align:left;transition:background .15s,border-color .15s}
.chip:hover{background:var(--soft);border-color:transparent}
.state{margin:auto;padding:24px;text-align:center;color:var(--muted);display:grid;gap:14px;justify-items:center}
.skel{height:38px;border-radius:20px;background:var(--soft);animation:pulse 1.4s infinite ease-in-out}
.skel:nth-child(2){width:62%}.skel:nth-child(3){width:44%;align-self:flex-end}.skel:nth-child(4){width:70%}
.btn{border:1px solid var(--line);background:transparent;border-radius:999px;padding:9px 18px;font-weight:600;
font-size:14px;transition:background .15s}
.btn:hover{background:var(--soft)}

.composer{display:flex;align-items:flex-end;gap:8px;padding:10px 12px 12px}
textarea{flex:1;min-width:0;resize:none;min-height:46px;max-height:124px;padding:12px 16px;border-radius:23px;
border:1px solid var(--line);background:var(--soft);font-size:16px;line-height:1.35;overflow-y:hidden;
transition:border-color .15s,background .15s}
textarea::placeholder{color:var(--muted);opacity:1}
textarea:focus-visible{outline:none;border-color:var(--focus);background:transparent}
textarea:disabled{opacity:.55}
.send{flex:none;width:46px;height:46px;border-radius:50%;border:0;background:var(--accent);color:var(--on-accent);
display:grid;place-items:center;transition:transform .2s cubic-bezier(.2,1.3,.4,1),opacity .15s}
.send:hover:not(:disabled){transform:scale(1.07)}
.send:disabled{opacity:.35;cursor:default}
.send svg{width:20px;height:20px}
.ended{padding:6px 18px 14px;display:grid;justify-items:center}
.foot{padding:0 18px max(12px,env(safe-area-inset-bottom));text-align:center;font-size:11.5px;color:var(--muted)}
.foot:empty{padding-top:0;padding-bottom:max(2px,env(safe-area-inset-bottom))}

@keyframes in{from{opacity:0;transform:translateY(8px) scale(.98)}to{opacity:1;transform:none}}
@keyframes dot{0%,70%,100%{opacity:.3;transform:none}35%{opacity:1;transform:translateY(-3px)}}
@keyframes pulse{0%,100%{opacity:.55}50%{opacity:1}}

@media (max-width:520px){
.root,.root[data-theme=dark]{--glass:.97}
.root[data-mode=float] .panel{inset:0;width:100%;max-width:none;height:100%;border-radius:0;border:0;
transform-origin:50% 100%;transform:translateY(24px)}
.root[data-mode=float][data-open] .launcher{opacity:0;pointer-events:none}
.root[data-mode=page] .panel{border-radius:0;border:0;box-shadow:none}
header{padding-top:max(14px,env(safe-area-inset-top))}
.row{max-width:88%}}
@media (prefers-reduced-motion:reduce){
*,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;
transition-duration:.01ms!important}
.root[data-mode=float] .panel{transform:none}}
@media (forced-colors:active){.bubble,.panel,.launcher,.send{border:1px solid CanvasText}}
`;

  var ICONS = {
    chat: '<svg class="i-chat" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M5.5 4h13A3.5 3.5 0 0 1 22 7.5v7a3.5 3.5 0 0 1-3.5 3.5H12l-5.2 3.6a.5.5 0 0 1-.8-.4V18h-.5A3.5 3.5 0 0 1 2 14.5v-7A3.5 3.5 0 0 1 5.5 4z"/></svg>',
    close: '<svg class="i-close" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    fresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12a8 8 0 0 1 13.7-5.6L20 8.5"/><path d="M20 4v4.5h-4.5"/><path d="M20 12a8 8 0 0 1-13.7 5.6L4 15.5"/><path d="M4 20v-4.5h4.5"/></svg>',
    send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5M5.5 11.5L12 5l6.5 6.5"/></svg>'
  };

  var host = document.createElement("div");
  host.setAttribute("data-enterprise-chat", slug);
  var target = embedded ? document.querySelector(attr("target", "")) : null;
  if (embedded) host.style.cssText = "display:block;width:100%;height:100%";
  (target || document.body).appendChild(host);
  var shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = "<style>" + CSS + "</style>" +
    '<div class="root" data-mode="' + (embedded ? mode : "float") + '" data-side="' + attr("position", "right") + '">' +
    (embedded ? "" : '<button class="launcher" type="button" aria-expanded="false" aria-controls="panel">' +
      ICONS.chat + ICONS.close + '<span class="badge" hidden></span></button>') +
    '<section class="panel" id="panel" role="' + (embedded ? "region" : "dialog") + '" aria-label="Chat">' +
    '<header><div class="logo" aria-hidden="true"></div><div class="title"><b></b>' +
    '<span><i aria-hidden="true"></i><em style="font-style:normal">Asistente virtual</em></span></div>' +
    '<button class="icon fresh" type="button" aria-label="Empezar una conversación nueva" hidden>' + ICONS.fresh + "</button>" +
    (embedded ? "" : '<button class="icon shut" type="button" aria-label="Cerrar el chat">' + ICONS.close.replace(' class="i-close"', "") + "</button>") +
    "</header>" +
    '<div class="bar" role="status" hidden></div>' +
    '<div class="log" role="log" aria-live="polite" aria-relevant="additions" tabindex="0" aria-label="Conversación"></div>' +
    '<div class="composer"><label class="sr" for="msg">Mensaje</label>' +
    '<textarea id="msg" rows="1" maxlength="1000" placeholder="Escribí tu consulta…" enterkeyhint="send"></textarea>' +
    '<button class="send" type="button" aria-label="Enviar" disabled>' + ICONS.send + "</button></div>" +
    '<div class="ended" hidden><button class="btn" type="button">Empezar una conversación nueva</button></div>' +
    '<div class="foot"></div></section></div>';

  var $ = function (selector) { return shadow.querySelector(selector); };
  var root = $(".root"), panel = $(".panel"), launcher = $(".launcher"), badge = $(".badge");
  // El campo de texto no va en un formulario: en un sitio que envuelve la página en uno,
  // el navegador descartaría uno anidado.
  var log = $(".log"), form = $(".composer"), input = $("textarea"), send = $(".send");
  var bar = $(".bar"), ended = $(".ended"), foot = $(".foot"), fresh = $(".fresh");
  var note = attr("note", "");

  var cfg = null, open = embedded, busy = false, typing = null, unread = 0, lastFocus = null;
  var state = load() || blank();

  function blank() { return { session: null, items: [], remaining: null, locked: false }; }
  function load() { try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { return null; } }
  function save() {
    try { state.items = state.items.slice(-80); localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* sin almacenamiento */ }
  }

  // -- color y tema ------------------------------------------------------------------------
  function rgb(hex) { var n = parseInt(hex.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; }
  function lum(c) {
    var f = function (v) { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  }
  function contrast(a, b) { var x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  function mix(c, to, amount) { return c.map(function (v, i) { return Math.round(v + (to[i] - v) * amount); }); }
  function theme() {
    var wanted = attr("theme", "auto");
    if (wanted === "light" || wanted === "dark") return wanted;
    // El sitio primero: si su fondo es oscuro, tema oscuro. Si no se puede saber, el sistema.
    var nodes = [document.body, document.documentElement];
    for (var i = 0; i < nodes.length; i++) {
      var m = /rgba?\(([^)]+)\)/.exec(getComputedStyle(nodes[i]).backgroundColor || "");
      if (!m) continue;
      var p = m[1].split(/[,\s/]+/).map(parseFloat);
      if (p.length < 4 || p[3] > 0.5) return lum(p) < 0.2 ? "dark" : "light";
    }
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function paint() {
    var dark = theme() === "dark";
    root.setAttribute("data-theme", dark ? "dark" : "light");
    var surface = dark ? [23, 25, 30] : [255, 255, 255];
    var accent = rgb(cfg ? cfg.accent : "#1e3a5f");
    // El acento tiene que distinguirse del fondo del panel en los dos temas.
    for (var i = 0; i < 6 && contrast(accent, surface) < 1.7; i++) accent = mix(accent, dark ? [255, 255, 255] : [0, 0, 0], 0.22);
    var white = [255, 255, 255], ink = [12, 14, 18];
    root.style.setProperty("--accent", "rgb(" + accent + ")");
    root.style.setProperty("--on-accent", contrast(accent, white) >= contrast(accent, ink) ? "#fff" : "#0c0e12");
    root.style.setProperty("--focus", contrast(accent, surface) >= 3 ? "rgb(" + accent + ")" : (dark ? "#f1f2f4" : "#12151a"));
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paint);

  // -- dibujo ------------------------------------------------------------------------------
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function escapeHtml(text) {
    return text.replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  }
  // Negrita estilo WhatsApp (*texto*) y saltos de línea; todo lo demás se muestra como texto.
  function format(text) {
    return escapeHtml(text).replace(/\*(\S(?:[^*\n]*\S)?)\*/g, "<strong>$1</strong>").replace(/\n/g, "<br>");
  }
  function scrollDown() { log.scrollTop = log.scrollHeight; }
  function brand() {
    var logo = $(".logo");
    $(".title b").textContent = cfg.name;
    panel.setAttribute("aria-label", "Chat con " + cfg.name);
    if (launcher) setLauncherLabel();
    logo.textContent = "";
    if (cfg.logo) {
      var img = el("img");
      img.alt = "";
      img.src = /^(https?:|data:|blob:)/.test(cfg.logo) ? cfg.logo : base + cfg.logo;
      img.onerror = function () { logo.textContent = cfg.name.charAt(0).toUpperCase(); };
      logo.appendChild(img);
    } else logo.textContent = cfg.name.charAt(0).toUpperCase();
    paint();
  }
  function setLauncherLabel() {
    var label = (open ? "Cerrar el chat con " : "Abrir el chat con ") + cfg.name;
    launcher.setAttribute("aria-label", unread && !open ? label + " (" + unread + " sin leer)" : label);
    launcher.setAttribute("aria-expanded", String(open));
    badge.hidden = !unread || open;
    badge.textContent = unread > 9 ? "9+" : String(unread);
  }
  function renderItem(item) {
    if (item.kind === "notice") { log.appendChild(el("div", "note", item.text)); return; }
    var row = el("div", "row " + item.kind + (item.failed ? " failed" : ""));
    if (item.kind === "human") row.appendChild(el("div", "who", "Equipo de " + cfg.name));
    var bubble = el("div", "bubble");
    bubble.innerHTML = format(item.text);
    bubble.title = new Date(item.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    row.appendChild(bubble);
    if (item.failed) {
      var meta = el("div", "meta", "No se envió. "), retry = el("button", "link", "Reintentar");
      retry.type = "button";
      retry.addEventListener("click", function () { submit(item.text, item); });
      meta.appendChild(retry);
      row.appendChild(meta);
    }
    log.appendChild(row);
  }
  function add(kind, text) {
    var item = { kind: kind, text: text, ts: Date.now() };
    state.items.push(item);
    save();
    var chips = $(".chips");
    if (chips) chips.remove();
    renderItem(item);
    fresh.hidden = false;
    if (!open && kind !== "user") { unread += 1; setLauncherLabel(); }
    scrollDown();
    return item;
  }
  function renderAll() {
    log.textContent = "";
    log.removeAttribute("aria-busy");
    renderItem({ kind: "agent", text: cfg.greeting, ts: state.items.length ? state.items[0].ts : Date.now() });
    state.items.forEach(renderItem);
    if (!state.items.length && cfg.suggestions.length) {
      var box = el("div", "chips");
      box.setAttribute("role", "group");
      box.setAttribute("aria-label", "Preguntas frecuentes");
      cfg.suggestions.forEach(function (text) {
        var chip = el("button", "chip", text);
        chip.type = "button";
        chip.addEventListener("click", function () { submit(text); });
        box.appendChild(chip);
      });
      log.appendChild(box);
    }
    fresh.hidden = !state.items.length;
    refresh();
    scrollDown();
  }
  function showState(text, action, onAction) {
    log.textContent = "";
    log.removeAttribute("aria-busy");
    var box = el("div", "state");
    box.appendChild(el("span", "", text));
    if (action) {
      var button = el("button", "btn", action);
      button.type = "button";
      button.addEventListener("click", onAction);
      box.appendChild(button);
    }
    log.appendChild(box);
    form.hidden = true;
  }
  function showLoading() {
    log.setAttribute("aria-busy", "true");
    log.innerHTML = '<span class="sr">Cargando el chat…</span><div class="skel"></div><div class="skel"></div><div class="skel"></div><div class="skel"></div>';
    form.hidden = true;
  }
  function showTyping() {
    if (typing) return;
    typing = el("div", "row agent");
    typing.innerHTML = '<div class="bubble typing"><span class="sr">Escribiendo…</span><i></i><i></i><i></i></div>';
    log.appendChild(typing);
    scrollDown();
  }
  function hideTyping() { if (typing) { typing.remove(); typing = null; } }
  function setBar(text, actions) {
    bar.textContent = "";
    bar.hidden = !text;
    if (!text) return;
    bar.appendChild(el("span", "", text));
    (actions || []).forEach(function (a) {
      var button = el("button", "link", a[0]);
      button.type = "button";
      button.addEventListener("click", a[1]);
      bar.appendChild(button);
    });
  }
  // Composer, pie y avisos según el estado: en línea, sin conexión, enviando, límite alcanzado.
  function refresh() {
    var offline = navigator.onLine === false;
    root.toggleAttribute("data-offline", offline);
    $(".title em").textContent = offline ? "Sin conexión" : "Asistente virtual";
    if (offline) setBar("Sin conexión. Revisá tu internet para seguir.");
    else if (bar.getAttribute("data-kind") !== "confirm") setBar("");
    form.hidden = !!state.locked;
    ended.hidden = !state.locked;
    input.disabled = offline;
    send.disabled = busy || offline || !input.value.trim();
    var r = state.remaining, left = "";
    if (r !== null && r <= 5 && r > 0 && !state.locked) left = r === 1 ? "Te queda 1 mensaje" : "Te quedan " + r + " mensajes";
    foot.textContent = [left, note].filter(Boolean).join(" · ");
  }
  function autosize() {
    input.style.height = "auto";
    var needed = input.scrollHeight + 2;
    input.style.height = Math.min(needed, 124) + "px";
    input.style.overflowY = needed > 124 ? "auto" : "hidden";
  }

  // -- red ---------------------------------------------------------------------------------
  function newSession() {
    return fetch(base + "/chat/session" + canal, { method: "POST" }).then(function (r) {
      if (!r.ok) throw new Error("session " + r.status);
      return r.json();
    }).then(function (data) {
      state.session = data.session;
      state.remaining = data.remaining;
      save();
      return data.session;
    });
  }
  function post(text, retried) {
    var ready = state.session ? Promise.resolve(state.session) : newSession();
    return ready.then(function (session) {
      return fetch(base + "/chat/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session: session, text: text })
      });
    }).then(function (response) {
      if (response.status === 401 && !retried) {  // sesión vencida: una nueva
        state.session = null;
        return post(text, true);
      }
      if (!response.ok) throw new Error("messages " + response.status);
      return response;
    });
  }
  // Server-Sent Events sobre un POST: se leen los bloques "event: …\ndata: …".
  function readEvents(response, onEvent) {
    var reader = response.body.getReader(), decoder = new TextDecoder(), buffer = "";
    function pump() {
      return reader.read().then(function (chunk) {
        if (chunk.done) return;
        buffer += decoder.decode(chunk.value, { stream: true });
        var cut;
        while ((cut = buffer.indexOf("\n\n")) >= 0) {
          var block = buffer.slice(0, cut), name = "message", data = "";
          buffer = buffer.slice(cut + 2);
          block.split("\n").forEach(function (line) {
            if (line.indexOf("event: ") === 0) name = line.slice(7);
            else if (line.indexOf("data: ") === 0) data += line.slice(6);
          });
          try { onEvent(name, data ? JSON.parse(data) : {}); } catch (e) { /* bloque inválido */ }
        }
        return pump();
      });
    }
    return pump();
  }
  function submit(text, failedItem) {
    text = (text || "").trim();
    if (!text || busy || state.locked || navigator.onLine === false) return;
    var item = failedItem;
    if (item) { delete item.failed; save(); renderAll(); } else item = add("user", text);
    input.value = "";
    autosize();
    // Si se envió con una sugerencia, el botón desaparece: el foco pasa al campo de texto.
    if (open && shadow.activeElement !== input) input.focus({ preventScroll: true });
    busy = true;
    refresh();
    showTyping();
    var answered = false, failure = null;
    post(text, false).then(function (response) {
      return readEvents(response, function (name, data) {
        if (name === "typing") showTyping();
        else if (name === "message") { answered = true; hideTyping(); add("agent", data.text); }
        else if (name === "done") {
          var today = typeof data.remaining_today === "number" ? data.remaining_today : data.remaining;
          state.remaining = Math.min(data.remaining, today);
          save();
        } else if (name === "limit") {
          answered = true;
          hideTyping();
          // El de la sesión cierra esta conversación; los diarios (IP o total) vencen solos.
          if (data.scope === "session") state.locked = true;
          add("notice", data.message);
        } else if (name === "error") failure = data.message;
      });
    }).catch(function () { failure = failure || "No pudimos conectar con el asistente."; }).then(function () {
      hideTyping();
      busy = false;
      if (failure && !answered) {  // el mensaje no se descontó: se puede reintentar
        item.failed = true;
        save();
        renderAll();
        log.appendChild(el("div", "note", failure));
        scrollDown();
      }
      refresh();
      if (open && !state.locked) input.focus();
    });
  }
  // Mensajes del equipo (respuestas o avisos de una aprobación): se consultan cada pocos
  // segundos. Una consulta a la vez, y cada mensaje se muestra una sola vez (por su id).
  var polling = false, tick = 0;
  function poll() {
    tick += 1;
    if (polling || !cfg || !state.session || document.hidden || (!open && tick % 3)) return;
    polling = true;
    var url = base + "/chat/updates?session=" + encodeURIComponent(state.session) +
      (state.lastHuman ? "&after=" + encodeURIComponent(state.lastHuman) : "");
    fetch(url).then(function (r) { return r.ok ? r.json() : { messages: [] }; }).then(function (data) {
      var seen = state.seenHuman || [];
      (data.messages || []).forEach(function (m) {
        state.lastHuman = m.at;
        if (seen.indexOf(m.id) >= 0) return;
        seen.push(m.id);
        state.seenHuman = seen.slice(-50);
        hideTyping();
        add("human", m.text);
      });
    }).catch(function () { /* sin conexión: se reintenta en la próxima vuelta */ }).then(function () { polling = false; });
  }
  setInterval(poll, 6000);
  document.addEventListener("visibilitychange", function () { tick = 2; poll(); });

  // -- abrir, cerrar y teclado -------------------------------------------------------------
  var small = matchMedia("(max-width: 520px)");
  function fit() {  // en celular, el panel sigue al teclado en pantalla
    var vv = window.visualViewport, full = small.matches && open && mode !== "inline";
    var box = embedded ? host : panel;
    box.style.height = full && vv ? vv.height + "px" : embedded ? "100%" : "";
    if (!embedded) panel.style.top = full && vv ? vv.offsetTop + "px" : "";
    if (!embedded) panel.style.bottom = full && vv ? "auto" : "";
    scrollDown();
  }
  if (window.visualViewport) { visualViewport.addEventListener("resize", fit); visualViewport.addEventListener("scroll", fit); }
  function setOpen(value) {
    if (embedded || !cfg || open === value) return;
    open = value;
    root.toggleAttribute("data-open", open);
    panel.setAttribute("aria-modal", String(open && small.matches));
    if (small.matches) document.documentElement.style.overflow = open ? "hidden" : "";
    if (open) {
      unread = 0;
      lastFocus = launcher;
      paint();
      fit();
      setTimeout(function () { (state.locked ? ended.querySelector("button") : input).focus(); scrollDown(); }, 60);
      tick = 2;
      poll();
    } else {
      panel.style.height = panel.style.top = panel.style.bottom = "";
      if (lastFocus) lastFocus.focus();
    }
    setLauncherLabel();
  }
  function reset() {
    state = blank();
    save();
    bar.removeAttribute("data-kind");
    renderAll();
    if (open) input.focus();
  }
  function askReset() {
    bar.setAttribute("data-kind", "confirm");
    setBar("¿Empezar una conversación nueva? Esta se borra de este dispositivo.", [
      ["Sí, empezar", reset],
      ["Cancelar", function () { bar.removeAttribute("data-kind"); refresh(); fresh.focus(); }]
    ]);
    bar.querySelector("button").focus();
  }
  if (launcher) launcher.addEventListener("click", function () { setOpen(!open); });
  if ($(".shut")) $(".shut").addEventListener("click", function () { setOpen(false); });
  fresh.addEventListener("click", askReset);
  ended.querySelector("button").addEventListener("click", reset);
  send.addEventListener("click", function () { submit(input.value); });
  input.addEventListener("input", function () { autosize(); refresh(); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(input.value); }
  });
  panel.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !embedded) { e.stopPropagation(); setOpen(false); }
    if (e.key !== "Tab" || embedded || !small.matches) return;
    // A pantalla completa el foco no sale del chat.
    var items = Array.prototype.filter.call(panel.querySelectorAll("button,textarea,[tabindex]"), function (n) {
      return !n.disabled && n.offsetParent !== null;
    });
    var first = items[0], last = items[items.length - 1], active = shadow.activeElement;
    if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
  });
  window.addEventListener("online", refresh);
  window.addEventListener("offline", refresh);

  // -- arranque ----------------------------------------------------------------------------
  function start() {
    if (embedded) showLoading();
    fetch(base + "/chat/widget/config" + canal).then(function (r) {
      if (!r.ok) throw new Error("config " + r.status);
      return r.json();
    }).then(function (data) {
      if (!data.allowed) {
        console.warn("[Enterprise Data AI] Este dominio no está en la lista de dominios permitidos del chat. Agregalo en el panel, en Configuración.");
        if (embedded) showState("Este chat no está disponible en este sitio.");
        else host.remove();
        return;
      }
      cfg = data;
      form.hidden = false;
      brand();
      renderAll();
      root.setAttribute("data-ready", "");
      if (embedded) fit();
    }).catch(function () {
      if (embedded) showState("No pudimos cargar el chat.", "Reintentar", start);
      else setTimeout(start, 15000);  // el sitio sigue igual; se reintenta sin molestar
    });
  }
  paint();
  start();

  // API mínima para el sitio: abrir o cerrar desde un botón propio, y la vista previa del panel.
  window.EnterpriseChat = {
    open: function () { setOpen(true); },
    close: function () { setOpen(false); },
    toggle: function () { setOpen(!open); },
    setBrand: function (changes) {
      if (!cfg) return;
      if (changes.name) { cfg.name = changes.name; cfg.greeting = "¡Hola! Soy el asistente virtual de " + changes.name + ". ¿En qué te puedo ayudar?"; }
      if (/^#[0-9a-f]{6}$/i.test(changes.accent || "")) cfg.accent = changes.accent;
      if ("logo" in changes) cfg.logo = changes.logo;
      if (changes.suggestions) cfg.suggestions = changes.suggestions;
      brand();
      renderAll();
    }
  };
})();
