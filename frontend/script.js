let systemInfo = null;
let chatAbort = null;
let chatHistory = [];
let currentSessionId = null;
let ollamaPoll = null;
let appSettings = {};
let settingsLoaded = false;

function escHtml(s) {
  if (typeof s !== "string") return "";
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

function mdToHtml(text) {
  let html = escHtml(text);

  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
    const langClass = lang ? ` class="language-${lang}"` : "";
    return `<pre><code${langClass}>${code}</code></pre><button class="copy-btn" onclick="copyCode(this)">Copy</button>`;
  });

  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/```([\s\S]*?)```/g, "<pre><code>$1</code></pre>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  html = html.replace(/^- (.+)$/gm, "&bull; $1<br>");
  html = html.replace(/^(\d+)\. (.+)$/gm, "$1. $2<br>");
  html = html.replace(/\n/g, "<br>");

  return html;
}

function copyCode(btn) {
  const code = btn.previousElementSibling.textContent;
  navigator.clipboard.writeText(code).then(() => {
    btn.textContent = "Copied!";
    setTimeout(() => { btn.textContent = "Copy"; }, 2000);
  });
}

function toast(msg, type) {
  const container = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = `toast ${type || "info"}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 300); }, 3500);
}

function openModal(title, bodyHtml) {
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-body").innerHTML = bodyHtml;
  document.getElementById("modal-overlay").classList.remove("hidden");
}

function closeModal() {
  document.getElementById("modal-overlay").classList.add("hidden");
}

function getRecsSortFn(key) {
  const fns = {
    score: (a, b) => b.score - a.score,
    vram: (a, b) => a.vram_gb - b.vram_gb,
    context: (a, b) => b.context - a.context,
    name: (a, b) => a.name.localeCompare(b.name),
  };
  return fns[key] || fns.score;
}

document.addEventListener("DOMContentLoaded", () => {
  initNav();
  checkOllama();
  loadDashboard();
  checkFirstRun();
  checkForUpdates();
  loadSettings();
  ollamaPoll = setInterval(checkOllama, 10000);
  setInterval(loadRunningModels, 15000);

  document.getElementById("chat-input").addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendChat(); }
  });
  document.getElementById("chat-model").addEventListener("change", selectChatModel);
  document.getElementById("chat-session").addEventListener("change", switchChatSession);
  document.getElementById("sort-recs").addEventListener("change", sortRecommendations);
  document.getElementById("model-search").addEventListener("input", filterInstalledModels);
  document.getElementById("sessions-search").addEventListener("input", filterSessions);
  document.getElementById("browse-search").addEventListener("input", debounce(loadBrowse, 300));
  document.getElementById("browse-capability").addEventListener("change", loadBrowse);
  document.getElementById("browse-sort").addEventListener("change", loadBrowse);
  document.getElementById("browse-compat").addEventListener("change", loadBrowse);

  document.addEventListener("keydown", e => {
    if (e.key === "Escape") closeModal();
  });

  document.addEventListener("click", e => {
    const pullBtn = e.target.closest("[data-pull]");
    if (pullBtn) { pullModel(decodeURIComponent(pullBtn.dataset.pull)); return; }
    const detailBtn = e.target.closest("[data-details]");
    if (detailBtn) { showDetails(detailBtn.dataset.details); return; }
    const runBtn = e.target.closest("[data-run]");
    if (runBtn) { runModel(decodeURIComponent(runBtn.dataset.run)); return; }
    const delBtn = e.target.closest("[data-delete]");
    if (delBtn) { deleteModel(decodeURIComponent(delBtn.dataset.delete)); return; }
    const copyBtn = e.target.closest("[data-copy]");
    if (copyBtn) { copyText(copyBtn.dataset.copy); return; }
    const pageBtn = e.target.closest("[data-page-btn]");
    if (pageBtn) { browsePage(parseInt(pageBtn.dataset.pageBtn)); return; }
    const browseDetailBtn = e.target.closest("[data-browse-details]");
    if (browseDetailBtn) { showBrowseDetails(decodeURIComponent(browseDetailBtn.dataset.browseDetails)); return; }
    const browseInfoBtn = e.target.closest("[data-browse-info]");
    if (browseInfoBtn) { showBrowseInfo(browseInfoBtn.dataset.browseInfo); return; }
    const sessionBtn = e.target.closest("[data-session-action]");
    if (sessionBtn) { handleSessionAction(sessionBtn); return; }
    const sessionLoad = e.target.closest("[data-session-load]");
    if (sessionLoad) { loadSessionIntoChat(sessionLoad.dataset.sessionLoad); return; }

    if (e.target.id === "modal-overlay" || e.target.closest("#modal-close")) closeModal();
    if (e.target.id === "btn-scan") runScan();
    if (e.target.id === "btn-recommend") runScanAndRecommend();
    if (e.target.id === "btn-refresh-models") { loadInstalledModels(); loadRunningModels(); }
    if (e.target.id === "btn-refresh-sessions") loadSessions();
    if (e.target.id === "btn-refresh-chat-models") loadChatModels();
    if (e.target.id === "btn-clear-chat") clearChat();
    if (e.target.id === "btn-save-session") saveCurrentSession();
    if (e.target.id === "btn-launch-cli") launchCliForModel();
    if (e.target.id === "btn-toggle-params") {
      const panel = document.getElementById("chat-params");
      panel.style.display = panel.style.display === "none" ? "flex" : "none";
    }
    if (e.target.id === "chat-stop") stopChat();
    if (e.target.id === "chat-send") sendChat();
    if (e.target.id === "param-temp") document.getElementById("param-temp-val").textContent = e.target.value;
    if (e.target.id === "param-topp") document.getElementById("param-topp-val").textContent = e.target.value;
    if (e.target.id === "param-topk") document.getElementById("param-topk-val").textContent = e.target.value;
    if (e.target.id === "param-repp") document.getElementById("param-repp-val").textContent = e.target.value;
    if (e.target.id === "btn-export-csv") exportRecsCSV();
    if (e.target.id === "sidebar-toggle") toggleSidebar();
    if (e.target.id === "dash-scan") { navigateTo("scan"); runScanAndRecommend(); }
    if (e.target.id === "dash-browse") navigateTo("browse");
    if (e.target.id === "dash-chat") navigateTo("chat");
    if (e.target.id === "btn-manual-install" || e.target.id === "btn-manual-install-dl") {
      const input = document.getElementById(e.target.id === "btn-manual-install" ? "manual-model-input" : "manual-model-input-dl");
      const name = input.value.trim();
      if (name) { pullModel(name); input.value = ""; }
    }
    if (e.target.id === "btn-save-settings") saveSettingsUi();
    if (e.target.id === "btn-export-all") exportAllSessions();
    if (e.target.id === "btn-import-session") importSessionFile();
  });
});

function toggleSidebar() {
  document.getElementById("sidebar").classList.toggle("open");
}

function navigateTo(page) {
  document.querySelectorAll(".sidebar nav a").forEach(x => x.classList.remove("active"));
  const link = document.querySelector(`.sidebar nav a[data-page="${page}"]`);
  if (link) link.classList.add("active");
  document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
  const el = document.getElementById(`page-${page}`);
  if (el) el.classList.add("active");
  document.getElementById("sidebar").classList.remove("open");
  if (page === "models") { loadInstalledModels(); loadRunningModels(); }
  if (page === "dashboard") loadDashboard();
  if (page === "chat") { loadChatModels(); loadSessionList(); }
  if (page === "browse") loadBrowse();
  if (page === "sessions") loadSessions();
  if (page === "settings") loadSettingsUi();
}

function initNav() {
  document.querySelectorAll(".sidebar nav a").forEach(a => {
    a.addEventListener("click", e => {
      e.preventDefault();
      navigateTo(a.dataset.page);
    });
  });
}

async function api(method, path, body) {
  const opts = { method, headers: { "Accept": "application/json" } };
  if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  return r.json();
}

async function checkOllama() {
  const badge = document.getElementById("ollama-badge");
  try {
    const r = await api("GET", "/api/ollama/status");
    if (r.running) {
      badge.className = "badge-online";
      badge.textContent = `Ollama: ${r.version || "running"}`;
    } else {
      badge.className = "badge-offline";
      badge.textContent = "Ollama: not running";
    }
  } catch {
    badge.className = "badge-offline";
    badge.textContent = "Ollama: unreachable";
  }
}

async function checkFirstRun() {
  try {
    const r = await api("GET", "/api/ollama/check-install-detailed");
    if (!r.installed) {
      const dlPage = document.getElementById("page-downloads");
      dlPage.innerHTML = `
        <h1>First-Run Setup</h1>
        <div class="result-box">
          <h3>Ollama Not Found</h3>
          <p>Model Hub requires <strong>Ollama</strong> to download and run models.</p>
          <p style="margin:16px 0">
            <a href="${escHtml(r.download_url)}" target="_blank" class="btn" style="text-decoration:none">Download Ollama</a>
          </p>
          <p style="font-size:0.85rem;color:var(--muted)">Install Ollama, then restart Model Hub. You can still browse recommendations without it.</p>
        </div>
        <h2>Downloads</h2>
        <div id="downloads-list"><em>No downloads yet.</em></div>
      `;
    }
  } catch {}
}

async function checkForUpdates() {
  try {
    const v = await api("GET", "/api/system/version");
    document.getElementById("version-badge").textContent = `v${v.version}`;
    const update = await api("GET", `/api/system/check-update?current=${v.version}`);
    if (update.update_available) {
      const badge = document.getElementById("ollama-badge");
      badge.innerHTML = `<a href="${escHtml(update.download_url)}" target="_blank" style="color:var(--accent);text-decoration:none">Update v${update.latest_version}</a>`;
    }
  } catch {
    document.getElementById("version-badge").textContent = "";
  }
}

async function loadDashboard() {
  try {
    const info = await api("GET", "/api/scan");
    systemInfo = info;
    renderSystemCard(info);
  } catch { document.getElementById("card-system").querySelector(".card-body").textContent = "Failed to scan"; }

  try {
    const r = await api("GET", "/api/ollama/status");
    const body = document.getElementById("card-ollama").querySelector(".card-body");
    body.innerHTML = r.running
      ? `<div><span class="badge badge-gpu">Running</span></div><div style="margin-top:4px">version ${escHtml(r.version || "?")}</div>`
      : '<span class="badge badge-offload">Not running</span>';
  } catch { document.getElementById("card-ollama").querySelector(".card-body").textContent = "Error checking"; }

  try {
    const models = await api("GET", "/api/ollama/models");
    const card = document.getElementById("card-models").querySelector(".card-body");
    if (models.length === 0) { card.textContent = "No models installed"; }
    else {
      const total = models.length;
      const totalGb = models.reduce((s, m) => s + m.size_gb, 0);
      card.innerHTML = `<div><strong>${total}</strong> models</div><div style="margin-top:4px">${totalGb.toFixed(1)} GB total</div>`;
    }
  } catch { document.getElementById("card-models").querySelector(".card-body").textContent = "Ollama not running"; }

  try {
    const r = await api("GET", "/api/library/browse?sort=pulls");
    const libBody = document.getElementById("card-library").querySelector(".card-body");
    if (r.models) {
      libBody.innerHTML = `<div><strong>${r.models.length}</strong> model variants</div><div style="margin-top:4px">${escHtml(r.system_vram ? r.system_vram + " GB VRAM" : "No GPU data")}</div>`;
    } else {
      libBody.textContent = "Library not loaded";
    }
  } catch { document.getElementById("card-library").querySelector(".card-body").textContent = "Error loading"; }

  loadRunningModels();
  updateRunningCard();
}

function renderSystemCard(info) {
  const sys = document.getElementById("card-system").querySelector(".card-body");
  sys.innerHTML = `
    <div><span class="label">OS:</span> <span class="value">${escHtml(info.os)}</span></div>
    <div><span class="label">CPU:</span> <span class="value">${escHtml(info.cpu)}</span></div>
    <div><span class="label">Cores:</span> <span class="value">${info.cores}</span></div>
    <div><span class="label">RAM:</span> <span class="value">${info.ram_gb} GB</span></div>
  `;
  const gpu = document.getElementById("card-gpu").querySelector(".card-body");
  if (info.gpus && info.gpus.length) {
    gpu.innerHTML = info.gpus.map(g =>
      `<div><span class="value">${escHtml(g.name)}</span> &mdash; ${g.vram_gb} GB <span class="label">(${escHtml(g.backend)})</span></div>`
    ).join("");
    loadQuickPicks(info.total_vram_gb || info.gpus[0].vram_gb);
  } else {
    gpu.textContent = "No GPU detected";
  }
}

async function loadQuickPicks(vram) {
  const div = document.getElementById("quick-picks");
  if (!vram || vram <= 0) { div.innerHTML = '<span class="empty-state">Scan your hardware first.</span>'; return; }
  try {
    const r = await api("GET", `/api/recommend?vram=${vram}&use_case=coding&top_k=3`);
    const recs = r.recommendations || [];
    if (!recs.length) { div.innerHTML = '<span class="empty-state">No recommendations</span>'; return; }
    div.innerHTML = recs.map((m, i) => {
      const tag = encodeURIComponent(m.model_id);
      return `<div class="result-box" style="margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div><strong>${i+1}.</strong> ${escHtml(m.name)} <span class="badge badge-${m.run_mode === 'gpu' ? 'gpu' : 'offload'}">${escHtml(m.quant)}</span></div>
          <button class="btn btn-sm" data-pull="${tag}">Install</button>
        </div>
        <div style="font-size:0.8rem;color:var(--muted);margin-top:4px">
          ${m.vram_gb} GB VRAM &middot; ${m.context} ctx &middot; Score ${m.score}
        </div>
      </div>`;
    }).join("");
  } catch { div.innerHTML = '<span class="empty-state">Error loading picks</span>'; }
}

let lastRecs = [];

async function runScan() {
  const div = document.getElementById("scan-result");
  div.innerHTML = '<div class="result-box"><div class="spinner"></div> Scanning...</div>';
  const info = await api("GET", "/api/scan");
  systemInfo = info;
  let html = '<div class="result-box"><h3>System</h3><table>';
  html += `<tr><td>OS</td><td>${escHtml(info.os)}</td></tr>`;
  html += `<tr><td>CPU</td><td>${escHtml(info.cpu)}</td></tr>`;
  html += `<tr><td>Cores</td><td>${info.cores}</td></tr>`;
  html += `<tr><td>RAM</td><td>${info.ram_gb} GB</td></tr>`;
  if (info.gpus && info.gpus.length) {
    info.gpus.forEach(g => {
      html += `<tr><td>GPU</td><td>${escHtml(g.name)} &mdash; ${g.vram_gb} GB (${escHtml(g.backend)})</td></tr>`;
    });
  } else {
    html += `<tr><td>GPU</td><td>None detected</td></tr>`;
  }
  html += '</table></div>';
  div.innerHTML = html;
}

async function runScanAndRecommend() {
  document.getElementById("scan-result").innerHTML = '<div class="result-box"><div class="spinner"></div> Scanning...</div>';
  document.getElementById("recs-result").innerHTML = '';
  const vramOverride = parseFloat(document.getElementById("vram-override").value) || 0;
  const useCase = document.getElementById("use-case").value;
  const vram = vramOverride > 0 ? vramOverride : null;

  const info = await api("GET", "/api/scan");
  systemInfo = info;
  let html = '<div class="result-box"><h3>System</h3><table>';
  html += `<tr><td>OS</td><td>${escHtml(info.os)}</td></tr>`;
  html += `<tr><td>CPU</td><td>${escHtml(info.cpu)}</td></tr>`;
  html += `<tr><td>RAM</td><td>${info.ram_gb} GB</td></tr>`;
  if (info.gpus && info.gpus.length) {
    info.gpus.forEach(g => {
      html += `<tr><td>GPU</td><td>${escHtml(g.name)} &mdash; ${g.vram_gb} GB (${escHtml(g.backend)})</td></tr>`;
    });
  }
  html += '</table></div>';
  document.getElementById("scan-result").innerHTML = html;

  document.getElementById("recs-result").innerHTML = '<div class="result-box"><div class="spinner"></div> Generating recommendations...</div>';
  const effectiveVram = vram || info.total_vram_gb || (info.gpus && info.gpus.length ? info.gpus[0].vram_gb : 0);
  const r = await api("GET", `/api/recommend?vram=${effectiveVram}&use_case=${useCase}&top_k=50`);
  lastRecs = r.recommendations || [];

  if (!lastRecs.length) {
    document.getElementById("recs-result").innerHTML = '<div class="result-box"><span class="empty-state">No models fit your hardware. Try a lower VRAM override or different use case.</span></div>';
    return;
  }

  renderRecommendations();
}

function renderRecommendations() {
  const sortKey = document.getElementById("sort-recs").value;
  const sorted = [...lastRecs].sort(getRecsSortFn(sortKey));

  let rh = `<div class="result-box"><h3>Recommended Models (${lastRecs.length} found)</h3><table>`;
  rh += `<tr><th>#</th><th>Model</th><th>Quant</th><th>Score</th><th>VRAM</th><th>Context</th><th>Mode</th><th>Actions</th></tr>`;
  sorted.forEach((m, i) => {
    const modeClass = m.run_mode === "gpu" ? "badge-gpu" : "badge-offload";
    const modeLabel = m.run_mode === "gpu" ? "GPU" : "Offload";
    const tag = encodeURIComponent(m.model_id);
    const details = JSON.stringify(m).replace(/"/g, "&quot;");
    rh += `<tr>
      <td>${i+1}</td>
      <td><strong>${escHtml(m.name)}</strong></td>
      <td><span class="badge badge-gpu">${escHtml(m.quant)}</span></td>
      <td>${m.score}</td>
      <td>${m.vram_gb}</td>
      <td>${m.context}</td>
      <td><span class="badge ${modeClass}">${modeLabel}</span></td>
      <td class="model-actions">
        <button class="btn btn-sm" data-pull="${tag}">Install</button>
        <button class="btn btn-sm btn-secondary" data-details="${details}">Details</button>
      </td>
    </tr>`;
  });
  rh += '</table></div>';
  document.getElementById("recs-result").innerHTML = rh;
}

function sortRecommendations() {
  if (lastRecs.length) renderRecommendations();
}

function exportRecsCSV() {
  if (!lastRecs.length) { toast("No recommendations to export.", "error"); return; }
  const headers = ["Name", "Quant", "Score", "VRAM GB", "Context", "Mode", "Provider", "Params B"];
  const rows = lastRecs.map(m => [
    m.name, m.quant, m.score, m.vram_gb, m.context, m.run_mode, m.provider || "", m.params_b
  ]);
  const csv = [headers.join(","), ...rows.map(r => r.join(","))].join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "model-recommendations.csv";
  a.click();
  URL.revokeObjectURL(a.href);
  toast("Exported recommendations as CSV.", "success");
}

function showDetails(raw) {
  const m = JSON.parse(raw);
  const bodyHtml = `
    <table>
      <tr><td>Provider</td><td>${escHtml(m.provider || "?")}</td></tr>
      <tr><td>Parameters</td><td>${m.params_b}B</td></tr>
      <tr><td>Quantization</td><td>${escHtml(m.quant)}</td></tr>
      <tr><td>VRAM needed</td><td>${m.vram_gb} GB</td></tr>
      <tr><td>Context window</td><td>${m.context} tokens</td></tr>
      <tr><td>Run mode</td><td>${escHtml(m.run_mode)}</td></tr>
      <tr><td>Quality score</td><td>${m.scores.quality}</td></tr>
      <tr><td>Speed score</td><td>${m.scores.speed}</td></tr>
      <tr><td>Fit score</td><td>${m.scores.fit}</td></tr>
      <tr><td>Context score</td><td>${m.scores.context}</td></tr>
      <tr><td>Ollama command</td><td><code>${escHtml(m.ollama_cmd)}</code></td></tr>
    </table>
    <div style="margin-top:12px"><button class="btn btn-sm btn-secondary" data-copy="${escHtml(m.ollama_cmd)}">Copy Command</button></div>
  `;
  openModal(escHtml(m.name), bodyHtml);
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => toast("Copied to clipboard!", "success")).catch(() => {});
}

async function pullModel(modelName) {
  if (!modelName) return;
  const dlDiv = document.getElementById("downloads-list");
  const id = `dl-${Date.now()}`;
  const el = document.createElement("div");
  el.className = "download-item";
  el.id = id;
  el.innerHTML = `<div class="title">Installing ${escHtml(modelName)}...</div>
    <div class="progress-bar"><div class="progress-fill" id="${id}-progress"></div></div>
    <div class="status" id="${id}-status">Starting download...</div>`;
  dlDiv.prepend(el);
  document.querySelector('[data-page="downloads"]').click();

  try {
    const resp = await fetch("/api/ollama/pull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: modelName }),
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const s = line.replace(/^data: /, "").trim();
        if (s === "[DONE]" || !s) continue;
        try {
          const p = JSON.parse(s);
          const progress = document.getElementById(`${id}-progress`);
          const status = document.getElementById(`${id}-status`);
          const title = el.querySelector(".title");
          if (p.error) {
            status.textContent = `Error: ${p.error}`;
            status.className = "status error";
            break;
          }
          if (p.status) status.textContent = p.status;
          if (p.completed && p.total) {
            const pct = Math.round((p.completed / p.total) * 100);
            if (progress) progress.style.width = `${Math.min(pct, 100)}%`;
            status.textContent = `${p.status || "Downloading..."} (${pct}%)`;
          }
          if (p.status === "success") {
            if (progress) progress.style.width = "100%";
            status.textContent = "Installed successfully!";
            status.className = "status done";
            if (title) title.textContent = `${modelName} &mdash; Installed`;
            toast(`Installed ${modelName}`, "success");
          }
        } catch {}
      }
    }
  } catch (err) {
    const status = document.getElementById(`${id}-status`);
    if (status) { status.textContent = `Failed: ${err.message}`; status.className = "status error"; }
    toast(`Download failed: ${err.message}`, "error");
  }
}

function filterInstalledModels() {
  const query = document.getElementById("model-search").value.toLowerCase();
  document.querySelectorAll("#installed-models table tr").forEach((tr, i) => {
    if (i === 0) return;
    tr.style.display = tr.textContent.toLowerCase().includes(query) ? "" : "none";
  });
}

async function loadInstalledModels() {
  const div = document.getElementById("installed-models");
  div.innerHTML = '<div class="spinner"></div>';
  try {
    const models = await api("GET", "/api/ollama/models");
    if (!models.length) {
      div.innerHTML = '<div class="result-box"><span class="empty-state">No models installed.</span><p style="margin-top:12px">Go to <strong>Scan &amp; Recommend</strong> to find and install models.</p></div>';
      return;
    }
    let html = '<div class="result-box"><table><tr><th>Model</th><th>Size</th><th>Modified</th><th>Actions</th></tr>';
    models.forEach(m => {
      const safeName = escHtml(m.name);
      html += `<tr>
        <td><strong>${safeName}</strong></td>
        <td>${m.size_gb} GB</td>
        <td>${new Date(m.modified).toLocaleDateString()}</td>
        <td class="model-actions">
          <button class="btn btn-sm btn-secondary" data-run="${encodeURIComponent(m.name)}">Run</button>
          <button class="btn btn-sm btn-danger" data-delete="${encodeURIComponent(m.name)}">Delete</button>
        </td>
      </tr>`;
    });
    html += '</table></div>';
    div.innerHTML = html;
    document.getElementById("model-search").value = "";
  } catch {
    div.innerHTML = '<div class="result-box"><span class="empty-state">Could not connect to Ollama.</span><p style="margin-top:12px">Make sure <a href="https://ollama.com/download" target="_blank" style="color:var(--accent)">Ollama</a> is installed and running.</p></div>';
  }
}

async function deleteModel(name) {
  if (!confirm(`Delete ${name}?`)) return;
  await api("POST", "/api/ollama/delete", { model: name });
  loadInstalledModels();
  toast(`Deleted ${name}`, "info");
}

function runModel(name) {
  const cmd = `ollama run ${name}`;
  copyText(cmd);
  toast(`Copied "${cmd}" to clipboard. Paste in your terminal to run!`, "info");
}

async function loadChatModels() {
  const sel = document.getElementById("chat-model");
  try {
    const models = await api("GET", "/api/ollama/models");
    sel.innerHTML = '<option value="">&mdash; Select a model &mdash;</option>';
    models.forEach(m => {
      const opt = document.createElement("option");
      opt.value = m.name;
      opt.textContent = `${m.name} (${m.size_gb} GB)`;
      sel.appendChild(opt);
    });
  } catch {
    sel.innerHTML = '<option value="">Ollama not running</option>';
  }
}

function selectChatModel() {
  const sel = document.getElementById("chat-model");
  const hasModel = sel.value !== "";
  document.getElementById("chat-input").disabled = !hasModel;
  document.getElementById("chat-send").disabled = !hasModel;
  if (hasModel) document.getElementById("chat-input").focus();
}

function clearChat() {
  if (!chatHistory.length && !document.getElementById("chat-box").querySelector(".chat-msg")) return;
  if (!confirm("Clear the chat history?")) return;
  chatHistory = [];
  currentSessionId = null;
  document.getElementById("chat-session").value = "";
  document.getElementById("chat-box").innerHTML = '<div class="chat-welcome">Select a model above and start chatting.</div>';
  toast("Chat cleared.", "info");
}

function getChatOptions() {
  return {
    options: {
      temperature: parseFloat(document.getElementById("param-temp").value),
      top_p: parseFloat(document.getElementById("param-topp").value),
      top_k: parseInt(document.getElementById("param-topk").value),
      repeat_penalty: parseFloat(document.getElementById("param-repp").value),
      num_predict: parseInt(document.getElementById("param-maxtokens").value),
    }
  };
}

async function sendChat() {
  const model = document.getElementById("chat-model").value;
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if (!model || !text) return;

  if (!currentSessionId && settingsLoaded && appSettings.auto_save_sessions !== false) {
    const sess = await api("POST", "/api/sessions", {
      name: `${model} - ${new Date().toLocaleString()}`,
      model,
    });
    currentSessionId = sess.id;
  }

  const box = document.getElementById("chat-box");
  input.value = "";
  chatHistory.push({ role: "user", content: text });
  appendChatMessage("user", text);

  if (currentSessionId) {
    await api("POST", `/api/sessions/${currentSessionId}/messages`, { role: "user", content: text });
  }

  const msgDiv = appendChatMessage("assistant", "Thinking...", true);

  const sendBtn = document.getElementById("chat-send");
  const stopBtn = document.getElementById("chat-stop");
  sendBtn.style.display = "none";
  stopBtn.style.display = "inline-flex";

  const options = getChatOptions();
  const body = { model, messages: chatHistory, ...options };

  try {
    const resp = await fetch("/api/ollama/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!resp.ok) {
      const errText = await resp.text();
      msgDiv.querySelector(".bubble").textContent = `Error: ${resp.status} ${errText}`;
      msgDiv.classList.remove("thinking");
      sendBtn.style.display = "inline-flex";
      stopBtn.style.display = "none";
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullContent = "";

    chatAbort = reader;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const s = line.replace(/^data: /, "").trim();
        if (s === "[DONE]" || !s) continue;
        try {
          const p = JSON.parse(s);
          if (p.error) {
            msgDiv.querySelector(".bubble").textContent = `Error: ${p.error}`;
            msgDiv.classList.remove("thinking");
            continue;
          }
          if (p.message && p.message.content) {
            fullContent += p.message.content;
            msgDiv.querySelector(".bubble").innerHTML = mdToHtml(fullContent);
            box.scrollTop = box.scrollHeight;
          }
        } catch {}
      }
    }

    msgDiv.classList.remove("thinking");
    chatHistory.push({ role: "assistant", content: fullContent });
    if (currentSessionId && fullContent) {
      await api("POST", `/api/sessions/${currentSessionId}/messages`, { role: "assistant", content: fullContent });
    }

  } catch (err) {
    if (fullContent) {
      chatHistory.push({ role: "assistant", content: fullContent });
      if (currentSessionId) {
        await api("POST", `/api/sessions/${currentSessionId}/messages`, { role: "assistant", content: fullContent });
      }
    }
    msgDiv.querySelector(".bubble").textContent = fullContent || `Error: ${err.message}`;
    msgDiv.classList.remove("thinking");
  }

  sendBtn.style.display = "inline-flex";
  stopBtn.style.display = "none";
  chatAbort = null;
}

function stopChat() {
  if (chatAbort) {
    chatAbort.cancel();
    chatAbort = null;
  }
}

function appendChatMessage(role, text, thinking) {
  const box = document.getElementById("chat-box");
  const welcome = box.querySelector(".chat-welcome");
  if (welcome) welcome.remove();

  const div = document.createElement("div");
  div.className = `chat-msg ${role}`;
  if (thinking) div.classList.add("thinking");

  const ts = new Date().toLocaleTimeString();
  const content = role === "user" ? escHtml(text) : (thinking ? escHtml(text) : mdToHtml(text));
  div.innerHTML = `<div class="label">${role === "user" ? "You" : "Assistant"}</div><div class="bubble">${content}</div><span class="timestamp">${ts}</span>`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  if (typeof hljs !== "undefined") {
    div.querySelectorAll("pre code").forEach(el => hljs.highlightElement(el));
  }
  return div;
}

let allBrowseModels = [];
let browsePageIdx = 0;
const BROWSE_PAGE_SIZE = 24;
let browseSystemVram = null;

async function loadBrowse() {
  const grid = document.getElementById("browse-grid");
  grid.innerHTML = '<div class="spinner"></div>';
  const q = document.getElementById("browse-search").value;
  const capability = document.getElementById("browse-capability").value;
  const sort = document.getElementById("browse-sort").value;
  const compat = document.getElementById("browse-compat").value;
  try {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (capability) params.set("capability", capability);
    if (compat) params.set("compatible", compat);
    params.set("sort", sort);
    const r = await api("GET", "/api/library/browse?" + params.toString());
    allBrowseModels = r.models || [];
    browseSystemVram = r.system_vram || null;
    browsePageIdx = 0;
    document.getElementById("browse-count").textContent = `${allBrowseModels.length} model variants`;
    updateBrowseSysSpecs();
    renderBrowsePage();
  } catch {
    grid.innerHTML = '<div class="result-box"><span class="empty-state">Failed to load model catalog.</span></div>';
  }
}

function updateBrowseSysSpecs() {
  const div = document.getElementById("browse-sys-specs");
  if (!browseSystemVram) {
    div.innerHTML = '<span class="spec"><span class="value">Scan your hardware on the Dashboard for VRAM-based compatibility.</span></span>';
    return;
  }
  div.innerHTML = `
    <span class="spec"><span class="label">Your GPU VRAM:</span> <span class="value">${browseSystemVram} GB</span></span>
    <span class="badge badge-fit-gpu">Fits GPU</span> = Q4 uses &le;90% VRAM
    <span class="badge badge-fit-offload">Offload</span> = Q4 uses up to 2x VRAM
    <span class="badge badge-fit-too-big">Too large</span> = Q4 needs &gt;2x VRAM
  `;
}

function fitBadge(fit) {
  const labels = { gpu: "Fits GPU", offload: "Offload", too_big: "Too large", unknown: "?", maybe: "Maybe" };
  const cls = fit === "gpu" ? "badge-fit-gpu" : fit === "offload" ? "badge-fit-offload" : fit === "too_big" ? "badge-fit-too-big" : "badge-fit-maybe";
  return fit && fit !== "unknown" ? `<span class="badge ${cls}">${labels[fit] || fit}</span>` : "";
}

function renderBrowsePage() {
  const grid = document.getElementById("browse-grid");
  const start = browsePageIdx * BROWSE_PAGE_SIZE;
  const page = allBrowseModels.slice(start, start + BROWSE_PAGE_SIZE);
  if (!page.length) {
    grid.innerHTML = '<div class="result-box"><span class="empty-state">No models found.</span></div>';
    renderBrowsePagination();
    return;
  }
  grid.innerHTML = page.map(m => {
    const display = m.display || m.name;
    const tag = encodeURIComponent(m.display || m.name);
    const caps = (m.capabilities || []).map(c => `<span class="badge-sm cap">${escHtml(c)}</span>`).join("");
    const fit = fitBadge(m.fit);
    const details = encodeURIComponent(JSON.stringify({
      name: display,
      params_b: m.params_b,
      vram_q4: m.vram_q4,
      vram_q8: m.vram_q8,
      context: m.context,
      pulls: m.pulls,
      capabilities: m.capabilities || [],
      description: m.description || "",
      variant: m.variant || ""
    }));
    const vramLine = m.vram_q4 > 0
      ? `<div style="font-size:0.75rem;color:var(--muted);margin-top:4px">
          <span>${escHtml(m.variant || "")}</span>
          <span style="margin:0 6px">&#8226;</span>
          <span><strong>${m.params_b}B</strong> params</span>
          <span style="margin:0 6px">&#8226;</span>
          <span>Q4: <strong>${m.vram_q4}GB</strong></span>
          <span style="margin:0 6px">&#8226;</span>
          <span>Q8: ${m.vram_q8}GB</span>
          <span style="margin:0 6px">&#8226;</span>
          <span>ctx: ${m.context.toLocaleString()}</span>
        </div>`
      : "";
    return `<div class="browse-card">
      <h3>${escHtml(display)}</h3>
      <div class="desc">${escHtml(m.description || "No description.")}</div>
      ${vramLine}
      <div class="meta">
        <span>${escHtml(m.pulls)} pulls</span>
        ${fit}
        ${caps}
      </div>
      <div class="actions">
        <button class="btn btn-sm" data-pull="${tag}">Install</button>
        <button class="btn btn-sm btn-secondary" data-browse-details="${tag}">Tags</button>
        <button class="btn btn-sm btn-secondary" data-browse-info='${details}'>Info</button>
      </div>
    </div>`;
  }).join("");
  renderBrowsePagination();
}

function renderBrowsePagination() {
  const total = allBrowseModels.length;
  const pages = Math.ceil(total / BROWSE_PAGE_SIZE);
  const container = document.getElementById("browse-pagination");
  if (pages <= 1) { container.innerHTML = ""; return; }
  let html = `<button class="page-btn" data-page-btn="${browsePageIdx - 1}" ${browsePageIdx === 0 ? "disabled" : ""}>&laquo; Prev</button>`;
  for (let i = Math.max(0, browsePageIdx - 3); i < Math.min(pages, browsePageIdx + 4); i++) {
    html += `<button class="page-btn${i === browsePageIdx ? " active" : ""}" data-page-btn="${i}">${i + 1}</button>`;
  }
  html += `<button class="page-btn" data-page-btn="${browsePageIdx + 1}" ${browsePageIdx >= pages - 1 ? "disabled" : ""}>Next &raquo;</button>`;
  container.innerHTML = html;
}

function browsePage(idx) {
  if (idx < 0 || idx >= Math.ceil(allBrowseModels.length / BROWSE_PAGE_SIZE)) return;
  browsePageIdx = idx;
  renderBrowsePage();
  document.getElementById("browse-grid").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function showBrowseDetails(name) {
  try {
    const r = await api("GET", `/api/library/tags?name=${encodeURIComponent(name)}`);
    if (r.error) { toast(`Error: ${r.error}`, "error"); return; }
    const tags = r.tags || [];
    if (!tags.length) {
      toast(`No tags found for ${name}`, "info");
      return;
    }
    const tagHtml = tags.map(t => {
      const fullTag = `${name}:${t}`;
      return `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid var(--border)">
        <code style="font-size:0.85rem">${escHtml(fullTag)}</code>
        <button class="btn btn-sm" data-pull="${encodeURIComponent(fullTag)}">Install</button>
      </div>`;
    }).join("");
    openModal(`Tags: ${escHtml(name)}`, `
      <p style="margin-bottom:12px;color:var(--muted);font-size:0.85rem">${tags.length} available variants</p>
      ${tagHtml}
    `);
  } catch {
    toast("Failed to load tags.", "error");
  }
}

async function updateRunningCard() {
  const card = document.getElementById("card-running");
  if (!card) return;
  const body = card.querySelector(".card-body");
  try {
    const r = await api("GET", "/api/ollama/ps");
    if (!r.running || !r.models.length) {
      body.textContent = "No models loaded";
    } else {
      const names = r.models.map(m => escHtml(m.name)).join(", ");
      body.innerHTML = `<div><strong>${r.models.length}</strong> loaded</div><div style="margin-top:4px;font-size:0.8rem;color:var(--muted)">${names}</div>`;
    }
  } catch {
    body.textContent = "Could not check";
  }
}

function showBrowseInfo(raw) {
  try {
    const m = JSON.parse(decodeURIComponent(raw));
    const bodyHtml = `
      <table>
        <tr><td>Name</td><td><strong>${escHtml(m.name)}</strong></td></tr>
        <tr><td>Description</td><td>${escHtml(m.description || "—")}</td></tr>
        <tr><td>Parameters</td><td>${m.params_b || "?"}B</td></tr>
        <tr><td>VRAM (Q4)</td><td>${m.vram_q4 || "?"} GB</td></tr>
        <tr><td>VRAM (Q8)</td><td>${m.vram_q8 || "?"} GB</td></tr>
        <tr><td>Context</td><td>${(m.context || 0).toLocaleString()} tokens</td></tr>
        <tr><td>Pulls</td><td>${escHtml(m.pulls || "?")}</td></tr>
        <tr><td>Capabilities</td><td>${(m.capabilities || []).length ? m.capabilities.join(", ") : "—"}</td></tr>
      </table>`;
    openModal(escHtml(m.name), bodyHtml);
  } catch {
    toast("Failed to load model info.", "error");
  }
}

async function loadRunningModels() {
  const div = document.getElementById("running-models");
  const badge = document.getElementById("running-models-badge");
  try {
    const r = await api("GET", "/api/ollama/ps");
    if (!r.running || !r.models.length) {
      div.innerHTML = '<em>No models currently loaded.</em>';
      badge.textContent = "";
      return;
    }
    badge.textContent = `${r.models.length} running`;
    div.innerHTML = r.models.map(m =>
      `<div style="margin-bottom:4px">${escHtml(m.name)} <span class="badge badge-gpu">${m.size_gb} GB</span></div>`
    ).join("");
  } catch {
    div.innerHTML = '<em>Could not check.</em>';
    badge.textContent = "";
  }
}

/* =========== SETTINGS =========== */

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme === "light" ? "light" : "dark");
}

async function loadSettings() {
  try {
    appSettings = await api("GET", "/api/settings");
    settingsLoaded = true;
    applyTheme(appSettings.theme);
    const host = appSettings.ollama_host || "http://localhost:11434";
    if (host !== "http://localhost:11434") {
      try { await api("GET", "/api/settings"); } catch {}
    }
  } catch {
    appSettings = {};
    settingsLoaded = true;
  }
}

async function loadSettingsUi() {
  if (!settingsLoaded) await loadSettings();
  const s = appSettings;
  document.getElementById("setting-default-use-case").value = s.default_use_case || "coding";
  document.getElementById("setting-ollama-host").value = s.ollama_host || "http://localhost:11434";
  document.getElementById("setting-max-tokens").value = s.max_tokens || 4096;
  document.getElementById("setting-theme").value = s.theme || "dark";
  document.getElementById("setting-auto-save").checked = s.auto_save_sessions !== false;

  const sel = document.getElementById("setting-default-model");
  try {
    const models = await api("GET", "/api/ollama/models");
    const current = s.default_model || "";
    sel.innerHTML = '<option value="">Auto (first installed)</option>';
    models.forEach(m => {
      const opt = document.createElement("option");
      opt.value = m.name;
      opt.textContent = m.name;
      if (m.name === current) opt.selected = true;
      sel.appendChild(opt);
    });
  } catch {
    sel.innerHTML = '<option value="">Ollama not running</option>';
  }
}

async function saveSettingsUi() {
  const updates = {
    default_use_case: document.getElementById("setting-default-use-case").value,
    ollama_host: document.getElementById("setting-ollama-host").value,
    max_tokens: parseInt(document.getElementById("setting-max-tokens").value) || 4096,
    theme: document.getElementById("setting-theme").value,
    auto_save_sessions: document.getElementById("setting-auto-save").checked,
    default_model: document.getElementById("setting-default-model").value,
  };
  await api("POST", "/api/settings", updates);
  appSettings = updates;
  applyTheme(updates.theme);
  const msg = document.getElementById("settings-saved-msg");
  msg.style.display = "inline";
  setTimeout(() => { msg.style.display = "none"; }, 2000);
  toast("Settings saved.", "success");
}

/* =========== SESSIONS =========== */

async function loadSessions() {
  const div = document.getElementById("sessions-list");
  div.innerHTML = '<div class="spinner"></div>';
  try {
    const sessions = await api("GET", "/api/sessions");
    if (!sessions.length) {
      div.innerHTML = '<div class="result-box"><span class="empty-state">No saved sessions. Start a chat and save it!</span></div>';
      return;
    }
    const q = (document.getElementById("sessions-search").value || "").toLowerCase();
    const filtered = q ? sessions.filter(s =>
      s.name.toLowerCase().includes(q) || s.model.toLowerCase().includes(q)
    ) : sessions;

    div.innerHTML = filtered.map(s => {
      const isActive = s.id === currentSessionId;
      const date = new Date(s.updated_at * 1000).toLocaleString();
      return `<div class="session-item${isActive ? ' active-session' : ''}">
        <div class="info" data-session-load="${s.id}">
          <div class="name">${escHtml(s.name || "Untitled")}</div>
          <div class="meta">
            <span>${escHtml(s.model || "—")}</span>
            <span>${s.message_count} messages</span>
            <span>${date}</span>
          </div>
        </div>
        <div class="actions">
          <button class="btn btn-sm" data-session-action="load" data-session-id="${s.id}">Load</button>
          <button class="btn btn-sm btn-secondary" data-session-action="export" data-session-id="${s.id}">Export</button>
          <button class="btn btn-sm btn-danger" data-session-action="delete" data-session-id="${s.id}">Delete</button>
        </div>
      </div>`;
    }).join("");

    if (!filtered.length) {
      div.innerHTML = '<div class="result-box"><span class="empty-state">No sessions match your filter.</span></div>';
    }
  } catch {
    div.innerHTML = '<div class="result-box"><span class="empty-state">Error loading sessions.</span></div>';
  }
}

function filterSessions() {
  loadSessions();
}

function handleSessionAction(btn) {
  const action = btn.dataset.sessionAction;
  const sid = btn.dataset.sessionId;
  if (action === "load") loadSessionIntoChat(sid);
  else if (action === "export") exportSession(sid);
  else if (action === "delete") deleteSessionPrompt(sid);
}

async function loadSessionIntoChat(sessionId) {
  try {
    const sess = await api("GET", `/api/sessions/${sessionId}`);
    if (!sess || sess.error) { toast("Session not found.", "error"); return; }
    const msgs = await api("GET", `/api/sessions/${sessionId}/messages`);
    currentSessionId = sessionId;
    chatHistory = msgs.map(m => ({ role: m.role, content: m.content }));
    const modelSel = document.getElementById("chat-model");
    if (sess.model && [...modelSel.options].some(o => o.value === sess.model)) {
      modelSel.value = sess.model;
    }
    selectChatModel();
    renderChatHistory();
    navigateTo("chat");
    toast(`Loaded session: ${sess.name || "Untitled"}`, "success");
  } catch {
    toast("Error loading session.", "error");
  }
}

async function saveCurrentSession() {
  const model = document.getElementById("chat-model").value;
  if (!model) { toast("Select a model first.", "error"); return; }
  if (!chatHistory.length) { toast("No messages to save.", "error"); return; }

  try {
    if (currentSessionId) {
      const existing = await api("GET", `/api/sessions/${currentSessionId}`);
      if (existing && !existing.error) {
        await api("PATCH", `/api/sessions/${currentSessionId}`, { model });
        const existingMsgs = await api("GET", `/api/sessions/${currentSessionId}/messages`);
        const newMsgs = chatHistory.slice(existingMsgs.length);
        for (const m of newMsgs) {
          await api("POST", `/api/sessions/${currentSessionId}/messages`, m);
        }
        toast("Session updated.", "success");
        return;
      }
    }
    const sess = await api("POST", "/api/sessions", {
      name: `${model} - ${new Date().toLocaleString()}`,
      model,
    });
    currentSessionId = sess.id;
    for (const m of chatHistory) {
      await api("POST", `/api/sessions/${sess.id}/messages`, m);
    }
    toast("Session saved.", "success");
  } catch {
    toast("Error saving session.", "error");
  }
}

async function loadSessionList() {
  const sel = document.getElementById("chat-session");
  try {
    const sessions = await api("GET", "/api/sessions");
    const current = sel.value;
    sel.innerHTML = '<option value="">Current session</option>';
    sessions.slice(0, 20).forEach(s => {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = `${s.name || "Untitled"} (${s.message_count})`;
      if (s.id === current || s.id === currentSessionId) opt.selected = true;
      sel.appendChild(opt);
    });
  } catch {}
}

async function switchChatSession() {
  const sel = document.getElementById("chat-session");
  const sid = sel.value;
  if (!sid) return;
  loadSessionIntoChat(sid);
}

function renderChatHistory() {
  const box = document.getElementById("chat-box");
  box.innerHTML = "";
  if (!chatHistory.length) {
    box.innerHTML = '<div class="chat-welcome">Select a model above and start chatting.</div>';
    return;
  }
  for (const m of chatHistory) {
    appendChatMessage(m.role, m.content);
  }
  box.scrollTop = box.scrollHeight;
}

async function deleteSessionPrompt(sessionId) {
  try {
    const sess = await api("GET", `/api/sessions/${sessionId}`);
    const name = sess && sess.name ? sess.name : "this session";
    if (!confirm(`Delete "${name}"?`)) return;
    await api("DELETE", `/api/sessions/${sessionId}`);
    if (currentSessionId === sessionId) {
      currentSessionId = null;
      chatHistory = [];
      document.getElementById("chat-box").innerHTML = '<div class="chat-welcome">Select a model above and start chatting.</div>';
    }
    loadSessions();
    toast("Session deleted.", "info");
  } catch {
    toast("Error deleting session.", "error");
  }
}

async function exportSession(sessionId) {
  try {
    const data = await api("GET", `/api/sessions/${sessionId}/export`);
    if (!data || data.error) { toast("Error exporting session.", "error"); return; }
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `session-${data.session.name || sessionId}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast("Session exported.", "success");
  } catch {
    toast("Error exporting session.", "error");
  }
}

async function exportAllSessions() {
  try {
    const sessions = await api("GET", "/api/sessions");
    if (!sessions.length) { toast("No sessions to export.", "error"); return; }
    const all = [];
    for (const s of sessions) {
      const data = await api("GET", `/api/sessions/${s.id}/export`);
      if (data) all.push(data);
    }
    const blob = new Blob([JSON.stringify(all, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `model-hub-sessions-${new Date().toISOString().slice(0,10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast(`${all.length} sessions exported.`, "success");
  } catch {
    toast("Error exporting sessions.", "error");
  }
}

function launchCliForModel() {
  const model = document.getElementById("chat-model").value;
  if (!model) { toast("Select a model first.", "error"); return; }
  const scriptPath = "cli.py";
  const cmd = `start cmd /k python "${scriptPath}" chat "${model}"`;
  try {
    const shell = new ActiveXObject("WScript.Shell");
    shell.Run(cmd, 1, false);
  } catch {
    window.open(`javascript:alert('Open a terminal and run: python cli.py chat ${model}')`);
  }
  toast(`CLI launched for ${model}`, "success");
}

function importSessionFile() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".json";
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const sessions = Array.isArray(data) ? data : [data];
      let count = 0;
      for (const s of sessions) {
        if (s.session && s.messages) {
          await api("POST", "/api/sessions/import", s);
          count++;
        }
      }
      toast(`${count} session(s) imported.`, "success");
      loadSessions();
    } catch {
      toast("Invalid session file.", "error");
    }
  };
  input.click();
}
