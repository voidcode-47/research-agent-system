"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// 加载真实脚本；仅替换浏览器和网络边界，不复制业务函数。
function loadPage() {
  const web = path.join(__dirname, "..", "web");
  const html = fs.readFileSync(path.join(web, "index.html"), "utf8");
  const elements = new Map();
  for (const match of html.matchAll(/\bid="([^"]+)"/g)) {
    const classes = new Set();
    elements.set(match[1], {
      value: "8", textContent: "", innerHTML: "", style: {}, disabled: false,
      addEventListener() {},
      classList: {
        add(name) { classes.add(name); },
        remove(name) { classes.delete(name); },
        contains(name) { return classes.has(name); },
        toggle(name, enabled) {
          const on = enabled === undefined ? !classes.has(name) : enabled;
          if (on) classes.add(name); else classes.delete(name);
          return on;
        },
      },
    });
  }
  const context = vm.createContext({
    document: {
      getElementById(id) {
        assert.ok(elements.has(id), `Missing page element: ${id}`);
        return elements.get(id);
      },
      querySelectorAll() { return []; },
    },
    window: { addEventListener() {} },
    fetch: () => new Promise(() => {}),
    setTimeout: () => 0,
    clearTimeout() {},
    setInterval: () => 0,
    console,
  });
  for (const script of ["app.js", "paper.js"]) {
    vm.runInContext(fs.readFileSync(path.join(web, script), "utf8"), context, { filename: script });
  }
  return { context, elements, run: (code) => vm.runInContext(code, context) };
}

test("progress clamps percentages and resets when hidden", () => {
  const { context, elements } = loadPage();
  for (const [input, expected] of [[-5, 0], [50.6, 51], [110, 100], [NaN, 0]]) {
    context.pwTaskProgress("paper-diag", input, "done");
    assert.equal(elements.get("paper-diag-fill").style.width, `${expected}%`);
    assert.equal(elements.get("paper-diag-percent").textContent, `${expected}%`);
    assert.equal(elements.get("paper-diag-stage").textContent, "已完成");
    assert.equal(elements.get("paper-diag-progress").classList.contains("hidden"), false);
  }
  context.pwTaskProgressHide("paper-diag");
  assert.equal(elements.get("paper-diag-progress").classList.contains("hidden"), true);
  assert.equal(elements.get("paper-diag-fill").style.width, "0%");
});

test("queued tasks display a waiting label", () => {
  const { context } = loadPage();
  assert.equal(context.pwStageText("queued"), "排队中");
  assert.equal(context.pwStageText("running"), "处理中");
});

test("guideline progress preserves status and cancellation controls", () => {
  const { context, elements } = loadPage();
  context.pwGuideProgress(37.5, "正在分析术语");
  assert.equal(elements.get("paper-guide-fill").style.width, "38%");
  assert.equal(elements.get("paper-guide-stage").textContent, "正在分析术语");
  assert.equal(elements.get("paper-guide-status").textContent, "正在分析术语");
  elements.get("btn-paper-guide-cancel").disabled = true;
  context.pwGuideProgressHide();
  assert.equal(elements.get("paper-guide-progress").classList.contains("hidden"), true);
  assert.equal(elements.get("paper-guide-percent").textContent, "0%");
  assert.equal(elements.get("btn-paper-guide-cancel").disabled, false);
});

test("diff choices reconstruct either original or adopted text", () => {
  const { run } = loadPage();
  run(`paperState.diff = pwGroupDiff([
    {type: "equal", text: "研究"},
    {type: "delete", text: "非常重要"},
    {type: "insert", text: "比较了三种方法"},
    {type: "equal", text: "。"},
    {type: "insert", text: "误差为 2%。"}
  ]);`);
  assert.equal(run("pwBuildRevised()"), "研究比较了三种方法。误差为 2%。");
  run("paperState.diff.groups[0].keep = false");
  assert.equal(run("pwBuildRevised()"), "研究非常重要。误差为 2%。");
  run("pwSetAllDiff(false)");
  assert.equal(run("pwBuildRevised()"), "研究非常重要。");
  run("pwSetAllDiff(true)");
  assert.equal(run("pwBuildRevised()"), "研究比较了三种方法。误差为 2%。");
});

test("exports use server filenames including Chinese RFC 5987 names", () => {
  const { context } = loadPage();
  const response = (value) => ({ headers: { get: () => value } });
  assert.equal(context.pwExportFilename(response("attachment; filename*=UTF-8''" + encodeURIComponent("论文_优化版.docx")), "docx"), "论文_优化版.docx");
  assert.equal(context.pwExportFilename(response('attachment; filename="paper.pdf"'), "pdf"), "paper.pdf");
  assert.equal(context.pwExportFilename(response(""), "summary"), "论文修改说明.docx");
  assert.equal(context.pwExportFilename(response(""), "zip", "论文_备份.zip"), "论文_备份.zip");
});

test("Markdown escapes markup and refuses executable links", () => {
  const { context } = loadPage();
  const rendered = context.renderMarkdown('<script>alert(1)</script>\n[bad](javascript:alert(1))\n[ok](https://example.com)');
  assert.ok(rendered.includes("&lt;script&gt;"));
  assert.ok(!rendered.includes("<script>"));
  assert.ok(!rendered.includes('href="javascript:'));
  assert.ok(rendered.includes('href="https://example.com"'));
});

test("Markdown renders quoted text with escaping", () => {
  const { context } = loadPage();
  assert.equal(context.renderMarkdown("> 引用 <b>内容</b>"), "<blockquote>引用 &lt;b&gt;内容&lt;/b&gt;</blockquote>");
});

test("model display follows effective server configuration", () => {
  const { run } = loadPage();
  run(`providersInfo = {
    current_provider: "", current_model: "",
    effective_provider: "deepseek", effective_model: "deepseek-chat",
    custom_api: { api_key: true, model: "old-custom-model" }
  };`);
  assert.equal(run("currentProviderModel().provider"), "deepseek");
  assert.equal(run("currentProviderModel().model"), "deepseek-chat");
});

test("adopting a rewrite refreshes the version in the paper selector", async () => {
  const { context, elements, run } = loadPage();
  const selector = elements.get("paper-select");
  selector.options = [];
  selector.appendChild = (option) => selector.options.push(option);
  context.document.createElement = () => ({});
  let sent = null;
  context.api = async (url, opts) => {
    if (url.endsWith("/apply")) {
      sent = JSON.parse(opts.body);
      return { version: 1, paragraph_id: "p_0003" };
    }
    return { papers: [
      { id: "paper-id", title: "测试论文", current_version: 1, paragraph_count: 11 },
    ] };
  };
  context.pwLoadPaper = async () => {};
  context.pwSelectParagraph = async () => {};
  context.pwRenderBatchResults = () => {};
  context.pwSetStep = () => {};
  context.pwAlert = () => {};
  run(`paperState.paperId = "paper-id";
    paperState.selected = { id: "p_0003" };
    paperState.rewrite = { paragraph_id: "p_0003", original: "旧正文", source_version: 0 };
    paperState.diff = pwGroupDiff([{type: "insert", text: "新正文"}]);`);

  await context.pwAdopt();

  assert.equal(selector.options.length, 1);
  assert.equal(selector.options[0].textContent, "测试论文（v1 · 11 段）");
  assert.equal(selector.value, "paper-id");
  assert.equal(sent.expected_version, 0);
  assert.equal(sent.expected_original, "旧正文");
});

test("adoption conflict keeps the local diff and refreshes version state", async () => {
  const { context, elements, run } = loadPage();
  const conflict = new Error("该段落已在论文中修改，改写结果基于旧正文，未保存。");
  conflict.status = 409;
  conflict.payload = {
    detail: conflict.message, conflict: true, reason: "paragraph_changed",
    current_version: 2, expected_version: 0,
  };
  context.api = async (url) => {
    if (url.endsWith("/apply")) throw conflict;
    return { current_version: 2, version_count: 3 };
  };
  context.pwLoadList = async () => {};
  const alerts = [];
  context.pwAlert = (msg, kind) => alerts.push({ msg, kind });
  run(`paperState.paperId = "paper-id";
    paperState.paper = { current_version: 0 };
    paperState.selected = { id: "p_0003" };
    paperState.rewrite = { paragraph_id: "p_0003", original: "旧正文", source_version: 0 };
    paperState.diff = pwGroupDiff([{type: "insert", text: "新正文"}]);`);

  await context.pwAdopt();

  assert.equal(run("!!paperState.diff"), true);
  assert.equal(run("paperState.rewrite.paragraph_id"), "p_0003");
  assert.ok(elements.get("paper-version").textContent.includes("v2"));
  assert.ok(alerts.some((a) => a.msg.includes("论文已更新")));
  assert.equal(elements.get("btn-paper-adopt").disabled, false);
});

test("batch adoption sends one atomic request and clears adopted results", async () => {
  const { context, elements, run } = loadPage();
  let sent = null;
  context.api = async (url, opts) => {
    if (url.endsWith("/apply-batch")) {
      sent = JSON.parse(opts.body);
      return { version: 4, paragraph_ids: ["p_0001", "p_0002"] };
    }
    throw new Error(`Unexpected request: ${url}`);
  };
  context.pwLoadList = async () => {};
  context.pwLoadPaper = async () => {};
  context.pwRenderBatchResults = () => {};
  context.pwSetStep = () => {};
  const alerts = [];
  context.pwAlert = (msg, kind) => alerts.push({ msg, kind });
  run(`paperState.paperId = "paper-id";
    paperState.batchResults = [
      { paragraph_id: "p_0001", revised: "新一", original: "旧一", source_version: 0 },
      { paragraph_id: "p_0002", revised: "新二", original: "旧二", source_version: 0 }
    ];
    paperState.batchIds = new Set(["p_0001", "p_0002"]);`);

  await context.pwAdoptBatch();

  assert.equal(sent.items.length, 2);
  assert.deepEqual(sent.items[0], {
    paragraph_id: "p_0001", revised: "新一",
    expected_version: 0, expected_original: "旧一",
  });
  assert.equal(run("paperState.batchResults.length"), 0);
  assert.equal(run("paperState.batchIds.size"), 0);
  assert.equal(elements.get("btn-paper-batch-adopt").classList.contains("hidden"), true);
  assert.ok(alerts.some((a) => a.msg.includes("已批量采纳 2 段") && a.msg.includes("v4")));
});

test("batch adoption conflict keeps all results and refreshes the version", async () => {
  const { context, run } = loadPage();
  const conflict = new Error("段落 p_0002 已在论文中修改，全部未保存。");
  conflict.status = 409;
  conflict.payload = {
    detail: conflict.message, conflict: true, reason: "paragraph_changed",
    current_version: 5, expected_version: 0,
  };
  context.api = async (url) => {
    if (url.endsWith("/apply-batch")) throw conflict;
    return { current_version: 5, version_count: 6 };
  };
  context.pwLoadList = async () => {};
  const alerts = [];
  context.pwAlert = (msg, kind) => alerts.push({ msg, kind });
  run(`paperState.paperId = "paper-id";
    paperState.batchResults = [
      { paragraph_id: "p_0001", revised: "新一", original: "旧一", source_version: 0 },
      { paragraph_id: "p_0002", revised: "新二", original: "旧二", source_version: 0 }
    ];
    paperState.batchIds = new Set(["p_0001", "p_0002"]);`);

  await context.pwAdoptBatch();

  assert.equal(run("paperState.batchResults.length"), 2);
  assert.equal(run("paperState.batchIds.size"), 2);
  assert.ok(alerts.some((a) => a.msg.includes("批量采纳未保存") && a.msg.includes("v5")));
});

test("paper list search and load-more use server pagination", async () => {
  const { context, elements, run } = loadPage();
  const selector = elements.get("paper-select");
  selector.options = [];
  selector.value = "";
  selector.appendChild = (option) => selector.options.push(option);
  elements.get("paper-search").value = "神经";
  context.document.createElement = () => ({});
  const urls = [];
  context.api = async (url) => {
    urls.push(url);
    if (url.includes("offset=0")) {
      return {
        papers: [
          { id: "p_000000000001", title: "神经网络 A", current_version: 1, paragraph_count: 2 },
          { id: "p_000000000002", title: "神经网络 B", current_version: 0, paragraph_count: 3 },
        ],
        total: 3, offset: 0, limit: 50, has_more: true,
      };
    }
    return {
      papers: [
        { id: "p_000000000003", title: "神经网络 C", current_version: 2, paragraph_count: 1 },
      ],
      total: 3, offset: 2, limit: 50, has_more: false,
    };
  };

  await run("pwLoadList({ reset: true })");

  assert.equal(urls[0], "/api/paper?offset=0&limit=50&q=%E7%A5%9E%E7%BB%8F");
  assert.equal(selector.options.length, 2);
  assert.equal(elements.get("btn-paper-more").classList.contains("hidden"), false);
  assert.equal(elements.get("paper-list-count").textContent, "匹配 3 篇，已显示 2 篇");

  await run("pwLoadList({ reset: false })");

  assert.equal(urls[1], "/api/paper?offset=2&limit=50&q=%E7%A5%9E%E7%BB%8F");
  assert.equal(selector.options.length, 3);
  assert.equal(elements.get("btn-paper-more").classList.contains("hidden"), true);
  assert.equal(elements.get("paper-list-count").textContent, "匹配 3 篇");
});

test("paper list keeps the current paper visible outside the search results", async () => {
  const { context, elements, run } = loadPage();
  const selector = elements.get("paper-select");
  selector.options = [];
  selector.value = "";
  selector.appendChild = (option) => selector.options.push(option);
  elements.get("paper-search").value = "找不到";
  context.document.createElement = () => ({});
  context.api = async () => ({
    papers: [], total: 0, offset: 0, limit: 50, has_more: false,
  });
  run(`paperState.paperId = "p_000000000009";
    paperState.paper = { id: "p_000000000009", title: "当前论文", current_version: 4, paragraph_count: 6 };`);

  await run("pwLoadList({ reset: true })");

  assert.equal(selector.options.length, 1);
  assert.equal(selector.options[0].value, "p_000000000009");
  assert.ok(selector.options[0].textContent.includes("当前"));
  assert.equal(selector.value, "p_000000000009");
  assert.equal(elements.get("paper-list-count").textContent, "无匹配论文");
});

test("task watcher consumes SSE and closes on the terminal frame", async () => {
  const { context, run } = loadPage();
  let source = null;
  context.window.EventSource = class {
    constructor(url) { this.url = url; source = this; }
    addEventListener(name, fn) { this[name] = fn; }
    close() { this.closed = true; }
  };
  const result = run(`new Promise((resolve, reject) => {
    pwWatchTask("p_test", "task-1", {
      statusUrl: "/fallback",
      onView: (t) => { if (t.status === "done") resolve(t.status); return t.status === "done"; },
      onError: reject,
    });
  })`);

  assert.equal(source.url, "/api/paper/p_test/tasks/task-1/events");
  source.onmessage({ data: JSON.stringify({ status: "running" }) });
  source.onmessage({ data: JSON.stringify({ status: "done" }) });
  source.end();

  assert.equal(await result, "done");
  assert.equal(source.closed, true);
});

test("task watcher falls back to status polling when SSE errors", async () => {
  const { context, run } = loadPage();
  let source = null;
  context.window.EventSource = class {
    constructor() { source = this; }
    addEventListener() {}
    close() { this.closed = true; }
  };
  context.api = async () => ({ status: "done", logs: [] });
  const result = run(`new Promise((resolve, reject) => {
    pwWatchTask("p_test", "task-2", {
      statusUrl: "/fallback",
      interval: 1000,
      onView: (t) => { resolve(t.status); return true; },
      onError: reject,
    });
  })`);

  source.onerror();
  assert.equal(source.closed, true);
  assert.equal(await result, "done");
});

test("api errors expose status and payload for conflict handling", async () => {
  const { context } = loadPage();
  context.fetch = async () => ({
    ok: false,
    status: 409,
    json: async () => ({ detail: "冲突", conflict: true, current_version: 2 }),
  });
  await assert.rejects(() => context.api("/conflict"), (err) => {
    assert.equal(err.status, 409);
    assert.equal(err.message, "冲突");
    assert.equal(err.payload.current_version, 2);
    return true;
  });
});
