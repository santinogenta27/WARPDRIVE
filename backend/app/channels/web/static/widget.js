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

  // Textos en el idioma de la empresa (decisión 036). Hasta leer la configuración, el de
  // data-lang o el de la página.
  var TEXTS = {
    es: {
      assistant: "Asistente virtual", offline: "Sin conexión", fresh: "Empezar una conversación nueva",
      close: "Cerrar el chat", log: "Conversación", message: "Mensaje", placeholder: "Escribí tu consulta…",
      send: "Enviar", chatWith: "Chat con ", openWith: "Abrir el chat con ", closeWith: "Cerrar el chat con ",
      unread: " sin leer", team: function (n) { return "Equipo de " + n; }, notSent: "No se envió. ",
      retry: "Reintentar", suggestions: "Preguntas frecuentes", loading: "Cargando el chat…",
      typing: "Pensando…", writing: "Escribiendo…", offlineBar: "Sin conexión. Revisá tu internet para seguir.",
      left: function (r) { return r === 1 ? "Te queda 1 mensaje" : "Te quedan " + r + " mensajes"; },
      noConnect: "No pudimos conectar con el asistente.",
      askReset: "¿Empezar una conversación nueva? Esta se borra de este dispositivo.",
      yesReset: "Sí, empezar", cancel: "Cancelar", unavailable: "Este chat no está disponible en este sitio.",
      noLoad: "No pudimos cargar el chat.", answers: "Respuestas rápidas", buy: "Completá tu compra",
      greeting: function (n) { return "¡Hola! Soy el asistente virtual de " + n + ". ¿En qué te puedo ayudar?"; },
      domain: "Este dominio no está en la lista de dominios permitidos del chat. Agregalo en el panel, en Configuración."
    },
    en: {
      assistant: "Virtual assistant", offline: "Offline", fresh: "Start a new conversation",
      close: "Close the chat", log: "Conversation", message: "Message", placeholder: "Type your message…",
      send: "Send", chatWith: "Chat with ", openWith: "Open the chat with ", closeWith: "Close the chat with ",
      unread: " unread", team: function (n) { return n + " team"; }, notSent: "Not sent. ",
      retry: "Retry", suggestions: "Suggested questions", loading: "Loading the chat…",
      typing: "Thinking…", writing: "Typing…", offlineBar: "You're offline. Check your connection to continue.",
      left: function (r) { return r === 1 ? "1 message left" : r + " messages left"; },
      noConnect: "We couldn't reach the assistant.",
      askReset: "Start a new conversation? This one will be cleared from this device.",
      yesReset: "Yes, start over", cancel: "Cancel", unavailable: "This chat isn't available on this site.",
      noLoad: "We couldn't load the chat.", answers: "Quick replies", buy: "Complete your purchase",
      greeting: function (n) { return "Hi! I'm the virtual assistant for " + n + ". How can I help you today?"; },
      domain: "This domain isn't on the chat's list of allowed domains. Add it in the dashboard, under Settings."
    }
  };
  var pick = function (value) { return /^en/i.test(value || "") ? "en" : "es"; };
  var lang = pick(attr("lang", document.documentElement.lang));
  var T = TEXTS[lang];

  var CSS = `
:host{all:initial}
*{box-sizing:border-box}
.root{--surface:255,255,255;--ink:#12151a;--muted:#5d6470;--line:rgba(18,21,26,.09);
--soft:rgba(18,21,26,.055);--warn-bg:rgba(180,83,9,.1);--warn:#8a4a06;--glass:.68;--top-glass:.6;
--idle:rgba(18,21,26,.08);--idle-ink:#8b919b;--thumb:rgba(18,21,26,.3);
--rim:rgba(255,255,255,.95);--rim-low:rgba(255,255,255,.3);
--shadow:0 1px 2px rgba(15,23,42,.06),0 14px 30px -14px rgba(15,23,42,.2),0 50px 110px -30px rgba(15,23,42,.42);
font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
color:var(--ink);-webkit-font-smoothing:antialiased;text-align:left}
.root[data-theme=dark]{--surface:23,25,30;--ink:#f1f2f4;--muted:#a4aab4;
--line:rgba(255,255,255,.1);--soft:rgba(255,255,255,.08);--warn-bg:rgba(251,191,36,.12);
--warn:#f5c56b;--glass:.72;--top-glass:.58;--idle:rgba(255,255,255,.09);--idle-ink:#7c828c;
--thumb:rgba(255,255,255,.32);--rim:rgba(255,255,255,.26);--rim-low:rgba(255,255,255,.05);
--shadow:0 1px 2px rgba(0,0,0,.35),0 16px 34px -14px rgba(0,0,0,.6),0 54px 120px -26px rgba(0,0,0,.9)}
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

.panel{position:relative;display:flex;flex-direction:column;overflow:hidden;background:rgba(var(--surface),.985);
border:1px solid var(--line);border-radius:24px;box-shadow:var(--shadow)}
/* Brillo del borde: un aro de 1px, más claro arriba, como la luz sobre un vidrio. */
.panel::before{content:"";position:absolute;inset:0;z-index:4;border-radius:inherit;padding:1px;pointer-events:none;
background:linear-gradient(180deg,var(--rim),var(--rim-low) 34%,var(--rim-low) 72%,var(--rim));
-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;
mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);mask-composite:exclude}
.top{position:absolute;top:0;left:0;right:0;z-index:3;background:rgba(var(--surface),.97);
border-bottom:1px solid var(--line)}
@supports ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){
/* El vidrio del panel va en una capa aparte: si lo tuviera el panel, el encabezado (que está
   adentro) no podría desenfocar los mensajes que pasan por debajo. */
.panel{background:transparent;z-index:0}
.panel::after{content:"";position:absolute;inset:0;z-index:-1;background:rgba(var(--surface),var(--glass));
-webkit-backdrop-filter:blur(32px) saturate(1.9);backdrop-filter:blur(32px) saturate(1.9)}
/* El encabezado es otra capa de vidrio: los mensajes pasan por debajo, desenfocados. */
.top{background:rgba(var(--surface),var(--top-glass));
-webkit-backdrop-filter:blur(18px) saturate(1.8);backdrop-filter:blur(18px) saturate(1.8)}}
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

header{display:flex;align-items:center;gap:12px;padding:16px 14px 14px 18px}
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

.log{position:relative;flex:1;overflow-y:auto;overscroll-behavior:contain;padding:86px 16px 10px;display:flex;
flex-direction:column;gap:10px;scrollbar-width:none}
.log::-webkit-scrollbar,textarea::-webkit-scrollbar{display:none}
.log:focus-visible{outline-offset:-5px;border-radius:14px}
/* La barra del navegador no se ve: esta, fina, aparece solo mientras se desplaza. */
.thumb{position:absolute;top:0;right:4px;z-index:2;width:4px;height:0;border-radius:2px;background:var(--thumb);
opacity:0;pointer-events:none;transition:opacity .6s ease}
.thumb.on{opacity:1;transition-duration:.12s}
.row{display:flex;flex-direction:column;max-width:84%;align-items:flex-start;
animation:in .32s cubic-bezier(.2,.9,.3,1) both}
.row.user{align-self:flex-end;align-items:flex-end}
.bubble{padding:10px 14px;border-radius:20px;border-bottom-left-radius:7px;background:var(--soft);
overflow-wrap:anywhere}
.user .bubble{background:var(--accent);color:var(--on-accent);border-radius:20px;border-bottom-right-radius:7px}
.user.failed .bubble{opacity:.55}
.bubble a{color:inherit;font-weight:550;text-decoration:underline;text-decoration-thickness:1px;
text-underline-offset:3px;border-radius:4px}
.bubble a:hover{text-decoration-thickness:2px}
.lnk i{font-style:normal;margin-left:3px;font-size:.85em;text-decoration:none;display:inline-block}
.dom{font-weight:450;opacity:.72;font-size:.92em}
.acts{display:flex;flex-direction:column;align-items:flex-start;gap:7px;margin-top:8px;max-width:100%;
animation:in .4s .06s both}
.buy{display:inline-flex;flex-wrap:wrap;align-items:baseline;justify-content:center;column-gap:8px;
padding:11px 20px;border-radius:999px;background:var(--accent);color:var(--on-accent);font-weight:620;
font-size:14.5px;text-decoration:none;box-shadow:0 6px 18px -8px rgba(15,23,42,.45);
transition:transform .2s cubic-bezier(.2,1.3,.4,1),filter .15s}
.buy:hover{transform:translateY(-1px);filter:brightness(1.07)}
.buy:active{transform:scale(.98)}
.pnote{font-size:12px;line-height:1.4;color:var(--muted);padding:0 8px}
.who{font-size:11.5px;font-weight:600;color:var(--muted);margin:0 0 4px 12px}
.meta{font-size:12px;color:var(--warn);margin:5px 4px 0}
.note{align-self:center;max-width:92%;padding:9px 14px;border-radius:14px;background:var(--warn-bg);
color:var(--warn);font-size:13px;text-align:center;animation:in .32s both}
/* "Escribiendo": sin burbuja, sobre el fondo del chat. Una marca con el acento que respira y un
   texto con un brillo que lo recorre. Al llegar la respuesta se desvanece en su lugar. */
.typing{flex-direction:row;align-items:center;gap:10px;min-height:41px;padding:0 4px}
.typing .mark{flex:none;width:10px;height:10px;border-radius:50%;background:var(--accent);
animation:breathe 1.5s ease-in-out infinite}
.typing .label{font-size:14px;color:var(--muted);
background:linear-gradient(90deg,var(--muted) 0 38%,var(--ink) 50%,var(--muted) 62% 100%) 0 0/250% 100%;
-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;
animation:shimmer 2s linear infinite}
.typing.out{position:absolute;left:16px;opacity:0;transition:opacity .18s ease-out;pointer-events:none}
.row.agent,.row.human{animation:rise .2s ease-out both}
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
scrollbar-width:none;transition:border-color .15s,background .15s}
textarea::placeholder{color:var(--muted);opacity:1}
textarea:focus-visible{outline:none;border-color:var(--focus);background:transparent}
textarea:disabled{opacity:.55}
.send{flex:none;width:46px;height:46px;border-radius:50%;border:0;background:var(--accent);color:var(--on-accent);
display:grid;place-items:center;
transition:transform .2s cubic-bezier(.2,1.3,.4,1),background-color .18s ease,color .18s ease}
.send:hover:not(:disabled){transform:scale(1.07)}
/* Gris con el campo vacío; el acento recién cuando hay algo para mandar. */
.send:disabled{background:var(--idle);color:var(--idle-ink);cursor:default}
.send svg{width:20px;height:20px}
.ended{padding:6px 18px 14px;display:grid;justify-items:center}
.foot{padding:0 18px max(12px,env(safe-area-inset-bottom));text-align:center;font-size:11.5px;color:var(--muted)}
.foot:empty{padding-top:0;padding-bottom:max(2px,env(safe-area-inset-bottom))}

@keyframes in{from{opacity:0;transform:translateY(8px) scale(.98)}to{opacity:1;transform:none}}
@keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes breathe{0%,100%{opacity:.5;transform:scale(.74)}50%{opacity:1;transform:scale(1)}}
@keyframes shimmer{from{background-position:100% 0}to{background-position:0 0}}
@keyframes pulse{0%,100%{opacity:.55}50%{opacity:1}}

@media (max-width:520px){
.root,.root[data-theme=dark]{--glass:.97}
.root[data-mode=float] .panel{inset:0;width:100%;max-width:none;height:100%;border-radius:0;border:0;
transform-origin:50% 100%;transform:translateY(24px)}
.root[data-mode=float][data-open] .launcher{opacity:0;pointer-events:none}
.root[data-mode=page] .panel{border-radius:0;border:0;box-shadow:none}
.root[data-mode=float] .panel::before,.root[data-mode=page] .panel::before{display:none}
header{padding-top:max(14px,env(safe-area-inset-top))}
.row{max-width:88%}}
@media (prefers-reduced-motion:reduce){
*,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;
transition-duration:.01ms!important}
.typing .mark{animation:none;opacity:1;transform:none}
.typing .label{animation:none;background:none;-webkit-text-fill-color:currentColor}
.root[data-mode=float] .panel{transform:none}}
@media (forced-colors:active){.bubble,.panel,.launcher,.send,.buy{border:1px solid CanvasText}.thumb{background:CanvasText}
.typing .label{background:none;-webkit-text-fill-color:CanvasText}.typing .mark{background:CanvasText}}
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
    '<div class="top"><header><div class="logo" aria-hidden="true"></div><div class="title"><b></b>' +
    '<span><i aria-hidden="true"></i><em style="font-style:normal"></em></span></div>' +
    '<button class="icon fresh" type="button" hidden>' + ICONS.fresh + "</button>" +
    (embedded ? "" : '<button class="icon shut" type="button">' + ICONS.close.replace(' class="i-close"', "") + "</button>") +
    "</header>" +
    '<div class="bar" role="status" hidden></div></div>' +
    '<div class="log" role="log" aria-live="polite" aria-relevant="additions" tabindex="0"></div>' +
    '<div class="thumb" aria-hidden="true"></div>' +
    '<div class="composer"><label class="sr" for="msg"></label>' +
    '<textarea id="msg" rows="1" maxlength="1000" enterkeyhint="send"></textarea>' +
    '<button class="send" type="button" disabled>' + ICONS.send + "</button></div>" +
    '<div class="ended" hidden><button class="btn" type="button"></button></div>' +
    '<div class="foot"></div></section></div>';

  var $ = function (selector) { return shadow.querySelector(selector); };
  var root = $(".root"), panel = $(".panel"), launcher = $(".launcher"), badge = $(".badge");
  // El campo de texto no va en un formulario: en un sitio que envuelve la página en uno,
  // el navegador descartaría uno anidado.
  var log = $(".log"), form = $(".composer"), input = $("textarea"), send = $(".send");
  var bar = $(".bar"), ended = $(".ended"), foot = $(".foot"), fresh = $(".fresh");
  var top = $(".top"), thumb = $(".thumb");
  var note = attr("note", "");

  // Los textos fijos del panel, en el idioma vigente.
  function applyTexts() {
    $(".title em").textContent = navigator.onLine === false ? T.offline : T.assistant;
    fresh.setAttribute("aria-label", T.fresh);
    if ($(".shut")) $(".shut").setAttribute("aria-label", T.close);
    log.setAttribute("aria-label", T.log);
    $(".composer label").textContent = T.message;
    input.placeholder = T.placeholder;
    send.setAttribute("aria-label", T.send);
    ended.querySelector("button").textContent = T.fresh;
  }
  applyTexts();

  var cfg = null, open = embedded, busy = false, typing = null, unread = 0, lastFocus = null;
  var pendingBrand = null;
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
  /* links:start — el texto de un mensaje, armado con nodos: nada se interpreta como HTML
     (decisión 103). Los tests lo corren con Node. */
  var LINK = /\[([^\]\n]{1,120})\]\(([^)\s]+)\)|\bhttps?:\/\/[^\s<>"'`]+/gi;
  function safeUrl(raw) {
    try { var u = new URL(raw); } catch (e) { return null; }
    return /^https?:$/.test(u.protocol) && u.hostname ? u : null;
  }
  // A una URL suelta no se le pega la puntuación de la frase. Un ")" queda si cierra uno suyo.
  function trimUrl(raw) {
    for (var c; (c = raw.slice(-1)) && /[.,;:!?'"\])]/.test(c);) {
      if (c === ")" && raw.split("(").length >= raw.split(")").length) break;
      raw = raw.slice(0, -1);
    }
    return raw;
  }
  function parseLinks(text) {
    var parts = [], last = 0, m;
    LINK.lastIndex = 0;
    while ((m = LINK.exec(text))) {
      var raw = m[2] || trimUrl(m[0]), url = safeUrl(raw);
      if (!url) continue;
      if (m.index > last) parts.push({ text: text.slice(last, m.index) });
      parts.push({ url: url, label: m[1] || "" });
      last = LINK.lastIndex = m.index + (m[2] ? m[0].length : raw.length);
    }
    if (last < text.length) parts.push({ text: text.slice(last) });
    return parts;
  }
  function bareHost(host) { return host.toLowerCase().replace(/^(\*|www)\./, ""); }
  function ownHost(host, hosts) {
    host = bareHost(host);
    return (hosts || []).some(function (h) { h = bareHost(h); return host === h || host.slice(-h.length - 1) === "." + h; });
  }
  function money(total, currency, lang) {
    try { return new Intl.NumberFormat(lang === "en" ? "en-US" : "es-AR", { style: "currency", currency: currency }).format(total); }
    catch (e) { return total + " " + currency; }
  }
  // Negrita estilo WhatsApp (*texto*) y saltos de línea, como nodos de texto.
  function addText(doc, box, text) {
    text.split("\n").forEach(function (line, i) {
      if (i) box.appendChild(doc.createElement("br"));
      line.split(/\*(\S(?:[^*\n]*\S)?)\*/).forEach(function (piece, j) {
        if (!piece) return;
        if (j % 2) box.appendChild(doc.createElement("strong")).textContent = piece;
        else box.appendChild(doc.createTextNode(piece));
      });
    });
  }
  function span(doc, cls, text) { var s = doc.createElement("span"); s.className = cls; s.textContent = text; return s; }
  // o: { hosts, checkouts: [{url, total, currency}], buy, lang, plain }. Un link de compra
  // (carrito o checkout) sale del texto y es un botón; los demás muestran el dominio.
  function renderMessage(doc, text, o) {
    var body = doc.createDocumentFragment(), buttons = [], pending = "", cut = false;
    parseLinks(text).forEach(function (part) {
      if (part.text != null) {
        var t = part.text;  // sin el link: "link: ." -> "link."
        if (cut && /^\s*[.,;!?]/.test(t)) { pending = pending.replace(/:$/, ""); t = t.replace(/^\s+/, ""); }
        cut = false;
        pending += t;
        return;
      }
      var url = part.url, host = bareHost(url.hostname), own = ownHost(host, o.hosts);
      var a = doc.createElement("a");
      a.setAttribute("href", url.href);
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
      if (!o.plain && /\/(cart|checkouts?)(\/|$)/i.test(url.pathname)) {
        var found = (o.checkouts || []).filter(function (c) { var u = safeUrl(c.url || ""); return u && u.href === url.href; })[0];
        a.className = "buy";
        a.appendChild(span(doc, "", o.buy + (found && found.total ? " · " + money(found.total, found.currency, o.lang) : "") + " →"));
        if (!own) a.appendChild(span(doc, "dom", host));
        buttons.push(a);
        pending = pending.replace(/[ \t]+$/, "");
        cut = true;
        return;
      }
      addText(doc, body, pending);
      pending = "";
      a.className = "lnk";
      a.setAttribute("title", url.href);
      a.appendChild(doc.createTextNode(part.label || host));
      if (part.label && !own) a.appendChild(span(doc, "dom", " · " + host));
      else if (!part.label) a.appendChild(doc.createElement("i")).textContent = "↗";
      body.appendChild(a);
    });
    addText(doc, body, buttons.length ? pending.replace(/\s+$/, "") : pending);
    return { body: body, buttons: buttons };
  }
  /* links:end */
  // Un desplazamiento del propio widget (llega un mensaje) no muestra la barra.
  var quietUntil = 0, thumbTimer = null;
  function scrollDown() { quietUntil = Date.now() + 150; log.scrollTop = log.scrollHeight; }
  // El encabezado flota sobre los mensajes: la lista empieza debajo de él, mida lo que mida.
  function padTop() { log.style.paddingTop = top.offsetHeight + 16 + "px"; }
  if (window.ResizeObserver) new ResizeObserver(padTop).observe(top);
  function placeThumb() {
    var view = log.clientHeight, full = log.scrollHeight;
    var start = top.offsetHeight + 6, track = log.offsetTop + view - 6 - start;
    if (full <= view + 1 || track < 40) return false;
    var size = Math.max(28, track * view / full);
    thumb.style.height = size + "px";
    thumb.style.transform = "translateY(" + (start + (track - size) * log.scrollTop / (full - view)) + "px)";
    return true;
  }
  log.addEventListener("scroll", function () {
    if (Date.now() < quietUntil || !placeThumb()) return;
    thumb.classList.add("on");
    clearTimeout(thumbTimer);
    thumbTimer = setTimeout(function () { thumb.classList.remove("on"); }, 900);
  }, { passive: true });
  function brand() {
    var logo = $(".logo");
    if (cfg.lang && pick(cfg.lang) !== lang) { lang = pick(cfg.lang); T = TEXTS[lang]; applyTexts(); }
    $(".title b").textContent = cfg.name;
    panel.setAttribute("aria-label", T.chatWith + cfg.name);
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
    var label = (open ? T.closeWith : T.openWith) + cfg.name;
    launcher.setAttribute("aria-label", unread && !open ? label + " (" + unread + T.unread + ")" : label);
    launcher.setAttribute("aria-expanded", String(open));
    badge.hidden = !unread || open;
    badge.textContent = unread > 9 ? "9+" : String(unread);
  }
  function renderItem(item) {
    if (item.kind === "notice") { log.appendChild(el("div", "note", item.text)); return; }
    var row = el("div", "row " + item.kind + (item.failed ? " failed" : ""));
    if (item.kind === "human") row.appendChild(el("div", "who", T.team(cfg.name)));
    var bubble = el("div", "bubble");
    var shown = renderMessage(document, item.text, {
      hosts: cfg.link_hosts, checkouts: item.checkouts, buy: T.buy, lang: lang, plain: item.kind === "user"
    });
    bubble.appendChild(shown.body);
    bubble.title = new Date(item.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    row.appendChild(bubble);
    if (shown.buttons.length) {  // el botón de compra, debajo del texto
      var acts = el("div", "acts");
      shown.buttons.forEach(function (b) { acts.appendChild(b); });
      if (cfg.purchase_note) acts.appendChild(el("div", "pnote", cfg.purchase_note));
      row.appendChild(acts);
    }
    if (item.failed) {
      var meta = el("div", "meta", T.notSent), retry = el("button", "link", T.retry);
      retry.type = "button";
      retry.addEventListener("click", function () { submit(item.text, item); });
      meta.appendChild(retry);
      row.appendChild(meta);
    }
    log.appendChild(row);
  }
  function add(kind, text, replies, checkouts) {
    var item = { kind: kind, text: text, ts: Date.now() };
    if (replies && replies.length) item.replies = replies;
    if (checkouts && checkouts.length) item.checkouts = checkouts;
    state.items.push(item);
    save();
    var shown = $(".chips");
    if (shown) shown.remove();
    renderItem(item);
    if (item.replies) chips(item.replies, T.answers);
    fresh.hidden = false;
    if (!open && kind !== "user") { unread += 1; setLauncherLabel(); }
    scrollDown();
    return item;
  }
  // Botones bajo el último mensaje: las preguntas sugeridas o las respuestas rápidas (sí / no a
  // la pregunta de satisfacción).
  function chips(texts, label) {
    var box = el("div", "chips");
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", label);
    texts.forEach(function (text) {
      var chip = el("button", "chip", text);
      chip.type = "button";
      chip.addEventListener("click", function () { submit(text); });
      box.appendChild(chip);
    });
    log.appendChild(box);
  }
  function renderAll() {
    log.textContent = "";
    log.removeAttribute("aria-busy");
    renderItem({ kind: "agent", text: cfg.greeting, ts: state.items.length ? state.items[0].ts : Date.now() });
    // Vista previa del panel: una respuesta de ejemplo con la voz de la marca (decisión 087).
    if (cfg.sample) renderItem({ kind: "agent", text: cfg.sample, ts: Date.now() });
    state.items.forEach(renderItem);
    var last = state.items[state.items.length - 1];
    if (!state.items.length && cfg.suggestions.length) chips(cfg.suggestions, T.suggestions);
    else if (last && last.replies && !state.locked) chips(last.replies, T.answers);
    fresh.hidden = !state.items.length;
    typing = null;  // el log se rehízo: el indicador, si hace falta, se vuelve a agregar
    if (state.awaiting) showTyping(T.writing);
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
    log.innerHTML = '<span class="sr"></span><div class="skel"></div><div class="skel"></div><div class="skel"></div><div class="skel"></div>';
    log.querySelector(".sr").textContent = T.loading;
    form.hidden = true;
  }
  // El widget no recibe los pasos del agente en vivo: el texto es "Thinking…". En el modo
  // supervisado, mientras una persona revisa la respuesta, "Typing…" (decisión 072).
  function showTyping(label) {
    if (typing) { typing.querySelector(".label").textContent = label || T.typing; return; }
    typing = el("div", "row agent typing");
    typing.appendChild(el("i", "mark")).setAttribute("aria-hidden", "true");
    typing.appendChild(el("span", "label", label || T.typing));
    log.appendChild(typing);
    scrollDown();
  }
  // `fade`: llega la respuesta. El indicador sale del flujo en el mismo lugar y se desvanece
  // mientras el mensaje entra ahí: no hay salto ni un instante sin ninguno de los dos.
  function hideTyping(fade) {
    if (!typing) return;
    var node = typing;
    typing = null;
    if (!fade || !node.isConnected) { node.remove(); return; }
    node.style.top = node.offsetTop + "px";
    node.classList.add("out");
    setTimeout(function () { node.remove(); }, 220);
  }
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
    $(".title em").textContent = offline ? T.offline : T.assistant;
    if (offline) setBar(T.offlineBar);
    else if (bar.getAttribute("data-kind") !== "confirm") setBar("");
    form.hidden = !!state.locked;
    ended.hidden = !state.locked;
    input.disabled = offline;
    send.disabled = busy || offline || !input.value.trim();
    var r = state.remaining, left = "";
    if (r !== null && r <= 5 && r > 0 && !state.locked) left = T.left(r);
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
    var answered = false, failure = null, held = false;
    post(text, false).then(function (response) {
      return readEvents(response, function (name, data) {
        if (name === "typing") showTyping();
        else if (name === "held") { answered = true; held = true; }
        else if (name === "message") { answered = true; hideTyping(true); add("agent", data.text, data.replies, data.checkouts); }
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
    }).catch(function () { failure = failure || T.noConnect; }).then(function () {
      if (held) { state.awaiting = true; save(); showTyping(T.writing); } else hideTyping();
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
        hideTyping(true);
        add(m.role === "agent" ? "agent" : "human", m.text, null, m.checkouts);
      });
      // Modo supervisado: "Typing…" mientras la respuesta espera a una persona del equipo.
      if (!busy) {
        if (data.typing) showTyping(T.writing);
        else if (state.awaiting) hideTyping();
        if (state.awaiting !== !!data.typing) { state.awaiting = !!data.typing; save(); }
      }
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
    setBar(T.askReset, [
      [T.yesReset, reset],
      [T.cancel, function () { bar.removeAttribute("data-kind"); refresh(); fresh.focus(); }]
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
    var items = Array.prototype.filter.call(panel.querySelectorAll("button,textarea,a[href],[tabindex]"), function (n) {
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
        console.warn("[Enterprise Data AI] " + T.domain);
        if (embedded) showState(T.unavailable);
        else host.remove();
        return;
      }
      cfg = data;
      form.hidden = false;
      // Cambios de la vista previa del panel que llegaron antes de la configuración.
      if (pendingBrand) { var early = pendingBrand; pendingBrand = null; window.EnterpriseChat.setBrand(early); }
      brand();
      renderAll();
      root.setAttribute("data-ready", "");
      if (embedded) fit();
    }).catch(function () {
      if (embedded) showState(T.noLoad, T.retry, start);
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
      if (!cfg) { pendingBrand = Object.assign(pendingBrand || {}, changes); return; }
      if (changes.name) { cfg.name = changes.name; cfg.greeting = T.greeting(changes.name); }
      if (changes.greeting) cfg.greeting = changes.greeting;
      if (/^#[0-9a-f]{6}$/i.test(changes.accent || "")) cfg.accent = changes.accent;
      if ("logo" in changes) cfg.logo = changes.logo;
      if (changes.suggestions) cfg.suggestions = changes.suggestions;
      if ("sample" in changes) cfg.sample = changes.sample;
      brand();
      renderAll();
    }
  };
})();
