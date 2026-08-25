/* Painel Palworld — lógica do frontend (vanilla JS, sem dependências) */
"use strict";

const $ = (sel) => document.querySelector(sel);

let token = localStorage.getItem("pp_token") || "";
let pollTimer = null;
let lastPlayers = [];
let lastStatus = null;
let usuarioAtual = null;   // {nome, papel} — preenchido no boot via /api/me

const ABAS_POR_PAPEL = {
  admin: null,  // todas
  moderador: ["overview", "players", "control", "console", "backups",
              "agenda", "saude"],
  viewer: ["overview", "players", "console"],
};

function aplicarPermissoesUI() {
  const papel = usuarioAtual?.papel || "admin";
  const permitidas = ABAS_POR_PAPEL[papel];
  document.querySelectorAll(".tabs button[data-tab]").forEach((b) => {
    if (permitidas === null || permitidas.includes(b.dataset.tab)) return;
    b.classList.add("hidden");
    b.disabled = true;
  });
  const badge = $("#user-badge");
  badge.textContent = `${usuarioAtual?.nome || "admin"} · ${papel}`;
  badge.classList.remove("hidden");
}

async function carregarUsuario() {
  try {
    const me = await api("me");
    usuarioAtual = { nome: me.usuario?.usuario || "admin",
                     papel: me.usuario?.papel || "admin" };
  } catch (_) {
    usuarioAtual = { nome: "admin", papel: "admin" };
  }
  aplicarPermissoesUI();
}

/* ================= utilidades ================= */

function toast(msg, type = "ok", ms = 3800) {
  const area = $("#toasts");
  const el = document.createElement("div");
  el.className = `toast ${type === "ok" ? "" : type}`.trim();
  el.textContent = msg;
  area.appendChild(el);
  while (area.children.length > 5) area.firstElementChild.remove();
  setTimeout(() => el.remove(), ms);
}

async function api(path, opts = {}) {
  const res = await fetch("/api/" + path, {
    method: opts.method || "GET",
    headers: {
      "Content-Type": "application/json",
      "X-Panel-Token": token,
    },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });

  if (res.status === 401 && path !== "login") {
    showLogin("Sessão expirada — entre novamente.");
    throw new Error("não autenticado");
  }

  let data = {};
  try { data = await res.json(); } catch (_) { /* resposta vazia */ }

  if (!res.ok) throw new Error(data.error || `Erro HTTP ${res.status}`);
  return data;
}

function fmtUptime(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}min`;
  return `${m}min`;
}

/* ================= login ================= */

function showLogin(msg = "") {
  stopPolling();
  $("#app-view").classList.add("hidden");
  $("#login-view").classList.remove("hidden");
  $("#login-error").textContent = msg;
  $("#login-pass").value = "";
  $("#login-pass").focus();
}

function showApp() {
  $("#login-view").classList.add("hidden");
  const app = $("#app-view");
  app.classList.remove("hidden");
  requestAnimationFrame(() => app.classList.add("ready"));
  refreshAll();
  startPolling();
  carregarUsuario();
  if (!csFonte && !csPollTimer) {
    csCarregarHistorico();
    csIniciarSSE();
  }
}

/* holofote que segue o cursor dentro dos cartões */
document.addEventListener("pointermove", (e) => {
  const card = e.target.closest(".card");
  if (!card) return;
  const r = card.getBoundingClientRect();
  card.style.setProperty("--mx", `${e.clientX - r.left}px`);
  card.style.setProperty("--my", `${e.clientY - r.top}px`);
}, { passive: true });

/* atualiza tudo de uma vez */
function refreshAll() {
  pollStatus();
  pollDashboard();
  loadPlayers();
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => {
    if (document.hidden) return;
    pollStatus();
    if ($("#tab-players").classList.contains("active")) loadPlayers();
    if ($("#tab-overview").classList.contains("active")) pollDashboard();
  }, 5000);
}

$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const botao = $("#login-form button[type=submit]");
  if (botao.disabled) return;
  botao.disabled = true;
  $("#login-error").textContent = "";
  try {
    const data = await api("login", {
      method: "POST",
      body: { password: $("#login-pass").value,
              usuario: $("#login-user").value.trim() || undefined },
    });
    token = data.token;
    usuarioAtual = { nome: data.usuario || "admin", papel: null };
    localStorage.setItem("pp_token", token);
    toast(`Bem-vindo, ${usuarioAtual.nome}!`);
    showApp();
  } catch (e) {
    $("#login-error").textContent = e.message === "não autenticado"
      ? "Senha incorreta." : e.message;
  } finally {
    botao.disabled = false;
  }
});

$("#btn-logout").addEventListener("click", async () => {
  try { await api("logout", { method: "POST" }); } catch (_) {}
  token = "";
  localStorage.removeItem("pp_token");
  showLogin();
});

/* ================= abas ================= */

function ativarAba(btn) {
  document.querySelectorAll(".tabs button").forEach((b) => {
    b.classList.remove("active");
    b.setAttribute("aria-selected", "false");
  });
  btn.classList.add("active");
  btn.setAttribute("aria-selected", "true");
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  $(`#tab-${btn.dataset.tab}`).classList.add("active");

  if (btn.dataset.tab === "players") {
    loadPlayers();
    loadRegistro();
    loadBans();
    loadMapa();
  }
  if (btn.dataset.tab === "console") csReaplicarFiltros();
  if (btn.dataset.tab === "settings") loadSettings();
  if (btn.dataset.tab === "backups") loadBackups();
  if (btn.dataset.tab === "agenda") loadAgenda();
  if (btn.dataset.tab === "saude") {
    loadSaude();
    loadRecuperacao();
    loadManutencao();
    loadNotificacoes();
  }
  if (btn.dataset.tab === "usuarios") {
    loadUsuarios();
    loadSessoes();
  }
}

document.querySelectorAll(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => ativarAba(btn));
});

/* setas esquerda/direita navegam entre as abas */
$(".tabs").addEventListener("keydown", (ev) => {
  if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
  const abas = [...document.querySelectorAll(".tabs button")];
  const atual = abas.findIndex((b) => b.classList.contains("active"));
  const delta = ev.key === "ArrowRight" ? 1 : -1;
  const proxima = abas[(atual + delta + abas.length) % abas.length];
  proxima.focus();
  ativarAba(proxima);
  ev.preventDefault();
});

/* ================= polling de status ================= */

function renderStatus(data) {
  const dot = $("#status-dot");
  const txt = $("#status-text");

  if (!data.online) {
    dot.className = "dot off";
    txt.textContent = "Servidor offline";
    $("#stat-fps").textContent = "—";
    $("#stat-frame").textContent = "—";
    $("#stat-players").textContent = "—";
    $("#stat-uptime").textContent = "—";
    lastPlayers = [];
    if ($("#tab-players").classList.contains("active")) renderPlayersTable();
    return;
  }

  dot.className = "dot on";
  txt.textContent = "Online";

  const info = data.info || {};
  const met = data.metrics || {};

  $("#srv-name").textContent = pick(info, "servername", "name") || "(sem nome)";
  $("#srv-version").textContent = pick(info, "version") || "";
  $("#srv-version").classList.toggle("hidden", !pick(info, "version"));

  const fpsAvg = pick(met, "serverfpsaverage");
  $("#stat-fps").textContent = pick(met, "serverfps", "serverFPS") ??
    (fpsAvg != null ? Math.round(fpsAvg) : "—");
  $("#stat-frame").textContent = (() => {
    const ft = pick(met, "serverframetime", "serverFrameTime");
    return ft != null ? Number(ft).toFixed(1) : "—";
  })();

  const cur = pick(met, "currentplayernum", "currentPlayerNum") ?? "?";
  const max = pick(met, "maxplayernum", "maxPlayerNum") ?? "?";
  $("#stat-players").textContent = `${cur}/${max}`;
  $("#stat-uptime").textContent = (() => {
    const up = pick(met, "uptime", "uptimeseconds", "uptimeSeconds");
    return up != null ? fmtUptime(up) : "—";
  })();

  lastStatus = data;
}

/* ---------- dashboard: cartões + gráfico histórico ---------- */

function fmtUptimeLong(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const partes = [];
  if (d) partes.push(`${d}d`);
  if (h) partes.push(`${h}h`);
  partes.push(`${m}min`);
  return partes.join(" ");
}

const CORES_SERIE = { fps: "#45c4ad", players: "#d9a13f", frame: "#7aa2f7" };
let dbRangeAtual = "24h";
let dbAmostras = [];
let dbSeriesVisiveis = { fps: true, players: true, frame: true };
let dbUltimoHistoricoEm = 0; // throttle: histórico é carregado no máx. 1x/min

function preencherCartoesDashboard(dash) {
  const s = dash.status || {};
  const info = s.info || {};
  const met = s.metrics || {};

  if (!s.online) {
    ["db-players", "db-fps", "db-frame", "db-uptime", "db-bases", "db-day", "db-ver"]
      .forEach((id) => { $("#" + id).textContent = "—"; });
    $("#db-cap").textContent = "offline";
  } else {
    const cur = pick(met, "currentplayernum", "currentPlayerNum") ?? "?";
    const max = pick(met, "maxplayernum", "maxPlayerNum") ?? "?";
    $("#db-players").textContent = cur;
    $("#db-cap").textContent = `de ${max} slots`;
    $("#db-fps").textContent = pick(met, "serverfps", "serverFPS") ?? "—";
    const ft = pick(met, "serverframetime", "serverFrameTime");
    $("#db-frame").textContent = ft != null ? Number(ft).toFixed(1) : "—";
    const up = pick(met, "uptime", "uptimeseconds", "uptimeSeconds");
    $("#db-uptime").textContent = up != null ? fmtUptimeLong(up) : "—";
    $("#db-bases").textContent = pick(met, "basecampnum", "baseCampCount") ?? "—";
    $("#db-day").textContent = pick(met, "days") != null
      ? `dia ${pick(met, "days")}` : "—";
    $("#db-ver").textContent = pick(info, "version") || "—";
    $("#srv-name2").textContent = pick(info, "servername", "name") || "(sem nome)";
    $("#wd-ver2").textContent = pick(info, "version") || "—";
  }

  if (dash.disco_livre_mb != null)
    $("#db-disk").textContent = fmtBytes(dash.disco_livre_mb * 1024 * 1024);

  // saúde recente
  $("#hs-backup").textContent = dash.ultimo_backup
    ? `${fmtDataHora(dash.ultimo_backup.modificado)} · ${fmtBytes(dash.ultimo_backup.bytes)}`
    : "nenhum backup visto";
  const queda = dash.ultima_queda;
  $("#hs-queda").textContent = queda ? fmtDataHora(queda.ts) : "nenhuma registrada";
  $("#hs-banco").textContent = dash.banco && dash.banco.ok
    ? `${dash.banco.metricas} amostras · ${fmtBytes(dash.banco.bytes)}`
    : "indisponível";

  atualizarResumo();
}

async function pollDashboard() {
  let dash;
  try {
    dash = await api("dashboard");
  } catch (_) { return; }
  preencherCartoesDashboard(dash);
  const agora = Date.now();
  if (agora - dbUltimoHistoricoEm > 60_000) {
    dbUltimoHistoricoEm = agora;
    carregarHistorico();
  }
}

async function carregarHistorico(forcar = false) {
  if (!forcar && Date.now() - dbUltimoHistoricoEm < 10_000) return;
  dbUltimoHistoricoEm = Date.now();
  let dados;
  try {
    dados = await api("metrics/history?range=" + dbRangeAtual);
  } catch (_) { return; }
  dbAmostras = dados.amostras || [];
  desenharGrafico();
  atualizarResumo(dados.resumo);
}

function rotuloTempo(ts) {
  const d = new Date(ts * 1000);
  if (dbRangeAtual === "1h" || dbRangeAtual === "6h")
    return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }) +
    " " + d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

function valoresSerie(a, chave) {
  if (chave === "fps") return a.fps;
  if (chave === "players") return a.players;
  if (chave === "frame") return a.frame_time;
  return null;
}

function desenharGrafico() {
  const cv = $("#db-chart");
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || cv.parentElement.clientWidth || 600;
  const h = 200;
  if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const padL = 8, padR = 8, padT = 10, padB = 22;
  const largura = w - padL - padR, altura = h - padT - padB;

  // eixo do tempo (base)
  ctx.strokeStyle = "rgba(141,160,175,.25)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padL, h - padB);
  ctx.lineTo(w - padR, h - padB);
  ctx.stroke();
  if (dbAmostras.length >= 2) {
    ctx.fillStyle = "rgba(141,160,175,.75)";
    ctx.font = "10px ui-monospace, monospace";
    ctx.textAlign = "left";
    ctx.fillText(rotuloTempo(dbAmostras[0].ts), padL, h - 6);
    ctx.textAlign = "right";
    ctx.fillText(rotuloTempo(dbAmostras[dbAmostras.length - 1].ts),
                 w - padR, h - 6);
  } else {
    ctx.fillStyle = "rgba(141,160,175,.6)";
    ctx.font = "12px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Coletando amostras… aguarde alguns minutos.",
                 w / 2, h / 2);
  }

  for (const serie of ["fps", "players", "frame"]) {
    if (!dbSeriesVisiveis[serie]) continue;
    const pontos = dbAmostras
      .map((a) => ({ x: a.ts, y: valoresSerie(a, serie) }))
      .filter((p) => p.y != null);
    if (pontos.length < 2) continue;

    const ys = pontos.map((p) => p.y);
    let min = Math.min(...ys), max = Math.max(...ys);
    if (serie === "players") { min = 0; }
    const span = Math.max(max - min, serie === "players" ? 1 : 0.001);
    const x = (i) => padL + (i / (pontos.length - 1)) * largura;
    const y = (v) => padT + (1 - (v - min) / span) * altura;

    // preenchimento suave
    const cor = CORES_SERIE[serie];
    ctx.beginPath();
    ctx.moveTo(x(0), y(pontos[0].y));
    pontos.forEach((p, i) => ctx.lineTo(x(i), y(p.y)));
    ctx.lineTo(x(pontos.length - 1), h - padB);
    ctx.lineTo(x(0), h - padB);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, padT, 0, h - padB);
    grad.addColorStop(0, cor + "30");
    grad.addColorStop(1, cor + "00");
    ctx.fillStyle = grad;
    ctx.fill();

    // linha principal
    ctx.beginPath();
    ctx.moveTo(x(0), y(pontos[0].y));
    pontos.forEach((p, i) => ctx.lineTo(x(i), y(p.y)));
    ctx.strokeStyle = cor;
    ctx.lineWidth = 1.8;
    ctx.lineJoin = "round";
    ctx.stroke();
  }
}

function atualizarResumo(resumo) {
  if (resumo === undefined) return; // chamado só p/ redesenhar
  const el = $("#db-resumo");
  if (!resumo || !resumo.amostras) {
    el.textContent = "Sem dados históricos ainda — o painel coleta uma "
                   + "amostra a cada 30s enquanto o servidor responde.";
    ["lg-fps", "lg-pl", "lg-ft"].forEach((id) => { $("#" + id).textContent = "—"; });
    $("#hs-online").textContent = "—";
    return;
  }
  $("#lg-fps").textContent = resumo.fps_med != null
    ? `méd ${resumo.fps_med} (${resumo.fps_min}–${resumo.fps_max})` : "—";
  $("#lg-pl").textContent = resumo.players_max != null
    ? `pico ${resumo.players_max}` : "—";
  $("#lg-ft").textContent = resumo.frame_med != null
    ? `méd ${resumo.frame_med} ms` : "—";
  $("#hs-online").textContent = resumo.pct_online != null
    ? `${resumo.pct_online}% online` : "—";
  el.textContent = `Últimas ${rotuloFaixa(dbRangeAtual)}: ${resumo.amostras} amostras`
    + (resumo.fps_med != null ? ` · FPS médio ${resumo.fps_med}` : "");
}

function rotuloFaixa(r) {
  return { "1h": "1 hora", "6h": "6 horas", "24h": "24 horas",
           "7d": "7 dias", "30d": "30 dias" }[r] || r;
}

$("#db-ranges").addEventListener("click", (ev) => {
  const chip = ev.target.closest(".chip[data-range]");
  if (!chip) return;
  dbRangeAtual = chip.dataset.range;
  document.querySelectorAll("#db-ranges .chip").forEach((c) =>
    c.classList.toggle("active", c === chip));
  carregarHistorico(true);
});

document.querySelectorAll(".chart-legend input[data-serie]").forEach((cb) => {
  cb.addEventListener("change", () => {
    dbSeriesVisiveis[cb.dataset.serie] = cb.checked;
    desenharGrafico();
  });
});

async function pollStatus() {
  try {
    renderStatus(await api("status"));
  } catch (_) { /* erro já tratado (401) ou rede */ }
}

function skelRow(cols, widths) {
  return `<tr>${Array.from({ length: cols }, (_, i) =>
    `<td><div class="skel" style="width:${widths[i] || 70}%"></div></td>`
  ).join("")}</tr>`;
}

async function loadPlayers() {
  const tbody = $("#players-tbody");
  if (!lastPlayers.length) {
    tbody.innerHTML = skelRow(6, [55, 30, 30, 45, 80, 60]) +
                      skelRow(6, [50, 35, 28, 40, 75, 60]);
  }
  try {
    const data = await api("players");
    // A API real devolve {"players":[...]}; versões antigas podiam devolver array puro.
    lastPlayers = Array.isArray(data) ? data : (data.players || []);
  } catch (_) {
    lastPlayers = [];
  }
  renderPlayersTable();
}

async function copiarTexto(texto) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(texto);
    return true;
  }
  // contexto inseguro (http em IP de rede local): fallback com textarea
  const aux = document.createElement("textarea");
  aux.value = texto;
  aux.style.position = "fixed";
  aux.style.opacity = "0";
  document.body.appendChild(aux);
  aux.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (_) { /* sem suporte */ }
  aux.remove();
  return ok;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* A API real do jogo usa caixas variadas entre versões (serverFPS/serverfps,
   currentPlayerNum/currentplayernum…). Esta função busca uma chave ignorando
   maiúsculas/minúsculas. */
function pick(obj, ...keys) {
  if (!obj) return undefined;
  const low = {};
  for (const k of Object.keys(obj)) low[k.toLowerCase()] = obj[k];
  for (const k of keys) {
    const v = low[k.toLowerCase()];
    if (v !== undefined && v !== null) return v;
  }
  return undefined;
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => {
    if (document.hidden) return;
    pollStatus();
    if ($("#tab-players").classList.contains("active")) loadPlayers();
  }, 5000);
}
function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

/* redesenha o gráfico quando a janela muda de tamanho */
window.addEventListener("resize", () => {
  if ($("#app-view") && !$("#app-view").classList.contains("hidden")) {
    desenharGrafico();
  }
}, { passive: true });

/* ================= jogadores: tabela + ações ================= */

function playerKey(p) {
  // A API real traz userId (ex.: "steam_76561..."); versões/labels variam,
  // então cobrimos todas as possibilidades.
  return pick(p, "userId", "userid", "steamId", "playerUid") ||
    pick(p, "playerId") || p.name || "";
}

function renderPlayersTable() {
  const tbody = $("#players-tbody");
  if (!lastPlayers.length) {
    tbody.innerHTML = `
      <tr><td colspan="6">
        <div class="empty-state">
          <svg class="icon"><use href="#i-users"/></svg>
          <div>Nenhum jogador conectado agora.</div>
        </div>
      </td></tr>`;
    return;
  }
  tbody.innerHTML = lastPlayers.map((p) => {
    const ip = pick(p, "iP", "ip") || "—";
    const ping = pick(p, "ping");
    const key = esc(playerKey(p));
    const nome = esc(pick(p, "name") || "?");
    return `
      <tr>
        <td class="cell-name">${nome}</td>
        <td><span class="badge-lvl">${pick(p, "level") ?? "?"}</span></td>
        <td class="mono">${ping != null ? Math.round(ping) : "?"}</td>
        <td class="mono">${esc(ip)}</td>
        <td class="cell-mono">${key.length > 22 ? key.slice(0, 20) + "…" : key}</td>
        <td class="actions">
          <button class="btn ghost small" data-act="kick" data-userid="${key}" data-name="${nome}">Chutar</button>
          <button class="btn danger small" data-act="ban" data-userid="${key}" data-name="${nome}">Banir</button>
        </td>
      </tr>`;
  }).join("");
}

$("#players-tbody").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const { act, userid, name } = btn.dataset;

  if (act === "kick") {
    const motivo = await askModal({
      title: `Chutar ${name}?`,
      text: "O jogador será desconectado e poderá voltar depois.",
      inputLabel: "Motivo (opcional):",
      okLabel: "Chutar",
    });
    if (motivo === null) return;
    btn.disabled = true;
    try {
      await api("kick", { method: "POST", body: { userid, message: motivo } });
      toast(`${name} chutado com sucesso.`);
    } catch (e) {
      toast(`Falhou: ${e.message}`, "err", 6000);
    }
    btn.disabled = false;
    setTimeout(loadPlayers, 800);
    return;
  }
  // ban → modal dedicado com duração
  $("#ban-title").textContent = `Banir ${name}?`;
  $("#ban-text").textContent = `ID: ${userid}`;
  $("#ban-motivo").value = "";
  $("#ban-duracao").value = "";
  $("#ban-ok").dataset.userid = userid;
  $("#ban-ok").dataset.name = name || "";
  $("#ban-backdrop").classList.remove("hidden");
  $("#ban-motivo").focus();
});

/* ---- modal de ban ---- */
let banPendente = null;

function fecharBanModal() {
  $("#ban-backdrop").classList.add("hidden");
}
$("#ban-cancel").addEventListener("click", fecharBanModal);
$("#ban-backdrop").addEventListener("click", (ev) => {
  if (ev.target === $("#ban-backdrop")) fecharBanModal();
});
$("#ban-ok").addEventListener("click", async () => {
  const btn = $("#ban-ok");
  const userid = btn.dataset.userid;
  const nome = btn.dataset.name || "";
  const motivo = $("#ban-motivo").value.trim();
  const horas = parseInt($("#ban-duracao").value, 10) || null;
  btn.disabled = true;
  try {
    await api("ban", { method: "POST",
      body: { userid, message: motivo, duracao_horas: horas } });
    toast(nome ? `${nome} banido${horas ? ` por ${horas}h` : " permanentemente"}.`
               : `Ban aplicado${horas ? ` (${horas}h)` : ""}.`, "ok", 6000);
    fecharBanModal();
    loadBans();
    setTimeout(loadPlayers, 800);
  } catch (e) {
    toast(`Falhou: ${e.message}`, "err", 6000);
  }
  btn.disabled = false;
});

/* ================= registro de jogadores ================= */

let regEstado = { busca: "", pagina: 1, ordem: "ultimo_visto" };
let regDebounce = null;

async function loadRegistro() {
  const tbody = $("#reg-tbody");
  let dados;
  try {
    dados = await api("players/registro?busca=" + encodeURIComponent(regEstado.busca)
      + "&pagina=" + regEstado.pagina + "&ordem=" + regEstado.ordem);
  } catch (_) { return; }
  const lista = dados.jogadores || [];
  if (!lista.length) {
    tbody.innerHTML = `<tr><td colspan="6"><div class="empty-state">
      <svg class="icon"><use href="#i-users"/></svg>
      <div>Nenhum jogador registrado ainda — o painel aprende observando quem conecta.</div>
    </div></td></tr>`;
  } else {
    tbody.innerHTML = lista.map((j) => `
      <tr class="reg-row" data-userid="${esc(j.userid)}" title="Ver ficha">
        <td class="cell-name">${esc(j.nome || "?")}<code class="mono small"> ${esc(j.userid)}</code></td>
        <td><span class="badge-lvl">${j.nivel_max ?? "?"}</span></td>
        <td class="mono">${fmtDuracao(j.tempo_total_s)}</td>
        <td class="mono">${j.sessoes}</td>
        <td class="mono small">${fmtDataHora(j.ultimo_visto)}</td>
        <td>${j.online ? '<span class="pill pill-on">online</span>'
            : j.banido ? '<span class="pill pill-ban">banido</span>'
            : '<span class="muted">—</span>'}</td>
      </tr>`).join("");
  }
  const total = dados.total || 0;
  const de = (dados.pagina - 1) * dados.por_pagina + 1;
  const ate = Math.min(total, dados.pagina * dados.por_pagina);
  $("#reg-info").textContent = total ? `${de}–${ate} de ${total}` : "0 registros";
  $("#reg-prev").disabled = dados.pagina <= 1;
  $("#reg-next").disabled = ate >= total;
}

function fmtDuracao(segundos) {
  segundos = Math.max(0, Math.floor(segundos || 0));
  const h = Math.floor(segundos / 3600);
  const m = Math.round((segundos % 3600) / 60);
  if (h >= 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  if (h > 0) return `${h}h ${m}min`;
  return `${m}min`;
}

$("#reg-busca").addEventListener("input", () => {
  clearTimeout(regDebounce);
  regDebounce = setTimeout(() => {
    regEstado.busca = $("#reg-busca").value.trim();
    regEstado.pagina = 1;
    loadRegistro();
  }, 300);
});
document.querySelectorAll(".sortable[data-ordem]").forEach((th) => {
  th.addEventListener("click", () => {
    regEstado.ordem = th.dataset.ordem;
    regEstado.pagina = 1;
    loadRegistro();
  });
});
$("#reg-prev").addEventListener("click", () => { regEstado.pagina--; loadRegistro(); });
$("#reg-next").addEventListener("click", () => { regEstado.pagina++; loadRegistro(); });

$("#reg-tbody").addEventListener("click", async (ev) => {
  const linha = ev.target.closest(".reg-row[data-userid]");
  if (!linha) return;
  let ficha;
  try {
    ficha = await api("players/detalhe?userid=" + encodeURIComponent(linha.dataset.userid));
  } catch (e) {
    return toast(`Falhou: ${e.message}`, "err", 5000);
  }
  $("#ficha-nome").textContent = ficha.nome || ficha.userid;
  $("#ficha-grid").innerHTML = `
    <div><span>ID</span><b class="mono small">${esc(ficha.userid)}</b></div>
    <div><span>Primeiro acesso</span><b>${fmtDataHora(ficha.primeiro_visto)}</b></div>
    <div><span>Último acesso</span><b>${fmtDataHora(ficha.ultima_visto)}</b></div>
    <div><span>Tempo total</span><b>${fmtDuracao(ficha.tempo_total_s)}</b></div>
    <div><span>Sessões</span><b>${ficha.sessoes}</b></div>
    <div><span>Nível máx.</span><b>${ficha.nivel_max ?? "—"}</b></div>
    <div><span>Banido?</span><b>${ficha.banido ? "SIM" : "não"}</b></div>
    <div><span>Status</span><b>${ficha.online ? "online agora" : "offline"}</b></div>`;
  const sessoes = ficha.sessoes_lista || [];
  $("#ficha-sessoes").innerHTML = sessoes.length ? sessoes.map((s) => `
    <tr>
      <td class="mono small">${fmtDataHora(s.inicio)}</td>
      <td class="mono small">${s.fim ? fmtDataHora(s.fim) : "—"}</td>
      <td class="mono">${s.duracao_s != null ? fmtDuracao(s.duracao_s)
                        : (s.fim ? "—" : "<em>em andamento</em>")}</td>
    </tr>`).join("")
    : `<tr><td colspan="3" class="muted center">Sem sessões registradas.</td></tr>`;
  $("#ficha-backdrop").classList.remove("hidden");
});
$("#ficha-close").addEventListener("click", () =>
  $("#ficha-backdrop").classList.add("hidden"));
$("#ficha-backdrop").addEventListener("click", (ev) => {
  if (ev.target === $("#ficha-backdrop"))
    $("#ficha-backdrop").classList.add("hidden");
});

/* ================= livro de bans ================= */

async function loadBans() {
  const tbody = $("#bans-tbody");
  const soAtivos = $("#bans-so-ativos").checked ? "1" : "";
  let dados;
  try {
    dados = await api("bans?ativos=" + soAtivos);
  } catch (_) { return; }
  const lista = dados.bans || [];
  if (!lista.length) {
    tbody.innerHTML = `<tr><td colspan="5"><div class="empty-state">
      <svg class="icon"><use href="#i-ban"/></svg>
      <div>Nenhum ban registrado pelo painel.</div></div></td></tr>`;
    return;
  }
  tbody.innerHTML = lista.map((b) => {
    let situacao;
    if (!b.ativo) situacao = `removido (${esc(b.removido_por || "?")})`;
    else if (b.vencido) situacao = "vencido — removendo";
    else if (b.expira_em) situacao = `ativo · falta ${fmtDuracao(b.expira_em - Date.now()/1000)}`;
    else situacao = "permanente";
    return `
      <tr>
        <td>${esc(b.nome || "?")}<code class="mono small"> ${esc(b.userid)}</code></td>
        <td class="small">${esc(b.motivo || "—")}</td>
        <td class="mono small">${fmtDataHora(b.criado_em)}</td>
        <td class="mono small">${b.expira_em ? fmtDataHora(b.expira_em) : "nunca"}</td>
        <td class="small">${esc(situacao)}</td>
      </tr>`;
  }).join("");
}
$("#bans-so-ativos").addEventListener("change", loadBans);

$("#pl-refresh").addEventListener("click", loadPlayers);

async function unban() {
  const id = $("#unban-id").value.trim();
  if (!id) return toast("Informe o ID do jogador (ex: steam_76561198…).", "warn");
  const btn = $("#btn-unban");
  btn.disabled = true;
  try {
    await api("unban", { method: "POST", body: { userid: id } });
    toast(`Ban removido: ${id}`);
    $("#unban-id").value = "";
    loadBans();
  } catch (e) {
    toast(`Falhou: ${e.message}`, "err", 6000);
  }
  btn.disabled = false;
}

$("#btn-unban").addEventListener("click", unban);
$("#unban-id").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") { ev.preventDefault(); unban(); }
});

/* ================= backups ================= */

function fmtBytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return `${n} B`;
  const unidades = ["KB", "MB", "GB", "TB"];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < unidades.length - 1);
  return `${n.toFixed(n >= 100 ? 0 : 1)} ${unidades[i]}`;
}

function fmtDataHora(epoch) {
  const d = new Date((epoch || 0) * 1000);
  return isNaN(d) ? "—" : d.toLocaleString("pt-BR");
}

async function loadBackups() {
  const tbody = $("#backups-tbody");
  const hint = $("#bk-hint");
  tbody.innerHTML = skelRow(4, [60, 25, 45, 30]) + skelRow(4, [50, 28, 40, 30]);
  hint.classList.add("hidden");
  try {
    const data = await api("backups");
    renderBackups(data);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="4" class="center" style="color:var(--red)">${esc(e.message)}</td></tr>`;
  }
}

function renderBackups(data) {
  const tbody = $("#backups-tbody");
  const hint = $("#bk-hint");

  if (data && data.configurado === false) {
    hint.textContent = "Pasta de backups não configurada — defina BACKUP_DIR no painel " +
                       "(no Docker, monte /palworld/backups e aponte BACKUP_DIR pra ela).";
    hint.classList.remove("hidden");
    tbody.innerHTML = `<tr><td colspan="4"><div class="empty-state">
      <svg class="icon"><use href="#i-archive"/></svg>
      <div>Backups não configurados.</div></div></td></tr>`;
    return;
  }

  const lista = data.backups || [];
  if (!lista.length) {
    tbody.innerHTML = `<tr><td colspan="4"><div class="empty-state">
      <svg class="icon"><use href="#i-archive"/></svg>
      <div>${esc(data.nota || "Nenhum backup encontrado ainda.")}</div></div></td></tr>`;
    return;
  }

  tbody.innerHTML = lista.map((b) => `
    <tr>
      <td class="cell-name mono" style="font-size:.84rem">${esc(b.nome)}</td>
      <td class="mono">${fmtBytes(b.bytes)}</td>
      <td class="mono">${fmtDataHora(b.modificado)}</td>
      <td class="actions">
        <button class="btn ghost small" data-bk="${esc(b.nome)}">
          <svg class="icon"><use href="#i-save"/></svg> Baixar
        </button>
        <button class="btn warn small" data-bk-acao="restaurar" data-bk="${esc(b.nome)}">
          Restaurar
        </button>
        <button class="btn danger small" data-bk-acao="excluir" data-bk="${esc(b.nome)}">
          Excluir
        </button>
      </td>
    </tr>`).join("");
}

async function baixarBackup(nome, btn) {
  btn.disabled = true;
  try {
    const res = await fetch("/api/backups/download?name=" + encodeURIComponent(nome), {
      headers: { "X-Panel-Token": token },
    });
    if (res.status === 401) { showLogin("Sessão expirada — entre novamente."); return; }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `Erro HTTP ${res.status}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = nome;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    toast(`Backup baixado: ${nome}`);
  } catch (e) {
    toast(`Falhou: ${e.message}`, "err", 6000);
  }
  btn.disabled = false;
}

$("#backups-tbody").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-bk]");
  if (btn) baixarBackup(btn.dataset.bk, btn);
});

$("#bk-refresh").addEventListener("click", loadBackups);

/* ---- criar backup manual do painel ---- */
$("#bk-criar").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  try {
    const res = await api("backups/criar", { method: "POST", body: {} });
    toast(`✅ Backup criado: ${res.nome} (${fmtBytes(res.bytes)})`, "ok", 7000);
    loadBackups();
  } catch (e) {
    toast(`Falhou: ${e.message}`, "err", 7000);
  }
  btn.disabled = false;
});

/* ---- excluir / restaurar backup ---- */
$("#backups-tbody").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-bk-acao]");
  if (!btn) return;
  const nome = btn.dataset.bk;
  const acao = btn.dataset.bkAcao;

  if (acao === "excluir") {
    const ok = await askConfirm({
      title: `Excluir ${nome}?`,
      text: "O arquivo será apagado permanentemente do disco.",
      okLabel: "Excluir",
    });
    if (!ok) return;
    btn.disabled = true;
    try {
      await api("backups/excluir", { method: "POST", body: { nome } });
      toast(`Backup excluído: ${nome}`);
      loadBackups();
    } catch (e) {
      toast(`Falhou: ${e.message}`, "err", 6000);
      btn.disabled = false;
    }
    return;
  }

  if (acao === "restaurar") {
    // dupla confirmação: entendimento + digitar o nome
    const primeiro = await askConfirm({
      title: `Restaurar o mundo a partir de ${nome}?`,
      text: "A pasta de saves atual será substituída pelo conteúdo deste "
          + "backup. O estado ATUAL é preservado em uma pasta .pre-restauro. "
          + "O servidor precisa ser reiniciado depois.",
      okLabel: "Entendi, continuar",
    });
    if (!primeiro) return;
    const digitado = await askModal({
      title: "Confirmação final",
      text: `Digite o nome do arquivo para confirmar o restauro:`,
      inputLabel: nome,
      okLabel: "Restaurar agora",
    });
    if (digitado === null) return;
    if (digitado !== nome)
      return toast("Nome não confere — restauro cancelado.", "warn");
    btn.disabled = true;
    try {
      const res = await api("backups/restaurar",
                            { method: "POST", body: { nome } });
      toast(`🌍 Mundo restaurado! Estado anterior salvo em `
          + `${res.anterior_preservado_em}. REINICIE O SERVIDOR.`,
            "ok", 12000);
      store_mod_console_evento(res);
    } catch (e) {
      toast(`Falhou: ${e.message}`, "err", 8000);
    }
    btn.disabled = false;
  }
});

function store_mod_console_evento(res) {
  // o servidor já registra o evento; aqui só reforçamos feedback local
  console.log("restore concluído:", res);
}

/* ================= agenda (scheduler) ================= */

const ROTULO_TIPO = {
  restart: "Reiniciar", save: "Salvar mundo", announce: "Anúncio",
  backup_mundo: "Backup do mundo", limpeza_backups: "Limpeza de backups",
};

async function loadAgenda() {
  const tbody = $("#agenda-tbody");
  let dados;
  try {
    dados = await api("scheduler");
  } catch (_) { return; }
  const jobs = dados.jobs || [];
  if (!jobs.length) {
    tbody.innerHTML = `<tr><td colspan="6"><div class="empty-state">
      <svg class="icon"><use href="#i-refresh"/></svg>
      <div>Nenhuma tarefa agendada.</div></div></td></tr>`;
    return;
  }
  tbody.innerHTML = jobs.map((j) => {
    const quando = j.hora ? `diário às ${j.hora}`
                 : `a cada ${j.intervalo_horas}h`;
    return `
      <tr class="${j.habilitado ? "" : "job-desativado"}">
        <td class="cell-name">${esc(j.nome)}</td>
        <td>${ROTULO_TIPO[j.tipo] || esc(j.tipo)}</td>
        <td class="mono small">${quando}</td>
        <td class="mono small">${j.habilitado ? fmtDataHora(j.proximo_run) : "—"}
          <button class="btn ghost tiny" data-ag-toggle="${j.id}"
                  title="${j.habilitado ? "Desativar" : "Ativar"}">
            ${j.habilitado ? "⏸" : "▶"}
          </button></td>
        <td class="small muted">${esc(j.ultimo_resultado || "nunca rodou")}</td>
        <td class="actions">
          <button class="btn ghost tiny" data-ag-run="${j.id}">Rodar</button>
          <button class="btn ghost tiny" data-ag-edit="${j.id}">Editar</button>
          <button class="btn danger tiny" data-ag-del="${j.id}">Excluir</button>
        </td>
      </tr>`;
  }).join("");
}

let jobEditando = null;

function abrirJobModal(job = null) {
  jobEditando = job;
  $("#job-title").textContent = job ? `Editar: ${job.nome}` : "Nova tarefa";
  $("#job-nome").value = job?.nome || "";
  $("#job-tipo").value = job?.tipo || "restart";
  const modo = job?.hora ? "hora" : "intervalo";
  $("#job-agenda-modo").value = modo;
  $("#job-hora").value = job?.hora || "04:00";
  $("#job-intervalo").value = job?.intervalo_horas ?? 24;
  sincronizarCamposJob();
  renderCamposExtra();
  $("#job-backdrop").classList.remove("hidden");
  $("#job-nome").focus();
}

function fecharJobModal() { $("#job-backdrop").classList.add("hidden"); }

function sincronizarCamposJob() {
  const modo = $("#job-agenda-modo").value;
  $("#job-hora").classList.toggle("hidden", modo !== "hora");
  $("#job-intervalo").classList.toggle("hidden", modo !== "intervalo");
  $("#job-intervalo-un").classList.toggle("hidden", modo !== "intervalo");
}

function renderCamposExtra() {
  const tipo = $("#job-tipo").value;
  const alvo = $("#job-campos-extra");
  const p = jobEditando?.params || {};
  if (tipo === "announce") {
    alvo.innerHTML = `
      <label class="field-label" for="job-msg">Mensagem</label>
      <textarea id="job-msg" rows="2" maxlength="500"
        placeholder="Mensagem do anúncio programado">${esc(p.message || "")}</textarea>`;
  } else if (tipo === "restart") {
    alvo.innerHTML = `
      <label class="field-label" for="job-wait">Contagem regressiva</label>
      <select id="job-wait">
        <option value="60">1 minuto</option>
        <option value="300" selected>5 minutos</option>
        <option value="600">10 minutos</option>
      </select>`;
    $("#job-wait").value = String(p.waittime || 300);
  } else if (tipo === "limpeza_backups") {
    alvo.innerHTML = `
      <label class="field-label" for="job-manter">Manter os N mais recentes</label>
      <input type="number" id="job-manter" min="1" max="500" value="${p.manter_ultimos ?? 10}">
      <label class="field-label" for="job-idade">Apagar acima de X dias</label>
      <input type="number" id="job-idade" min="0" max="3650" value="${p.idade_dias ?? 30}">`;
  } else {
    alvo.innerHTML = "";
  }
}

$("#ag-nova").addEventListener("click", () => abrirJobModal());
$("#job-cancel").addEventListener("click", fecharJobModal);
$("#job-backdrop").addEventListener("click", (ev) => {
  if (ev.target === $("#job-backdrop")) fecharJobModal();
});
$("#job-tipo").addEventListener("change", renderCamposExtra);
$("#job-agenda-modo").addEventListener("change", sincronizarCamposJob);

$("#job-ok").addEventListener("click", async () => {
  const btn = $("#job-ok");
  const modo = $("#job-agenda-modo").value;
  const corpo = {
    nome: $("#job-nome").value.trim(),
    tipo: $("#job-tipo").value,
    habilitado: true,
    hora: modo === "hora" ? $("#job-hora").value : null,
    intervalo_horas: modo === "intervalo"
      ? parseFloat($("#job-intervalo").value) || null : null,
    params: {},
  };
  if (corpo.tipo === "announce")
    corpo.params.message = ($("#job-msg")?.value || "").trim();
  if (corpo.tipo === "restart")
    corpo.params.waittime = parseInt($("#job-wait")?.value || "300", 10);
  if (corpo.tipo === "limpeza_backups") {
    corpo.params.manter_ultimos = parseInt($("#job-manter")?.value || "10", 10);
    corpo.params.idade_dias = parseInt($("#job-idade")?.value || "30", 10);
  }
  if (!corpo.nome) return toast("Dê um nome à tarefa.", "warn");

  btn.disabled = true;
  try {
    if (jobEditando) {
      await api("scheduler/" + jobEditando.id,
                { method: "POST", body: corpo });
    } else {
      await api("scheduler", { method: "POST", body: corpo });
    }
    toast("Tarefa salva.");
    fecharJobModal();
    loadAgenda();
  } catch (e) {
    toast(`Falhou: ${e.message}`, "err", 6000);
  }
  btn.disabled = false;
});

$("#agenda-tbody").addEventListener("click", async (ev) => {
  const runBtn = ev.target.closest("[data-ag-run]");
  const editBtn = ev.target.closest("[data-ag-edit]");
  const delBtn = ev.target.closest("[data-ag-del]");
  const toggleBtn = ev.target.closest("[data-ag-toggle]");
  const tbody = $("#agenda-tbody");

  if (runBtn) {
    const id = runBtn.dataset.agRun;
    runBtn.disabled = true;
    try {
      const res = await api(`scheduler/${id}/run`, { method: "POST" });
      toast(`Execução: ${res.resultado}`, res.ok ? "ok" : "err", 8000);
    } catch (e) { toast(e.message, "err", 8000); }
    runBtn.disabled = false;
    loadAgenda();
  } else if (editBtn) {
    try {
      const dados = await api("scheduler");
      const job = (dados.jobs || []).find((j) => String(j.id) === editBtn.dataset.agEdit);
      if (job) abrirJobModal(job);
    } catch (_) {}
  } else if (delBtn) {
    const linha = delBtn.closest("tr");
    const nome = linha?.querySelector(".cell-name")?.textContent?.trim() || `#${delBtn.dataset.agDel}`;
    const ok = await askConfirm({
      title: `Excluir tarefa "${nome}"?`,
      text: "Ela deixará de ser executada automaticamente.",
      okLabel: "Excluir",
    });
    if (!ok) return;
    try {
      await fetch("/api/scheduler/" + delBtn.dataset.agDel, {
        method: "DELETE", headers: { "X-Panel-Token": token } });
      toast("Tarefa excluída.");
      loadAgenda();
    } catch (e) { toast(e.message, "err"); }
  } else if (toggleBtn) {
    const id = toggleBtn.dataset.agToggle;
    try {
      const dados = await api("scheduler");
      const job = (dados.jobs || []).find((j) => String(j.id) === id);
      if (job) {
        await api("scheduler/" + id,
                  { method: "POST", body: { habilitado: !job.habilitado } });
        loadAgenda();
      }
    } catch (e) { toast(e.message, "err"); }
  }
});

/* ================= visão geral ================= */

$("#ov-msg").addEventListener("input", () => {
  $("#ov-count").textContent = `${$("#ov-msg").value.length}/500`;
});

async function enviarAnuncio(msg, botao) {
  msg = (msg || "").trim();
  if (!msg) return toast("Escreva uma mensagem primeiro.", "warn");
  if (botao) botao.disabled = true;
  try {
    await api("announce", { method: "POST", body: { message: msg } });
    toast("Anúncio enviado ao servidor! 📣");
    $("#ov-msg").value = "";
    $("#ov-count").textContent = "0/500";
  } catch (e) {
    toast(`Falhou: ${e.message}`, "err", 6000);
  }
  if (botao) botao.disabled = false;
}

$("#ov-send").addEventListener("click", (ev) => enviarAnuncio($("#ov-msg").value, ev.target));
$("#ct-send").addEventListener("click", (ev) => enviarAnuncio($("#ct-msg-input").value, ev.target));

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => enviarAnuncio(chip.dataset.msg));
});

/* ================= controle ================= */

$("#ct-save").addEventListener("click", async (ev) => {
  ev.target.disabled = true;
  try {
    await api("save", { method: "POST" });
    toast("Mundo salvo! 💾");
  } catch (e) {
    toast(`Falhou: ${e.message}`, "err", 6000);
  }
  ev.target.disabled = false;
});

$("#ct-restart").addEventListener("click", async (ev) => {
  const wait = parseInt($("#ct-restart-wait").value, 10);
  const ok = await askConfirm({
    title: "Reiniciar o servidor?",
    text: `Os jogadores receberão um aviso e o mundo será salvo. O servidor cai em ${wait}s e volta sozinho em seguida.`,
    okLabel: "Reiniciar",
  });
  if (!ok) return;
  ev.target.disabled = true;
  toast(`Reinício agendado em ${wait}s…`, "warn", 6000);
  try {
    await api("restart", { method: "POST", body: { waittime: wait } });
    toast("Comandos enviados. O Docker religa o servidor automaticamente.", "ok", 7000);
  } catch (e) {
    toast(`Falhou: ${e.message}`, "err", 6000);
  }
  ev.target.disabled = false;
});

$("#ct-shutdown").addEventListener("click", async (ev) => {
  const wait = parseInt($("#ct-shutdown-wait").value, 10);
  const ok = await askConfirm({
    title: "Desligar o servidor?",
    text: `Aviso aos jogadores, save do mundo e desligamento em ${wait}s. Obs.: o container reinicia sozinho pelo Docker — para deixar offline, pare-o no CasaOS.`,
    okLabel: "Desligar",
  });
  if (!ok) return;
  ev.target.disabled = true;
  try {
    await api("shutdown", { method: "POST", body: { waittime: wait } });
    toast(`Servidor desligando em ${wait}s…`, "warn", 7000);
  } catch (e) {
    toast(`Falhou: ${e.message}`, "err", 6000);
  }
  ev.target.disabled = false;
});

/* ---- emergência ---- */
$("#ct-emergency").addEventListener("click", async () => {
  const ok = await askConfirm({
    title: "🚨 Executar EMERGÊNCIA?",
    text: "Sequência: anúncio → save → backup do mundo → desligamento "
        + "em 60s. Operações concorrentes ficam bloqueadas.",
    okLabel: "Executar agora",
  });
  if (!ok) return;
  toast("Emergência em andamento…", "warn");
  try {
    const res = await api("emergency", { method: "POST" });
    toast(`Passos: ${Object.entries(res.passos)
      .map(([k, v]) => `${k}=${v}`).join(", ")}`, res.ok ? "ok" : "err", 9000);
  } catch (e) {
    toast(`Falhou: ${e.message}`, "err", 7000);
  }
});

$("#st-refresh").addEventListener("click", loadSettings);

/* ================= modal genérico ================= */

function askModal({ title, text, inputLabel = "", okLabel = "Confirmar", inputValue = "" }) {
  return new Promise((resolve) => {
    const backdrop = $("#modal-backdrop");
    const input = $("#modal-input");
    const okBtn = $("#modal-ok");

    $("#modal-title").textContent = title;
    $("#modal-text").textContent = text;
    okBtn.textContent = okLabel;
    okBtn.className = `btn ${title.toLowerCase().includes("ban") ? "danger" : "primary"}`;

    if (inputLabel) {
      input.classList.remove("hidden");
      input.placeholder = inputLabel;
      input.value = inputValue;
    } else {
      input.classList.add("hidden");
      input.value = "";
    }

    backdrop.classList.remove("hidden");
    setTimeout(() => (inputLabel ? input.focus() : okBtn.focus()), 30);

    function done(result) {
      backdrop.classList.add("hidden");
      okBtn.removeEventListener("click", onOk);
      $("#modal-cancel").removeEventListener("click", onCancel);
      input.removeEventListener("keydown", onEnter);
      document.removeEventListener("keydown", onEsc);
      backdrop.removeEventListener("click", onBackdrop);
      resolve(result);
    }
    function onOk() { done(input.value.trim()); }
    function onCancel() { done(null); }
    function onEnter(ev) { if (ev.key === "Enter") { ev.preventDefault(); onOk(); } }
    function onEsc(ev) { if (ev.key === "Escape") onCancel(); }
    function onBackdrop(ev) { if (ev.target === backdrop) onCancel(); }

    okBtn.addEventListener("click", onOk);
    $("#modal-cancel").addEventListener("click", onCancel);
    input.addEventListener("keydown", onEnter);
    document.addEventListener("keydown", onEsc);
    backdrop.addEventListener("click", onBackdrop);
  });
}

function askConfirm(opts) {
  return askModal(opts).then((v) => v !== null);
}

/* ================= editor de configurações ================= */

const MASKA = "\u2022\u2022\u2022\u2022\u2022\u2022";
let seEstado = null;        // resposta de /api/settings/editor
let seMudancas = {};        // chave → valor bruto digitado
let sePorChave = {};        // chave → item do catálogo
let seCatAtiva = "todas";
let sePresetsCarregados = false;

function seValorAtual(item) {
  if (Object.prototype.hasOwnProperty.call(seMudancas, item.chave))
    return seMudancas[item.chave];
  const v = seEstado.valores[item.chave];
  return v === "" ? (item.padrao ?? "") : v;
}

function seInputHTML(item) {
  const valor = seValorAtual(item);
  const id = `se-in-${item.chave}`;
  const senhaTipo = item.senha ? "password" : "text";
  if (item.tipo === "bool") {
    const marcado = valor === true || valor === "true" || valor === "True";
    return `<label class="switch"><input type="checkbox" data-chave="${item.chave}" ${marcado ? "checked" : ""}><span></span></label>`;
  }
  if (item.tipo === "enum") {
    return `<select data-chave="${item.chave}">${item.opcoes.map(o =>
      `<option value="${esc(o)}" ${o === valor ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  }
  if (item.tipo === "int")
    return `<input type="number" step="1" inputmode="numeric"
              min="${item.min ?? ""}" max="${item.max ?? ""}"
              data-chave="${item.chave}" value="${esc(valor)}">`;
  if (item.tipo === "float")
    return `<input type="number" step="any" inputmode="decimal"
              min="${item.min ?? ""}" max="${item.max ?? ""}"
              data-chave="${item.chave}" value="${esc(valor)}">`;
  if (item.tipo === "password")
    return `<input type="${senhaTipo}" autocomplete="new-password"
              placeholder="${valor ? MASKA : "(vazio)"}"
              data-chave="${item.chave}" value="">`;
  return `<input type="text" maxlength="${item.max_len || 300}" data-chave="${item.chave}" value="${esc(valor)}">`;
}

function seRenderGrupos() {
  const alvo = $("#se-grupos");
  const cats = seEstado.categorias;
  $("#se-cats").innerHTML =
    `<button class="chip active" data-cat="todas">Todas</button>` +
    cats.map((c) => `<button class="chip" data-cat="${c.id}">${esc(c.rotulo)}</button>`).join("");
  alvo.innerHTML = cats.map((c) => {
    const itens = seEstado.catalogo.filter((i) => i.categoria === c.id);
    if (!itens.length) return "";
    const linhas = itens.map((item) => {
      const alterada = Object.prototype.hasOwnProperty.call(seMudancas, item.chave);
      return `
      <div class="se-item ${alterada ? "changed" : ""}" data-chave="${item.chave}">
        <div class="se-info">
          <span class="se-label">${esc(item.label || item.chave)}
            <code>${esc(item.chave)}</code>
            <span class="badge-restart" title="Vale após reiniciar o servidor">restart</span>
          </span>
          <small class="muted">${esc(item.desc || "")}${item.unidade ? ` (${esc(item.unidade)})` : ""}</small>
        </div>
        <div class="se-ctrl">
          ${seInputHTML(item)}
          <button class="btn ghost tiny se-default" title="Restaurar padrão (${esc(String(item.padrao))})">↺</button>
        </div>
      </div>`;
    }).join("");
    return `<div class="card se-grupo" data-cat="${c.id}">
      <h3>${esc(c.rotulo)}</h3>${linhas}</div>`;
  }).join("");
}

function seAtualizarContagem() {
  const n = Object.keys(seMudancas).length;
  $("#se-count").textContent = n === 0 ? "sem alterações"
    : `${n} alteração${n > 1 ? "ões" : ""}`;
  $("#se-save").disabled = n === 0;
  $("#se-cancel").disabled = n === 0;
  document.querySelectorAll("#se-grupos .se-item").forEach((el) => {
    el.classList.toggle("changed",
      Object.prototype.hasOwnProperty.call(seMudancas, el.dataset.chave));
  });
}

async function loadSettings() {
  try {
    seEstado = await api("settings/editor");
  } catch (e) {
    toast(`Configurações: ${e.message}`, "err", 6000);
    return;
  }
  seMudancas = {};
  sePorChave = {};
  seEstado.catalogo.forEach((i) => { sePorChave[i.chave] = i; });
  $("#se-aviso-ini").classList.toggle("hidden", !!seEstado.ini_existe);
  seRenderGrupos();
  seAtualizarContagem();
  if (!sePresetsCarregados) carregarPresetsSelect();
  loadSettingsBackups();
}

/* ---- mudanças nos campos (delegação) ---- */
$("#se-grupos").addEventListener("input", (ev) => {
  const alvo = ev.target.closest("[data-chave]");
  if (!alvo || !seEstado) return;
  const chave = alvo.dataset.chave;
  const item = sePorChave[chave];
  if (!item) return;
  const bruto = item.tipo === "bool" ? alvo.checked : alvo.value;
  compararMudanca(chave, bruto, item);
});

function compararMudanca(chave, bruto, item) {
  const atual = seEstado.valores[chave];
  const efetivo = atual === "" ? (item.padrao ?? "") : atual;
  if (item.senha && bruto === "") {
    delete seMudancas[chave];           // senha tocada mas vazia = sem mudança
  } else if (String(efetivo) === String(bruto) || efetivo === bruto) {
    delete seMudancas[chave];
  } else {
    seMudancas[chave] = bruto;
  }
  seAtualizarContagem();
}

$("#se-grupos").addEventListener("click", (ev) => {
  const btn = ev.target.closest(".se-default");
  if (!btn || !seEstado) return;
  const wrap = btn.closest(".se-item");
  const chave = wrap.dataset.chave;
  const item = sePorChave[chave];
  if (!item) return;
  const efetivo = seEstado.valores[chave];
  const jaEhPadrao = String(efetivo === "" ? "" : efetivo) === String(item.padrao);
  if (jaEhPadrao) delete seMudancas[chave];
  else seMudancas[chave] = item.padrao;
  seRenderItem(chave);
  seAtualizarContagem();
});

function seRenderItem(chave) {
  const item = sePorChave[chave];
  const wrap = document.querySelector(`#se-grupos .se-item[data-chave="${CSS.escape(chave)}"]`);
  if (!wrap) return;
  const ctrl = wrap.querySelector(".se-ctrl");
  const antigo = ctrl.querySelector("[data-chave]");
  const foco = document.activeElement === antigo;
  ctrl.innerHTML = seInputHTML(item) +
    `<button class="btn ghost tiny se-default" title="Restaurar padrão (${esc(String(item.padrao))})">↺</button>`;
  if (foco) ctrl.querySelector("[data-chave]").focus();
}

/* ---- filtro por categoria ---- */
$("#se-cats").addEventListener("click", (ev) => {
  const chip = ev.target.closest(".chip[data-cat]");
  if (!chip) return;
  seCatAtiva = chip.dataset.cat;
  document.querySelectorAll("#se-cats .chip").forEach((c) =>
    c.classList.toggle("active", c === chip));
  document.querySelectorAll("#se-grupos .se-grupo").forEach((g) =>
    g.classList.toggle("hidden", !(seCatAtiva === "todas" || g.dataset.cat === seCatAtiva)));
});

/* ---- descartar / salvar ---- */
$("#se-cancel").addEventListener("click", () => {
  seMudancas = {};
  seRenderGrupos();
  seAtualizarContagem();
});

$("#st-refresh").addEventListener("click", loadSettings);

$("#se-save").addEventListener("click", async () => {
  const botao = $("#se-save");
  botao.disabled = true;
  let diff;
  try {
    diff = await api("settings/diff", { method: "POST", body: { mudancas: seMudancas } });
  } catch (e) {
    toast(`Falhou ao validar: ${e.message}`, "err", 6000);
    botao.disabled = false;
    return;
  }
  botao.disabled = false;
  if (diff.invalidas.length) {
    toast(diff.invalidas.map(i => `${i.chave}: ${i.motivo}`).join(" · "), "err", 7000);
    return;
  }
  if (!diff.validas.length) { toast("Nenhuma alteração real para aplicar.", "warn"); return; }
  abrirSeModal({
    titulo: "Revisar alterações",
    texto: "Confira antes de gravar no PalWorldSettings.ini:",
    linhas: diff.validas,
    nota: "⚠️ As novas configurações valem após REINICIAR o servidor. Um backup do arquivo atual será criado automaticamente.",
    okLabel: "Aplicar e criar backup",
    onOk: async () => {
      try {
        const res = await api("settings/apply", { method: "POST", body: { mudancas: seMudancas } });
        if (!res.ok) {
          toast(res.invalidas.map(i => `${i.chave}: ${i.motivo}`).join(" · "), "err", 7000);
          return;
        }
        toast(`✅ ${res.aplicadas.length} configuração(ões) gravadas. Backup: ${res.backup}. Reinicie o servidor!`, "ok", 9000);
        sePresetsCarregados = false; // recarrega tudo
        await loadSettings();
      } catch (e) {
        toast(`Falhou: ${e.message}`, "err", 7000);
      }
    },
  });
});

/* ---- modal genérico do editor (diff) ---- */
let seModalOnOk = null;

function abrirSeModal({ titulo, texto, linhas, nota, okLabel, onOk }) {
  seModalOnOk = onOk || null;
  $("#se-modal-title").textContent = titulo;
  $("#se-modal-text").textContent = texto || "";
  $("#se-diff-tbody").innerHTML = (linhas || []).map((l) => `
    <tr>
      <td><code>${esc(l.chave)}</code></td>
      <td class="mono">${esc(formatarValor(l.de))}</td>
      <td class="mono se-para">${esc(formatarValor(l.para))}</td>
    </tr>`).join("");
  $("#se-modal-note").textContent = nota || "";
  $("#se-modal-ok").textContent = okLabel || "Aplicar";
  $("#se-modal-backdrop").classList.remove("hidden");
  $("#se-modal-ok").focus();
}

function formatarValor(v) {
  if (v === true) return "True";
  if (v === false) return "False";
  if (v === null || v === undefined || v === "") return "(padrão)";
  return String(v);
}

function fecharSeModal() {
  $("#se-modal-backdrop").classList.add("hidden");
  seModalOnOk = null;
}
$("#se-modal-cancel").addEventListener("click", fecharSeModal);
$("#se-modal-backdrop").addEventListener("click", (ev) => {
  if (ev.target === $("#se-modal-backdrop")) fecharSeModal();
});
$("#se-modal-ok").addEventListener("click", async () => {
  const fn = seModalOnOk;
  fecharSeModal();
  if (fn) await fn();
});

/* ---- presets ---- */
async function carregarPresetsSelect() {
  try {
    const dados = await api("settings/presets");
    window.__se_presets = dados.presets || [];
    const sel = $("#se-preset-sel");
    sel.innerHTML = '<option value="">Presets…</option>' +
      window.__se_presets.map((p, i) =>
        `<option value="${i}">${esc(rotuloPreset(p.nome))}</option>`).join("");
    sePresetsCarregados = true;
  } catch (_) { /* offline — segue sem presets */ }
}

function rotuloPreset(nome) {
  const mapa = {
    casual: "Casual", normal: "Normal (oficial)", hardcore: "Hardcore",
    pve: "PvE", pvp: "PvP", small_server: "Servidor pequeno",
    large_server: "Servidor grande", performance: "Performance",
    xp_rapido: "XP rápido", farm_rapido: "Farm rápido",
  };
  return mapa[nome] || nome;
}

$("#se-preset-sel").addEventListener("change", (ev) => {
  const idx = parseInt(ev.target.value, 10);
  ev.target.value = "";
  if (isNaN(idx)) return;
  const preset = (window.__se_presets || [])[idx];
  if (!preset) return;
  if (!preset.validas.length) { toast("Este preset não muda nada em relação ao atual.", "warn"); return; }
  abrirSeModal({
    titulo: `Preset: ${rotuloPreset(preset.nome)}`,
    texto: preset.descricao,
    linhas: preset.validas,
    nota: "As mudanças caem no formulário — você ainda revisa e salva depois.",
    okLabel: "Carregar no formulário",
    onOk: () => {
      preset.validas.forEach((v) => { seMudancas[v.chave] = v.para; seRenderItem(v.chave); });
      seAtualizarContagem();
      toast(`${preset.validas.length} mudança(s) carregadas. Revise e clique em Salvar.`);
    },
  });
});

/* ---- exportar ---- */
$("#se-export").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  try {
    const res = await fetch("/api/settings/export", { headers: { "X-Panel-Token": token } });
    if (res.status === 401) { showLogin("Sessão expirada — entre novamente."); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `config-palworld-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  } catch (e) { toast(`Falhou: ${e.message}`, "err"); }
  btn.disabled = false;
});

/* ---- importar ---- */
$("#se-import").addEventListener("click", () => $("#se-file-import").click());
$("#se-file-import").addEventListener("change", async (ev) => {
  const arquivo = ev.target.files[0];
  ev.target.value = "";
  if (!arquivo) return;
  let json;
  try { json = JSON.parse(await arquivo.text()); }
  catch (_) { return toast("Arquivo não é um JSON válido.", "err"); }
  try {
    const res = await api("settings/import", { method: "POST", body: json });
    if (!res.validas.length && !res.invalidas.length)
      return toast("Nenhuma configuração conhecida no arquivo.", "warn");
    abrirSeModal({
      titulo: "Importação validada",
      texto: res.invalidas.length
        ? "Alguns valores são inválidos e foram ignorados:"
        : "Valores prontos para entrar no formulário:",
      linhas: res.validas,
      nota: res.invalidas.length
        ? ("Inválidos ignorados: " + res.invalidas.map(i => `${i.chave} (${i.motivo})`).join(", "))
        : "",
      okLabel: "Carregar no formulário",
      onOk: () => {
        res.validas.forEach((v) => { seMudancas[v.chave] = v.para; seRenderItem(v.chave); });
        seAtualizarContagem();
      },
    });
  } catch (e) {
    toast(`Falhou: ${e.message}`, "err", 6000);
  }
});

/* ---- modo bruto ---- */
$("#se-raw").addEventListener("click", async () => {
  try {
    const res = await api("settings/raw");
    $("#se-raw-pre").textContent = res.texto || "(vazio)";
    $("#se-raw-backdrop").classList.remove("hidden");
  } catch (e) {
    toast(e.message, "warn", 5000);
  }
});
$("#se-raw-close").addEventListener("click", () => $("#se-raw-backdrop").classList.add("hidden"));
$("#se-raw-copy").addEventListener("click", async () => {
  if (await copiarTexto($("#se-raw-pre").textContent))
    toast("Texto copiado.");
});

/* ---- backups de config ---- */
async function loadSettingsBackups() {
  const tbody = $("#se-bk-tbody");
  try {
    const dados = await api("settings/backups");
    const lista = dados.backups || [];
    if (!lista.length) {
      tbody.innerHTML = `<tr><td colspan="4" class="muted center">Nenhum backup ainda.</td></tr>`;
      return;
    }
    tbody.innerHTML = lista.map((b) => `
      <tr>
        <td class="mono small">${esc(b.nome)}</td>
        <td class="mono">${fmtBytes(b.bytes)}</td>
        <td class="mono">${fmtDataHora(b.modificado)}</td>
        <td class="actions">
          <button class="btn danger tiny" data-restaurar="${esc(b.nome)}">Restaurar</button>
        </td>
      </tr>`).join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="4" class="center muted">${esc(e.message)}</td></tr>`;
  }
}
$("#se-bk-tbody").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-restaurar]");
  if (!btn) return;
  const nome = btn.dataset.restaurar;
  const ok = await askConfirm({
    title: `Restaurar ${nome}?`,
    text: "O PalWorldSettings.ini atual será substituído pelo conteúdo deste backup. Reinicie o servidor depois.",
    okLabel: "Restaurar",
  });
  if (!ok) return;
  btn.disabled = true;
  try {
    const res = await api("settings/backups/restore", { method: "POST", body: { nome } });
    toast(res.aviso || "Restaurado.", "ok", 8000);
    await loadSettings();
  } catch (e) {
    toast(`Falhou: ${e.message}`, "err", 6000);
    btn.disabled = false;
  }
});

/* ================= console em tempo real ================= */

const CS_MAX_LINHAS = 1000;
let csLinhas = [];            // eventos recebidos (todos)
let csPausado = false;
let csNivelFiltro = "";
let csBusca = "";
let csUltimoId = 0;
let csFonte = null;           // EventSource ativo
let csPollTimer = null;       // fallback de polling
let csPerdidasEnquantoPausado = 0;

function csCorresponde(ev) {
  if (csNivelFiltro && ev.level !== csNivelFiltro) return false;
  if (csBusca) {
    const alvo = `${ev.message} ${ev.kind}`.toLowerCase();
    if (!alvo.includes(csBusca)) return false;
  }
  return true;
}

function csRenderLinha(ev) {
  const el = document.createElement("div");
  el.className = `cs-linha lv-${ev.level}`;
  el.innerHTML =
    `<span class="cs-ts">${esc(fmtDataHora(ev.ts))}</span>` +
    `<span class="cs-kind">[${esc(ev.kind)}]</span>` +
    `<span>${esc(ev.message)}</span>`;
  return el;
}

function csReaplicarFiltros() {
  const caixa = $("#cs-log");
  caixa.innerHTML = "";
  let visiveis = 0;
  for (const ev of csLinhas) {
    if (!csCorresponde(ev)) continue;
    caixa.appendChild(csRenderLinha(ev));
    visiveis++;
  }
  csRolagem();
}

function csRolagem() {
  if (!$("#cs-autoscroll").checked) return;
  const caixa = $("#cs-log");
  caixa.scrollTop = caixa.scrollHeight;
}

function csAdicionar(ev) {
  if (ev.id <= csUltimoId) return;
  csUltimoId = ev.id;
  csLinhas.push(ev);
  while (csLinhas.length > CS_MAX_LINHAS) csLinhas.shift();
  if (csPausado) { csPerdidasEnquantoPausado++; return; }
  if (!csCorresponde(ev)) return;
  const caixa = $("#cs-log");
  caixa.appendChild(csRenderLinha(ev));
  while (caixa.children.length > CS_MAX_LINHAS) caixa.firstElementChild.remove();
  csRolagem();
}

async function csCarregarHistorico() {
  try {
    const dados = await api("events?limite=300");
    for (const ev of (dados.eventos || []).reverse()) csAdicionar(ev);
    csReaplicarFiltros();
  } catch (_) { /* sem histórico — segue */ }
}

function csIniciarSSE() {
  try { if (csFonte) csFonte.close(); } catch (_) {}
  clearInterval(csPollTimer);
  $("#cs-modo").textContent = "ao vivo";
  $("#cs-modo").className = "pill pill-on small";
  const fonte = new EventSource("/api/stream?token=" + encodeURIComponent(token));
  csFonte = fonte;
  fonte.onmessage = (m) => {
    try { csAdicionar(JSON.parse(m.data)); } catch (_) {}
  };
  fonte.onerror = () => {
    // SSE caiu (rede/servidor reiniciou) → polling até voltar
    fonte.close(); csFonte = null;
    $("#cs-modo").textContent = "polling";
    $("#cs-modo").className = "pill pill-warn small";
    csPollTimer = setInterval(async () => {
      try {
        const dados = await api("events?limite=100&desde_id=" + csUltimoId);
        for (const ev of dados.eventos || []) csAdicionar(ev);
      } catch (_) { /* segue tentando */ }
    }, 3000);
  };
}

$("#cs-pause").addEventListener("click", () => {
  csPausado = !csPausado;
  $("#cs-pause").textContent = csPausado ? "Retomar" : "Pausar";
  $("#cs-pause").classList.toggle("warn", csPausado);
  if (!csPausado && csPerdidasEnquantoPausado > 0) {
    toast(`${csPerdidasEnquantoPausado} linha(s) recebidas enquanto pausado.`);
    csPerdidasEnquantoPausado = 0;
  }
});
$("#cs-clear").addEventListener("click", () => { $("#cs-log").innerHTML = ""; });
$("#cs-copy").addEventListener("click", async () => {
  const texto = [...document.querySelectorAll("#cs-log .cs-linha")]
    .map((l) => l.textContent).join("\n");
  if (!texto) return toast("Console vazio.", "warn");
  if (await copiarTexto(texto)) toast("Console copiado.");
});
$("#cs-download").addEventListener("click", () => {
  const texto = [...document.querySelectorAll("#cs-log .cs-linha")]
    .map((l) => l.textContent).join("\n") || "";
  const blob = new Blob([texto], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `console-painel-${new Date().toISOString().slice(0,19).replace(/[:T]/g,"-")}.log`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
});
$("#cs-filtros").addEventListener("click", (ev) => {
  const chip = ev.target.closest(".chip[data-level]");
  if (!chip) return;
  csNivelFiltro = chip.dataset.level;
  document.querySelectorAll("#cs-filtros .chip").forEach((c) =>
    c.classList.toggle("active", c === chip));
  csReaplicarFiltros();
});
$("#cs-busca").addEventListener("input", () => {
  csBusca = $("#cs-busca").value.trim().toLowerCase();
  csReaplicarFiltros();
});

/* ================= saúde: diagnóstico + recuperação + notificações ================= */

const ROTULO_ESTADO = {
  healthy: ["healthy", "Tudo certo"],
  degraded: ["degraded", "Degradado"],
  critical: ["critical", "Crítico"],
  offline: ["offline", "Servidor offline"],
  maintenance: ["maintenance", "Manutenção"],
};

async function loadSaude() {
  let diag;
  try {
    diag = await api("health");
  } catch (e) {
    return toast(`Diagnóstico falhou: ${e.message}`, "err");
  }
  const [classe, rotulo] = ROTULO_ESTADO[diag.estado] || ["", diag.estado];
  const pill = $("#hs-estado");
  pill.textContent = rotulo;
  pill.className = `pill estado-${classe}`;

  $("#saude-lista").innerHTML = (diag.itens || []).map((i) => `
    <div class="saude-item saude-${i.estado}">
      <div>
        <b>${esc(i.mensagem)}</b>
        ${i.sugestao ? `<small class="muted">${esc(i.sugestao)}</small>` : ""}
      </div>
      <div class="actions">
        ${i.corrigivel
          ? `<button class="btn ghost small" data-corrigir="${i.acao.tipo}">Corrigir</button>`
          : ""}
      </div>
    </div>`).join("");
}

$("#hs-refresh").addEventListener("click", loadSaude);
$("#saude-lista").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-corrigir]");
  if (!btn) return;
  btn.disabled = true;
  try {
    const res = await api("health/corrigir",
                          { method: "POST", body: { acao: btn.dataset.corrigir } });
    toast(`✅ ${res.resultado}`, "ok", 7000);
    loadSaude();
  } catch (e) {
    toast(`Falhou: ${e.message}`, "err", 6000);
    btn.disabled = false;
  }
});

/* ---- auto-recuperação ---- */
async function loadRecuperacao() {
  try {
    const cfg = await api("recovery");
    $("#rec-habilitado").checked = !!cfg.habilitado;
    $("#rec-fps").value = cfg.fps_minimo;
    $("#rec-janela").value = cfg.janela_minutos;
    $("#rec-cooldown").value = cfg.cooldown_minutos;
    $("#rec-max").value = cfg.max_tentativas_dia;
  } catch (_) {}
}
$("#rec-salvar").addEventListener("click", async () => {
  try {
    await api("recovery", { method: "POST", body: {
      habilitado: $("#rec-habilitado").checked,
      fps_minimo: parseInt($("#rec-fps").value, 10),
      janela_minutos: parseInt($("#rec-janela").value, 10),
      cooldown_minutos: parseInt($("#rec-cooldown").value, 10),
      max_tentativas_dia: parseInt($("#rec-max").value, 10),
    }});
    toast("Configuração de recuperação salva.");
  } catch (e) { toast(e.message, "err"); }
});

/* ---- modo manutenção ---- */
async function loadManutencao() {
  try {
    const estado = await api("maintenance");
    $("#mnt-ativo").checked = !!estado.ativo;
    $("#mnt-motivo").value = estado.motivo || "";
  } catch (_) {}
}
$("#mnt-salvar").addEventListener("click", async () => {
  const ativo = $("#mnt-ativo").checked;
  if (ativo) {
    const ok = await askConfirm({
      title: "Ativar MODO MANUTENÇÃO?",
      text: "Todas as ações administrativas do painel ficarão bloqueadas "
          + "(restart, save, bans, backups, editor…).",
      okLabel: "Ativar",
    });
    if (!ok) return;
  }
  try {
    await api("maintenance", { method: "POST", body: {
      ativo, motivo: $("#mnt-motivo").value.trim() } });
    toast(ativo ? "🛠️ Modo manutenção ATIVADO."
                : "Modo manutenção desativado.", ativo ? "warn" : "ok");
  } catch (e) { toast(e.message, "err"); }
});

/* ---- notificações ---- */
let notifCanais = [];

function renderNotifCanais() {
  const alvo = $("#notif-canal-lista");
  if (!notifCanais.length) {
    alvo.innerHTML = `<p class="muted small">Nenhum canal configurado.</p>`;
    return;
  }
  alvo.innerHTML = notifCanais.map((c, i) => `
    <div class="notif-canal">
      <select data-nc="${i}" data-campo="tipo">
        <option value="discord" ${c.tipo === "discord" ? "selected" : ""}>Discord webhook</option>
        <option value="telegram" ${c.tipo === "telegram" ? "selected" : ""}>Telegram bot</option>
        <option value="webhook" ${c.tipo === "webhook" ? "selected" : ""}>Webhook genérico</option>
      </select>
      <input type="text" data-nc="${i}" data-campo="nome" placeholder="Nome do canal"
             value="${esc(c.nome || "")}">
      ${c.tipo === "telegram"
        ? `<input type="text" data-nc="${i}" data-campo="token" placeholder="Token do bot" value="${esc(c.token || "")}">
           <input type="text" data-nc="${i}" data-campo="chat_id" placeholder="Chat ID" value="${esc(c.chat_id || "")}">`
        : `<input type="password" data-nc="${i}" data-campo="webhook_url" placeholder="URL do webhook" value="${esc(c.webhook_url || "")}">`}
      <details class="se-bk-details">
        <summary class="muted small">Eventos (${(c.eventos || []).length || "todos"})</summary>
        <div class="chips">
          ${(notifEventosDisponiveis || []).map((e2) => `
            <label class="chk-inline"><input type="checkbox" data-nc-ev="${i}"
              data-ev="${e2}" ${(c.eventos || []).includes(e2) ? "checked" : ""}>
              ${e2}</label>`).join("")}
        </div>
      </details>
      <label class="chk-inline"><input type="checkbox" data-nc="${i}"
        data-campo="habilitado" data-tipo="bool" ${c.habilitado !== false ? "checked" : ""}>
        ativo</label>
      <button class="btn danger tiny" data-nc-del="${i}">Remover</button>
      <button class="btn ghost tiny" data-nc-teste="${i}">Testar</button>
    </div>`).join("");
}

let notifEventosDisponiveis = [];

async function loadNotificacoes() {
  try {
    const dados = await api("notifications");
    notifCanais = dados.canais || [];
    notifEventosDisponiveis = dados.eventos || [];
    renderNotifCanais();
  } catch (_) {}
}

$("#notif-add").addEventListener("click", () => {
  notifCanais.push({ tipo: "discord", nome: "", webhook_url: "",
                     eventos: [], habilitado: true,
                     id: "c" + Date.now() });
  renderNotifCanais();
});
$("#notif-canal-lista").addEventListener("click", async (ev) => {
  const delBtn = ev.target.closest("[data-nc-del]");
  const testeBtn = ev.target.closest("[data-nc-teste]");
  if (delBtn) {
    notifCanais.splice(parseInt(delBtn.dataset.ncDel, 10), 1);
    renderNotifCanais();
  }
  if (testeBtn) {
    // salva primeiro p/ testar com os valores atuais
    await salvarNotificacoes({ silencioso: true });
    const idx = parseInt(testeBtn.dataset.ncTeste, 10);
    testeBtn.disabled = true;
    try {
      const res = await api("notifications/teste",
        { method: "POST", body: { id: notifCanais[idx]?.id } });
      toast(res.ok ? "Mensagem de teste enviada!" : `Falhou: ${res.erro}`,
            res.ok ? "ok" : "err", 7000);
    } catch (e) { toast(e.message, "err"); }
    testeBtn.disabled = false;
  }
});
$("#notif-canal-lista").addEventListener("change", (ev) => {
  const alvo = ev.target.closest("[data-nc]");
  if (!alvo) return;
  const idx = parseInt(alvo.dataset.nc, 10);
  if (!notifCanais[idx]) return;
  if (alvo.dataset.ncEv) {
    const evento = alvo.dataset.ev;
    const lista = notifCanais[idx].eventos || (notifCanais[idx].eventos = []);
    if (alvo.checked && !lista.includes(evento)) lista.push(evento);
    if (!alvo.checked) notifCanais[idx].eventos =
      lista.filter((x) => x !== evento);
    return;
  }
  const campo = alvo.dataset.campo;
  notifCanais[idx][campo] = alvo.dataset.tipo === "bool"
    ? alvo.checked : alvo.value;
  if (campo === "tipo") renderNotifCanais();
});

async function salvarNotificacoes(opcoes = {}) {
  const silencioso = typeof opcoes === "object" && opcoes.silencioso;
  try {
    await api("notifications", { method: "POST", body: { canais: notifCanais } });
    if (!silencioso) toast("Notificações salvas.");
  } catch (e) {
    if (!silencioso) toast(`Falhou: ${e.message}`, "err");
    else throw e;
  }
}
$("#notif-salvar").addEventListener("click", () => salvarNotificacoes());

/* ================= mapa (game-data opcional) ================= */

async function loadMapa() {
  const aviso = $("#mp-aviso");
  const canvas = $("#mapa-canvas");
  try {
    const dados = await api("gamedata");
    if (!dados.disponivel) {
      aviso.textContent = "🗺️ " + (dados.motivo ||
        "Game Data indisponível neste servidor.");
      canvas.classList.add("hidden");
      $("#mapa-legenda").classList.add("hidden");
      return;
    }
    // extrai entidades com coordenadas numéricas sem inventar formato
    const brutas = [];
    const varrer = (obj, ehJogadorIds) => {
      if (!Array.isArray(obj)) return;
      for (const item of obj) {
        if (!item || typeof item !== "object") continue;
        const x = item.locationX ?? item.x ?? item.X;
        const z = item.locationZ ?? item.z ?? item.Z;
        if (typeof x === "number" && typeof z === "number") {
          brutas.push({ nome: item.name || item.playerName || "?",
                        x, z, jogador: ehJogadorIds });
        }
      }
    };
    const gd = dados.dados || {};
    varrer(gd.players || [], true);
    varrer(gd.characters || gd.pals || gd.actors || [], false);
    if (!brutas.length) {
      aviso.textContent = "A API respondeu, mas não há entidades com "
        + "coordenadas nesta resposta.";
      canvas.classList.add("hidden");
      return;
    }
    aviso.textContent = `${brutas.length} entidade(s) com coordenadas.`;
    desenharMapa(brutas);
  } catch (e) {
    aviso.textContent = "Mapa indisponível: " + e.message;
  }
}

function desenharMapa(entidades) {
  const cv = $("#mapa-canvas");
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 600, h = 320;
  cv.width = w * dpr; cv.height = h * dpr;
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const xs = entidades.map((e) => e.x), zs = entidades.map((e) => e.z);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minZ = Math.min(...zs), maxZ = Math.max(...zs);
  const spanX = Math.max(maxX - minX, 1), spanZ = Math.max(maxZ - minZ, 1);
  const pad = 30;
  ctx.fillStyle = "#0b0f14";
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = "#212b38";
  ctx.strokeRect(1, 1, w - 2, h - 2);

  for (const e of entidades) {
    const px = pad + ((e.x - minX) / spanX) * (w - pad * 2);
    const pz = h - pad - ((e.z - minZ) / spanZ) * (h - pad * 2);
    ctx.beginPath();
    ctx.arc(px, pz, e.jogador ? 6 : 3.5, 0, Math.PI * 2);
    ctx.fillStyle = e.jogador ? "#45c4ad" : "#d9a13f";
    ctx.fill();
    if (e.jogador) {
      ctx.strokeStyle = "#0a0d12"; ctx.lineWidth = 2; ctx.stroke();
      ctx.fillStyle = "#e9eef5";
      ctx.font = "11px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(e.nome.slice(0, 14), px, pz - 10);
    }
  }
  ctx.fillStyle = "#5d6d7d";
  ctx.font = "10px ui-monospace, monospace";
  ctx.textAlign = "left";
  ctx.fillText(`X ${minX}…${maxX}`, pad, h - 8);
  ctx.textAlign = "right";
  ctx.fillText(`Z ${minZ}…${maxZ}`, w - pad, h - 8);
  canvas.classList.remove("hidden");
  $("#mapa-legenda").classList.remove("hidden");
}
$("#mp-refresh").addEventListener("click", loadMapa);

/* ================= usuários & sessões (admin) ================= */

async function loadUsuarios() {
  const tbody = $("#usuarios-tbody");
  try {
    const dados = await api("usuarios");
    tbody.innerHTML = (dados.usuarios || []).map((u) => `
      <tr>
        <td class="cell-name">${esc(u.usuario)}</td>
        <td>
          <select data-usr="${u.id}" data-campo="papel"
                  ${u.papel === "admin" ? "disabled" : ""}>
            <option value="admin" ${u.papel === "admin" ? "selected" : ""}>Admin</option>
            <option value="moderador" ${u.papel === "moderador" ? "selected" : ""}>Moderador</option>
            <option value="viewer" ${u.papel === "viewer" ? "selected" : ""}>Viewer</option>
          </select>
        </td>
        <td>${u.ativo ? '<span class="pill pill-on">ativo</span>'
                       : '<span class="pill pill-ban">desativado</span>'}</td>
        <td class="mono small">${u.ultimo_login ? fmtDataHora(u.ultimo_login) : "—"}</td>
        <td class="actions">
          <button class="btn ghost tiny" data-usr-senha="${u.id}">Senha</button>
          ${u.papel === "admin" ? "" :
            `<button class="btn warn tiny" data-usr-toggle="${u.id}"
                     data-ativo="${u.ativo ? 1 : 0}">
               ${u.ativo ? "Desativar" : "Ativar"}
             </button>
             <button class="btn danger tiny" data-usr-del="${u.id}">Excluir</button>`}
        </td>
      </tr>`).join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" class="center muted">${esc(e.message)}</td></tr>`;
  }
}

async function loadSessoes() {
  const tbody = $("#sessoes-tbody");
  try {
    const dados = await api("sessoes");
    const lista = dados.sessoes || [];
    if (!lista.length) {
      tbody.innerHTML = `<tr><td colspan="4" class="muted center">Nenhuma sessão ativa.</td></tr>`;
      return;
    }
    tbody.innerHTML = lista.map((s) => `
      <tr>
        <td>${esc(s.usuario)} <small class="muted">(${esc(s.papel)})</small></td>
        <td class="mono small">${esc(s.token_curto)}</td>
        <td class="mono small">${fmtDataHora(s.criado)}</td>
        <td class="mono small">${fmtDataHora(s.expira)}</td>
      </tr>`).join("");
  } catch (_) {}
}

$("#usuarios-tbody").addEventListener("change", async (ev) => {
  const sel = ev.target.closest("[data-usr][data-campo='papel']");
  if (!sel) return;
  try {
    await api("usuarios/" + sel.dataset.usr,
              { method: "POST", body: { papel: sel.value } });
    toast(`Papel alterado para ${sel.value}.`);
  } catch (e) {
    toast(e.message, "err", 6000);
  }
  loadUsuarios();
});
$("#usuarios-tbody").addEventListener("click", async (ev) => {
  const senhaBtn = ev.target.closest("[data-usr-senha]");
  const toggleBtn = ev.target.closest("[data-usr-toggle]");
  const delBtn = ev.target.closest("[data-usr-del]");
  if (senhaBtn) {
    const nova = await askModal({
      title: "Nova senha",
      text: "Mínimo de 6 caracteres.",
      inputLabel: "Digite a nova senha:",
      okLabel: "Trocar",
    });
    if (nova === null || !nova) return;
    try {
      await api("usuarios/" + senhaBtn.dataset.usrSenha,
                { method: "POST", body: { senha: nova } });
      toast("Senha atualizada.");
    } catch (e) { toast(e.message, "err"); }
    return;
  }
  if (toggleBtn) {
    try {
      await api("usuarios/" + toggleBtn.dataset.usrToggle,
                { method: "POST",
                  body: { ativo: toggleBtn.dataset.ativo !== "1" } });
      loadUsuarios();
    } catch (e) { toast(e.message, "err"); }
    return;
  }
  if (delBtn) {
    const ok = await askConfirm({
      title: `Excluir usuário #${delBtn.dataset.usrDel}?`,
      text: "As sessões dele serão revogadas junto.",
      okLabel: "Excluir",
    });
    if (!ok) return;
    try {
      await fetch("/api/usuarios/" + delBtn.dataset.usrDel, {
        method: "DELETE", headers: { "X-Panel-Token": token } });
      loadUsuarios();
    } catch (e) { toast(e.message, "err"); }
  }
});

$("#usr-add").addEventListener("click", async () => {
  const nome = await askModal({
    title: "Novo usuário",
    text: "",
    inputLabel: "Nome de usuário (mín. 3):",
    okLabel: "Continuar",
  });
  if (nome === null || !nome) return;
  const papel = await askModal({
    title: `Papel de "${nome}"`,
    inputLabel: "admin, moderador ou viewer:",
    okLabel: "Criar usuário",
  }) || "";
  const senha = await askModal({
    title: "Senha inicial",
    inputLabel: "Senha (mín. 6):",
    okLabel: "Criar",
  });
  if (senha === null || !senha || !["admin","moderador","viewer"].includes(papel.trim().toLowerCase())) {
    if (senha !== null && papel !== null)
      toast("Criação cancelada — papel inválido ou vazio.", "warn");
    return;
  }
  try {
    await api("usuarios", { method: "POST",
      body: { usuario: nome, password: senha,
              papel: papel.trim().toLowerCase() } });
    toast(`Usuário ${nome} criado.`);
    loadUsuarios();
  } catch (e) { toast(e.message, "err", 6000); }
});

$("#sess-revogar-todas").addEventListener("click", async () => {
  const ok = await askConfirm({
    title: "Revogar TODAS as sessões?",
    text: "Todos os logins (inclusive o seu) serão deslogados.",
    okLabel: "Revogar todas",
  });
  if (!ok) return;
  await salvarNotificacoes({ silencioso: true }).catch(() => {});
  try {
    await api("sessoes/revogar", { method: "POST", body: { todos: true } });
    showLogin("Todas as sessões foram revogadas — entre novamente.");
  } catch (e) { toast(e.message, "err"); }
});

/* PWA: registra o service worker (silencioso onde não suportado/http LAN) */
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

/* ================= busca global (Ctrl+K) ================= */

const COMANDOS = [
  { rotulo: "Ir para: Visão geral", aba: "overview" },
  { rotulo: "Ir para: Jogadores", aba: "players" },
  { rotulo: "Ir para: Controle", aba: "control", perm: ["admin", "moderador"] },
  { rotulo: "Ir para: Console", aba: "console" },
  { rotulo: "Ir para: Backups", aba: "backups", perm: ["admin", "moderador"] },
  { rotulo: "Ir para: Agenda", aba: "agenda", perm: ["admin", "moderador"] },
  { rotulo: "Ir para: Configurações", aba: "settings", perm: ["admin", "moderador"] },
  { rotulo: "Ir para: Saúde e diagnóstico", aba: "saude", perm: ["admin", "moderador"] },
  { rotulo: "Ação: Salvar mundo agora", exec: () => api("save", { method: "POST" }).then(() => toast("Mundo salvo! 💾")), perm: ["admin", "moderador"] },
  { rotulo: "Ação: Criar backup do mundo", exec: () => api("backups/criar", { method: "POST" }).then((r) => toast(`Backup criado: ${r.nome}`)), perm: ["admin", "moderador"] },
  { rotulo: "Ação: Rodar diagnóstico de saúde", exec: () => { ativarAba(document.querySelector('[data-tab="saude"]')); loadSaude(); }, perm: ["admin", "moderador"] },
  { rotulo: "Ação: Atualizar jogadores", exec: () => { ativarAba(document.querySelector('[data-tab="players"]')); } },
];

function cmdkAbrir() {
  $("#cmdk-backdrop").classList.remove("hidden");
  const inp = $("#cmdk-input");
  inp.value = "";
  cmdkRender("");
  inp.focus();
}
function cmdkFechar() { $("#cmdk-backdrop").classList.add("hidden"); }

let cmdkSelecionado = 0;

function cmdkRender(filtro) {
  const papel = usuarioAtual?.papel || "admin";
  const termo = filtro.trim().toLowerCase();
  const lista = COMANDOS.filter((c) =>
    (!c.perm || c.perm.includes(papel)) &&
    (!termo || c.rotulo.toLowerCase().includes(termo)));
  cmdkSelecionado = 0;
  const ul = $("#cmdk-resultados");
  ul.innerHTML = lista.map((c, i) =>
    `<li class="${i === 0 ? "ativo" : ""}" data-i="${i}">${esc(c.rotulo)}</li>`).join("")
    || `<li class="muted">Nada encontrado.</li>`;
  ul.dataset.total = lista.length;
  ul._lista = lista;
}

function cmdkExecutar() {
  const lista = $("#cmdk-resultados")._lista || [];
  const item = lista[cmdkSelecionado];
  if (!item) return;
  cmdkFechar();
  if (item.aba) {
    const btn = document.querySelector(`[data-tab="${item.aba}"]:not([disabled])`);
    if (btn) ativarAba(btn);
    else toast("Seção indisponível para seu papel.", "warn");
    return;
  }
  if (item.exec) item.exec();
}

document.addEventListener("keydown", (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
    ev.preventDefault();
    const aberto = !$("#cmdk-backdrop").classList.contains("hidden");
    aberto ? cmdkFechar() : cmdkAbrir();
    return;
  }
  if ($("#cmdk-backdrop").classList.contains("hidden")) return;
  const itens = $("#cmdk-resultados")._lista || [];
  if (ev.key === "Escape") cmdkFechar();
  else if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
    ev.preventDefault();
    if (!itens.length) return;
    cmdkSelecionado = (cmdkSelecionado + (ev.key === "ArrowDown" ? 1 : -1)
                       + itens.length) % itens.length;
    [...$("#cmdk-resultados").children].forEach((li, i) =>
      li.classList.toggle("ativo", i === cmdkSelecionado));
  } else if (ev.key === "Enter") {
    ev.preventDefault();
    cmdkExecutar();
  }
});
$("#cmdk-input").addEventListener("input", (ev) => cmdkRender(ev.target.value));
$("#cmdk-backdrop").addEventListener("click", (ev) => {
  if (ev.target.id === "cmdk-backdrop") cmdkFechar();
});
$("#cmdk-resultados").addEventListener("click", (ev) => {
  const li = ev.target.closest("li[data-i]");
  if (li) { cmdkSelecionado = parseInt(li.dataset.i, 10); cmdkExecutar(); }
});

/* ================= boot ================= */

(async function boot() {
  if (!token) return showLogin();
  // valida a sessão guardada
  try {
    await api("status");
    showApp();
  } catch (e) {
    if (e.message !== "não autenticado") {
      // painel respondeu mas deu outro erro — sessão vale, mostra app mesmo assim
      showApp();
    }
  }
})();
