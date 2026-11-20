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
    let payload = null;
    try { payload = await resp.json(); } catch (e) { /* non-JSON error body */ }
    const detail = payload && payload.detail;
    const message = typeof detail === "string"
      ? detail
      : (detail && detail.message) || `HTTP ${resp.status}`;
    const err = new Error(message);
    err.status = resp.status;
    err.payload = payload;
    throw err;
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
    } else if (/^&gt;\s?/.test(html)) {
      out.push(`<blockquote>${html.replace(/^&gt;\s?/, "")}</blockquote>`);
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
const PAGES = ["paper", "research", "settings"];
function switchPage(name) {
  if (!PAGES.includes(name)) name = "paper";
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
let providersInfo = null;
let authInfo = { authenticated: false, username: "", role: "user", unlimited: false, can_manage_models: false };

function isAdmin() {
  return !!authInfo.can_manage_models;
}

function renderAuth() {
  const status = $("auth-status");
  const btn = $("btn-auth");
  if (isAdmin()) {
    status.textContent = `管理员 · ${authInfo.username}`;
    status.className = "auth-status auth-admin";
    btn.textContent = "退出登录";
  } else {
    status.textContent = "普通用户 · 模型只读";
    status.className = "auth-status";
    btn.textContent = "管理员登录";
  }
  $("btn-test").classList.toggle("hidden", !isAdmin());
  $("admin-password-card").classList.toggle("hidden", !isAdmin());
  $("active-model-form").classList.toggle("hidden", !isAdmin());
  $("provider-config-list").classList.toggle("hidden", !isAdmin());
}

async function loadAuth() {
  try {
    authInfo = await api("/api/auth/me");
  } catch (e) {
    authInfo = { authenticated: false, username: "", role: "user", unlimited: false, can_manage_models: false };
  }
  renderAuth();
}

function openLogin() {
  $("login-error").textContent = "";
  $("login-modal").classList.remove("hidden");
  setTimeout(() => $("login-user").focus(), 30);
}

function closeLogin() {
  $("login-modal").classList.add("hidden");
  $("login-pass").value = "";
}

$("btn-auth").addEventListener("click", async () => {
  if (!isAdmin()) { openLogin(); return; }
  try {
    await postJSON("/api/auth/logout", {});
  } catch (e) {}
  authInfo = { authenticated: false, username: "", role: "user", unlimited: false, can_manage_models: false };
  renderAuth();
  await loadProviders().catch(() => {});
  if (typeof reloadAfterAuthChange === "function") reloadAfterAuthChange();
});

$("btn-login-cancel").addEventListener("click", closeLogin);
$("login-modal").addEventListener("click", (e) => {
  if (e.target === $("login-modal")) closeLogin();
});

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = $("login-user").value.trim();
  const password = $("login-pass").value;
  if (!username || !password) { $("login-error").textContent = "请输入用户名和密码"; return; }
  const btn = $("btn-login-submit");
  btn.disabled = true;
  try {
    authInfo = await postJSON("/api/auth/login", { username, password });
    closeLogin();
    renderAuth();
    await loadProviders().catch(() => {});
    if (typeof reloadAfterAuthChange === "function") reloadAfterAuthChange();
  } catch (err) {
    $("login-error").textContent = `登录失败：${err.message}`;
  } finally {
    btn.disabled = false;
  }
});

$("change-password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const status = $("password-status");
  const current = $("password-current").value;
  const next = $("password-new").value;
  if (next !== $("password-confirm").value) {
    status.textContent = "❌ 两次输入的新密码不一致";
    return;
  }
  const button = $("btn-change-password");
  button.disabled = true;
  try {
    await postJSON("/api/auth/password", { current_password: current, new_password: next });
    $("change-password-form").reset();
    status.textContent = "✅ 密码已更新";
  } catch (err) {
    status.textContent = "❌ " + err.message;
  } finally {
    button.disabled = false;
  }
});

// 登录/退出后刷新论文工作台的配额与按钮状态
async function reloadAfterAuthChange() {
  if (typeof reloadPaperAuthState === "function") reloadPaperAuthState();
}

function modelDisplay(p, m) {
  const label = (providersInfo && providersInfo.labels?.[p]) || p || "未设置";
  return m ? `${label} · ${m}` : `${label} · 默认模型`;
}

// 全局模型：展示服务端实际生效的提供商与模型
function currentProviderModel() {
  if (!providersInfo) return { provider: "", model: "" };
  const provider = providersInfo.effective_provider || providersInfo.current_provider || "";
  const model = providersInfo.effective_model || providersInfo.current_model || "";
  return { provider, model };
}

function renderCurrentModel() {
  const { provider, model } = currentProviderModel();
  $("model-current").textContent = provider
    ? modelDisplay(provider, model)
    : "（未配置，点「去设置」）";
}

// 拉取提供商信息并重渲染所有依赖它的界面块
async function loadProviders() {
  providersInfo = await api("/api/providers");
  renderCurrentModel();
  updateSettingsTable();
  renderProviderConfig();
}

// ---------- 侧边栏事件 ----------
$("btn-test").addEventListener("click", async () => {
  const { provider, model } = currentProviderModel();
  if (!provider) { setConn("未配置模型，请先在设置页填写 API Key", "err"); return; }
  setConn("检测中...", "pending");
  try {
    // 用已保存的 Key / URL 测试全局模型
    const r = await postJSON("/api/health-check", { provider, model: model || null });
    if (r.model_ready) setConn(`✅ ${r.detail}`, "ok");
    else if (r.reachable) setConn(`⚠️ ${r.detail}`, "warn");
    else setConn(`❌ ${r.detail}`, "err");
  } catch (e) { setConn(`❌ ${e.message}`, "err"); }
});

$("btn-goto-settings").addEventListener("click", () => switchPage("settings"));

function setConn(text, kind) {
  const el = $("conn-status");
  el.textContent = text;
  el.className = "conn " + (kind || "");
}

// ---------- 对话页 ----------
let researchPoll = null;
let researchTaskId = null;
let currentReport = null; // {name, text}
let previewedReportName = "";
let reportPreviewRequest = 0;
let lastRefsKey = "";

$("results").addEventListener("input", () => ($("v-results").textContent = $("results").value));
$("v-results").textContent = $("results").value;

function logLine(text) {
  const el = $("research-logs");
  const div = document.createElement("div");
  div.textContent = text;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

const STAGE_LABELS = {
  starting: "准备检索",
  search: "检索参考文献",
  write: "生成综合报告",
  save: "保存报告",
  done: "已完成",
  cancelled: "已取消",
  error: "调研失败",
};

function setResearchRunning(running) {
  $("btn-research").disabled = running;
  $("btn-research-cancel").classList.toggle("hidden", !running);
  $("btn-research-cancel").disabled = false;
}

function setResearchProgress(progress, stage, isError) {
  const n = Number(progress);
  const pct = Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : 0;
  const fill = $("research-progress-fill");
  const cancelled = stage === "cancelled";
  fill.style.width = pct + "%";
  fill.classList.toggle("is-error", !!isError);
  fill.classList.toggle("is-cancelled", cancelled);
  fill.classList.toggle("is-running", !isError && !cancelled && pct > 0 && pct < 100);
  $("research-percent").textContent = pct + "%";
  if (cancelled) {
    $("research-stage").textContent = "⏹ 已取消调研";
    return;
  }
  const label = STAGE_LABELS[stage] || "处理中";
  if (isError) $("research-stage").textContent = `❌ ${label}`;
  else if (pct >= 100) $("research-stage").textContent = "✅ 调研完成";
  else $("research-stage").textContent = `${label}...`;
}

function applyResearchTask(t) {
  const cancelled = t.status === "cancelled";
  const failed = t.status === "error" || t.status === "failed";
  const done = t.status === "done";
  setResearchProgress(t.progress || 0, cancelled ? "cancelled" : t.stage, failed);
  renderResearchRefs(t.references || []);
  $("research-logs").innerHTML = "";
  for (const l of t.logs || []) logLine(l);
  return { cancelled, failed, done };
}

function renderResearchRefs(refs) {
  if (!Array.isArray(refs) || !refs.length) return;
  $("research-refs").classList.remove("hidden");
  $("research-ref-count").textContent = `共 ${refs.length} 篇`;
  const key = refs.map((r) => r.title).join("|");
  if (key === lastRefsKey) return;
  lastRefsKey = key;
  $("research-ref-list").innerHTML = refs.map((r, i) => {
    const link = /^https?:\/\//i.test(r.url || "") ? r.url
      : (r.doi ? `https://doi.org/${r.doi}` : "");
    const meta = [r.authors, r.year, r.journal].filter(Boolean).map(esc).join(" · ");
    const extra = [r.cited_by ? `被引 ${r.cited_by}` : "",
      r.doi ? `DOI: ${esc(r.doi)}` : ""].filter(Boolean).join(" · ");
    const oa = r.oa ? '<span class="tag-ok">开放获取</span>' : "";
    const abs = r.abstract ? `<p class="ref-abstract">${esc(r.abstract)}</p>` : "";
    return `<div class="research-ref">
      <div class="ref-index">[${i + 1}]</div>
      <div class="ref-body">
        <div class="ref-title">${link
          ? `<a href="${esc(link)}" target="_blank" rel="noopener noreferrer">${esc(r.title)}</a>`
          : esc(r.title)}</div>
        ${meta ? `<div class="ref-meta">${meta}</div>` : ""}
        ${(extra || oa) ? `<div class="ref-meta">${extra} ${oa}</div>` : ""}
        ${abs}
      </div>
    </div>`;
  }).join("");
}

$("btn-research").addEventListener("click", async () => {
  const topic = $("research-topic").value.trim();
  if (!topic) { alert("请输入论文方向或研究主题"); return; }
  if (researchPoll) { clearInterval(researchPoll); researchPoll = null; }

  lastRefsKey = "";
  researchTaskId = null;
  setResearchRunning(true);
  $("research-progress").classList.remove("hidden");
  $("research-refs").classList.add("hidden");
  $("research-result").classList.add("hidden");
  $("research-ref-list").innerHTML = "";
  $("research-logs").innerHTML = "";
  setResearchProgress(0, "starting", false);

  try {
    const r = await postJSON("/api/research", {
      topic,
      max_results: parseInt($("results").value, 10),
      detailed: $("report-style").value === "detailed",
    });
    researchTaskId = r.task_id;
    pollResearch(r.task_id);
  } catch (e) {
    setResearchProgress(0, "error", true);
    logLine(`❌ 启动失败：${e.message}`);
    setResearchRunning(false);
  }
});

$("btn-research-cancel").addEventListener("click", async () => {
  if (!researchTaskId) return;
  const taskId = researchTaskId;
  $("btn-research-cancel").disabled = true;
  try {
    const t = await postJSON(`/api/research/${taskId}/cancel`, {});
    if (researchPoll) { clearInterval(researchPoll); researchPoll = null; }
    const state = applyResearchTask(t);
    setResearchRunning(false);
    researchTaskId = null;
    if (state.done) {
      showReport(t.report, t.report_file);
      loadReports();
    }
  } catch (e) {
    $("btn-research-cancel").disabled = false;
    alert(`取消失败：${e.message}`);
  }
});

function pollResearch(taskId) {
  researchPoll = setInterval(async () => {
    try {
      const t = await api(`/api/research/${taskId}`);
      const state = applyResearchTask(t);
      if (state.done) {
        clearInterval(researchPoll); researchPoll = null;
        setResearchRunning(false);
        researchTaskId = null;
        showReport(t.report, t.report_file);
        loadReports();
      } else if (state.cancelled || state.failed) {
        clearInterval(researchPoll); researchPoll = null;
        setResearchRunning(false);
        researchTaskId = null;
        if (state.failed) logLine(`\n${t.error || "调研失败"}`);
      }
    } catch (e) {
      // 瞬时失败忽略，继续轮询
    }
  }, 1000);
}

function showReport(text, name) {
  currentReport = { name, text };
  $("research-result").classList.remove("hidden");
  $("report-body").innerHTML = renderMarkdown(text);
  $("research-result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function downloadReportFile(name, format) {
  if (!name) return;
  const url = `/api/reports/${encodeURIComponent(name)}${format === "docx" ? "?format=docx" : ""}`;
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
function downloadReport(format) {
  if (currentReport) downloadReportFile(currentReport.name, format);
}
$("btn-dl-md").addEventListener("click", () => downloadReport("md"));
$("btn-dl-docx").addEventListener("click", () => downloadReport("docx"));
$("btn-report-preview-md").addEventListener("click", () => downloadReportFile(previewedReportName, "md"));
$("btn-report-preview-docx").addEventListener("click", () => downloadReportFile(previewedReportName, "docx"));
$("btn-close-report-preview").addEventListener("click", () => $("report-preview-dialog").close());
$("report-preview-dialog").addEventListener("close", () => {
  reportPreviewRequest += 1;
  previewedReportName = "";
});
$("report-preview-dialog").addEventListener("click", (e) => {
  if (e.target === $("report-preview-dialog")) $("report-preview-dialog").close();
});

async function previewReport(name) {
  const dialog = $("report-preview-dialog");
  const title = $("report-preview-title");
  const status = $("report-preview-status");
  const body = $("report-preview-body");
  const requestId = ++reportPreviewRequest;
  previewedReportName = "";
  title.textContent = name;
  status.textContent = "正在加载报告…";
  body.innerHTML = "";
  $("btn-report-preview-md").disabled = true;
  $("btn-report-preview-docx").disabled = true;
  if (!dialog.open) dialog.showModal();
  try {
    const response = await fetch(`/api/reports/${encodeURIComponent(name)}`);
    if (!response.ok) {
      let detail = "";
      try { detail = (await response.json()).detail || ""; } catch (e) {}
      throw new Error(detail || `HTTP ${response.status}`);
    }
    const text = await response.text();
    if (requestId !== reportPreviewRequest) return;
    body.innerHTML = renderMarkdown(text);
    previewedReportName = name;
    status.textContent = "";
    $("btn-report-preview-md").disabled = false;
    $("btn-report-preview-docx").disabled = false;
  } catch (e) {
    if (requestId === reportPreviewRequest) status.textContent = `预览失败：${e.message}`;
  }
}

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
  try {
    const resp = await fetch("/api/research/followup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ report: currentReport.text, question: q }),
    });
    // 非 2xx 时后端返回 JSON 错误体，直接读流会解析不到 data: 行
    if (!resp.ok || !resp.body) {
      let detail = "";
      try { detail = (await resp.json()).detail || ""; } catch (e) {}
      throw new Error(detail || `HTTP ${resp.status}`);
    }
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
        <button class="btn btn-ghost" data-act="preview" data-name="${esc(r.name)}">预览</button>
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
  if (btn.dataset.act === "preview") {
    await previewReport(name);
  } else if (btn.dataset.act === "dl") {
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

function updateSettingsTable() {
  if (!providersInfo) return;
  const { provider, model } = currentProviderModel();
  $("set-current-model").textContent = provider
    ? modelDisplay(provider, model)
    : "未配置";
  const admin = isAdmin();
  $("settings-readonly").classList.toggle("hidden", admin);
  $("btn-save-research-settings").disabled = !admin;
  $("set-results").disabled = !admin;
  $("set-style").disabled = !admin;
  // 调研默认参数回填
  const rp = providersInfo.research_prefs || {};
  if (rp.max_results) { $("set-results").value = Math.min(10, Math.max(5, rp.max_results)); $("set-v-results").textContent = $("set-results").value; }
  if (rp.detailed !== undefined) $("set-style").value = rp.detailed ? "detailed" : "standard";
}

$("set-results").addEventListener("input", () => ($("set-v-results").textContent = $("set-results").value));

$("btn-save-research-settings").addEventListener("click", async () => {
  const statusEl = $("research-settings-status");
  if (!isAdmin()) { statusEl.textContent = "仅管理员可修改设置"; return; }
  try {
    await postJSON("/api/settings", {
      research_max_results: parseInt($("set-results").value, 10),
      research_detailed: $("set-style").value === "detailed",
    });
    statusEl.textContent = "✅ 已保存，下次调研任务生效";
    setTimeout(() => (statusEl.textContent = ""), 3000);
  } catch (e) { statusEl.textContent = `❌ 保存失败：${e.message}`; }
});

// 调研页初始化：读取设置页保存的默认参数
function applyResearchDefaults() {
  if (!providersInfo) return;
  const rp = providersInfo.research_prefs || {};
  if (rp.max_results) {
    $("results").value = Math.min(10, Math.max(5, rp.max_results));
    $("v-results").textContent = $("results").value;
  }
  if (rp.detailed !== undefined) $("report-style").value = rp.detailed ? "detailed" : "standard";
}

// ---------- 设置页：模型服务 ----------
const PROVIDER_PRESETS = {
  deepseek: { name: "DeepSeek", url: "https://api.deepseek.com/v1" },
  zhipu: { name: "智谱 GLM", url: "https://open.bigmodel.cn/api/paas/v4" },
  qwen: { name: "通义千问", url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
};
const PROTOCOL_LABELS = { openai: "OpenAI 兼容", anthropic: "Anthropic Messages", gemini: "Gemini" };
let editingServiceId = null;

function keyTag(has) {
  return has ? '<span class="tag-ok">已配置</span>' : '<span class="tag-missing">未配置</span>';
}

function renderProviderConfig() {
  const customList = $("custom-provider-list");
  const localList = $("local-provider-list");
  const serviceSelect = $("active-provider");
  if (!isAdmin() || providersInfo.read_only) {
    customList.replaceChildren();
    localList.replaceChildren();
    serviceSelect.replaceChildren();
    $("active-model").replaceChildren();
    return;
  }
  const { provider, model } = currentProviderModel();
  const services = providersInfo.custom_services || [];
  customList.innerHTML = services.map((service) => {
    const { id, name, protocol, models, default_model, has_api_key } = service;
    const active = provider === `custom:${id}`;
    const modelList = models.map((m) => `
      <li><code>${esc(m)}</code>${m === default_model ? '<span class="muted">默认</span>' : ""}</li>`).join("");
    return `
      <div class="custom-service-row">
        <div class="prov-head">
          <div class="provider-description">
            <span class="prov-name">${esc(name)}</span>
            ${active ? '<span class="tag-ok">使用中</span>' : ""}
            <p class="hint-line">${esc(PROTOCOL_LABELS[protocol] || protocol)} · ${models.length} 个模型
              · API Key${keyTag(has_api_key)}</p>
          </div>
          <div class="provider-actions">
            <button type="button" class="btn" data-service-action="edit" data-service-id="${esc(id)}">编辑</button>
            <button type="button" class="btn btn-ghost" data-service-action="delete" data-service-id="${esc(id)}">删除</button>
          </div>
        </div>
        ${modelList ? `<ul class="service-models">${modelList}</ul>` : '<p class="muted">未添加模型</p>'}
      </div>`;
  }).join("") || '<p class="muted">还没有自定义服务</p>';

  localList.innerHTML = Object.entries(providersInfo.local_api || {}).map(([p, url]) => `
    <form class="prov-item" data-local-provider="${esc(p)}">
      <div class="prov-head"><span class="prov-name">${esc(providersInfo.labels[p] || p)}</span>
        <button type="submit" class="btn">保存地址</button></div>
      <label class="lbl" for="pu-${esc(p)}">Base URL</label>
      <input type="url" class="input" id="pu-${esc(p)}" value="${esc(url)}" spellcheck="false" required>
      <p class="hint-line" id="ps-${esc(p)}" role="status"></p>
    </form>`).join("");

  const selectable = providersInfo.providers.filter((p) => {
    const service = services.find((item) => p === `custom:${item.id}`);
    return !service || service.models.length;
  });
  serviceSelect.replaceChildren(...selectable.map((p) => new Option(providersInfo.labels[p] || p, p)));
  if (selectable.includes(provider)) serviceSelect.value = provider;
  renderActiveModels(serviceSelect.value, serviceSelect.value === provider ? model : "");
}

function renderActiveModels(provider, current = "") {
  const service = (providersInfo.custom_services || []).find((item) => provider === `custom:${item.id}`);
  const select = $("active-model");
  const input = $("active-local-model");
  const local = !service;
  select.classList.toggle("hidden", local);
  input.classList.toggle("hidden", !local);
  $("active-model-label").htmlFor = local ? input.id : select.id;
  if (local) {
    select.replaceChildren();
    input.value = current;
  } else {
    select.replaceChildren(...service.models.map((model) => new Option(model, model)));
    select.value = service.models.includes(current) ? current : service.default_model;
  }
}

$("active-provider").addEventListener("change", (e) => {
  const active = currentProviderModel();
  renderActiveModels(e.target.value, e.target.value === active.provider ? active.model : "");
  $("active-model-status").textContent = "";
});

$("active-model-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!isAdmin()) return;
  const provider = $("active-provider").value;
  const model = provider.startsWith("custom:") ? $("active-model").value : $("active-local-model").value.trim();
  const button = $("btn-use-model");
  const status = $("active-model-status");
  button.disabled = true;
  try {
    await postJSON("/api/settings", { provider, model });
    await loadProviders();
    status.textContent = "当前模型已更新";
  } catch (err) {
    status.textContent = `切换失败：${err.message}`;
  } finally {
    button.disabled = false;
  }
});

function addServiceModel(model = "", selected = false) {
  const row = document.createElement("div");
  row.className = "service-model-row";
  row.innerHTML = `
    <input class="input" type="text" aria-label="模型 ID" placeholder="模型 ID" value="${esc(model)}" spellcheck="false" required>
    <label><input type="radio" name="service-default-model"${selected ? " checked" : ""}> 默认</label>
    <button class="btn btn-ghost" type="button" data-remove-model aria-label="移除模型" title="移除模型">×</button>`;
  $("custom-provider-models").appendChild(row);
  return row.querySelector(".input");
}

function openCustomProvider(service = null) {
  if (!isAdmin()) return;
  editingServiceId = service?.id ?? null;
  $("custom-provider-title").textContent = service ? "编辑自定义服务" : "新增自定义服务";
  $("custom-provider-preset-field").classList.toggle("hidden", !!service);
  $("custom-provider-preset").replaceChildren(
    new Option("手动配置", ""),
    ...Object.entries(PROVIDER_PRESETS).map(([id, preset]) => new Option(preset.name, id)),
  );
  $("custom-provider-name").value = service?.name || "";
  $("custom-provider-protocol").value = service?.protocol || "openai";
  $("custom-provider-url").value = service?.base_url || "";
  const key = $("custom-provider-key");
  key.value = "";
  key.placeholder = service?.has_api_key ? "已保存，留空保留原密钥" : "输入服务的 API Key（如需）";
  $("custom-provider-key-status").innerHTML = service ? keyTag(service.has_api_key) : "";
  $("custom-provider-error").textContent = "";
  const list = $("custom-provider-models");
  list.replaceChildren();
  const models = service?.models?.length ? service.models : [""];
  const defaultModel = models.includes(service?.default_model) ? service.default_model : models[0];
  for (const model of models) addServiceModel(model, model === defaultModel);
  $("custom-provider-dialog").showModal();
}

$("btn-add-custom-provider").addEventListener("click", () => openCustomProvider());

$("custom-provider-preset").addEventListener("change", (e) => {
  const preset = PROVIDER_PRESETS[e.target.value];
  if (!preset) return;
  $("custom-provider-name").value = preset.name;
  $("custom-provider-protocol").value = "openai";
  $("custom-provider-url").value = preset.url;
  $("custom-provider-key").value = "";
});

$("btn-add-service-model").addEventListener("click", () => addServiceModel().focus());
$("custom-provider-models").addEventListener("click", (e) => {
  const button = e.target.closest("[data-remove-model]");
  if (!button) return;
  const list = $("custom-provider-models");
  if (list.children.length === 1) {
    $("custom-provider-error").textContent = "请至少保留一个模型";
    return;
  }
  button.closest(".service-model-row").remove();
  if (!list.querySelector('input[type="radio"]:checked')) list.querySelector('input[type="radio"]').checked = true;
  $("custom-provider-error").textContent = "";
});

const providerDialog = $("custom-provider-dialog");
$("btn-cancel-custom-provider").addEventListener("click", () => providerDialog.close());
providerDialog.addEventListener("cancel", (e) => {
  if ($("custom-provider-fields").disabled) e.preventDefault();
});
providerDialog.addEventListener("close", () => { $("custom-provider-key").value = ""; });

$("custom-provider-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!isAdmin()) return;
  const rows = [...$("custom-provider-models").children];
  const models = rows.map((row) => row.querySelector(".input").value.trim());
  const error = $("custom-provider-error");
  if (models.some((model) => !model)) {
    error.textContent = "请填写每一行的模型 ID，或移除空白行";
    return;
  }
  if (new Set(models).size !== models.length) {
    error.textContent = "模型 ID 重复，请移除重复项";
    return;
  }
  const defaultIndex = rows.findIndex((row) => row.querySelector('input[type="radio"]').checked);
  const body = {
    name: $("custom-provider-name").value.trim(),
    protocol: $("custom-provider-protocol").value,
    base_url: $("custom-provider-url").value.trim(),
    api_key: $("custom-provider-key").value.trim(),
    models,
    default_model: models[defaultIndex] || models[0],
  };
  const id = editingServiceId;
  const fields = $("custom-provider-fields");
  const status = $("custom-provider-status");
  fields.disabled = true;
  error.textContent = "";
  try {
    if (id) {
      await api(`/api/custom-providers/${encodeURIComponent(id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } else {
      await postJSON("/api/custom-providers", body);
    }
  } catch (err) {
    error.textContent = `保存失败：${err.message}`;
    return;
  } finally {
    fields.disabled = false;
  }
  providerDialog.close();
  status.textContent = "服务已保存";
  try { await loadProviders(); }
  catch (err) { status.textContent = `已保存，但刷新失败：${err.message}`; }
});

$("custom-provider-list").addEventListener("click", async (e) => {
  const button = e.target.closest("[data-service-action]");
  if (!button || !isAdmin()) return;
  const service = providersInfo.custom_services.find((item) => item.id === button.dataset.serviceId);
  if (!service) return;
  if (button.dataset.serviceAction === "edit") {
    openCustomProvider(service);
    return;
  }
  const active = currentProviderModel().provider === `custom:${service.id}`;
  const note = active ? "删除后将自动切换到其他服务。" : "";
  if (!confirm("删除「" + service.name + "」及其模型列表？" + note)) return;
  button.disabled = true;
  const status = $("custom-provider-status");
  try {
    await api(`/api/custom-providers/${encodeURIComponent(service.id)}`, { method: "DELETE" });
    await loadProviders();
    status.textContent = "服务已删除";
  } catch (err) {
    status.textContent = `删除失败：${err.message}`;
  } finally {
    button.disabled = false;
  }
});

$("local-provider-list").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!isAdmin()) return;
  const p = e.target.dataset.localProvider;
  const button = e.target.querySelector("button");
  button.disabled = true;
  try {
    await postJSON("/api/settings", { [`${p}_base_url`]: $(`pu-${p}`).value.trim() });
    await loadProviders();
    $(`ps-${p}`).textContent = "地址已保存";
  } catch (err) {
    $(`ps-${p}`).textContent = `保存失败：${err.message}`;
  } finally {
    button.disabled = false;
  }
});

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
  await loadAuth();
  try { await loadProviders(); } catch (e) { console.error(e); }
  applyResearchDefaults();
  switchPage("paper");
  if (typeof initPaperPage === "function") initPaperPage();
  else window.addEventListener("load", () => {
    if (typeof initPaperPage === "function") initPaperPage();
  });
  loadReports();
  setInterval(checkServer, 30000);
})();
