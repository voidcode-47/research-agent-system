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
  function makeElement() {
    return {
      value: "", textContent: "", style: {}, disabled: false, children: [], listeners: new Map(),
      _html: "",
      set innerHTML(value) {
        this._html = value;
        if (this.className === "service-model-row") {
          const model = value.match(/value="([^"]*)"/)?.[1] || "";
          this.children = [Object.assign(makeElement(), { value: model, className: "input" }),
            Object.assign(makeElement(), { checked: value.includes(" checked") })];
        }
      },
      get innerHTML() { return this._html; },
      addEventListener(type, fn) {
        const listeners = this.listeners.get(type) || [];
        listeners.push(fn);
        this.listeners.set(type, listeners);
      },
      async dispatch(type, event = {}) {
        for (const listener of this.listeners.get(type) || []) await listener({
          preventDefault() {},
          target: this,
          ...event,
        });
      },
      appendChild(child) { this.children.push(child); return child; },
      replaceChildren(...children) { this.children = children; },
      querySelector(selector) {
        if (selector === ".input") return this.children[0];
        if (selector === 'input[type="radio"]') return this.children[1];
        return null;
      },
      focus() {},
      blur() {},
      setAttribute() {},
      scrollIntoView() {},
      showModal() { this.open = true; },
      close() { this.open = false; this.dispatch("close"); },
      classList: {
        add() {},
        remove() {},
        contains() { return false; },
        toggle() {},
      },
    };
  }
  for (const match of html.matchAll(/\bid="([^"]+)"/g)) {
    const element = makeElement();
    const tagStart = html.lastIndexOf("<", match.index);
    const tagEnd = html.indexOf(">", match.index);
    const className = html.slice(tagStart, tagEnd).match(/\bclass="([^"]*)"/)?.[1] || "";
    const classes = new Set(className.split(/\s+/).filter(Boolean));
    element.classList = {
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
      contains(name) { return classes.has(name); },
      toggle(name, enabled) {
        const on = enabled === undefined ? !classes.has(name) : enabled;
        if (on) classes.add(name); else classes.delete(name);
        return on;
      },
    };
    elements.set(match[1], element);
  }
  const context = vm.createContext({
    document: {
      getElementById(id) {
        assert.ok(elements.has(id), `Missing page element: ${id}`);
        return elements.get(id);
      },
      createElement() { return makeElement(); },
      querySelectorAll() { return []; },
    },
    window: { addEventListener() {} },
    fetch: () => new Promise(() => {}),
    Option: class {
      constructor(text, value) { this.textContent = text; this.value = value; }
    },
    confirm: () => true,
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

test("custom provider preset adds a model and saves a named default model", async () => {
  const { context, elements, run } = loadPage();
  const sent = [];
  const service = {
    id: "s1", name: "DeepSeek", protocol: "openai",
    base_url: "https://api.deepseek.com/v1", models: ["deepseek-chat", "deepseek-reasoner"],
    default_model: "deepseek-reasoner", has_api_key: false,
  };
  run("authInfo.can_manage_models = true");
  context.fetch = async (url, options = {}) => {
    const method = options.method || "GET";
    if (url === "/api/providers") {
      return { ok: true, json: async () => ({
        providers: ["custom:s1", "ollama", "lmstudio"],
        labels: { "custom:s1": "DeepSeek", ollama: "Ollama (本地)", lmstudio: "LM Studio (本地)" },
        current_provider: "custom:s1", current_model: "deepseek-reasoner",
        effective_provider: "custom:s1", effective_model: "deepseek-reasoner",
        custom_services: [service], local_api: { ollama: "http://localhost:11434", lmstudio: "http://localhost:1234" },
        research_prefs: { max_results: 8, detailed: true }, read_only: false,
      }) };
    }
    if (url === "/api/settings" && method === "POST") return { ok: true, json: async () => ({ ok: true }) };
    if (url === "/api/custom-providers" && method === "POST") {
      sent.push(JSON.parse(options.body));
      return { ok: true, json: async () => service };
    }
    return { ok: true, json: async () => ({}) };
  };
  elements.get("custom-provider-preset").value = "deepseek";
  await elements.get("custom-provider-preset").dispatch("change");
  assert.equal(elements.get("custom-provider-url").value, "https://api.deepseek.com/v1");

  run("openCustomProvider()");
  elements.get("custom-provider-preset").value = "deepseek";
  await elements.get("custom-provider-preset").dispatch("change");
  elements.get("custom-provider-key").value = "user-entered-key";
  const rows = elements.get("custom-provider-models");
  rows.children[0].querySelector(".input").value = "deepseek-chat";
  await elements.get("btn-add-service-model").dispatch("click");
  rows.children[1].querySelector(".input").value = "deepseek-reasoner";
  rows.children[0].querySelector('input[type="radio"]').checked = false;
  rows.children[1].querySelector('input[type="radio"]').checked = true;
  await elements.get("custom-provider-form").dispatch("submit");

  assert.deepEqual(sent, [{
    name: "DeepSeek", protocol: "openai", base_url: "https://api.deepseek.com/v1",
    api_key: "user-entered-key", models: ["deepseek-chat", "deepseek-reasoner"],
    default_model: "deepseek-reasoner",
  }]);
  assert.equal(elements.get("custom-provider-key").value, "");
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

test("paper Q&A evidence opens the source paragraph in the rewrite step", async () => {
  const { context, run } = loadPage();
  const calls = [];
  run('paperState.paperId = "paper-1"');
  context.pwLoadParagraphs = async () => calls.push("load");
  context.pwSelectParagraph = async (id) => {
    calls.push(`select:${id}`);
    run(`paperState.selected = { id: "${id}" }`);
  };
  context.pwSetStep = (step, force) => calls.push(`step:${step}:${force}`);

  await run('pwAskOpenBasis("p_0006")');

  assert.deepEqual(calls, ["load", "select:p_0006", "step:5:true"]);
});

test("paper Q&A renders deduplicated and safely escaped source buttons", () => {
  const { run } = loadPage();

  const markup = run('pwRenderAskBasis(["p_0006", "p_0006", "x\\" onclick=\\"alert(1)"])');

  assert.equal((markup.match(/data-ask-basis=/g) || []).length, 2);
  assert.ok(markup.includes('data-ask-basis="p_0006"'));
  assert.ok(markup.includes("&quot; onclick=&quot;"));
  assert.ok(!markup.includes('data-ask-basis="x" onclick='));
});

test("history reports expose preview and download actions", async () => {
  const { context, elements } = loadPage();
  context.api = async () => ({ reports: [{ name: '报告 "A".md' }] });

  await context.loadReports();

  const markup = elements.get("report-list").children[0].innerHTML;
  assert.match(markup, /data-act="preview"/);
  assert.match(markup, /data-act="dl"/);
  assert.match(markup, /报告 &quot;A&quot;\.md/);
});

test("history preview action opens the selected report", async () => {
  const { context, elements } = loadPage();
  context.fetch = async () => ({ ok: true, text: async () => "# Report" });
  const button = { dataset: { act: "preview", name: "report.md" } };

  await elements.get("report-list").dispatch("click", {
    target: { closest: () => button },
  });

  assert.equal(elements.get("report-preview-dialog").open, true);
  assert.equal(elements.get("report-preview-title").textContent, "report.md");
});

test("history preview fetches and safely renders the selected report", async () => {
  const { context, elements, run } = loadPage();
  run('currentReport = { name: "current.md", text: "current report" }');
  const requested = [];
  context.fetch = async (url) => {
    requested.push(url);
    return {
      ok: true,
      text: async () => "# Historical report\n\n<script>not executable</script>\n\n**Findings**",
    };
  };

  await context.previewReport("报告 A.md");

  assert.deepEqual(requested, ["/api/reports/%E6%8A%A5%E5%91%8A%20A.md"]);
  assert.equal(elements.get("report-preview-dialog").open, true);
  assert.equal(elements.get("report-preview-title").textContent, "报告 A.md");
  assert.ok(elements.get("report-preview-body").innerHTML.includes("&lt;script&gt;"));
  assert.ok(elements.get("report-preview-body").innerHTML.includes("<strong>Findings</strong>"));
  assert.equal(run("currentReport.name"), "current.md");
});

test("history preview reports fetch errors inside the dialog", async () => {
  const { context, elements } = loadPage();
  context.fetch = async () => ({
    ok: false,
    status: 404,
    json: async () => ({ detail: "报告不存在" }),
  });

  await context.previewReport("missing.md");

  assert.equal(elements.get("report-preview-dialog").open, true);
  assert.equal(elements.get("report-preview-status").textContent, "预览失败：报告不存在");
});

test("history preview download buttons target the previewed report", async () => {
  const { context, elements } = loadPage();
  const anchors = [];
  context.document.body = { appendChild() {} };
  context.document.createElement = () => {
    const anchor = { click() {}, remove() {} };
    anchors.push(anchor);
    return anchor;
  };
  context.fetch = async () => ({ ok: true, text: async () => "# Report" });

  await context.previewReport("preview.md");
  await elements.get("btn-report-preview-md").dispatch("click");
  await elements.get("btn-report-preview-docx").dispatch("click");

  assert.deepEqual(anchors.map((anchor) => anchor.href), [
    "/api/reports/preview.md",
    "/api/reports/preview.md?format=docx",
  ]);
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
