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
  // 左侧模型配置跟随当前界面切换（对话/研究/知识库各用各的模型）
  syncSidebarScene();
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

let providersInfo = null; // {providers, labels, current_provider, current_model, custom_models, custom_api, scene_models}

// ---------- 分场景模型（池） ----------
// 页面 → 场景：智能对话→chat，多智能体研究→research，知识库→kb，设置→chat
const SCENE_BY_PAGE = { chat: "chat", research: "research", knowledge: "kb", settings: "chat" };
const SCENE_LABELS = { chat: "智能对话", research: "多智能体研究", kb: "知识库" };
let currentScene = "chat";

function sceneModelOf(scene) {
  const sm = (providersInfo && providersInfo.scene_models) || {};
  const e = sm[scene] || {};
  return {
    provider: e.provider || "",
    model: e.model || "",
    models: Array.isArray(e.models) ? e.models : [],
  };
}

function modelDisplay(p, m) {
  const label = (providersInfo && providersInfo.labels?.[p]) || p || "未设置";
  return m ? `${label} · ${m}` : `${label} · 默认模型`;
}

function fillProviderOptions(sel, selected) {
  sel.innerHTML = "";
  for (const p of providersInfo.providers) {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = providersInfo.labels[p] || p;
    sel.appendChild(opt);
  }
  if (selected && providersInfo.providers.includes(selected)) sel.value = selected;
}

// 侧边栏：只显示当前功能在用的模型；下拉列出该功能的模型池，选择即切换
function syncSidebarScene() {
  if (!providersInfo) return;
  const page = document.querySelector(".nav-item.active")?.dataset.page || "chat";
  currentScene = SCENE_BY_PAGE[page] || "chat";
  $("scene-label").textContent = SCENE_LABELS[currentScene];
  const sc = sceneModelOf(currentScene);
  const sel = $("model-switch");
  sel.innerHTML = "";
  if (!sc.models.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "（未配置，点「添加模型」）";
    sel.appendChild(opt);
    return;
  }
  let activeIdx = sc.models.findIndex((e) => e.provider === sc.provider && e.model === sc.model);
  if (activeIdx < 0) activeIdx = 0;
  sc.models.forEach((e, i) => {
    const opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = modelDisplay(e.provider, e.model);
    sel.appendChild(opt);
  });
  sel.value = String(activeIdx);
}

// 当前侧边栏选中的池内模型
function selectedPoolModel() {
  const idx = parseInt($("model-switch").value, 10);
  const sc = sceneModelOf(currentScene);
  return sc.models[idx] || null;
}

// 拉取提供商信息并重渲染所有依赖它的界面块
async function loadProviders() {
  providersInfo = await api("/api/providers");
  syncSidebarScene();
  updateSettingsTable();
  renderScenePools();
  renderProviderConfig();
}

// 切换某场景当前使用的模型（写入 scene_models[scene].provider/model）
async function saveActiveModel(scene, provider, model) {
  const payload = {};
  payload[scene + "_provider"] = provider;
  payload[scene + "_model"] = model;
  const r = await postJSON("/api/settings", payload);
  await loadProviders();
  return r;
}

// ---------- 侧边栏事件 ----------
// 切换当前功能的模型（池内选择即保存）
$("model-switch").addEventListener("change", async () => {
  const m = selectedPoolModel();
  if (!m) return;
  setConn("保存中...", "pending");
  try {
    await saveActiveModel(currentScene, m.provider, m.model);
    setConn("✅ 已切换", "ok");
  } catch (e) { setConn(`❌ ${e.message}`, "err"); }
});

$("btn-test").addEventListener("click", async () => {
  const m = selectedPoolModel();
  if (!m) { setConn("未配置模型，请先在设置页添加", "err"); return; }
  setConn("检测中...", "pending");
  try {
    // 用已保存的 Key / URL 测试当前功能选中的模型
    const r = await postJSON("/api/health-check", { provider: m.provider, model: m.model || null });
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
  // 对话固定用「智能对话」场景配置的模型
  const sc = sceneModelOf("chat");
  return {
    mode: $("chat-mode").value,
    tools: sel,
    use_rag: $("chat-rag").checked,
    provider: sc.provider || null,
    model: sc.model || null,
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
          // 深度检索路径先推 tool/observation、最后才推 final，这里必须先建出消息容器；
          // 否则 finalMsg 还是 null，调用轨迹会被静默丢弃（只有刷新页面才看得到）
          mkBubble();
          traces.push({ type: "tool", tool: ev.tool, args: ev.args });
          appendTraceChip(finalMsg, traces[traces.length - 1]);
        } else if (ev.type === "observation") {
          mkBubble();
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
  if (loadMessages().length && !confirm("清空当前对话内容？此操作不可恢复。")) return;
  store.set("chat_messages", []);
  // 同步清空当前会话的快照，避免切走再切回时内容又恢复
  const cid = currentSessionId();
  if (cid) {
    const ss = loadSessions();
    const s = ss.find((x) => x.id === cid);
    if (s) {
      s.messages = [];
      s.title = "（空会话）";
      s.ts = Date.now();
      saveSessions(ss);
    }
  }
  renderChatHistory();
  renderChatSessions();
});

// ---------- 会话历史（新建对话 / 切换历史会话 / 置顶 / 重命名 / 删除） ----------
function loadSessions() {
  const ss = store.get("chat_sessions", []);
  // 清理历史遗留的重复快照：同标题+同内容只保留最新一条
  const seen = new Set();
  const clean = [];
  for (const s of [...ss].reverse()) {
    const key = (s.title || "") + "|" + JSON.stringify(s.messages || []);
    if (seen.has(key)) continue;
    seen.add(key);
    clean.unshift(s);
  }
  if (clean.length !== ss.length) saveSessions(clean);
  return clean;
}
function saveSessions(ss) { store.set("chat_sessions", ss.slice(-20)); }

// 排序：置顶在前，其余按最近时间倒序
function sortSessions(ss) {
  return [...ss].sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0) || (b.ts || 0) - (a.ts || 0));
}

function currentSessionId() { return store.get("chat_current", ""); }
function setCurrentSessionId(id) { store.set("chat_current", id || ""); }

function renderChatSessions() {
  const sel = $("chat-sessions");
  const cur = sel.value;
  sel.innerHTML = `<option value="">会话历史</option>`;
  const ss = sortSessions(loadSessions());
  for (const s of ss) {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = (s.pinned ? "📌 " : "") + (s.title || "（无标题会话）");
    sel.appendChild(opt);
  }
  sel.value = cur && ss.some((s) => s.id === cur) ? cur : "";
  renderSessionList();
}

// 会话管理面板：每个会话一行，⋮ 菜单提供 置顶/重命名/删除
function renderSessionList() {
  const list = $("session-list");
  list.innerHTML = "";
  const ss = sortSessions(loadSessions());
  if (!ss.length) {
    list.innerHTML = `<div class="sess-empty">暂无历史会话。发消息后点「新建对话」即可保存。</div>`;
    return;
  }
  for (const s of ss) {
    const row = document.createElement("div");
    row.className = "sess-item" + (s.id === currentSessionId() ? " active" : "");
    row.dataset.id = s.id;
    row.innerHTML =
      `<span class="sess-title">${s.pinned ? "📌 " : ""}${esc(s.title || "（无标题会话）")}</span>` +
      `<button class="sess-dots" title="更多操作">⋮</button>` +
      `<div class="sess-menu hidden">` +
      `<button data-act="pin">${s.pinned ? "取消置顶" : "置顶"}</button>` +
      `<button data-act="rename">重命名</button>` +
      `<button data-act="del" class="danger">删除</button>` +
      `</div>`;
    list.appendChild(row);
  }
}

// 把当前对话快照存入历史（供"新建对话"保留旧内容）
// 内容与已有快照相同 → 覆盖更新而非重复新增，避免"同一个会话出现三次"
function snapshotCurrentSession() {
  const msgs = loadMessages();
  if (!msgs.length) return null;
  const first = msgs.find((m) => m.role === "user");
  const title = (first ? first.content : "对话").replace(/\s+/g, " ").slice(0, 20);
  const snapshot = { id: "s" + Date.now(), title, messages: msgs.slice(-60), ts: Date.now() };
  const ss = loadSessions();
  const dup = ss.find((s) => s.title === title &&
    JSON.stringify(s.messages) === JSON.stringify(snapshot.messages));
  if (dup) {
    dup.ts = snapshot.ts;
    saveSessions(ss);
    return ss;
  }
  ss.push(snapshot);
  saveSessions(ss);
  return ss;
}

$("btn-new-chat").addEventListener("click", () => {
  if (loadMessages().length && !confirm("新建对话会清空当前内容，历史将保存在「会话历史」中。继续？")) return;
  snapshotCurrentSession();
  setCurrentSessionId("");
  store.set("chat_messages", []);
  renderChatHistory();
  renderChatSessions();
});

$("chat-sessions").addEventListener("change", () => {
  const id = $("chat-sessions").value;
  if (!id) return;
  const s = loadSessions().find((x) => x.id === id);
  if (!s) return;
  setCurrentSessionId(id);
  store.set("chat_messages", s.messages || []);
  renderChatHistory();
  renderChatSessions();
});

// ---- 会话管理侧栏（⋯按钮） ----
$("btn-session-menu").addEventListener("click", (e) => {
  e.stopPropagation();
  const pop = $("session-menu");
  pop.classList.toggle("hidden");
  $("view-chat").classList.toggle("sess-open", !pop.classList.contains("hidden"));
  renderSessionList();
});
document.addEventListener("click", (e) => {
  const pop = $("session-menu");
  if (!pop.classList.contains("hidden") && !pop.contains(e.target) && e.target !== $("btn-session-menu")) {
    pop.classList.add("hidden");
    $("view-chat").classList.remove("sess-open");
  }
  // ⋮ 点击由行内逻辑处理，这里不参与关闭
  if (e.target.closest && e.target.closest(".sess-dots")) return;
  // 关闭所有展开的行内菜单
  document.querySelectorAll(".sess-menu:not(.hidden)").forEach((m) => {
    if (!m.contains(e.target)) m.classList.add("hidden");
  });
});
// 会话侧栏：✕ 收起
$("btn-session-close").addEventListener("click", (e) => {
  e.stopPropagation();
  $("session-menu").classList.add("hidden");
  $("view-chat").classList.remove("sess-open");
});

$("session-list").addEventListener("click", (e) => {
  const item = e.target.closest(".sess-item");
  if (!item) return;
  const id = item.dataset.id;
  const menu = item.querySelector(".sess-menu");
  // ⋮ → 展开/收起行内菜单
  if (e.target.closest(".sess-dots")) {
    menu.classList.toggle("hidden");
    document.querySelectorAll(".sess-menu:not(.hidden)").forEach((m) => { if (m !== menu) m.classList.add("hidden"); });
    if (!menu.classList.contains("hidden")) {
      // fixed 定位到视口：下方空间够就向下弹，不够向上弹，避免被面板滚动/裁剪
      const r = item.getBoundingClientRect();
      const mh = 112;
      const below = window.innerHeight - r.bottom;
      menu.style.left = Math.max(8, Math.min(r.right - 128, window.innerWidth - 128)) + "px";
      menu.style.right = "auto";
      menu.style.bottom = "auto";
      if (below < mh) {
        menu.style.top = "auto";
        menu.style.bottom = (window.innerHeight - r.top + 4) + "px";
      } else {
        menu.style.top = (r.bottom + 4) + "px";
      }
    }
    return;
  }
  const actBtn = e.target.closest("button[data-act]");
  if (actBtn) {
    const act = actBtn.dataset.act;
    const ss = loadSessions();
    const s = ss.find((x) => x.id === id);
    if (!s) return;
    if (act === "pin") {
      s.pinned = !s.pinned;
      s.ts = Date.now();
      saveSessions(ss);
    } else if (act === "rename") {
      const name = prompt("新的会话名称：", s.title || "");
      if (name === null) return;
      s.title = name.trim().slice(0, 30) || s.title;
      saveSessions(ss);
    } else if (act === "del") {
      if (!confirm(`删除会话「${s.title || "（无标题会话）"}」？此操作不可恢复。`)) return;
      saveSessions(ss.filter((x) => x.id !== id));
      if (currentSessionId() === id) {
        setCurrentSessionId("");
        store.set("chat_messages", []);
        renderChatHistory();
      }
    }
    $("session-menu").classList.add("hidden");
    renderChatSessions();
    return;
  }
  // 点击会话行 → 切换会话
  const s = loadSessions().find((x) => x.id === id);
  if (!s) return;
  setCurrentSessionId(id);
  store.set("chat_messages", s.messages || []);
  renderChatHistory();
  renderChatSessions();
  $("session-menu").classList.add("hidden");
});

renderChatSessions();

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

  // 研究固定用「多智能体研究」场景配置的模型
  const sr = sceneModelOf("research");
  const provider = sr.provider || null;
  const model = sr.model || null;

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
  // 追问属于对话场景（研究报告问答走对话模型）
  const sc = sceneModelOf("chat");
  const provider = sc.provider || null;
  const model = sc.model || null;
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
    // 与 sendChat 保持一致：非 2xx 时后端返回的是 JSON 错误体，
    // 直接读流会解析不到任何 data: 行，最终误显示为"（无回复）"
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
      statusEl.innerHTML = `<div class="card"><div class="section-title">📚 知识库</div><div class="muted">⚠️ 向量存储不可用：${esc(data.reason || "embedding 未配置")}<br>请在设置页「模型提供商」中填写 embedding 提供商的 Key（智谱/通义），或在 Ollama / LM Studio 中加载 embedding 模型后重启服务。</div></div>`;
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
      html += `<p class="${it.ok ? "txt-ok" : "txt-err"}">${esc(it.name)}：${it.ok ? `${it.chunks} 块` : esc(it.error || "失败")}</p>`;
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
    $("kb-url-result").innerHTML = r.ok
      ? `<p class="txt-ok">✅ 已入库 ${r.chunks} 块 → ${esc(r.collection)}</p>`
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
  const sm = providersInfo.scene_models || {};
  const fmt = (s) => {
    const e = sm[s] || {};
    if (!e.provider && !e.model) return "未设置";
    const label = providersInfo.labels[e.provider] || e.provider;
    return `${label} / ${e.model || "默认"}`;
  };
  $("set-chat-model").textContent = fmt("chat");
  $("set-research-model").textContent = fmt("research");
  $("set-kb-model").textContent = fmt("kb");
  const cus = providersInfo.custom_api;
  $("set-custom").textContent = cus && cus.base_url ? `${cus.base_url} / ${cus.api_key ? "已填 Key" : "未填 Key"}` : "未配置";
  // 研究默认参数回填
  const rp = providersInfo.research_prefs || {};
  if (rp.max_iterations) { $("set-iters").value = rp.max_iterations; $("set-v-iters").textContent = rp.max_iterations; }
  if (rp.max_results) { $("set-results").value = rp.max_results; $("set-v-results").textContent = rp.max_results; }
  if (rp.detailed !== undefined) $("set-style").value = rp.detailed ? "detailed" : "standard";
  if (rp.use_rag !== undefined) $("set-rag").checked = rp.use_rag;
  if (rp.pre_search !== undefined) $("set-presearch").checked = rp.pre_search;
  if (rp.auto_ingest !== undefined) $("set-autoingest").checked = rp.auto_ingest;
}

$("set-iters").addEventListener("input", () => ($("set-v-iters").textContent = $("set-iters").value));
$("set-results").addEventListener("input", () => ($("set-v-results").textContent = $("set-results").value));

$("btn-save-research-settings").addEventListener("click", async () => {
  const statusEl = $("research-settings-status");
  try {
    await postJSON("/api/settings", {
      research_max_iterations: parseInt($("set-iters").value, 10),
      research_max_results: parseInt($("set-results").value, 10),
      research_detailed: $("set-style").value === "detailed",
      research_pre_search: $("set-presearch").checked,
      research_auto_ingest: $("set-autoingest").checked,
      research_use_rag: $("set-rag").checked,
    });
    statusEl.textContent = "✅ 已保存，下次研究任务生效";
    setTimeout(() => (statusEl.textContent = ""), 3000);
  } catch (e) { statusEl.textContent = `❌ 保存失败：${e.message}`; }
});

// 研究页初始化：读取设置页保存的默认参数
function applyResearchDefaults() {
  if (!providersInfo) return;
  const rp = providersInfo.research_prefs || {};
  if (rp.max_iterations) { $("iters").value = rp.max_iterations; $("v-iters").textContent = rp.max_iterations; }
  if (rp.max_results) { $("results").value = rp.max_results; $("v-results").textContent = rp.max_results; }
  if (rp.detailed !== undefined) $("report-style").value = rp.detailed ? "detailed" : "standard";
  if (rp.use_rag !== undefined) $("rag-use").checked = rp.use_rag;
  if (rp.pre_search !== undefined) $("pre-search").checked = rp.pre_search;
  if (rp.auto_ingest !== undefined) $("auto-ingest").checked = rp.auto_ingest;
}

// ---------- 设置页：各功能模型池 ----------
const SCENES = ["chat", "research", "kb"];
// 每个场景的添加行模型列表加载令牌：丢弃过期响应，避免旧结果填进新提供商
const addModelToken = {};

function flashStatus(text, ms) {
  const el = $("scene-settings-status");
  el.textContent = text;
  setTimeout(() => { if (el.textContent === text) el.textContent = ""; }, ms || 2500);
}

// 渲染三个功能的模型池 + 添加行
function renderScenePools() {
  if (!providersInfo) return;
  for (const scene of SCENES) {
    const sc = sceneModelOf(scene);
    const list = $("pool-" + scene);
    list.innerHTML = "";
    if (!sc.models.length) {
      const empty = document.createElement("div");
      empty.className = "pool-empty";
      empty.textContent = "暂未配置模型，在下方添加";
      list.appendChild(empty);
    }
    sc.models.forEach((e) => {
      const active = e.provider === sc.provider && e.model === sc.model;
      const row = document.createElement("div");
      row.className = "pool-row" + (active ? " active" : "");
      row.innerHTML =
        `<span class="pool-name">${esc(modelDisplay(e.provider, e.model))}</span>` +
        (active ? `<span class="badge-cur">当前</span>` : "") +
        `<button class="btn btn-ghost" data-act="use" data-provider="${esc(e.provider)}" data-model="${esc(e.model)}"${active ? " disabled" : ""}>设为当前</button>` +
        `<button class="btn btn-ghost pool-del" data-act="del" data-provider="${esc(e.provider)}" data-model="${esc(e.model)}" title="从列表移除">✕</button>`;
      list.appendChild(row);
    });
    // 添加行：提供商重置，模型区回占位
    fillProviderOptions($("add-" + scene + "-provider"), "");
    const sel = $("add-" + scene + "-model");
    const txt = $("add-" + scene + "-model-custom");
    sel.classList.remove("hidden");
    txt.classList.add("hidden");
    sel.innerHTML = `<option value="">选择模型...</option>`;
  }
}

// 添加行：按提供商加载模型列表（知识库场景列 embedding 模型）。
// 无列表可用（custom / 本地服务未启动）→ 切换为手填输入框。
async function loadAddModelOptions(scene) {
  const provider = $("add-" + scene + "-provider").value;
  const sel = $("add-" + scene + "-model");
  const txt = $("add-" + scene + "-model-custom");
  const token = (addModelToken[scene] = (addModelToken[scene] || 0) + 1);
  sel.classList.remove("hidden");
  txt.classList.add("hidden");
  sel.innerHTML = `<option value="">加载中...</option>`;
  if (!provider) { sel.innerHTML = `<option value="">先选提供商</option>`; return; }
  const kind = scene === "kb" ? "embedding" : "llm";
  let models = [];
  try {
    const data = await api(`/api/models?provider=${encodeURIComponent(provider)}&kind=${kind}`);
    if (addModelToken[scene] !== token) return; // 已切换到其他提供商，丢弃过期响应
    models = data.models || [];
  } catch (e) {
    if (addModelToken[scene] !== token) return;
  }
  if (provider === "custom" || !models.length) {
    sel.classList.add("hidden");
    txt.classList.remove("hidden");
    txt.value = (provider === "custom"
      ? providersInfo.custom_api?.model
      : providersInfo.custom_models?.[provider]) || "";
    return;
  }
  // 用户曾手填过的模型名也放进可选列表
  const extra = providersInfo.custom_models?.[provider];
  if (extra && !models.includes(extra)) models = [...models, extra];
  sel.innerHTML = "";
  for (const m of models) {
    const opt = document.createElement("option");
    opt.value = m; opt.textContent = m;
    sel.appendChild(opt);
  }
}

// 整体替换某场景的模型池
async function savePool(scene, pool, okMsg) {
  const payload = {};
  payload[scene + "_models"] = pool;
  try {
    await postJSON("/api/settings", payload);
    await loadProviders();
    flashStatus(okMsg);
  } catch (e) { flashStatus(`❌ ${e.message}`, 4000); }
}

for (const scene of SCENES) {
  $("add-" + scene + "-provider").addEventListener("change", () => loadAddModelOptions(scene));

  $("pool-" + scene).addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn || btn.disabled) return;
    const p = btn.dataset.provider;
    const m = btn.dataset.model;
    if (btn.dataset.act === "use") {
      try {
        await saveActiveModel(scene, p, m);
        flashStatus(`✅ 已切换：${modelDisplay(p, m)}`);
      } catch (err) { flashStatus(`❌ ${err.message}`, 4000); }
    } else {
      const sc = sceneModelOf(scene);
      const pool = sc.models.filter((x) => !(x.provider === p && x.model === m));
      await savePool(scene, pool, "✅ 已移除");
    }
  });
}

// 添加按钮：把 (提供商, 模型) 追加进该功能的池
document.querySelectorAll("button[data-add]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const scene = btn.dataset.add;
    const provider = $("add-" + scene + "-provider").value;
    if (!provider) { flashStatus("请先选择提供商", 3000); return; }
    const sel = $("add-" + scene + "-model");
    const txt = $("add-" + scene + "-model-custom");
    const model = (sel.classList.contains("hidden") ? txt.value : sel.value).trim();
    const sc = sceneModelOf(scene);
    if (sc.models.some((x) => x.provider === provider && x.model === model)) {
      flashStatus("该模型已在列表中", 3000);
      return;
    }
    const pool = [...sc.models, { provider, model }];
    await savePool(scene, pool, "✅ 已添加");
  });
});

// ---------- 设置页：模型提供商（Key / Base URL 集中配置） ----------
const PROVIDER_ORDER = ["deepseek", "zhipu", "qwen", "custom", "ollama", "lmstudio"];

function keyTag(has) {
  return has ? '<span class="tag-ok">已配置</span>' : '<span class="tag-missing">未配置</span>';
}

function renderProviderConfig() {
  if (!providersInfo) return;
  const wrap = $("provider-config-list");
  wrap.innerHTML = "";
  for (const p of PROVIDER_ORDER) {
    const label = providersInfo.labels[p] || p;
    const card = document.createElement("div");
    card.className = "prov-item";
    let fields = "";
    if (providersInfo.cloud?.[p]) {
      const c = providersInfo.cloud[p];
      fields = `
        <label class="lbl">API Key ${keyTag(c.has_key)}</label>
        <input type="password" class="input" id="pk-${p}" placeholder="${c.has_key ? "已保存，填新值可替换" : "sk-..."}" autocomplete="off">
        <label class="lbl">Base URL</label>
        <input type="text" class="input" id="pu-${p}" value="${esc(c.base_url || "")}" spellcheck="false" autocomplete="off">`;
    } else if (p === "custom") {
      const c = providersInfo.custom_api || {};
      fields = `
        <label class="lbl">Base URL</label>
        <input type="text" class="input" id="pu-${p}" value="${esc(c.base_url || "")}" placeholder="https://api.example.com/v1" spellcheck="false" autocomplete="off">
        <label class="lbl">API Key ${keyTag(c.api_key)}</label>
        <input type="password" class="input" id="pk-${p}" placeholder="${c.api_key ? "已保存，填新值可替换" : "sk-..."}" autocomplete="off">
        <label class="lbl">默认模型名</label>
        <input type="text" class="input" id="pm-${p}" value="${esc(c.model || "")}" placeholder="模型 ID" spellcheck="false" autocomplete="off">`;
    } else {
      const u = (providersInfo.local_api || {})[p] || "";
      fields = `
        <label class="lbl">Base URL</label>
        <input type="text" class="input" id="pu-${p}" value="${esc(u)}" spellcheck="false" autocomplete="off">
        <div class="hint-line">本地服务，无需 API Key</div>`;
    }
    card.innerHTML =
      `<div class="prov-head"><span class="prov-name">${esc(label)}</span>` +
      `<button class="btn" data-prov="${p}">保存</button></div>` +
      fields +
      `<div class="hint-line" id="ps-${p}"></div>`;
    wrap.appendChild(card);
  }
}

$("provider-config-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-prov]");
  if (!btn) return;
  const p = btn.dataset.prov;
  const status = $("ps-" + p);
  const key = $("pk-" + p)?.value.trim();
  const url = $("pu-" + p)?.value.trim();
  const model = $("pm-" + p)?.value.trim();
  const payload = { provider: p };
  try {
    if (providersInfo.cloud?.[p]) {
      if (key) payload.cloud_api_key = key;
      if (url) payload.cloud_base_url = url;
      if (!payload.cloud_api_key && !payload.cloud_base_url) {
        status.textContent = "没有需要保存的修改";
        return;
      }
    } else if (p === "custom") {
      if (url) payload.custom_base_url = url;
      if (key) payload.custom_api_key = key;
      if (model !== undefined) payload.custom_model = model;
    } else {
      if (!url) { status.textContent = "没有需要保存的修改"; return; }
      payload[p + "_base_url"] = url; // ollama_base_url / lmstudio_base_url
    }
    btn.disabled = true;
    await postJSON("/api/settings", payload);
    if ($("pk-" + p)) $("pk-" + p).value = ""; // Key 不回显，保存后清空输入框
    await loadProviders();
    status.textContent = "✅ 已保存";
    setTimeout(() => { if (status.textContent === "✅ 已保存") status.textContent = ""; }, 2500);
  } catch (err) {
    status.textContent = `❌ ${err.message}`;
  } finally {
    btn.disabled = false;
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
  try { await loadProviders(); } catch (e) { console.error(e); }
  renderChatHistory();
  applyResearchDefaults();
  switchPage("chat");
  loadReports();
  loadKb();
  setInterval(checkServer, 30000);
})();
