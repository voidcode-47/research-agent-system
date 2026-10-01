/* =========================================================
 * 研究助手 SPA 前端逻辑
 * 页面切换零重载；所有数据通过 JSON API + SSE 与后端交互
 * ========================================================= */
"use strict";

// ---------- 基础工具 ----------
const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

async function api(url, opts) {
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    let detail = "";
    try { detail = (await resp.json()).detail || ""; } catch (e) {}
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

const postJSON = (url, body) => api(url, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

// ---------- Markdown 轻量渲染（防 XSS，覆盖报告常用语法） ----------
function renderMarkdown(text) {
  if (!text) return "";
  let lines = String(text).split(/\r?\n/);
  let out = [];
  let inCode = false, codeBuf = [];

  const flushCode = () => {
    if (codeBuf.length) {
      out.push(`<pre><code>${esc(codeBuf.join("\n"))}</code></pre>`);
      codeBuf = [];
    }
  };

  for (let raw of lines) {
    const line = raw;
    if (line.trim().startsWith("```")) {
      if (inCode) { flushCode(); inCode = false; }
      else { flushCode(); inCode = true; }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }
    if (!line.trim()) { out.push(""); continue; }

    let html = esc(line);
    // 链接 [t](u)
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');
    // 加粗
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

    if (/^#{1,6}\s/.test(html)) {
      const level = html.match(/^#{1,6}/)[0].length;
      html = html.replace(/^#{1,6}\s/, "");
      out.push(`<h${level}>${html}</h${level}>`);
    } else if (/^>\s?/.test(html)) {
      out.push(`<blockquote>${html.replace(/^>\s?/, "")}</blockquote>`);
    } else if (/^\s*[-*]\s+/.test(html)) {
      out.push(`<li>${html.replace(/^\s*[-*]\s+/, "")}</li>`);
    } else if (/^\s*\d+[.、]\s+/.test(html)) {
      out.push(`<li>${html.replace(/^\s*\d+[.、]\s+/, "")}</li>`);
    } else if (/^---+$/.test(html.trim())) {
      out.push("<hr>");
    } else {
      out.push(`<p>${html}</p>`);
    }
  }
  flushCode();
  // 无序/有序列表分组
  let final = [];
  let listTag = null;
  for (const item of out) {
    if (item.startsWith("<li>")) {
      if (!listTag) { listTag = "ul"; final.push(`<${listTag}>`); }
      final.push(item);
    } else {
      if (listTag) { final.push(`</${listTag}>`); listTag = null; }
      final.push(item);
    }
  }
  if (listTag) final.push(`</${listTag}>`);
  return final.join("\n");
}

// ---------- 页面导航（零重载） ----------
const PAGES = ["chat", "research", "knowledge", "settings"];
function switchPage(name) {
  if (!PAGES.includes(name)) name = "chat";
  document.querySelectorAll(".nav-item").forEach((b) => {
    b.classList.toggle("active", b.dataset.page === name);
  });
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  $(`view-${name}`).classList.add("active");
}
document.getElementById("nav").addEventListener("click", (e) => {
  const btn = e.target.closest(".nav-item");
  if (btn) switchPage(btn.dataset.page);
});

// ---------- 状态 ----------
const store = {
  get(key, def) {
    try { const v = localStorage.getItem(key); return v === null ? def : JSON.parse(v); }
    catch (e) { return def; }
  },
  set(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) {} },
};

let providersInfo = null; // {providers, labels, current_provider, current_model, custom_models, custom_api}

// ---------- 模型配置 ----------
async function loadProviders() {
  providersInfo = await api("/api/providers");
  const sel = $("provider-select");
  sel.innerHTML = "";
  for (const p of providersInfo.providers) {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = providersInfo.labels[p] || p;
    sel.appendChild(opt);
  }
  const cur = providersInfo.current_provider;
  if (cur && providersInfo.providers.includes(cur)) sel.value = cur;
  $("custom-base").value = providersInfo.custom_api?.base_url || "";
  $("custom-key").value = "";
  $("custom-model").value = providersInfo.custom_api?.model || "";
  await loadModels(sel.value);
  updateSettingsTable();
}

async function loadModels(provider) {
  const ms = $("model-select");
  ms.innerHTML = "";
  const isCustom = provider === "custom";
  $("custom-fields").classList.toggle("hidden", !isCustom);
  if (isCustom) { loadModelsDone(provider, []); return; }
  try {
    const data = await api(`/api/models?provider=${encodeURIComponent(provider)}`);
    const models = data.models || [];
    if (!models.length) {
      const opt = document.createElement("option");
      opt.value = ""; opt.textContent = "（无静态列表，可手动输入）";
      ms.appendChild(opt);
    } else {
      for (const m of models) {
        const opt = document.createElement("option");
        opt.value = m; opt.textContent = m;
        ms.appendChild(opt);
      }
    }
    loadModelsDone(provider, models);
  } catch (e) {
    ms.innerHTML = `<option value="">模型加载失败</option>`;
    loadModelsDone(provider, []);
  }
}

function loadModelsDone(provider, models) {
  const saved = providersInfo.current_model;
  const customSaved = providersInfo.custom_models?.[provider];
  const ms = $("model-select");
  if (saved && [...ms.options].some((o) => o.value === saved)) ms.value = saved;
  else if (saved && customSaved && saved === customSaved) {
    // 已保存的自定义模型名：不在静态列表里 → 选择自定义输入
    if (provider !== "custom") { enterCustomMode(); }
  }
}

function enterCustomMode() {
  // 已保存模型名不在列表：显示自定义输入并预填
  $("custom-fields").classList.remove("hidden");
}

$("provider-select").addEventListener("change", () => loadModels($("provider-select").value));

$("btn-test").addEventListener("click", async () => {
  const provider = $("provider-select").value;
  let model = $("model-select").value;
  if (provider === "custom") model = $("custom-model").value.trim();
  setConn("检测中...", "pending");
  try {
    const r = await postJSON("/api/health-check", { provider, model: model || null });
    if (r.model_ready) setConn(`✅ ${r.detail}`, "ok");
    else if (r.reachable) setConn(`⚠️ ${r.detail}`, "warn");
    else setConn(`❌ ${r.detail}`, "err");
  } catch (e) { setConn(`❌ ${e.message}`, "err"); }
});

function setConn(text, kind) {
  const el = $("conn-status");
  el.textContent = text;
  el.className = "conn " + (kind || "");
}

$("btn-save").addEventListener("click", async () => {
  const provider = $("provider-select").value;
  let model = $("model-select").value;
  const payload = { provider };
  if (provider === "custom") {
    model = $("custom-model").value.trim();
    payload.model = model;
    payload.custom_base_url = $("custom-base").value;
    payload.custom_api_key = $("custom-key").value;
    payload.custom_model = model;
  } else {
    payload.model = model;
  }
  try {
    const r = await postJSON("/api/settings", payload);
    setConn(`✅ 已保存：${r.provider} / ${r.model || "默认"}`, "ok");
    store.set("lastModel", { provider: r.provider, model: r.model });
  } catch (e) { setConn(`❌ 保存失败：${e.message}`, "err"); }
});

// ---------- 对话页 ----------
let chatBusy = false;

function loadMessages() {
  return store.get("chat_messages", []);
}
function saveMessages(msgs) {
  if (msgs.length > 60) msgs = msgs.slice(-60);
  store.set("chat_messages", msgs);
}

function renderChatHistory() {
  const area = $("chat-messages");
  area.innerHTML = "";
  const msgs = loadMessages();
  if (!msgs.length) {
    area.innerHTML = `<div class="chat-hint">输入问题开始对话。普通问题快速回答，研究类问题自动深度检索。</div>`;
    return;
  }
  for (const m of msgs) appendChatMsg(m.role, m.content, m.traces, false);
}

function appendChatMsg(role, content, traces, persist) {
  const area = $("chat-messages");
  const hint = area.querySelector(".chat-hint");
  if (hint) hint.remove();
  const wrap = document.createElement("div");
  wrap.className = `chat-msg ${role}`;
  if (traces && traces.length) {
    for (const t of traces) {
      const d = document.createElement("div");
      d.className = "tool-trace";
      if (t.type === "tool") d.innerHTML = `<span class="trace-action">🔧 调用 ${esc(t.tool)}</span>：<code>${esc(JSON.stringify(t.args || {}))}</code>`;
      else d.innerHTML = `<span class="trace-observation">👁️ ${esc(t.text)}</span>`;
      wrap.appendChild(d);
    }
  }
  const b = document.createElement("div");
  b.className = "bubble";
  b.innerHTML = renderMarkdown(content);
  wrap.appendChild(b);
  area.appendChild(wrap);
  area.scrollTop = area.scrollHeight;
  if (persist) {
    const msgs = loadMessages();
    msgs.push({ role, content, traces: traces || [] });
    saveMessages(msgs);
  }
  return b;
}

function getChatConfig() {
  const tools = [...$("chat-tools").selectedOptions].map((o) => o.value);
  // multiple select：全选无值时回退默认
  const toolsAll = ["web_search_cn", "academic_search", "web_scraper"];
  const sel = tools.length ? tools : toolsAll;
  return {
    mode: $("chat-mode").value,
    tools: sel,
    use_rag: $("chat-rag").checked,
    provider: $("provider-select").value,
    model: $("provider-select").value === "custom" ? $("custom-model").value.trim() : $("model-select").value,
  };
}

async function sendChat() {
  if (chatBusy) return;
  const input = $("chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  chatBusy = true;
  $("btn-send").disabled = true;

  appendChatMsg("user", text, [], true);

  const cfg = getChatConfig();
  const msgs = loadMessages().map((m) => ({ role: m.role, content: m.content })).slice(-20);

  let finalMsg = null;
  let traces = [];
  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...cfg, messages: msgs }),
    });
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let full = "";
    let bubble = null;

    const mkBubble = () => {
      if (!bubble) {
        const area = $("chat-messages");
        const hint = area.querySelector(".chat-hint");
        if (hint) hint.remove();
        finalMsg = document.createElement("div");
        finalMsg.className = "chat-msg assistant";
        bubble = document.createElement("div");
        bubble.className = "bubble typing";
        finalMsg.appendChild(bubble);
        area.appendChild(finalMsg);
        area.scrollTop = area.scrollHeight;
      }
      return bubble;
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const part of parts) {
        if (!part.startsWith("data: ")) continue;
        let ev;
        try { ev = JSON.parse(part.slice(6)); } catch (e) { continue; }
        if (ev.type === "delta") {
          full += ev.text || "";
          mkBubble().innerHTML = renderMarkdown(full);
        } else if (ev.type === "tool") {
          traces.push({ type: "tool", tool: ev.tool, args: ev.args });
          appendTraceChip(finalMsg, traces[traces.length - 1]);
        } else if (ev.type === "observation") {
          traces.push({ type: "observation", text: ev.text });
          appendTraceChip(finalMsg, traces[traces.length - 1]);
        } else if (ev.type === "final") {
          full = ev.text || "";
          mkBubble().classList.remove("typing");
          mkBubble().innerHTML = renderMarkdown(full);
        } else if (ev.type === "error") {
          full = `❌ ${ev.text || ev.content || "出错了"}`;
          mkBubble().classList.remove("typing");
          mkBubble().innerHTML = renderMarkdown(full);
        }
      }
    }
    if (finalMsg) finalMsg.querySelector(".bubble")?.classList.remove("typing");
    if (!full) {
      appendChatMsg("assistant", "（未收到模型回复）", [], false);
    } else {
      const msgs2 = loadMessages();
      msgs2.push({ role: "assistant", content: full, traces });
      saveMessages(msgs2);
    }
  } catch (e) {
    appendChatMsg("assistant", `❌ 对话失败：${esc(e.message)}`, [], false);
  } finally {
    chatBusy = false;
    $("btn-send").disabled = false;
  }
}

function appendTraceChip(msgWrap, t) {
  if (!msgWrap) return;
  const d = document.createElement("div");
  d.className = "tool-trace";
  if (t.type === "tool") d.innerHTML = `<span class="trace-action">🔧 调用 ${esc(t.tool)}</span>：<code>${esc(JSON.stringify(t.args || {}))}</code>`;
  else d.innerHTML = `<span class="trace-observation">👁️ ${esc(t.text)}</span>`;
  const b = msgWrap.querySelector(".bubble");
  msgWrap.insertBefore(d, b);
  $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
}

$("btn-send").addEventListener("click", sendChat);
$("chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } });
$("btn-clear-chat").addEventListener("click", () => {
  store.set("chat_messages", []);
  renderChatHistory();
});

// ---------- 研究页 ----------
let researchPoll = null;
let currentReport = null; // {name, text}

$("iters").addEventListener("input", () => ($("v-iters").textContent = $("iters").value));
$("results").addEventListener("input", () => ($("v-results").textContent = $("results").value));
$("v-iters").textContent = $("iters").value;
$("v-results").textContent = $("results").value;

function logLine(text) {
  const el = $("research-logs");
  const div = document.createElement("div");
  div.textContent = text;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

$("btn-research").addEventListener("click", async () => {
  const topic = $("research-topic").value.trim();
  if (!topic) { alert("请输入研究主题"); return; }
  if (researchPoll) { clearInterval(researchPoll); researchPoll = null; }

  $("btn-research").disabled = true;
  $("research-progress").classList.remove("hidden");
  $("research-result").classList.add("hidden");
  $("research-logs").innerHTML = "";

  const provider = $("provider-select").value;
  const model = provider === "custom" ? $("custom-model").value.trim() : $("model-select").value;

  try {
    const r = await postJSON("/api/research", {
      topic,
      max_iterations: parseInt($("iters").value, 10),
      max_results: parseInt($("results").value, 10),
      use_rag: $("rag-use").checked,
      detailed: $("report-style").value === "detailed",
      pre_search: $("pre-search").checked,
      auto_ingest: $("auto-ingest").checked,
      provider,
      model,
    });
    pollResearch(r.task_id);
  } catch (e) {
    logLine(`❌ 启动失败：${e.message}`);
    $("btn-research").disabled = false;
  }
});

function pollResearch(taskId) {
  researchPoll = setInterval(async () => {
    try {
      const t = await api(`/api/research/${taskId}`);
      $("research-logs").innerHTML = "";
      for (const l of t.logs) logLine(l);
      if (t.status === "done") {
        clearInterval(researchPoll); researchPoll = null;
        $("btn-research").disabled = false;
        showReport(t.report, t.report_file);
      } else if (t.status === "error" || t.status === "failed") {
        clearInterval(researchPoll); researchPoll = null;
        $("btn-research").disabled = false;
        logLine(`\n${t.error || "研究失败"}`);
      }
    } catch (e) {
      // 瞬时失败忽略，继续轮询
    }
  }, 1500);
}

function showReport(text, name) {
  currentReport = { name, text };
  $("research-result").classList.remove("hidden");
  $("report-body").innerHTML = renderMarkdown(text);
  $("research-result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function downloadReport(format) {
  if (!currentReport || !currentReport.name) return;
  const url = `/api/reports/${encodeURIComponent(currentReport.name)}${format === "docx" ? "?format=docx" : ""}`;
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
$("btn-dl-md").addEventListener("click", () => downloadReport("md"));
$("btn-dl-docx").addEventListener("click", () => downloadReport("docx"));

$("btn-copy").addEventListener("click", async () => {
  if (!currentReport) return;
  try {
    await navigator.clipboard.writeText(currentReport.text);
    const b = $("btn-copy"); const old = b.textContent;
    b.textContent = "✅ 已复制";
    setTimeout(() => (b.textContent = old), 1200);
  } catch (e) { alert("复制失败，请手动选择文本复制"); }
});

// 追问
$("btn-followup").addEventListener("click", async () => {
  const q = $("followup-input").value.trim();
  if (!q || !currentReport) return;
  $("btn-followup").disabled = true;
  const ans = $("followup-answer");
  ans.innerHTML = "<p class=\"muted\">思考中...</p>";
  const provider = $("provider-select").value;
  const model = provider === "custom" ? $("custom-model").value.trim() : $("model-select").value;
  try {
    const msgs = [
      { role: "system", content: "你是研究助手。基于用户提供的研究报告回答追问。要求：回答具体、有依据；引用报告中已有的来源编号 [n]；若报告信息不足，明确说明并给出建议的补充检索方向，不要编造。" },
      { role: "user", content: `研究报告（主题：${currentReport.text.slice(0, 40)}）：\n\n${currentReport.text}\n\n追问：${q}` },
    ];
    // 走快速对话流式
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: msgs, mode: "chat", tools: [], use_rag: false, provider, model }),
    });
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "", full = "";
    ans.innerHTML = "<p></p>";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const part of parts) {
        if (!part.startsWith("data: ")) continue;
        let ev; try { ev = JSON.parse(part.slice(6)); } catch (e) { continue; }
        if (ev.type === "delta") full += ev.text || "";
        else if (ev.type === "final") full = ev.text || "";
        else if (ev.type === "error") full = `❌ ${ev.text || ev.content || ""}`;
        ans.innerHTML = renderMarkdown(full);
      }
    }
    if (!full) ans.innerHTML = "<p class=\"muted\">（无回复）</p>";
  } catch (e) {
    ans.innerHTML = `<p>❌ ${esc(e.message)}</p>`;
  } finally {
    $("btn-followup").disabled = false;
  }
});

// 历史报告列表
async function loadReports() {
  const el = $("report-list");
  try {
    const data = await api("/api/reports");
    const reps = data.reports || [];
    if (!reps.length) { el.innerHTML = `<div class="rep-empty">暂无历史报告</div>`; return; }
    el.innerHTML = "";
    for (const r of reps) {
      const row = document.createElement("div");
      row.className = "rep-row";
      row.innerHTML = `
        <span class="rep-name" title="${esc(r.name)}">${esc(r.name)}</span>
        <button class="btn btn-ghost" data-act="dl" data-name="${esc(r.name)}">下载</button>
        <button class="btn btn-ghost" data-act="del" data-name="${esc(r.name)}">🗑️</button>`;
      el.appendChild(row);
    }
  } catch (e) {
    el.innerHTML = `<div class="rep-empty">加载失败：${esc(e.message)}</div>`;
  }
}

$("report-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const name = btn.dataset.name;
  if (btn.dataset.act === "dl") {
    const a = document.createElement("a");
    a.href = `/api/reports/${encodeURIComponent(name)}`;
    a.download = "";
    document.body.appendChild(a); a.click(); a.remove();
  } else if (btn.dataset.act === "del") {
    if (!confirm(`删除报告：${name}？`)) return;
    try {
      await api(`/api/reports/${encodeURIComponent(name)}`, { method: "DELETE" });
      loadReports();
    } catch (err) { alert(`删除失败：${err.message}`); }
  }
});

// ---------- 知识库页 ----------
async function loadKb() {
  const statusEl = $("kb-status");
  try {
    const data = await api("/api/kb");
    if (!data.available) {
      statusEl.innerHTML = `<div class="card"><div class="section-title">📚 知识库</div><div class="muted">⚠️ 向量存储不可用：请先在「设置 / 侧边栏」配置 Embedding（云端 Key 或本地 embedding 模型）。</div></div>`;
      $("kb-collections").innerHTML = "";
      return;
    }
    statusEl.innerHTML = "";
    renderKbCollections(data.collections || []);
  } catch (e) {
    statusEl.innerHTML = `<div class="muted">加载失败：${esc(e.message)}</div>`;
  }
}

function renderKbCollections(cols) {
  const el = $("kb-collections");
  el.innerHTML = "";
  const colSel1 = $("kb-upload-collection");
  const colSel2 = $("kb-search-collection");
  colSel1.innerHTML = ""; colSel2.innerHTML = "";
  const names = cols.length ? cols.map((c) => c.name) : ["default"];
  for (const n of names) {
    const o1 = document.createElement("option"); o1.value = n; o1.textContent = n; colSel1.appendChild(o1);
    const o2 = document.createElement("option"); o2.value = n; o2.textContent = n; colSel2.appendChild(o2);
  }
  if (!cols.length) {
    el.innerHTML = `<div class="rep-empty">暂无知识库，输入名称创建</div>`;
    return;
  }
  for (const c of cols) {
    const row = document.createElement("div");
    row.className = "kb-col";
    row.innerHTML = `<span class="name">📁 ${esc(c.name)}</span><span class="count">📄 ${c.count} 个文档</span>
      <button class="btn btn-ghost" data-act="del" data-name="${esc(c.name)}">删除</button>`;
    el.appendChild(row);
  }
}

$("btn-kb-create").addEventListener("click", async () => {
  const name = $("kb-new-name").value.trim();
  if (!name) return;
  try {
    await postJSON("/api/kb/collections", { name });
    $("kb-new-name").value = "";
    loadKb();
  } catch (e) { alert(`创建失败：${e.message}`); }
});

$("kb-collections").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const name = btn.dataset.name;
  if (!confirm(`删除知识库「${name}」？该操作不可恢复。`)) return;
  try {
    await api(`/api/kb/collections/${encodeURIComponent(name)}`, { method: "DELETE" });
    loadKb();
  } catch (err) { alert(`删除失败：${err.message}`); }
});

$("btn-kb-upload").addEventListener("click", async () => {
  const files = $("kb-files").files;
  if (!files.length) { alert("请先选择文件"); return; }
  const collection = $("kb-upload-collection").value;
  const fd = new FormData();
  fd.append("collection", collection);
  for (const f of files) fd.append("files", f);
  const res = $("kb-upload-result");
  res.innerHTML = `<p class="muted">入库中，请稍候（大文件需要一些时间）...</p>`;
  try {
    const r = await api("/api/kb/upload", { method: "POST", body: fd });
    let html = `<p>✅ 共入库 ${r.total_chunks} 块 → ${esc(r.collection)}</p>`;
    for (const it of r.results) {
      html += `<p style="font-size:12.5px;color:${it.ok ? "#15803d" : "#dc2626"}">${esc(it.name)}：${it.ok ? `${it.chunks} 块` : esc(it.error || "失败")}</p>`;
    }
    res.innerHTML = html;
    loadKb();
  } catch (e) { res.innerHTML = `<p class="muted">❌ ${esc(e.message)}</p>`; }
});

$("btn-kb-url").addEventListener("click", async () => {
  const url = $("kb-url").value.trim();
  if (!url) { alert("请输入 URL"); return; }
  const collection = $("kb-upload-collection").value;
  try {
    const r = await postJSON("/api/kb/url", { url, collection });
    $("kb-url").value = "";
    $("kb-upload-result").innerHTML = r.ok
      ? `<p style="color:#15803d">✅ 已入库 ${r.chunks} 块 → ${esc(r.collection)}</p>`
      : `<p class="muted">${esc(r.error || "未提取到内容")}</p>`;
    loadKb();
  } catch (e) { $("kb-upload-result").innerHTML = `<p class="muted">❌ ${esc(e.message)}</p>`; }
});

$("btn-kb-search").addEventListener("click", async () => {
  const q = $("kb-query").value.trim();
  if (!q) return;
  const collection = $("kb-search-collection").value;
  const el = $("kb-search-result");
  el.innerHTML = `<p class="muted">检索中...</p>`;
  try {
    const r = await postJSON("/api/kb/search", { collection, query: q, k: 5 });
    if (!r.results.length) { el.innerHTML = `<p class="muted">未找到相关文档</p>`; return; }
    let html = "";
    r.results.forEach((item, i) => {
      const src = item.metadata?.source || "未知";
      html += `<div class="kb-result-item"><strong>结果 ${i + 1}</strong>（相似度 ${item.score.toFixed(2)}）<br><small>来源：${esc(src)}</small><br>${esc((item.content || "").slice(0, 300))}...</div>`;
    });
    el.innerHTML = html;
  } catch (e) { el.innerHTML = `<p class="muted">❌ ${esc(e.message)}</p>`; }
});

// ---------- 设置页 ----------
function updateSettingsTable() {
  if (!providersInfo) return;
  const cur = providersInfo.current_provider;
  $("set-provider").textContent = cur ? (providersInfo.labels[cur] || cur) : "未设置";
  $("set-model").textContent = providersInfo.current_model || "默认";
  const cus = providersInfo.custom_api;
  $("set-custom").textContent = cus && cus.base_url ? `${cus.base_url} / ${cus.api_key ? "已填 Key" : "未填 Key"} / ${cus.model || "未填模型"}` : "未配置";
}

// ---------- 服务状态 ----------
async function checkServer() {
  try {
    await api("/api/health");
    $("srv-dot").className = "dot dot-on";
    $("srv-txt").textContent = "服务正常";
    $("set-srv").textContent = "✅ 正常";
  } catch (e) {
    $("srv-dot").className = "dot dot-off";
    $("srv-txt").textContent = "连接失败";
    $("set-srv").textContent = `❌ ${esc(e.message)}`;
  }
}

// ---------- 启动 ----------
(async function init() {
  checkServer();
  try { await loadProviders(); } catch (e) { console.error(e); }
  renderChatHistory();
  switchPage("chat");
  loadReports();
  loadKb();
  setInterval(checkServer, 30000);
})();
