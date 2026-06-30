let activeDownloads = {};
let systemInfo = null;
let chatAbort = null;
let chatHistory = [];

document.addEventListener("DOMContentLoaded", () => {
  initNav();
  checkOllama();
  loadDashboard();
  checkFirstRun();
  checkForUpdates();

  const sel = document.getElementById("chat-model");
  sel.addEventListener("change", selectChatModel);
  const input = document.getElementById("chat-input");
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
  });
});

function initNav() {
  document.querySelectorAll(".sidebar nav a").forEach(a => {
    a.addEventListener("click", e => {
      e.preventDefault();
      const page = a.dataset.page;
      document.querySelectorAll(".sidebar nav a").forEach(x => x.classList.remove("active"));
      a.classList.add("active");
      document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
      document.getElementById(`page-${page}`).classList.add("active");
      if (page === "models") loadInstalledModels();
      if (page === "dashboard") loadDashboard();
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
            <a href="${r.download_url}" target="_blank" class="btn" style="text-decoration:none">Download Ollama</a>
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
      badge.innerHTML = `<a href="${update.download_url}" target="_blank" style="color:var(--accent);text-decoration:none">Update v${update.latest_version}</a>`;
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
  } catch { document.getElementById("card-system").querySelector(".card-body").innerHTML = '<span class="empty-state">Failed to scan</span>'; }

  try {
    const r = await api("GET", "/api/ollama/status");
    document.getElementById("card-ollama").querySelector(".card-body").innerHTML = r.running
      ? `<span class="badge badge-gpu">Running</span> version ${r.version || "?"}`
      : '<span class="badge badge-offload">Not running</span> - <a href="https://ollama.com/download" target="_blank" style="color:var(--accent)">Install Ollama</a>';
  } catch { document.getElementById("card-ollama").querySelector(".card-body").textContent = "Error checking"; }

  try {
    const models = await api("GET", "/api/ollama/models");
    const card = document.getElementById("card-models").querySelector(".card-body");
    if (models.length === 0) { card.innerHTML = '<span class="empty-state">No models installed</span>'; }
    else { card.innerHTML = models.map(m => `<div>${m.name} <span class="badge badge-gpu">${m.size_gb} GB</span></div>`).join(""); }
  } catch { document.getElementById("card-models").querySelector(".card-body").textContent = "Ollama not running"; }
}

function renderSystemCard(info) {
  const sys = document.getElementById("card-system").querySelector(".card-body");
  sys.innerHTML = `
    <div><span class="label">OS:</span> <span class="value">${info.os}</span></div>
    <div><span class="label">CPU:</span> <span class="value">${info.cpu}</span></div>
    <div><span class="label">Cores:</span> <span class="value">${info.cores}</span></div>
    <div><span class="label">RAM:</span> <span class="value">${info.ram_gb} GB</span></div>
  `;
  const gpu = document.getElementById("card-gpu").querySelector(".card-body");
  if (info.gpus && info.gpus.length) {
    gpu.innerHTML = info.gpus.map(g => `<div><span class="value">${g.name}</span> — ${g.vram_gb} GB <span class="label">(${g.backend})</span></div>`).join("");
    loadQuickPicks(info.total_vram_gb || info.gpus[0].vram_gb);
  } else {
    gpu.innerHTML = '<span class="empty-state">No GPU detected</span>';
  }
}

async function loadQuickPicks(vram) {
  const div = document.getElementById("quick-picks");
  if (!vram || vram <= 0) { div.innerHTML = '<span class="empty-state">Scan your hardware first.</span>'; return; }
  try {
    const r = await api("GET", `/api/recommend?vram=${vram}&use_case=coding&top_k=3`);
    const recs = r.recommendations || [];
    if (!recs.length) { div.innerHTML = '<span class="empty-state">No recommendations</span>'; return; }
    div.innerHTML = recs.map((m, i) =>
      `<div class="result-box" style="margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div><strong>${i+1}.</strong> ${m.name} <span class="badge badge-${m.run_mode === 'gpu' ? 'gpu' : 'offload'}">${m.quant}</span></div>
          <button class="btn btn-sm" onclick="pullModel('${m.ollama_cmd.replace('ollama run ', '')}')">Install</button>
        </div>
        <div style="font-size:0.8rem;color:var(--muted);margin-top:4px">
          ${m.vram_gb} GB VRAM · ${m.context} ctx · Score ${m.score}
        </div>
      </div>`
    ).join("");
  } catch { div.innerHTML = '<span class="empty-state">Error loading picks</span>'; }
}

async function runScan() {
  document.getElementById("scan-result").innerHTML = '<p><em>Scanning...</em></p>';
  const info = await api("GET", "/api/scan");
  systemInfo = info;
  let html = '<div class="result-box"><h3>System</h3><table>';
  html += `<tr><td>OS</td><td>${info.os}</td></tr>`;
  html += `<tr><td>CPU</td><td>${info.cpu}</td></tr>`;
  html += `<tr><td>Cores</td><td>${info.cores}</td></tr>`;
  html += `<tr><td>RAM</td><td>${info.ram_gb} GB</td></tr>`;
  if (info.gpus && info.gpus.length) {
    info.gpus.forEach(g => {
      html += `<tr><td>GPU</td><td>${g.name} — ${g.vram_gb} GB (${g.backend})</td></tr>`;
    });
  } else {
    html += `<tr><td>GPU</td><td>None detected</td></tr>`;
  }
  html += '</table></div>';
  document.getElementById("scan-result").innerHTML = html;
}

async function runScanAndRecommend() {
  document.getElementById("scan-result").innerHTML = '<p><em>Scanning...</em></p>';
  document.getElementById("recs-result").innerHTML = '';
  const vramOverride = parseFloat(document.getElementById("vram-override").value) || 0;
  const useCase = document.getElementById("use-case").value;
  const vram = vramOverride > 0 ? vramOverride : null;

  const info = await api("GET", "/api/scan");
  systemInfo = info;
  let html = '<div class="result-box"><h3>System</h3><table>';
  html += `<tr><td>OS</td><td>${info.os}</td></tr>`;
  html += `<tr><td>CPU</td><td>${info.cpu}</td></tr>`;
  html += `<tr><td>RAM</td><td>${info.ram_gb} GB</td></tr>`;
  if (info.gpus && info.gpus.length) {
    info.gpus.forEach(g => {
      html += `<tr><td>GPU</td><td>${g.name} — ${g.vram_gb} GB (${g.backend})</td></tr>`;
    });
  }
  html += '</table></div>';
  document.getElementById("scan-result").innerHTML = html;

  document.getElementById("recs-result").innerHTML = '<p><em>Generating recommendations...</em></p>';
  const effectiveVram = vram || info.total_vram_gb || (info.gpus && info.gpus.length ? info.gpus[0].vram_gb : 0);
  const r = await api("GET", `/api/recommend?vram=${effectiveVram}&use_case=${useCase}&top_k=8`);
  const recs = r.recommendations || [];

  if (!recs.length) {
    document.getElementById("recs-result").innerHTML = '<div class="result-box"><span class="empty-state">No models fit your hardware. Try a lower VRAM override or different use case.</span></div>';
    return;
  }

  let rh = `<div class="result-box"><h3>Recommended Models (VRAM: ${r.vram_gb} GB | RAM: ${r.ram_gb} GB)</h3><table>`;
  rh += `<tr><th>#</th><th>Model</th><th>Quant</th><th>Score</th><th>VRAM</th><th>Context</th><th>Mode</th><th>Actions</th></tr>`;
  recs.forEach((m, i) => {
    const modeClass = m.run_mode === "gpu" ? "badge-gpu" : "badge-offload";
    const modeLabel = m.run_mode === "gpu" ? "GPU" : "Offload";
    const modelTag = m.ollama_cmd.replace("ollama run ", "");
    rh += `<tr>
      <td>${i+1}</td>
      <td><strong>${m.name}</strong></td>
      <td><span class="badge badge-gpu">${m.quant}</span></td>
      <td>${m.score}</td>
      <td>${m.vram_gb}</td>
      <td>${m.context}</td>
      <td><span class="badge ${modeClass}">${modeLabel}</span></td>
      <td class="model-actions">
        <button class="btn btn-sm" onclick="pullModel('${modelTag}')">Install</button>
        <button class="btn btn-sm btn-secondary" onclick="showDetails('${encodeURIComponent(JSON.stringify(m))}')">Details</button>
      </td>
    </tr>`;
  });
  rh += '</table></div>';
  document.getElementById("recs-result").innerHTML = rh;
}

function showDetails(encoded) {
  const m = JSON.parse(decodeURIComponent(encoded));
  const body = document.getElementById("recs-result");
  body.innerHTML += `
    <div class="result-box">
      <h3>${m.name}</h3>
      <table>
        <tr><td>Provider</td><td>${m.provider || "?"}</td></tr>
        <tr><td>Parameters</td><td>${m.params_b}B</td></tr>
        <tr><td>Quantization</td><td>${m.quant}</td></tr>
        <tr><td>VRAM needed</td><td>${m.vram_gb} GB</td></tr>
        <tr><td>Context window</td><td>${m.context} tokens</td></tr>
        <tr><td>Run mode</td><td>${m.run_mode}</td></tr>
        <tr><td>Quality score</td><td>${m.scores.quality}</td></tr>
        <tr><td>Speed score</td><td>${m.scores.speed}</td></tr>
        <tr><td>Fit score</td><td>${m.scores.fit}</td></tr>
        <tr><td>Context score</td><td>${m.scores.context}</td></tr>
        <tr><td>Ollama command</td><td><code>${m.ollama_cmd}</code></td></tr>
      </table>
    </div>
  `;
  body.scrollIntoView({ behavior: "smooth", block: "end" });
}

async function pullModel(modelName) {
  if (!modelName) return;
  const dlDiv = document.getElementById("downloads-list");
  const id = `dl-${Date.now()}`;
  const el = document.createElement("div");
  el.className = "download-item";
  el.id = id;
  el.innerHTML = `<div class="title">Installing ${modelName}...</div>
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
          if (p.error) {
            status.textContent = `Error: ${p.error}`;
            status.className = "status";
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
            document.querySelector(".download-item .title").textContent = `${modelName} — Installed`;
          }
        } catch {}
      }
    }
  } catch (err) {
    const status = document.getElementById(`${id}-status`);
    if (status) { status.textContent = `Failed: ${err.message}`; }
  }
}

async function loadInstalledModels() {
  const div = document.getElementById("installed-models");
  div.innerHTML = '<em>Loading...</em>';
  try {
    const models = await api("GET", "/api/ollama/models");
    if (!models.length) {
      div.innerHTML = '<div class="result-box"><span class="empty-state">No models installed.</span><p style="margin-top:12px">Go to <strong>Scan &amp; Recommend</strong> to find and install models.</p></div>';
      return;
    }
    let html = '<div class="result-box"><table><tr><th>Model</th><th>Size</th><th>Modified</th><th>Actions</th></tr>';
    models.forEach(m => {
      html += `<tr>
        <td><strong>${m.name}</strong></td>
        <td>${m.size_gb} GB</td>
        <td>${new Date(m.modified).toLocaleDateString()}</td>
        <td class="model-actions">
          <button class="btn btn-sm btn-secondary" onclick="runModel('${m.name}')">Run</button>
          <button class="btn btn-sm btn-danger" onclick="deleteModel('${m.name}')">Delete</button>
        </td>
      </tr>`;
    });
    html += '</table></div>';
    div.innerHTML = html;
  } catch {
    div.innerHTML = '<div class="result-box"><span class="empty-state">Could not connect to Ollama.</span><p style="margin-top:12px">Make sure <a href="https://ollama.com/download" target="_blank" style="color:var(--accent)">Ollama</a> is installed and running.</p></div>';
  }
}

async function deleteModel(name) {
  if (!confirm(`Delete ${name}?`)) return;
  await api("POST", "/api/ollama/delete", { model: name });
  loadInstalledModels();
}

function runModel(name) {
  const encoded = encodeURIComponent(name);
  window.open(`ollama://run/${encoded}`, "_blank");
}

async function loadChatModels() {
  const sel = document.getElementById("chat-model");
  try {
    const models = await api("GET", "/api/ollama/models");
    sel.innerHTML = '<option value="">— Select a model —</option>';
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

async function sendChat() {
  const model = document.getElementById("chat-model").value;
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if (!model || !text) return;

  const box = document.getElementById("chat-box");
  input.value = "";
  chatHistory.push({ role: "user", content: text });
  appendChatMessage("user", text);
  const msgDiv = appendChatMessage("assistant", "Thinking...", true);

  const sendBtn = document.getElementById("chat-send");
  const stopBtn = document.getElementById("chat-stop");
  sendBtn.style.display = "none";
  stopBtn.style.display = "inline-flex";

  try {
    const resp = await fetch("/api/ollama/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, messages: chatHistory }),
    });

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
            msgDiv.querySelector(".bubble").textContent = fullContent;
            box.scrollTop = box.scrollHeight;
          }
        } catch {}
      }
    }

    msgDiv.classList.remove("thinking");
    chatHistory.push({ role: "assistant", content: fullContent });

  } catch (err) {
    msgDiv.querySelector(".bubble").textContent = `Error: ${err.message}`;
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
  const div = document.createElement("div");
  div.className = `chat-msg ${role}`;
  if (thinking) div.classList.add("thinking");
  div.innerHTML = `<div class="label">${role === "user" ? "You" : "Assistant"}</div><div class="bubble">${text}</div>`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}
