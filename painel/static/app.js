/* Painel Palworld — lógica do frontend (vanilla JS, sem dependências) */
"use strict";

const $ = (sel) => document.querySelector(sel);

let token = localStorage.getItem("pp_token") || "";
let pollTimer = null;
let lastPlayers = [];
let lastStatus = null;
let fpsHistory = [];

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
  loadPlayers();
  if ($("#tab-settings").classList.contains("active")) loadSettings();
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
      body: { password: $("#login-pass").value },
    });
    token = data.token;
    localStorage.setItem("pp_token", token);
    toast("Bem-vindo de volta! 🐑");
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

  if (btn.dataset.tab === "players") loadPlayers();
  if (btn.dataset.tab === "settings") loadSettings();
  if (btn.dataset.tab === "backups") loadBackups();
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

  const fps = pick(met, "serverfps", "serverFPS");
  if (fps != null) {
    fpsHistory.push(Number(fps));
    if (fpsHistory.length > 60) fpsHistory.shift();
  }
  drawSpark();
  renderWorldCard(info, met);
}

/* ---------- sparkline de FPS ---------- */
function drawSpark() {
  const cv = $("#fps-spark");
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || cv.parentElement.clientWidth;
  const h = 72;
  if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  $("#spark-cur").textContent = fpsHistory.length ? fpsHistory[fpsHistory.length - 1] : "—";
  if (fpsHistory.length < 2) {
    $("#spark-min").textContent = "—";
    $("#spark-max").textContent = "—";
    return;
  }

  const min = Math.min(...fpsHistory) - 3;
  const max = Math.max(...fpsHistory) + 3;
  const span = Math.max(max - min, 1);
  const x = (i) => (i / (fpsHistory.length - 1)) * (w - 2) + 1;
  const y = (v) => h - 4 - ((v - min) / span) * (h - 10);

  // preenchimento
  ctx.beginPath();
  ctx.moveTo(x(0), y(fpsHistory[0]));
  fpsHistory.forEach((v, i) => ctx.lineTo(x(i), y(v)));
  ctx.lineTo(x(fpsHistory.length - 1), h);
  ctx.lineTo(x(0), h);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "rgba(69,196,173,.28)");
  grad.addColorStop(1, "rgba(69,196,173,0)");
  ctx.fillStyle = grad;
  ctx.fill();

  // linha
  ctx.beginPath();
  ctx.moveTo(x(0), y(fpsHistory[0]));
  fpsHistory.forEach((v, i) => ctx.lineTo(x(i), y(v)));
  ctx.strokeStyle = "#45c4ad";
  ctx.lineWidth = 1.8;
  ctx.lineJoin = "round";
  ctx.stroke();

  $("#spark-min").textContent = Math.round(Math.min(...fpsHistory));
  $("#spark-max").textContent = Math.round(Math.max(...fpsHistory));
}

/* ---------- mundo & servidor ---------- */
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

function renderWorldCard(info, met) {
  const dias = pick(met, "days");
  const bases = pick(met, "basecampnum", "baseCampCount");
  $("#wd-days").textContent = dias != null ? `dia ${dias}` : "—";
  $("#wd-bases").textContent = bases != null ? bases : "—";
  $("#wd-ver").textContent = pick(info, "version") || "—";
  const up = pick(met, "uptime", "uptimeseconds", "uptimeSeconds");
  $("#wd-uptime").textContent = up != null ? fmtUptimeLong(up) : "—";
}

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

async function loadSettings() {
  const tbody = $("#settings-tbody");
  tbody.innerHTML = skelRow(2, [38, 60]) + skelRow(2, [45, 52]) +
                    skelRow(2, [30, 66]) + skelRow(2, [42, 48]) +
                    skelRow(2, [36, 58]) + skelRow(2, [48, 44]);
  try {
    const s = await api("settings");
    const entries = Object.entries(s || {}).sort((a, b) => String(a[0]).localeCompare(String(b[0])));
    if (!entries.length) {
      tbody.innerHTML = `<tr><td colspan="2" class="muted center">Sem dados.</td></tr>`;
      return;
    }
    tbody.innerHTML = entries.map(([k, v]) => {
      const val = typeof v === "object" ? JSON.stringify(v) : String(v);
      return `<tr><td><code>${esc(k)}</code></td>` +
             `<td title="Clique para copiar" data-copy="${esc(val)}">${esc(val)}</td></tr>`;
    }).join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="2" class="center" style="color:var(--red)">${esc(e.message)}</td></tr>`;
  }
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

$("#settings-tbody").addEventListener("click", async (ev) => {
  const td = ev.target.closest("td[data-copy]");
  if (!td) return;
  const tr = td.parentElement;
  try {
    if (await copiarTexto(td.dataset.copy)) {
      tr.classList.add("copied");
      setTimeout(() => tr.classList.remove("copied"), 1200);
    } else {
      toast("Não deu pra copiar — selecione o valor manualmente.", "warn");
    }
  } catch (_) {
    toast("Não deu pra copiar — selecione o valor manualmente.", "warn");
  }
});

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
  if ($("#app-view") && !$("#app-view").classList.contains("hidden")) drawSpark();
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

  const motivo = await askModal({
    title: act === "kick" ? `Chutar ${name}?` : `Banir ${name}?`,
    text: act === "kick"
      ? "O jogador será desconectado e poderá voltar depois."
      : "O jogador será banido PERMANENTEMENTE do servidor.",
    inputLabel: "Motivo (opcional):",
    okLabel: act === "kick" ? "Chutar" : "Banir mesmo assim",
  });
  if (motivo === null) return;

  btn.disabled = true;
  try {
    await api(act, { method: "POST", body: { userid, message: motivo } });
    toast(`${name} ${act === "kick" ? "chutado" : "banido"} com sucesso.`);
  } catch (e) {
    toast(`Falhou: ${e.message}`, "err", 6000);
  }
  btn.disabled = false;
  setTimeout(loadPlayers, 800);
});

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
