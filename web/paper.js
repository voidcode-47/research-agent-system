/* =========================================================
 * 论文诊断与优化工作台：五步向导（上传 / 需求 / 诊断 / 相似 / 改写）
 * 依赖 app.js 暴露的 $ / esc / api / postJSON / renderMarkdown
 * ========================================================= */
"use strict";

const paperState = {
  inited: false,
  options: null,
  paperId: "",
  paper: null,
  maxStep: 1,
  selected: null,
  rewrite: null,
  diff: null,
  guideline: null,
  sections: [],
  paragraphs: [],
  tables: [],
  diagTimer: null,
  simTimer: null,
  guideTimer: null,
  guideTaskId: "",
  guidePaperId: "",
  diagPaperId: "",
  diagTaskId: "",
  uploadTimer: null,
  uploadXhr: null,
  uploadTaskId: "",
  uploadPaperId: "",
  similarTaskId: "",
  rewriteTimer: null,
  rewriteTaskId: "",
  rewritePaperId: "",
  rewriteParaId: "",
  batchTimer: null,
  batchTaskId: "",
  batchPaperId: "",
  batchResults: [],
  batchErrors: [],
  batchIds: new Set(),
  highRiskIds: [],
  paragraphSuggestions: new Map(),
  instructionPid: "",
  instructionFromIssue: false,
  refs: null,
  refsPaperId: "",
  refsSeq: 0,
  askDockBound: false,
  listQuery: "",
  listOffset: 0,
  listLimit: 50,
  listTotal: 0,
  listHasMore: false,
  listSeq: 0,
  loadSeq: 0,
  paragraphSeq: 0,
  askSeq: 0,
  adoptionSeq: 0,
  listTimer: null,
  taskSources: new Set(),
};

const PAPER_DIM_LABELS = {
  level: "论文层次",
  feasibility: "内容可实现性",
  format: "正文格式",
  deai: "AI 率",
};

const PAPER_DIM_ORDER = ["level", "feasibility", "format", "deai"];

const PAPER_MODE_LABELS = {
  deai: "去 AI 味",
  polish: "学术润色",
  tighten: "精简压缩",
};

// ---------- 通用 ----------
function pwAlert(msg, kind, step) {
  // step 用于异步回调：只在对应步骤仍处于激活状态时展示，避免旧步骤提示串到新步骤
  if (msg && step != null && step !== paperState.step) return;
  const el = $("paper-alert");
  el.textContent = msg || "";
  el.className = "paper-alert" + (kind ? " " + kind : "");
  el.classList.toggle("hidden", !msg);
}

function pwHideAlert() {
  $("paper-alert").classList.add("hidden");
}

function pwRewriteLimit() {
  return Number((paperState.options || {}).rewrite_max_chars) || 2000;
}

function pwSetStep(n, force) {
  if (!force && n > paperState.maxStep) return;
  paperState.step = n;
  document.querySelectorAll(".paper-step").forEach((b) => {
    const s = Number(b.dataset.step);
    b.disabled = s > paperState.maxStep;
    b.setAttribute("aria-current", s === n ? "step" : "false");
    b.classList.toggle("active", s === n);
    b.classList.toggle("done", s < paperState.maxStep);
  });
  document.querySelectorAll(".paper-step-panel").forEach((p) => p.classList.toggle("active", p.id === `paper-step-${n}`));
  if (n === 3) pwLoadReferences();
  if (n === 4) pwLoadSimilar();
  if (n === 5) pwLoadParagraphs();
  pwSaveLastState();
}

function pwFillSelect(el, items, opts) {
  const o = opts || {};
  el.innerHTML = "";
  if (o.placeholder) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = o.placeholder;
    el.appendChild(opt);
  }
  (items || []).forEach((it) => {
    const opt = document.createElement("option");
    opt.value = it.value;
    opt.textContent = it.label;
    el.appendChild(opt);
  });
}

function pwQuotaText(q) {
  if (!q) return "";
  if (q.unlimited) {
    return `管理员账号：今日已用 ${q.used} 次，不限额度`;
  }
  const reserved = Number(q.reserved || 0);
  const suffix = reserved > 0 ? `（任务预留 ${reserved} 次）` : "";
  return `今日 LLM 配额：剩余 ${q.remaining} / 共 ${q.limit} 次${suffix}`;
}

function reloadPaperAuthState() {
  pwRefreshQuota();
}

function pwRefreshQuota() {
  api("/api/paper/options").then((opts) => {
    paperState.options = opts;
    $("paper-quota").textContent = pwQuotaText(opts.quota);
  }).catch(() => {});
}

function pwClearTimers() {
  if (paperState.diagTimer) { clearTimeout(paperState.diagTimer); paperState.diagTimer = null; }
  if (paperState.simTimer) { clearTimeout(paperState.simTimer); paperState.simTimer = null; }
  if (paperState.guideTimer) { clearTimeout(paperState.guideTimer); paperState.guideTimer = null; }
  if (paperState.uploadTimer) { clearTimeout(paperState.uploadTimer); paperState.uploadTimer = null; }
  if (paperState.rewriteTimer) { clearTimeout(paperState.rewriteTimer); paperState.rewriteTimer = null; }
  if (paperState.batchTimer) { clearTimeout(paperState.batchTimer); paperState.batchTimer = null; }
  paperState.taskSources.forEach((source) => {
    try { source.close(); } catch (e) { /* ignore */ }
  });
  paperState.taskSources.clear();
}

// 任务状态优先走 SSE；不支持或连接失败时回退到单次状态查询轮询。
function pwWatchTask(paperId, taskId, opts) {
  const terminal = new Set(["done", "error", "cancelled"]);
  const interval = opts.interval || 1200;
  let stopped = false;
  let fallbackStarted = false;
  let timer = null;
  let source = null;
  let chain = Promise.resolve();

  const stop = () => {
    if (stopped) return;
    stopped = true;
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    if (source) {
      paperState.taskSources.delete(source);
      try { source.close(); } catch (e) { /* ignore */ }
      source = null;
    }
    if (opts.timerKey) paperState[opts.timerKey] = null;
  };

  const handle = (view) => {
    if (stopped) return;
    if (opts.isCurrent && !opts.isCurrent()) { stop(); return; }
    const isTerminal = terminal.has(view.status);
    chain = chain
      .then(() => opts.onView(view))
      .catch((e) => {
        stop();
        if (opts.onError) opts.onError(e);
      })
      .finally(() => {
        if (isTerminal) stop();
      });
  };

  const fallback = () => {
    if (fallbackStarted) return;
    fallbackStarted = true;
    stop();
    stopped = false;
    poll();
  };

  const poll = async () => {
    if (stopped) return;
    try {
      const view = await api(opts.statusUrl);
      if (stopped) return;
      if (opts.isCurrent && !opts.isCurrent()) { stop(); return; }
      chain = chain.then(() => opts.onView(view));
      const isTerminal = terminal.has(view.status);
      await chain;
      if (isTerminal) { stop(); return; }
      if (!stopped) {
        timer = setTimeout(poll, interval);
        if (opts.timerKey) paperState[opts.timerKey] = timer;
      }
    } catch (e) {
      stop();
      if (opts.onError) opts.onError(e);
    }
  };

  if (!paperId || !taskId || typeof window.EventSource !== "function") {
    poll();
    return;
  }

  try {
    const base = `/api/paper/${encodeURIComponent(paperId)}/tasks/${encodeURIComponent(taskId)}/events`;
    source = new window.EventSource(base);
    paperState.taskSources.add(source);
    source.onmessage = (event) => {
      try { handle(JSON.parse(event.data)); } catch (e) { /* ignore malformed frame */ }
    };
    source.addEventListener("end", () => stop());
    source.onerror = () => {
      if (stopped) return;
      fallback();
    };
  } catch (e) {
    fallback();
  }
}

// ---------- 通用任务进度 ----------
const PAPER_STAGE_LABELS = {
  queued: "排队中",
  starting: "准备中",
  init: "准备中",
  parse: "解析中",
  search: "检索中",
  rewrite: "改写中",
  batch: "批量改写中",
  running: "处理中",
  done: "已完成",
  cancelling: "正在取消",
  cancelled: "已取消",
  error: "失败",
};

function pwStageText(stage) {
  const raw = String(stage || "");
  return PAPER_STAGE_LABELS[raw] || raw;
}

function pwTaskProgress(prefix, percent, stage) {
  const box = $(`${prefix}-progress`);
  if (!box) return;
  box.classList.remove("hidden");
  const p = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
  const fill = $(`${prefix}-fill`);
  const pct = $(`${prefix}-percent`);
  const st = $(`${prefix}-stage`);
  if (fill) fill.style.width = p + "%";
  if (pct) pct.textContent = p + "%";
  if (st && stage) st.textContent = pwStageText(stage);
}

function pwTaskProgressHide(prefix) {
  const box = $(`${prefix}-progress`);
  if (box) box.classList.add("hidden");
  const fill = $(`${prefix}-fill`);
  if (fill) fill.style.width = "0%";
  const pct = $(`${prefix}-percent`);
  if (pct) pct.textContent = "0%";
}

// ---------- 上次进度 ----------
const PAPER_LAST_KEY = "paper.lastState";

function pwSaveLastState() {
  if (!paperState.paperId) return;
  try {
    localStorage.setItem(PAPER_LAST_KEY, JSON.stringify({
      paperId: paperState.paperId,
      step: paperState.step || 1,
    }));
  } catch (e) { /* 本地存储不可用时忽略 */ }
}

function pwReadLastState() {
  try {
    const data = JSON.parse(localStorage.getItem(PAPER_LAST_KEY) || "null");
    return data && data.paperId ? data : null;
  } catch (e) {
    return null;
  }
}

function pwClearLastState() {
  try { localStorage.removeItem(PAPER_LAST_KEY); } catch (e) { /* ignore */ }
}

function pwDiagCancelReset() {
  const btn = $("btn-paper-cancel");
  if (!btn) return;
  btn.classList.add("hidden");
  btn.disabled = false;
  btn.textContent = "取消诊断";
}

// ---------- 初始化 ----------
async function initPaperPage() {
  if (paperState.inited) return;
  paperState.inited = true;

  $("btn-paper-upload").addEventListener("click", () => pwUpload().catch(pwError));
  $("paper-file").addEventListener("change", pwUpdateFileChoice);
  $("paper-select").addEventListener("change", (e) => {
    if (e.target.value) pwLoadPaper(e.target.value).catch(pwError);
    else pwResetPaper();
  });
  $("paper-search").addEventListener("input", () => {
    clearTimeout(paperState.listTimer);
    paperState.listTimer = setTimeout(() => {
      pwLoadList({ reset: true }).catch(pwError);
    }, 250);
  });
  $("btn-paper-more").addEventListener("click", () => {
    pwLoadList({ reset: false }).catch(pwError);
  });
  $("btn-paper-refresh").addEventListener("click", () => {
    pwLoadList().catch(pwError);
    if (paperState.paperId) pwLoadPaper(paperState.paperId).catch(pwError);
  });
  $("btn-paper-new").addEventListener("click", () => pwNewPaper());
  $("btn-paper-restore").addEventListener("click", () => $("paper-restore-file").click());
  $("paper-restore-file").addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (file) pwRestoreBackup(file).catch(pwError);
  });
  $("btn-paper-delete").addEventListener("click", () => pwDelete().catch(pwError));
  $("btn-paper-backup").addEventListener("click", () => pwBackup().catch(pwError));
  $("btn-paper-cleanup").addEventListener("click", () => pwCleanupVersions().catch(pwError));
  $("btn-paper-confirm").addEventListener("click", () => pwConfirm().catch(pwError));
  $("btn-paper-upload-cancel").addEventListener("click", () => pwCancelUpload().catch(pwError));
  $("btn-paper-diagnose").addEventListener("click", () => pwStartDiagnose().catch(pwError));
  $("btn-paper-cancel").addEventListener("click", () => pwCancelDiagnose().catch(pwError));
  $("btn-paper-refs-check").addEventListener("click", () => pwLoadReferences(true).catch(pwError));
  $("btn-paper-refs-apply").addEventListener("click", () => pwApplyReferences().catch(pwError));
  $("btn-paper-similar").addEventListener("click", () => pwStartSimilar(false).catch(pwError));
  $("btn-paper-similar-refresh").addEventListener("click", () => pwStartSimilar(true).catch(pwError));
  $("btn-paper-similar-cancel").addEventListener("click", () => pwCancelSimilar().catch(pwError));
  $("paper-section-filter").addEventListener("change", () => pwLoadParagraphs().catch(pwError));
  $("btn-paper-rewrite").addEventListener("click", () => pwRewrite().catch(pwError));
  $("btn-paper-rewrite-cancel").addEventListener("click", () => pwCancelRewrite().catch(pwError));
  $("paper-instruction").addEventListener("input", () => {
    // 用户改动过即视为手写要求，不再随段落切换自动替换
    paperState.instructionFromIssue = false;
    paperState.instructionPid = "";
  });
  $("btn-paper-batch").addEventListener("click", () => pwBatchRewrite().catch(pwError));
  $("btn-paper-batch-adopt").addEventListener("click", () => pwAdoptBatch().catch(pwError));
  $("btn-paper-batch-clear").addEventListener("click", () => pwClearBatchSelection());
  $("btn-paper-batch-cancel").addEventListener("click", () => pwCancelBatch().catch(pwError));
  $("paper-batch-results").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-batch-pid]");
    if (!btn) return;
    const item = (paperState.batchResults || []).find((r) => r.paragraph_id === btn.dataset.batchPid);
    if (!item) return;
    pwShowBatchResult(item).catch(pwError);
  });
  $("btn-paper-export-summary").addEventListener("click", () => pwExport("summary"));
  $("btn-paper-guide").addEventListener("click", () => pwGenerateGuideline().catch(pwError));
  $("btn-paper-guide-cancel").addEventListener("click", () => pwCancelGuideline().catch(pwError));
  $("btn-paper-adopt").addEventListener("click", () => pwAdopt().catch(pwError));
  $("paper-diff").addEventListener("click", (e) => {
    const el = e.target.closest(".diff-change");
    if (!el || !paperState.diff) return;
    const block = paperState.diff.groups[Number(el.dataset.ci)];
    if (!block) return;
    block.keep = !block.keep;
    pwSyncDiffUI();
  });
  $("btn-diff-keep-all").addEventListener("click", () => pwSetAllDiff(true));
  $("btn-diff-revert-all").addEventListener("click", () => pwSetAllDiff(false));
  $("btn-paper-export").addEventListener("click", () => pwExport("docx"));
  $("btn-paper-export-pdf").addEventListener("click", () => pwExport("pdf"));
  $("btn-paper-export-tracked").addEventListener("click", () => pwExport("docx", true));
  $("btn-paper-ask").addEventListener("click", () => pwAsk().catch(pwError));
  $("paper-ask-answer").addEventListener("click", (e) => {
    const source = e.target.closest("[data-ask-basis]");
    if (source) {
      pwAskOpenBasis(source.dataset.askBasis, source.dataset.askKind || "").catch(pwError);
    }
  });
  $("btn-paper-table-close").addEventListener("click", pwCloseTable);
  $("paper-table-modal").addEventListener("click", (e) => {
    if (e.target === $("paper-table-modal")) pwCloseTable();
  });
  $("paper-ask-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") pwAsk().catch(pwError);
  });
  if (!paperState.askDockBound) {
    paperState.askDockBound = true;
    $("btn-ask-toggle").addEventListener("click", (e) => {
      e.stopPropagation();
      pwAskOpen(!$("paper-ask-dock").classList.contains("open"));
    });
    document.addEventListener("click", (e) => {
      const dock = $("paper-ask-dock");
      if (dock && dock.classList.contains("open") && !dock.contains(e.target)) {
        pwAskOpen(false);
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      const modal = $("paper-table-modal");
      if (modal && !modal.classList.contains("hidden")) {
        pwCloseTable();
        return;
      }
      if ($("paper-ask-dock").classList.contains("open")) pwAskOpen(false);
    });
  }
  $("paper-steps").addEventListener("click", (e) => {
    const btn = e.target.closest(".paper-step");
    if (!btn) return;
    const target = Number(btn.dataset.step);
    // 切换步骤时清掉上一步留下的提示，避免误以为当前步骤刚完成
    if (target !== paperState.step) pwHideAlert();
    pwSetStep(target);
  });
  $("paper-issues").addEventListener("click", (e) => {
    const aiBtn = e.target.closest(".issue-ai");
    if (aiBtn) {
      e.preventDefault();
      e.stopPropagation();
      pwAiRewriteIssue(aiBtn.dataset.pid, aiBtn.dataset.instruction || "", aiBtn.dataset.mode || "").catch(pwError);
      return;
    }
    const item = e.target.closest(".issue-item");
    if (!item || !item.dataset.pid) return;
    pwGotoIssue(item.dataset.pid, item.dataset.instruction || "", item.dataset.mode || "").catch(pwError);
  });
  $("paper-trend").addEventListener("click", (e) => {
    if (e.target.closest("#btn-paper-batch-high")) {
      pwBatchHighRisk().catch(pwError);
      return;
    }
    const cell = e.target.closest(".heat-cell");
    if (!cell || !cell.dataset.pid) return;
    pwGotoParagraph(cell.dataset.pid).catch(pwError);
  });

  try {
    paperState.options = await api("/api/paper/options");
    pwApplyOptions();
  } catch (e) {
    pwAlert("加载论文工作台配置失败：" + e.message, "err");
  }
  await pwLoadList();
  const fromUrl = new URLSearchParams(location.search).get("paper");
  const saved = pwReadLastState();
  const target = fromUrl || (saved && saved.paperId) || "";
  if (target) {
    const restoreStep = !fromUrl && saved && saved.paperId === target ? saved.step : 0;
    try {
      await pwLoadPaper(target, { restoreStep });
    } catch (e) {
      pwClearLastState();
      pwSetStep(1, true);
    }
  } else {
    pwSetStep(1, true);
  }
}

function pwApplyOptions() {
  const o = paperState.options || {};
  pwFillSelect($("paper-level"), (o.levels || []).map((l) => ({
    value: l.value,
    label: `${l.label}（约 ${l.min_chars} 字）`,
  })), { placeholder: "请选择论文层次" });
  pwFillSelect($("paper-template"), (o.format_templates || []).map((t) => ({
    value: t.value,
    label: t.label,
  })));
  pwFillSelect($("paper-refstyle"), o.reference_styles || []);
  pwFillSelect($("paper-deai-strength"), (o.deai_strengths || []).map((s) => ({
    value: s.value,
    label: `${s.label}：${s.description}`,
  })));
  pwFillSelect($("paper-mode"), o.rewrite_modes || []);
  $("paper-quota").textContent = pwQuotaText(o.quota);
  const template = $("paper-template");
  if (!template.dataset.formatBound) {
    template.dataset.formatBound = "1";
    template.addEventListener("change", () => {
      const defaults = (paperState.options || {}).format_defaults || {};
      const chosen = defaults[template.value];
      if (!chosen) return;
      pwApplyFormatToInputs(chosen);
      if (chosen.reference_style) $("paper-refstyle").value = chosen.reference_style;
    });
  }
}

function pwEffectiveRequirementFormat(fmt) {
  const o = paperState.options || {};
  const base = o.format_baseline || {};
  const template = fmt.template || "generic_thesis";
  if (fmt.explicit) return { ...base, ...fmt, template };
  const overrides = (o.format_defaults || {})[template] || {};
  const eff = { ...base, ...overrides };
  Object.keys(fmt).forEach((key) => {
    if (key in overrides && fmt[key] === base[key]) return;
    eff[key] = fmt[key];
  });
  eff.template = template;
  return eff;
}

function pwApplyFormatToInputs(fmt) {
  $("paper-font-cn").value = fmt.font_body || "宋体";
  $("paper-font-latin").value = fmt.font_latin || "Times New Roman";
  $("paper-font-size").value = fmt.font_size_body_pt != null ? fmt.font_size_body_pt : 12;
  $("paper-line-spacing").value = fmt.line_spacing != null ? fmt.line_spacing : 1.5;
  $("paper-space-before").value = fmt.space_before_pt != null ? fmt.space_before_pt : 0;
  $("paper-space-after").value = fmt.space_after_pt != null ? fmt.space_after_pt : 0;
  $("paper-indent").value = fmt.first_line_indent_chars != null ? fmt.first_line_indent_chars : 2;
  $("paper-page-break").checked = fmt.page_break_before_heading !== false;
}

function pwError(e) {
  console.error(e);
  pwAlert(e && e.message ? e.message : "操作失败，请稍后重试", "err");
}

// ---------- 列表 / 详情 ----------
function pwPaperOptionText(p) {
  const title = p.title || p.filename || p.id;
  return `${title}（v${p.current_version || 0} · ${p.paragraph_count || 0} 段）`;
}

function pwHasOption(sel, value) {
  return Array.from(sel.options || []).some((x) => x.value === value);
}

function pwAppendPaperOption(sel, p, suffix) {
  if (!p || !p.id || pwHasOption(sel, p.id)) return;
  const opt = document.createElement("option");
  opt.value = p.id;
  opt.textContent = pwPaperOptionText(p) + (suffix || "");
  sel.appendChild(opt);
}

function pwEnsurePaperOption(sel, paperId) {
  if (!paperId || pwHasOption(sel, paperId)) return;
  const current = paperState.paper && paperState.paper.id === paperId
    ? paperState.paper
    : { id: paperId, title: "当前论文" };
  pwAppendPaperOption(sel, current, "（当前）");
}

function pwRenderListCount() {
  const el = $("paper-list-count");
  if (!el) return;
  const shown = Math.min(paperState.listOffset, paperState.listTotal);
  if (!paperState.listQuery) {
    el.textContent = paperState.listTotal
      ? `共 ${paperState.listTotal} 篇${paperState.listHasMore ? `，已显示 ${shown} 篇` : ""}`
      : "";
    return;
  }
  el.textContent = paperState.listTotal
    ? `匹配 ${paperState.listTotal} 篇${paperState.listHasMore ? `，已显示 ${shown} 篇` : ""}`
    : "无匹配论文";
}

async function pwLoadList(opts = {}) {
  const reset = opts.reset !== false;
  if (reset) {
    paperState.listQuery = String(
      opts.query !== undefined ? opts.query : ($("paper-search").value || "")
    ).trim();
  }
  const seq = ++paperState.listSeq;
  const offset = reset ? 0 : paperState.listOffset;
  let params = `offset=${offset}&limit=${paperState.listLimit}`;
  if (paperState.listQuery) params += "&q=" + encodeURIComponent(paperState.listQuery);
  const more = $("btn-paper-more");
  more.disabled = true;
  let data;
  try {
    data = await api("/api/paper?" + params);
  } finally {
    if (seq === paperState.listSeq) more.disabled = false;
  }
  if (seq !== paperState.listSeq) return;

  const sel = $("paper-select");
  const keep = paperState.paperId || sel.value;
  if (reset) sel.innerHTML = '<option value="">选择论文...</option>';
  (data.papers || []).forEach((p) => pwAppendPaperOption(sel, p));
  paperState.listOffset = Number(data.offset || 0) + (data.papers || []).length;
  paperState.listTotal = Number(data.total || 0);
  paperState.listHasMore = !!data.has_more;
  if (keep) {
    pwEnsurePaperOption(sel, keep);
    sel.value = keep;
  }
  more.classList.toggle("hidden", !paperState.listHasMore);
  pwRenderListCount();
}

function pwResetPaper() {
  paperState.loadSeq++;
  paperState.paragraphSeq++;
  paperState.askSeq++;
  pwClearTimers();
  paperState.paperId = "";
  paperState.paper = null;
  paperState.selected = null;
  paperState.rewrite = null;
  paperState.paragraphSuggestions = new Map();
  paperState.instructionPid = "";
  paperState.instructionFromIssue = false;
  $("paper-instruction").value = "";
  paperState.guideline = null;
  paperState.guideTaskId = "";
  paperState.guidePaperId = "";
  pwGuideProgressHide();
  $("btn-paper-guide").disabled = false;
  paperState.maxStep = 1;
  paperState.paragraphs = [];
  paperState.sections = [];
  paperState.tables = [];
  $("paper-section-filter").value = "";
  $("paper-select").value = "";
  $("btn-paper-ask").disabled = false;
  pwCloseTable();
  $("paper-upload-result").innerHTML = "";
  $("paper-dim-cards").innerHTML = "";
  $("paper-total-score").textContent = "";
  $("paper-issues").innerHTML = "";
  $("paper-similar-list").innerHTML = "";
  $("paper-paragraph-list").innerHTML = "";
  $("paper-original").textContent = "在左侧选择一个正文段落。";
  pwClearDiff();
  $("paper-rewrite-meta").textContent = "";
  $("paper-rewrite-warning").classList.add("hidden");
  $("btn-paper-adopt").classList.add("hidden");
  $("paper-version").textContent = "";
  $("paper-req-status").textContent = "";
  $("paper-diag-logs").classList.add("hidden");
  paperState.diagPaperId = "";
  paperState.diagTaskId = "";
  paperState.similarTaskId = "";
  paperState.rewriteTaskId = "";
  paperState.rewritePaperId = "";
  paperState.rewriteParaId = "";
  paperState.batchTaskId = "";
  paperState.batchPaperId = "";
  paperState.batchResults = [];
  paperState.batchErrors = [];
  paperState.batchIds = new Set();
  paperState.refs = null;
  paperState.refsPaperId = "";
  $("paper-refs-list").innerHTML = "";
  $("paper-refs-status").textContent = "";
  $("btn-paper-refs-apply").classList.add("hidden");
  $("btn-paper-refs-apply").disabled = true;
  pwTaskProgressHide("paper-diag");
  pwDiagCancelReset();
  pwTaskProgressHide("paper-upload");
  pwTaskProgressHide("paper-similar");
  pwTaskProgressHide("paper-rewrite");
  pwTaskProgressHide("paper-batch");
  $("paper-batch-results").innerHTML = "";
  $("btn-paper-batch-adopt").classList.add("hidden");
  $("btn-paper-batch-adopt").disabled = true;
  $("btn-paper-similar-cancel").classList.add("hidden");
  $("btn-paper-rewrite-cancel").classList.add("hidden");
  $("paper-ask-answer").classList.add("hidden");
  pwAskOpen(false);
  pwSetStep(1, true);
  pwHideAlert();
}

function pwNewPaper() {
  pwClearLastState();
  pwResetPaper();
  pwLoadList().catch(() => {});
  $("paper-file").focus();
}

async function pwLoadPaper(paperId, opts = {}) {
  const seq = ++paperState.loadSeq;
  const p = await api(`/api/paper/${encodeURIComponent(paperId)}`);
  if (seq !== paperState.loadSeq) return;
  pwClearTimers();
  const prevPaperId = paperState.paperId;
  const samePaper = prevPaperId === paperId;
  const oldSimilarTask = paperState.similarTaskId;
  if (oldSimilarTask && !samePaper) {
    postJSON(`/api/paper/${encodeURIComponent(prevPaperId)}/similar/${encodeURIComponent(oldSimilarTask)}/cancel`, {})
      .catch(() => {});
  }
  if (paperState.batchTaskId && !samePaper) {
    postJSON(`/api/paper/${encodeURIComponent(prevPaperId)}/rewrite/${encodeURIComponent(paperState.batchTaskId)}/cancel`, {})
      .catch(() => {});
  }
  if (!samePaper) {
    paperState.askSeq++;
    $("btn-paper-ask").disabled = false;
    $("paper-section-filter").value = "";
    paperState.batchResults = [];
    paperState.batchErrors = [];
    paperState.batchIds = new Set();
    paperState.refs = null;
    paperState.refsPaperId = "";
    $("paper-refs-list").innerHTML = "";
    $("paper-refs-status").textContent = "";
    $("btn-paper-refs-apply").classList.add("hidden");
    $("btn-paper-refs-apply").disabled = true;
    paperState.batchPaperId = "";
    paperState.batchTaskId = "";
    $("paper-batch-results").innerHTML = "";
    $("btn-paper-batch-cancel").disabled = false;
    pwTaskProgressHide("paper-batch");
    pwSyncBatchAdoptButton();
  }
  if (paperState.diagPaperId && paperState.diagPaperId !== paperId) {
    paperState.diagPaperId = "";
    paperState.diagTaskId = "";
    pwTaskProgressHide("paper-diag");
    pwDiagCancelReset();
  }
  paperState.paperId = paperId;
  paperState.paper = p;
  paperState.selected = null;
  paperState.rewrite = null;
  paperState.paragraphSuggestions = new Map();
  paperState.instructionPid = "";
  paperState.instructionFromIssue = false;
  $("paper-instruction").value = "";
  paperState.guideline = null;
  paperState.tables = [];
  pwCloseTable();
  if (paperState.guideTaskId) {
    paperState.guideTaskId = "";
    paperState.guidePaperId = "";
    pwGuideProgressHide();
    $("btn-paper-guide").disabled = false;
  }
  if (paperState.rewritePaperId && paperState.rewritePaperId !== paperId) {
    const oldPaper = paperState.rewritePaperId;
    const oldTask = paperState.rewriteTaskId;
    if (oldTask) {
      postJSON(`/api/paper/${encodeURIComponent(oldPaper)}/rewrite/${encodeURIComponent(oldTask)}/cancel`, {})
        .catch(() => {});
    }
    paperState.rewriteTaskId = "";
    paperState.rewritePaperId = "";
    paperState.rewriteParaId = "";
    pwRewriteBusy(false);
    pwTaskProgressHide("paper-rewrite");
  }
  paperState.similarTaskId = "";
  pwTaskProgressHide("paper-similar");
  $("btn-paper-similar-cancel").classList.add("hidden");
  $("btn-paper-similar").disabled = false;
  $("btn-paper-similar-refresh").disabled = false;
  $("paper-similar-status").textContent = "";
  pwRenderGuideline({ guideline: null });
  pwEnsurePaperOption($("paper-select"), paperId);
  $("paper-select").value = paperId;
  pwClearDiff();
  $("paper-original").textContent = "在左侧选择一个正文段落。";
  $("paper-rewrite-meta").textContent = "";
  $("paper-rewrite-warning").classList.add("hidden");
  $("btn-paper-adopt").classList.add("hidden");
  $("paper-similar-list").innerHTML = "";
  $("paper-ask-answer").classList.add("hidden");
  $("paper-ask-input").value = "";
  pwAskOpen(false);

  const confirmed = !!(p.requirement && p.requirement.confirmed);
  pwFillRequirement(p.requirement || {});
  if (!confirmed && p.suggestion) pwFillRequirement(p.suggestion);
  paperState.maxStep = confirmed ? 5 : 2;
  if (p.analysis_results && Object.keys(p.analysis_results).length) {
    pwRenderDiagnosis(p.analysis_results, p.total_score, p.analysis_history);
  } else {
    $("paper-dim-cards").innerHTML = "";
    $("paper-total-score").textContent = "";
    $("paper-issues").innerHTML = "";
    pwRenderTrend(null, null);
  }
  const analyzed = p.analyzed || {};
  const done = Object.keys(analyzed).filter((k) => analyzed[k]).length;
  $("btn-paper-diagnose").textContent = done > 0 && done < PAPER_DIM_ORDER.length
    ? "补全未完成诊断" : (done === PAPER_DIM_ORDER.length ? "重新诊断" : "开始诊断");
  $("paper-req-status").textContent = confirmed
    ? `需求已确认 · 当前版本 v${p.current_version || 0} · 已完成 ${done}/4 个维度`
    : (p.suggestion
      ? "已根据论文内容自动预填，请核对后点击「确认需求」"
      : "需求未确认：请填写并确认后再诊断");

  const archivedNote = p.archived_count ? `，已归档 ${p.archived_count} 个` : "";
  $("paper-version").textContent = `当前版本 v${p.current_version || 0}（共 ${p.version_count || 1} 个版本${archivedNote}）`;
  $("paper-upload-result").innerHTML =
    `<p><b>${esc(p.title || p.filename || paperId)}</b></p>
     <p class="muted">${esc(p.filename || "")} · ${p.pages || 0} 页 · ${p.chars || 0} 字符 · ${p.paragraph_count || 0} 段 · ${p.source_type === "docx" ? "Word" : "PDF"}</p>`;
  pwEmphasizeExport(p.source_type === "docx" ? "docx" : "pdf");

  const step = opts.restoreStep && opts.restoreStep <= paperState.maxStep
    ? opts.restoreStep
    : (confirmed ? 3 : 2);
  pwSetStep(step, true);
  await Promise.all([pwLoadParagraphs(), pwLoadGuideline(), pwLoadSimilar()]);
  if (seq !== paperState.loadSeq) return;
  if (paperState.batchResults.length) pwRenderBatchResults();
}

function pwEmphasizeExport(preferred) {
  $("btn-paper-export").classList.toggle("btn-primary", preferred === "docx");
  $("btn-paper-export").classList.toggle("btn-ghost", preferred !== "docx");
  $("btn-paper-export-pdf").classList.toggle("btn-primary", preferred === "pdf");
  $("btn-paper-export-pdf").classList.toggle("btn-ghost", preferred !== "pdf");
  $("btn-paper-export").title = preferred === "docx" ? "推荐：与上传格式一致" : "";
  $("btn-paper-export-pdf").title = preferred === "pdf" ? "推荐：与上传格式一致" : "";
}

// ---------- 上传 ----------
function pwUpdateFileChoice() {
  const input = $("paper-file");
  const f = input && input.files && input.files[0];
  const nameEl = $("paper-file-name");
  const zone = $("paper-upload-zone");
  if (!nameEl || !zone) return;
  if (f) {
    const kb = (f.size || 0) / 1024;
    const size = kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(kb))} KB`;
    nameEl.textContent = `${f.name} · ${size} · 点击可重新选择`;
    nameEl.classList.remove("hidden");
    zone.classList.add("has-file");
  } else {
    nameEl.textContent = "";
    nameEl.classList.add("hidden");
    zone.classList.remove("has-file");
  }
}

async function pwUpload() {
  const f = $("paper-file").files[0];
  if (!f) { pwAlert("请先选择 PDF 或 Word 文件", "warn"); return; }
  if (!/\.(pdf|docx)$/i.test(f.name)) {
    pwAlert("仅支持 PDF（.pdf）或 Word（.docx）文件；旧版 .doc 请先另存为 .docx", "err");
    return;
  }
  const fd = new FormData();
  fd.append("file", f);
  const btn = $("btn-paper-upload");
  btn.disabled = true;
  $("paper-upload-result").innerHTML = "";
  paperState.uploadTaskId = "";
  paperState.uploadPaperId = "";
  pwTaskProgress("paper-upload", 0, "正在上传文件…");
  try {
    const created = await pwUploadFile(fd);
    paperState.uploadTaskId = created.task_id;
    paperState.uploadPaperId = created.paper_id || "";
    const report = await pwPollUpload(created.task_id);
    pwTaskProgress("paper-upload", 100, "解析完成");
    $("paper-file").value = "";
    pwUpdateFileChoice();
    await pwLoadList();
    await pwLoadPaper(report.paper_id);
    $("paper-upload-result").innerHTML =
      `<p><b>解析完成：${esc(report.title)}</b></p>
       <p class="muted">${report.pages} 页 · ${report.chars} 字符 · ${report.section_count} 个章节 · ${report.paragraph_count} 段</p>`;
    pwAlert("上传成功，已根据内容预填需求，请核对后确认。", "ok");
  } catch (e) {
    if (!e.cancelled) {
      $("paper-upload-result").innerHTML = "";
      pwAlert(e.message, "err");
    }
  } finally {
    btn.disabled = false;
    paperState.uploadXhr = null;
    paperState.uploadTaskId = "";
    setTimeout(() => pwTaskProgressHide("paper-upload"), 800);
  }
}

function pwUploadFile(fd) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    paperState.uploadXhr = xhr;
    xhr.open("POST", "/api/paper/upload");
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const real = Math.round((e.loaded / e.total) * 100);
      pwTaskProgress("paper-upload", Math.round(real * 0.2), `正在上传文件… ${real}%`);
    };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText || "{}"); } catch (err) { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data || {});
      else reject(new Error((data && data.detail) || `上传失败（HTTP ${xhr.status}）`));
    };
    xhr.onerror = () => reject(new Error("上传失败，请检查网络或服务状态"));
    xhr.onabort = () => {
      const err = new Error("已取消上传");
      err.cancelled = true;
      reject(err);
    };
    xhr.send(fd);
  });
}

function pwPollUpload(taskId) {
  return new Promise((resolve, reject) => {
    pwWatchTask(paperState.uploadPaperId || paperState.paperId, taskId, {
      statusUrl: `/api/paper/upload/${encodeURIComponent(taskId)}`,
      interval: 700,
      timerKey: "uploadTimer",
      isCurrent: () => paperState.uploadTaskId === taskId,
      onView: (t) => {
        const pct = 20 + Math.round((Number(t.progress) || 0) * 0.8);
        pwTaskProgress("paper-upload", pct, t.stage || "正在解析论文…");
        if (t.status === "done") { resolve(t.report || {}); return true; }
        if (t.status === "cancelled") {
          const err = new Error("已取消上传");
          err.cancelled = true;
          reject(err);
          return true;
        }
        if (t.status === "error") { reject(new Error(t.error || "论文解析失败")); return true; }
        return false;
      },
      onError: (e) => reject(e),
    });
  });
}

async function pwCancelUpload() {
  if (paperState.uploadXhr) {
    try { paperState.uploadXhr.abort(); } catch (e) { /* ignore */ }
  }
  const taskId = paperState.uploadTaskId;
  if (taskId) {
    try { await postJSON(`/api/paper/upload/${encodeURIComponent(taskId)}/cancel`, {}); } catch (e) { /* ignore */ }
  }
  if (paperState.uploadTimer) { clearTimeout(paperState.uploadTimer); paperState.uploadTimer = null; }
  pwTaskProgressHide("paper-upload");
  $("btn-paper-upload").disabled = false;
  pwAlert("已取消上传，不会保存本次论文。", "warn", 1);
}

// ---------- 需求确认 ----------
function pwFillRequirement(req) {
  const fmt = req.format || {};
  const deai = req.deai || {};
  $("paper-level").value = req.level || "";
  $("paper-template").value = fmt.template || "generic_thesis";
  $("paper-refstyle").value = fmt.reference_style || "gbt7714";
  pwApplyFormatToInputs(pwEffectiveRequirementFormat(fmt));
  $("paper-deai-strength").value = deai.strength || "medium";
  const keep = deai.keep || ["数据", "术语", "引用"];
  document.querySelectorAll("#paper-keep input").forEach((el) => {
    el.checked = keep.includes(el.value);
  });
  $("paper-banned").value = (deai.banned_phrases || []).join("、");
  $("paper-notes").value = req.notes || "";
}

function pwNum(id, fallback) {
  const value = Number($(id).value);
  return Number.isFinite(value) ? value : fallback;
}

async function pwConfirm() {
  if (!paperState.paperId) { pwAlert("请先上传或选择论文", "warn"); return; }
  const level = $("paper-level").value;
  if (!level) { pwAlert("请选择论文层次（课程论文 / 本科 / 硕士 / 博士 / 期刊等）", "warn"); return; }
  const keep = Array.from(document.querySelectorAll("#paper-keep input:checked")).map((el) => el.value);
  const banned = $("paper-banned").value.split(/[、,，;；\s]+/).map((s) => s.trim()).filter(Boolean);
  const fmt = (paperState.paper && paperState.paper.requirement && paperState.paper.requirement.format) || {};
  const body = {
    level,
    format: {
      ...fmt,
      template: $("paper-template").value,
      reference_style: $("paper-refstyle").value,
      explicit: true,
      font_body: $("paper-font-cn").value.trim() || "宋体",
      font_latin: $("paper-font-latin").value.trim() || "Times New Roman",
      font_size_body_pt: pwNum("paper-font-size", 12),
      line_spacing: pwNum("paper-line-spacing", 1.5),
      space_before_pt: pwNum("paper-space-before", 0),
      space_after_pt: pwNum("paper-space-after", 0),
      first_line_indent_chars: pwNum("paper-indent", 2),
      page_break_before_heading: $("paper-page-break").checked,
    },
    deai: { strength: $("paper-deai-strength").value, keep, banned_phrases: banned },
    notes: $("paper-notes").value.trim(),
    confirmed: true,
  };
  const btn = $("btn-paper-confirm");
  btn.disabled = true;
  try {
    const r = await api(`/api/paper/${encodeURIComponent(paperState.paperId)}/requirement`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    pwAlert(r.message || "需求已确认", "ok");
    await pwLoadPaper(paperState.paperId);
    pwSetStep(3, true);
  } finally {
    btn.disabled = false;
  }
}

// ---------- 诊断 ----------
async function pwStartDiagnose() {
  if (!paperState.paperId) { pwAlert("请先上传论文", "warn"); return; }
  const confirmed = paperState.paper && paperState.paper.requirement && paperState.paper.requirement.confirmed;
  if (!confirmed) { pwAlert("请先完成需求确认", "warn"); pwSetStep(2); return; }
  const btn = $("btn-paper-diagnose");
  btn.disabled = true;
  const logs = $("paper-diag-logs");
  logs.classList.remove("hidden");
  logs.textContent = "";
  paperState.diagPaperId = paperState.paperId;
  paperState.diagTaskId = "";
  $("btn-paper-cancel").classList.remove("hidden");
  $("btn-paper-cancel").disabled = false;
  $("btn-paper-cancel").textContent = "取消诊断";
  pwTaskProgress("paper-diag", 0, "正在准备论文与模型…");
  pwRenderDiagnosis({}, null);
  try {
    const existing = paperState.paper.analysis_results || {};
    const missing = PAPER_DIM_ORDER.filter((key) => !existing[key] || existing[key].stale);
    const r = await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/diagnose`, {
      dimensions: missing.length < PAPER_DIM_ORDER.length ? missing : [],
    });
    paperState.diagTaskId = r.task_id;
    pwPollDiagnose(r.task_id);
  } catch (e) {
    btn.disabled = false;
    paperState.diagPaperId = "";
    paperState.diagTaskId = "";
    pwTaskProgressHide("paper-diag");
    pwDiagCancelReset();
    pwAlert(e.message, "err");
  }
}

async function pwCancelDiagnose() {
  const taskId = paperState.diagTaskId;
  const pid = paperState.diagPaperId;
  if (!taskId || !pid) return;
  const btn = $("btn-paper-cancel");
  btn.disabled = true;
  btn.textContent = "正在取消…";
  try {
    const r = await postJSON(
      `/api/paper/${encodeURIComponent(pid)}/diagnose/${encodeURIComponent(taskId)}/cancel`, {});
    if (r.status === "running" && r.cancel_requested) {
      $("paper-diag-stage").textContent = "正在取消，当前维度完成后停止…";
    }
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "取消诊断";
    throw e;
  }
}

function pwPollDiagnose(taskId) {
  const pid = paperState.paperId;
  pwWatchTask(pid, taskId, {
    statusUrl: `/api/paper/${encodeURIComponent(pid)}/diagnose/${taskId}`,
    interval: 1200,
    timerKey: "diagTimer",
    isCurrent: () => paperState.paperId === pid,
    onView: (t) => {
      const logLines = t.logs || [];
      $("paper-diag-logs").textContent = logLines.join("\n");
      pwTaskProgress("paper-diag",
        typeof t.progress === "number" ? t.progress : 0,
        logLines[logLines.length - 1] || "正在诊断…"
      );
      if (t.status === "running" && t.report) {
        pwRenderDiagnosis(t.report.dimensions || {}, null);
      }
      if (t.status === "done") {
        $("btn-paper-diagnose").disabled = false;
        pwTaskProgress("paper-diag", 100, "诊断完成");
        paperState.diagPaperId = "";
        paperState.diagTaskId = "";
        pwDiagCancelReset();
        setTimeout(() => {
          if (!paperState.diagPaperId) pwTaskProgressHide("paper-diag");
        }, 1500);
        if (t.report) pwRenderDiagnosis(t.report.dimensions || {}, t.report.total_score);
        if (t.report && t.report.errors && Object.keys(t.report.errors).length) {
          pwAlert("部分维度诊断失败：" + Object.values(t.report.errors).join("；") + "。可再次点击诊断重试。", "warn", 3);
        } else {
          pwAlert("诊断完成。可查看右侧问题列表，或继续相似论文与段落改写。", "ok", 3);
        }
        pwRefreshQuota();
        pwLoadPaper(pid).catch(() => {});
        return true;
      }
      if (t.status === "cancelled") {
        $("btn-paper-diagnose").disabled = false;
        paperState.diagPaperId = "";
        paperState.diagTaskId = "";
        pwDiagCancelReset();
        pwTaskProgressHide("paper-diag");
        pwAlert("诊断已取消，已完成维度的结果会保留。可重新点击「开始诊断」。", "warn", 3);
        pwLoadPaper(pid).catch(() => {});
        return true;
      }
      if (t.status === "error") {
        $("btn-paper-diagnose").disabled = false;
        paperState.diagPaperId = "";
        paperState.diagTaskId = "";
        pwDiagCancelReset();
        pwTaskProgressHide("paper-diag");
        pwAlert("诊断失败：" + (t.error || "未知错误"), "err", 3);
        return true;
      }
      return false;
    },
    onError: (e) => {
      $("btn-paper-diagnose").disabled = false;
      paperState.diagPaperId = "";
      paperState.diagTaskId = "";
      pwDiagCancelReset();
      pwTaskProgressHide("paper-diag");
      pwAlert("查询诊断进度失败：" + e.message, "err", 3);
    },
  });
}

function pwScoreClass(score) {
  if (score >= 80) return "good";
  if (score >= 60) return "warn";
  return "bad";
}

function pwRenderDiagnosis(dims, total, history) {
  const wrap = $("paper-dim-cards");
  wrap.innerHTML = "";
  PAPER_DIM_ORDER.forEach((key) => {
    const d = dims[key];
    const label = PAPER_DIM_LABELS[key] || key;
    if (!d) {
      const card = document.createElement("div");
      card.className = "paper-dim-card";
      card.innerHTML = `<div class="dim-head"><span>${esc(label)}</span><b>—</b></div><p class="muted">${paperState.diagPaperId ? "等待诊断结果…" : "尚未完成，可重新诊断"}</p>`;
      wrap.appendChild(card);
      return;
    }
    const score = Number(d.score || 0);
    const stale = d.stale ? '<span class="paper-stale">已过期</span>' : "";
    const issues = Array.isArray(d.issues) ? d.issues : [];
    const card = document.createElement("div");
    card.className = "paper-dim-card " + pwScoreClass(score);
    let extra = "";
    if (d.coverage) {
      const c = d.coverage;
      const sections = Array.isArray(c.sections) ? c.sections.join("、") : "";
      extra += `<p class="muted">证据覆盖：${Number(c.sampled_paragraphs) || 0}/${Number(c.total_paragraphs) || 0} 段，${Number(c.sampled_chars) || 0}/${Number(c.total_chars) || 0} 字。${esc(sections)}${c.partial ? "。仅基于抽样证据，未覆盖内容仍需核对。" : ""}</p>`;
    }
    if (key === "feasibility" && d.minimal_plan) {
      extra += `<p class="dim-extra"><b>最小可行路径：</b>${esc(d.minimal_plan)}</p>`;
    }
    if (key === "feasibility" && d.disclaimer) {
      extra += `<p class="muted">${esc(d.disclaimer)}</p>`;
    }
    if (key === "deai") {
      if (d.ai_rate != null) {
        const rate = Math.max(0, Math.min(100, Math.round(Number(d.ai_rate) || 0)));
        const lvl = rate < 30 ? "low" : rate < 60 ? "medium" : "high";
        extra += `<div class="dim-rate ${lvl}">
            <span>AI 生成率</span>
            <span class="dim-rate-track"><i class="dim-rate-fill" style="width:${rate}%"></i></span>
            <b>${rate}%</b>
          </div>`;
      }
      const langMap = { zh: "中文", en: "英文", mixed: "中英混合" };
      if (d.language) {
        extra += `<p class="muted">检测语言：${esc(langMap[d.language] || d.language)}</p>`;
      }
      if (Array.isArray(d.patterns) && d.patterns.length) {
        extra += `<div class="dim-patterns">${d.patterns.slice(0, 5).map((p) =>
          `<span class="dim-chip">${esc(p.label)} ×${Number(p.count) || 0}</span>`
        ).join("")}</div>`;
      }
      if (d.level) {
        const levelMap = { low: "低", medium: "中", high: "高" };
        extra += `<p class="muted">模板化程度：${esc(levelMap[d.level] || d.level)}</p>`;
      }
      if (d.disclaimer) {
        extra += `<p class="muted">${esc(d.disclaimer)}</p>`;
      }
    }
    card.innerHTML =
      `<div class="dim-head"><span>${esc(label)}${stale}</span><b>${score}</b></div>
       <p class="dim-summary">${esc(d.summary || "")}</p>
       ${extra}
       <div class="dim-foot">问题 ${issues.length} 条</div>`;
    wrap.appendChild(card);
  });
  const t = Number(total || 0);
  $("paper-total-score").innerHTML = total == null
    ? ""
    : `<span>综合得分</span><b class="${pwScoreClass(t)}">${t}</b><em>/ 100（按已诊断维度加权）</em>`;
  pwRenderTrend(history, dims.deai);
  pwRenderIssues(dims);
}

function pwTrendSvg(points) {
  const w = 320;
  const h = 72;
  const pad = 8;
  const n = points.length;
  const xs = points.map((_, i) => pad + (w - pad * 2) * (n === 1 ? 0.5 : i / (n - 1)));
  const ys = points.map((p) => {
    const v = Math.max(0, Math.min(100, Number(p.ai_rate) || 0));
    return h - pad - (h - pad * 2) * (v / 100);
  });
  const line = xs.map((x, i) => `${i ? "L" : "M"}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
  const area = `${line} L${xs[n - 1].toFixed(1)},${h - pad} L${xs[0].toFixed(1)},${h - pad} Z`;
  const dots = points.map((p, i) => {
    const label = `${Number(p.ai_rate) || 0}%${p.generated_at ? " · " + p.generated_at : ""}`;
    return `<circle class="trend-dot" cx="${xs[i].toFixed(1)}" cy="${ys[i].toFixed(1)}" r="2.6"><title>${esc(label)}</title></circle>`;
  }).join("");
  return `<svg class="trend-svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="AI 生成率趋势">
      <path class="trend-area" d="${area}"></path>
      <path class="trend-line" d="${line}"></path>
      ${dots}
    </svg>`;
}

function pwRenderTrend(history, deai) {
  const box = $("paper-trend");
  if (!box) return;
  const rows = (Array.isArray(history) ? history : [])
    .filter((h) => h && h.dimension === "deai"
      && h.ai_rate != null && Number.isFinite(Number(h.ai_rate)))
    .map((h) => ({ ...h, ai_rate: Number(h.ai_rate) }));
  const risks = deai && Array.isArray(deai.paragraph_risks) ? deai.paragraph_risks : [];
  // 供「一键改写高风险段落」使用：只保留能映射到正文段落的 id，并去重
  paperState.highRiskIds = Array.from(new Set(
    risks.filter((r) => r && r.risk === "high" && r.paragraph_id).map((r) => r.paragraph_id)
  ));
  if (!rows.length && !risks.length) {
    box.innerHTML = "";
    box.classList.add("hidden");
    return;
  }
  let html = "";
  if (rows.length >= 2) {
    const latest = rows[rows.length - 1];
    const delta = latest.ai_rate - rows[rows.length - 2].ai_rate;
    const deltaClass = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
    const deltaText = delta === 0 ? "与上次持平" : `较上次 ${delta > 0 ? "+" : ""}${delta}%`;
    html += `<div class="trend-block">
        <div class="trend-head"><span>AI 生成率趋势</span><span class="muted">最近 ${rows.length} 次诊断</span></div>
        ${pwTrendSvg(rows)}
        <div class="trend-meta"><b>${latest.ai_rate}%</b><span class="trend-delta ${deltaClass}">${deltaText}</span></div>
      </div>`;
  } else if (rows.length === 1) {
    html += `<p class="muted">再完成一次诊断后可查看 AI 生成率趋势（当前 ${rows[0].ai_rate}%）。</p>`;
  }
  if (risks.length) {
    const counts = deai.paragraph_risk || {};
    const high = Number(counts.high) || risks.filter((r) => r.risk === "high").length;
    const medium = Number(counts.medium) || risks.filter((r) => r.risk === "medium").length;
    const mapped = risks.some((r) => r.paragraph_id);
    const cells = risks.map((r) => {
      const risk = ["high", "medium", "low"].includes(r.risk) ? r.risk : "low";
      const riskLabel = risk === "high" ? "高" : risk === "medium" ? "中" : "低";
      const hits = (Array.isArray(r.hits) ? r.hits.slice(0, 3).join("、") : "");
      const label = `第 ${Number(r.index) + 1} 段 · ${riskLabel}风险 ${Number(r.risk_points) || 0} 分${hits ? " · " + hits : ""}`;
      return `<span class="heat-cell risk-${risk}" data-pid="${esc(r.paragraph_id || "")}" title="${esc(label)}"></span>`;
    }).join("");
    html += `<div class="trend-block">
        <div class="trend-head"><span>段落风险热力</span><span class="muted">高 ${high} · 中 ${medium} · 共 ${risks.length} 段${mapped ? " · 点击色块定位段落" : ""}</span></div>
        <div class="paper-heat">${cells}</div>
        <div class="heat-legend"><i class="heat-key risk-low"></i>低<i class="heat-key risk-medium"></i>中<i class="heat-key risk-high"></i>高</div>
        ${paperState.highRiskIds.length ? `<div class="heat-actions">
          <button class="btn btn-primary" id="btn-paper-batch-high">一键改写高风险段落（${paperState.highRiskIds.length}）</button>
          <span class="muted">自动勾选高风险正文段，单次最多改写 8 段</span>
        </div>` : ""}
      </div>`;
  }
  box.innerHTML = html;
  box.classList.remove("hidden");
}

// ---------- 参考文献检查 ----------
async function pwLoadReferences(force) {
  if (!paperState.paperId) {
    if (force) pwAlert("请先上传论文", "warn", 3);
    return;
  }
  const pid = paperState.paperId;
  if (!force && paperState.refs && paperState.refsPaperId === pid) return;
  const seq = ++paperState.refsSeq;
  $("paper-refs-status").textContent = "正在检查参考文献…";
  try {
    const data = await api(`/api/paper/${encodeURIComponent(pid)}/references`);
    if (pid !== paperState.paperId || seq !== paperState.refsSeq) return;
    paperState.refs = data;
    paperState.refsPaperId = pid;
    pwRenderReferences();
  } catch (e) {
    if (pid !== paperState.paperId || seq !== paperState.refsSeq) return;
    $("paper-refs-status").textContent = "";
    pwAlert("参考文献检查失败：" + e.message, "err", 3);
  }
}

function pwRenderReferences() {
  const box = $("paper-refs-list");
  const data = paperState.refs;
  const applyBtn = $("btn-paper-refs-apply");
  if (!data) {
    box.innerHTML = "";
    $("paper-refs-status").textContent = "";
    applyBtn.classList.add("hidden");
    applyBtn.disabled = true;
    return;
  }
  const entries = data.entries || [];
  const changed = entries.filter((e) => e.changed);
  applyBtn.textContent = `一键规范化并保存（${changed.length}）`;
  applyBtn.classList.toggle("hidden", changed.length === 0);
  applyBtn.disabled = !changed.length;
  $("paper-refs-status").textContent = data.summary || "";
  if (!entries.length) {
    box.innerHTML = '<p class="muted">未识别到参考文献条目，请确认论文含「参考文献」章节。</p>';
    return;
  }
  const numeric = data.reference_style === "gbt7714" || data.reference_style === "ieee";
  box.innerHTML = entries.map((e) => {
    const num = e.number == null ? "未编号" : `[${e.number}]`;
    const chips = [];
    if (numeric && !e.cited) chips.push('<span class="chip ref-warn">未引用</span>');
    if (e.type_label) chips.push(`<span class="chip">${esc(e.type_label)}</span>`);
    if (e.changed) chips.push('<span class="chip ref-ok">可规范化</span>');
    const problems = (e.problems || []).map((p) =>
      `<div class="ref-problem sev-${p.severity === "high" ? "high" : p.severity === "low" ? "low" : "medium"}">${esc(p.message)}</div>`
    ).join("");
    const normalized = e.changed
      ? `<div class="ref-normalized"><span>规范化后</span>${esc(e.normalized)}</div>` : "";
    return `<div class="ref-item${e.changed ? " changed" : ""}">
        <div class="ref-meta"><b>${esc(num)}</b><span class="muted">第 ${e.page || "-"} 页</span>${chips.join("")}</div>
        <div class="ref-text">${esc(e.text)}</div>
        ${problems}${normalized}
      </div>`;
  }).join("");
}

async function pwApplyReferences() {
  const data = paperState.refs;
  if (!data || !paperState.paperId) return;
  const items = (data.entries || []).filter((e) => e.changed).map((e) => ({
    paragraph_id: e.paragraph_id,
    revised: e.normalized,
    expected_original: e.text,
  }));
  if (!items.length) {
    pwAlert("没有需要规范化的文献条目。", "warn", 3);
    return;
  }
  const btn = $("btn-paper-refs-apply");
  btn.disabled = true;
  try {
    const r = await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/references/apply`, { items });
    pwAlert(`已规范化并保存 ${items.length} 条参考文献（v${r.version}）。`, "ok", 3);
    await pwLoadPaper(paperState.paperId);
    paperState.refs = null;
    await pwLoadReferences(true);
  } catch (e) {
    btn.disabled = false;
    pwAlert(e.message, "err", 3);
  }
}

function pwIssueRewriteMode(dimKey, it) {
  if (dimKey === "deai") return "deai";
  if (it.rule_id === "FMT-009") return "tighten";
  return "polish";
}

function pwRenderIssues(dims) {
  const box = $("paper-issues");
  const rows = [];
  const suggestions = new Map();
  const sevRank = { high: 0, medium: 1, low: 2 };
  PAPER_DIM_ORDER.forEach((key) => {
    const d = dims[key];
    if (!d) return;
    (d.issues || []).forEach((it) => {
      const sev = ["high", "medium", "low"].includes(it.severity) ? it.severity : "medium";
      const title = it.rule_id || it.issue || PAPER_DIM_LABELS[key];
      const page = it.page ? `第 ${it.page} 页` : "";
      const loc = it.paragraph_id || "";
      const instruction = it.suggestion || it.issue || "";
      const mode = pwIssueRewriteMode(key, it);
      if (loc && instruction) {
        const prev = suggestions.get(loc);
        // 同一段落有多条问题时，取严重度最高的一条作为默认建议
        if (!prev || sevRank[sev] < sevRank[prev.severity]) {
          suggestions.set(loc, { instruction, mode, severity: sev });
        }
      }
      rows.push(
        `<div class="issue-item sev-${sev}" data-pid="${esc(loc)}" data-page="${esc(it.page || "")}" data-instruction="${esc(instruction)}" data-mode="${esc(mode)}">
           <div class="issue-head">
             <span class="sev-tag">${sev === "high" ? "高" : sev === "medium" ? "中" : "低"}</span>
             <b>${esc(title)}</b>
             <span class="muted">${esc(PAPER_DIM_LABELS[key])} ${esc(page)}</span>
           </div>
           ${it.evidence ? `<blockquote>${esc(it.evidence)}</blockquote>` : ""}
           ${it.suggestion ? `<p class="issue-sug">建议：${esc(it.suggestion)}</p>` : ""}
           ${it.basis ? `<p class="muted">依据：${esc(it.basis)}</p>` : ""}
           ${loc ? `<div class="issue-actions"><button class="btn btn-ghost issue-ai" data-pid="${esc(loc)}" data-instruction="${esc(instruction)}" data-mode="${esc(mode)}">AI 改写并审核</button></div>` : ""}
         </div>`
      );
    });
  });
  paperState.paragraphSuggestions = suggestions;
  box.innerHTML = rows.length
    ? `<div class="section-title">问题清单（点击条目自动填入建议并匹配改写模式；点「AI 改写并审核」先生成改写稿，逐处对比后再采纳）</div>${rows.join("")}`
    : '<p class="muted">暂无问题条目。</p>';
}

function pwSetRewriteMode(mode) {
  if (!mode) return;
  const sel = $("paper-mode");
  if (!pwHasOption(sel, mode)) {
    const opt = document.createElement("option");
    opt.value = mode;
    opt.textContent = PAPER_MODE_LABELS[mode] || mode;
    sel.appendChild(opt);
  }
  sel.value = mode;
}

function pwFillInstructionForParagraph(pid) {
  const sug = paperState.paragraphSuggestions.get(pid);
  $("paper-instruction").value = sug ? sug.instruction : "";
  if (sug) pwSetRewriteMode(sug.mode);
  paperState.instructionPid = sug ? pid : "";
  paperState.instructionFromIssue = !!sug;
}

function pwSyncInstructionWithParagraph(pid) {
  // 只同步"由建议自动填入"的内容；用户手写的要求不因切换段落被覆盖
  if (!paperState.instructionFromIssue || paperState.instructionPid === pid) return;
  pwFillInstructionForParagraph(pid);
}

function pwApplyIssueRewrite(instruction, mode) {
  $("paper-instruction").value = (instruction || "").trim();
  paperState.instructionPid = (paperState.selected && paperState.selected.id) || "";
  paperState.instructionFromIssue = true;
  if (!mode) return;
  pwSetRewriteMode(mode);
  pwAlert(`已自动填入该条建议，改写模式：${PAPER_MODE_LABELS[mode] || mode}`, "ok", 5);
}

async function pwGotoIssue(pid, instruction, mode) {
  await pwGotoParagraph(pid);
  if (!paperState.selected || paperState.selected.id !== pid) return;
  pwApplyIssueRewrite(instruction, mode);
}

async function pwGotoParagraph(pid) {
  await pwLoadParagraphs();
  if (!(paperState.paragraphs || []).some((p) => p.id === pid)) {
    pwAlert("该问题定位到的内容不是可改写正文（可能是标题、目录、题注或参考文献）。", "warn");
    return;
  }
  await pwSelectParagraph(pid);
  if (!paperState.selected || paperState.selected.id !== pid) return;
  pwSetStep(5, true);
}

async function pwAiRewriteIssue(pid, instruction, mode) {
  if (!paperState.paperId) { pwAlert("请先上传论文", "warn"); return; }
  await pwGotoParagraph(pid);
  if (!paperState.selected || paperState.selected.id !== pid) return;
  const limit = pwRewriteLimit();
  if ((paperState.selected.text || "").length > limit) {
    pwAlert(`该段超过 ${limit} 字，请先在原文中拆分后再改写。`, "warn");
    return;
  }
  pwApplyIssueRewrite(instruction, mode);
  await pwRewrite();
}

// ---------- 相似论文 ----------
async function pwLoadSimilar() {
  if (!paperState.paperId) return;
  const pid = paperState.paperId;
  const seq = paperState.loadSeq;
  try {
    const data = await api(`/api/paper/${encodeURIComponent(pid)}/similar`);
    if (pid !== paperState.paperId || seq !== paperState.loadSeq) return;
    if (data.cached) pwRenderSimilar(data);
  } catch (e) { /* 未检索过时忽略 */ }
}

async function pwStartSimilar(refresh) {
  if (!paperState.paperId) { pwAlert("请先上传论文", "warn"); return; }
  const btn = refresh ? $("btn-paper-similar-refresh") : $("btn-paper-similar");
  btn.disabled = true;
  $("paper-similar-status").textContent = "检索中...";
  $("btn-paper-similar-cancel").classList.remove("hidden");
  pwTaskProgress("paper-similar", 0, "正在准备检索…");
  try {
    const r = await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/similar`, {
      limit: 10,
      refresh: !!refresh,
    });
    paperState.similarTaskId = r.task_id;
    pwPollSimilar(r.task_id);
  } catch (e) {
    btn.disabled = false;
    pwSimilarBusy(false);
    pwAlert(e.message, "err");
  }
}

function pwSimilarBusy(busy) {
  $("btn-paper-similar").disabled = busy;
  $("btn-paper-similar-refresh").disabled = busy;
  $("btn-paper-similar-cancel").classList.toggle("hidden", !busy);
  if (!busy) $("paper-similar-status").textContent = "";
}

function pwPollSimilar(taskId) {
  const pid = paperState.paperId;
  pwWatchTask(pid, taskId, {
    statusUrl: `/api/paper/${encodeURIComponent(pid)}/similar/${taskId}`,
    interval: 1000,
    timerKey: "simTimer",
    isCurrent: () => paperState.paperId === pid,
    onView: (t) => {
      $("paper-similar-status").textContent = pwStageText(t.stage) || "检索中...";
      pwTaskProgress("paper-similar", t.progress || 0, t.stage || "检索中…");
      if (t.status === "done") {
        paperState.similarTaskId = "";
        pwSimilarBusy(false);
        pwTaskProgress("paper-similar", 100, "检索完成");
        setTimeout(() => pwTaskProgressHide("paper-similar"), 800);
        pwRenderSimilar(t.report || { items: [] });
        if (t.report && t.report.error) pwAlert(t.report.error, "warn", 4);
        return true;
      }
      if (t.status === "cancelled") {
        paperState.similarTaskId = "";
        pwSimilarBusy(false);
        pwTaskProgressHide("paper-similar");
        pwAlert("已取消相似论文检索。", "warn", 4);
        return true;
      }
      if (t.status === "error") {
        paperState.similarTaskId = "";
        pwSimilarBusy(false);
        pwTaskProgressHide("paper-similar");
        pwAlert("相似论文检索失败：" + (t.error || "未知错误"), "err", 4);
        return true;
      }
      return false;
    },
    onError: (e) => {
      paperState.similarTaskId = "";
      pwSimilarBusy(false);
      pwTaskProgressHide("paper-similar");
      pwAlert("查询检索进度失败：" + e.message, "err", 4);
    },
  });
}

async function pwCancelSimilar() {
  const taskId = paperState.similarTaskId;
  const pid = paperState.paperId;
  if (!taskId || !pid) return;
  $("btn-paper-similar-cancel").disabled = true;
  try {
    await postJSON(`/api/paper/${encodeURIComponent(pid)}/similar/${encodeURIComponent(taskId)}/cancel`, {});
  } finally {
    $("btn-paper-similar-cancel").disabled = false;
  }
}

function pwRenderSimilar(report) {
  const box = $("paper-similar-list");
  const items = (report && report.items) || [];
  if (!items.length) {
    box.innerHTML = `<p class="muted">${esc((report && report.error) || "暂无相似论文结果。")}</p>`;
    return;
  }
  const rows = items.map((it, i) => {
    const pct = Math.round((it.similarity || 0) * 100);
    const link = /^https?:\/\//i.test(it.url || "") ? it.url : (it.doi ? `https://doi.org/${it.doi}` : "");
    const title = link
      ? `<a href="${esc(link)}" target="_blank" rel="noopener">${esc(it.title)}</a>`
      : esc(it.title);
    const ptText = Array.isArray(it.similar_points)
      ? it.similar_points.join("；")
      : String(it.similar_points || "");
    return `<div class="similar-item">
      <div class="similar-head">
        <span class="similar-rank">${i + 1}</span>
        <div><div class="similar-title">${title}</div>
        <div class="muted">${esc(it.authors || "作者未知")} ${it.year ? "· " + esc(it.year) : ""} ${it.journal ? "· " + esc(it.journal) : ""}</div></div>
        <span class="similar-score">${pct}%</span>
      </div>
      ${it.abstract ? `<p class="similar-abstract">${esc(String(it.abstract).slice(0, 220))}${String(it.abstract).length > 220 ? "..." : ""}</p>` : ""}
      ${ptText ? `<p class="similar-points">${esc(ptText)}</p>` : ""}
      <div class="similar-foot">
        ${it.cited_by ? `<span class="muted">被引 ${it.cited_by}</span>` : ""}
        ${it.oa ? '<span class="chip oa">开放获取</span>' : ""}
      </div>
    </div>`;
  });
  box.innerHTML = `<div class="muted">更新时间：${esc((report && report.updated_at) || "-")}（缓存 7 天）</div>${rows.join("")}`;
}

// ---------- 段落 ----------
async function pwLoadParagraphs() {
  if (!paperState.paperId) return;
  const pid = paperState.paperId;
  const seq = ++paperState.paragraphSeq;
  const section = $("paper-section-filter").value;
  const qs = section ? `?section=${encodeURIComponent(section)}` : "";
  try {
    const data = await api(`/api/paper/${encodeURIComponent(paperState.paperId)}/paragraphs${qs}`);
    if (pid !== paperState.paperId || seq !== paperState.paragraphSeq) return;
    paperState.sections = data.sections || [];
    paperState.paragraphs = (data.paragraphs || []).filter(
      (p) => p.type === "body" && !p.table_id
    );
    paperState.tables = data.tables || [];
    pwFillSectionFilter();
    pwRenderParagraphs();
  } catch (e) {
    if (pid !== paperState.paperId || seq !== paperState.paragraphSeq) return;
    pwAlert("加载段落失败：" + e.message, "err");
  }
}

function pwFillSectionFilter() {
  const sel = $("paper-section-filter");
  const cur = sel.value;
  sel.innerHTML = '<option value="">全部章节</option>';
  (paperState.sections || []).forEach((s) => {
    if (!s.title) return;
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = s.title;
    sel.appendChild(opt);
  });
  if (cur && Array.from(sel.options).some((o) => o.value === cur)) sel.value = cur;
}

function pwRenderParagraphs() {
  const box = $("paper-paragraph-list");
  const list = paperState.paragraphs || [];
  if (!list.length) {
    box.innerHTML = '<p class="muted">没有可显示的段落。</p>';
    pwUpdateBatchBar();
    return;
  }
  box.innerHTML = list.map((p) => {
    const sec = p.section ? ` · ${esc(p.section)}` : "";
    const type = p.type === "body" ? "" : `<span class="chip">${esc(p.type)}</span>`;
    const selectable = p.type === "body" && (p.text || "").length <= pwRewriteLimit();
    const checked = selectable && paperState.batchIds.has(p.id);
    const check = selectable
      ? `<input type="checkbox" class="para-check" data-id="${esc(p.id)}"${checked ? " checked" : ""} title="勾选后可批量改写">`
      : '<span class="para-check-spacer"></span>';
    return `<div class="paper-para-row${checked ? " batch-active" : ""}" data-row="${esc(p.id)}">
      ${check}
      <button class="paper-para-item${paperState.selected && paperState.selected.id === p.id ? " active" : ""}" data-id="${esc(p.id)}">
        <span class="para-tag">${esc(p.id)} · 第 ${p.page || "-"} 页${sec} ${type}</span>
        <span class="para-text">${esc((p.text || "").slice(0, 120))}</span>
        <span class="para-len">${(p.text || "").length} 字</span>
      </button>
    </div>`;
  }).join("");
  box.querySelectorAll(".paper-para-item").forEach((btn) => {
    btn.addEventListener("click", () => pwSelectParagraph(btn.dataset.id).catch(pwError));
  });
  box.querySelectorAll(".para-check").forEach((el) => {
    el.addEventListener("change", () => {
      if (el.checked) paperState.batchIds.add(el.dataset.id);
      else paperState.batchIds.delete(el.dataset.id);
      const row = el.closest(".paper-para-row");
      if (row) row.classList.toggle("batch-active", el.checked);
      pwUpdateBatchBar();
    });
  });
  pwUpdateBatchBar();
}

function pwSelectedBatchIds() {
  const alive = new Set((paperState.paragraphs || []).map((p) => p.id));
  return Array.from(paperState.batchIds).filter((id) => alive.has(id));
}

function pwUpdateBatchBar() {
  const ids = pwSelectedBatchIds();
  const btn = $("btn-paper-batch");
  btn.disabled = !ids.length || !!paperState.batchTaskId;
  btn.textContent = `批量改写选中（${ids.length}）`;
}

function pwClearBatchSelection() {
  paperState.batchIds.clear();
  document.querySelectorAll("#paper-paragraph-list .para-check").forEach((el) => {
    el.checked = false;
  });
  document.querySelectorAll("#paper-paragraph-list .paper-para-row").forEach((row) => {
    row.classList.remove("batch-active");
  });
  pwUpdateBatchBar();
}

async function pwBatchHighRisk() {
  if (!paperState.paperId) { pwAlert("请先上传论文", "warn"); return; }
  const ids = Array.isArray(paperState.highRiskIds) ? paperState.highRiskIds : [];
  if (!ids.length) { pwAlert("当前诊断暂无可定位的高风险段落。", "warn", 3); return; }
  // 清空章节筛选，确保跨章节的高风险段落都能被勾选到
  $("paper-section-filter").value = "";
  pwSetStep(5, true);
  await pwLoadParagraphs();
  const limit = pwRewriteLimit();
  const alive = new Map((paperState.paragraphs || []).map((p) => [p.id, p]));
  const picked = ids.filter((id) => {
    const p = alive.get(id);
    return p && p.type === "body" && !p.table_id && (p.text || "").length <= limit;
  });
  if (!picked.length) {
    pwAlert("高风险段落都不属于可改写正文（可能已变更、超长或被拆表），请在左侧手动勾选。", "warn", 5);
    return;
  }
  const capped = picked.slice(0, 8);
  const overflow = picked.length - capped.length;
  paperState.batchIds = new Set(capped);
  pwRenderParagraphs();
  const sel = $("paper-mode");
  if (!pwHasOption(sel, "deai")) {
    const opt = document.createElement("option");
    opt.value = "deai";
    opt.textContent = PAPER_MODE_LABELS.deai || "deai";
    sel.appendChild(opt);
  }
  sel.value = "deai";
  pwAlert(`已勾选 ${capped.length} 段高风险正文，模式为「去 AI 味」`
    + (overflow ? `；另有 ${overflow} 段超出单次 8 段上限，可改写后再处理` : "")
    + "。确认后点击「批量改写选中」。", overflow ? "warn" : "ok", 5);
}

async function pwBatchRewrite() {
  if (!paperState.paperId) { pwAlert("请先上传论文", "warn"); return; }
  if (paperState.rewriteTaskId) {
    pwAlert("单段改写正在进行中，请等待完成或先取消单段任务。", "warn");
    return;
  }
  const ids = pwSelectedBatchIds();
  if (!ids.length) { pwAlert("请先勾选要批量改写的正文段落", "warn"); return; }
  if (ids.length > 8) { pwAlert("一次最多批量改写 8 个段落", "warn"); return; }
  const btn = $("btn-paper-batch");
  btn.disabled = true;
  paperState.batchTaskId = "";
  paperState.batchPaperId = paperState.paperId;
  paperState.batchResults = [];
  paperState.batchErrors = [];
  $("paper-batch-results").innerHTML = "";
  pwSyncBatchAdoptButton();
  pwTaskProgress("paper-batch", 0, "正在准备批量改写…");
  try {
    const r = await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/rewrite/batch`, {
      paragraph_ids: ids,
      mode: $("paper-mode").value || "deai",
      instruction: $("paper-instruction").value.trim(),
      use_guideline: !!(paperState.guideline && $("paper-guide-use").checked),
    });
    paperState.batchTaskId = r.task_id;
    pwPollBatch(r.task_id);
  } catch (e) {
    paperState.batchTaskId = "";
    pwTaskProgressHide("paper-batch");
    btn.disabled = false;
    pwAlert(e.message, "err", 5);
  }
}

function pwPollBatch(taskId) {
  const pid = paperState.paperId;
  pwWatchTask(pid, taskId, {
    statusUrl: `/api/paper/${encodeURIComponent(pid)}/rewrite/${taskId}`,
    interval: 1200,
    timerKey: "batchTimer",
    isCurrent: () => paperState.paperId === pid,
    onView: (t) => {
      const lastLog = (t.logs || []).slice(-1)[0];
      pwTaskProgress("paper-batch", t.progress || 0, lastLog || "正在批量改写…");
      if (t.status === "done" || t.status === "cancelled") {
        const report = t.report || {};
        paperState.batchResults = report.items || [];
        paperState.batchErrors = report.errors || [];
        paperState.batchTaskId = "";
        pwRenderBatchResults();
        $("btn-paper-batch").disabled = false;
        if (t.status === "cancelled") {
          pwAlert(`已取消批量改写，已完成 ${paperState.batchResults.length} 段，可查看已完成结果。`, "warn", 5);
        } else {
          const failed = paperState.batchErrors.length;
          pwAlert(`批量改写完成：成功 ${paperState.batchResults.length} 段`
            + (failed ? `，失败 ${failed} 段` : "") + "。请逐段查看差异，或使用全部采纳成功段。", "ok", 5);
          pwRefreshQuota();
        }
        pwTaskProgress("paper-batch", 100, t.status === "cancelled" ? "已取消" : "批量改写完成");
        setTimeout(() => pwTaskProgressHide("paper-batch"), 900);
        return true;
      }
      if (t.status === "error") {
        paperState.batchTaskId = "";
        pwTaskProgressHide("paper-batch");
        $("btn-paper-batch").disabled = false;
        pwSyncBatchAdoptButton();
        pwAlert("批量改写失败：" + (t.error || "未知错误"), "err", 5);
        return true;
      }
      return false;
    },
    onError: (e) => {
      paperState.batchTaskId = "";
      pwTaskProgressHide("paper-batch");
      $("btn-paper-batch").disabled = false;
      pwSyncBatchAdoptButton();
      pwAlert("查询批量改写进度失败：" + e.message, "err", 5);
    },
  });
}

async function pwCancelBatch() {
  const taskId = paperState.batchTaskId;
  if (!taskId) return;
  $("btn-paper-batch-cancel").disabled = true;
  try {
    await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/rewrite/${encodeURIComponent(taskId)}/cancel`, {});
  } finally {
    $("btn-paper-batch-cancel").disabled = false;
  }
}

function pwSyncBatchAdoptButton() {
  const btn = $("btn-paper-batch-adopt");
  const count = (paperState.batchResults || []).length;
  btn.textContent = `全部采纳成功段（${count}）`;
  btn.classList.toggle("hidden", count === 0);
  btn.disabled = count === 0 || !!paperState.batchTaskId;
}

function pwRenderBatchResults() {
  const box = $("paper-batch-results");
  paperState.batchResults = (paperState.batchResults || []).filter((item) => {
    const error = pwRewriteLanguageError(item);
    if (!error) return true;
    paperState.batchErrors.push({ paragraph_id: item.paragraph_id, error });
    return false;
  });
  const items = paperState.batchResults || [];
  const errors = paperState.batchErrors || [];
  if (!items.length && !errors.length) {
    box.innerHTML = "";
    pwSyncBatchAdoptButton();
    return;
  }
  const rows = items.map((r) => {
    const before = Number(r.ai_score_before || 0);
    const after = Number(r.ai_score_after || 0);
    const hasVersion = r.source_version !== undefined && r.source_version !== null;
    const current = Number((paperState.paper && paperState.paper.current_version) || 0);
    const versionTag = hasVersion
      ? ` · 基于 v${r.source_version}${Number(r.source_version) !== current ? "（论文已更新）" : ""}`
      : "";
    return `<div class="batch-item">
      <div class="batch-meta"><b>${esc(r.paragraph_id)} · 已完成</b>AI 味 ${before} → ${after} · ${(r.revised || "").length} 字${versionTag}</div>
      <button class="btn btn-ghost" data-batch-pid="${esc(r.paragraph_id)}">查看差异</button>
    </div>`;
  });
  const errRows = errors.map((e) => `<div class="batch-item err">
      <div class="batch-meta"><b>${esc(e.paragraph_id)} · 失败</b>${esc(e.error || "未知错误")}</div>
    </div>`);
  box.innerHTML = `<div class="muted">批量结果：可逐段查看并采纳；全部采纳会一次保存为同一版本，任一段正文变化或校验失败则全部不保存，失败段不会保存。</div>${rows.concat(errRows).join("")}`;
  pwSyncBatchAdoptButton();
}

async function pwShowBatchResult(item) {
  await pwSelectParagraph(item.paragraph_id);
  paperState.rewrite = item;
  pwRenderDiff(item);
}

async function pwSelectParagraph(pid) {
  let p = (paperState.paragraphs || []).find((x) => x.id === pid);
  if (!p) {
    $("paper-section-filter").value = "";
    await pwLoadParagraphs();
    p = (paperState.paragraphs || []).find((x) => x.id === pid);
  }
  if (!p) { pwAlert("段落不存在或已被删除", "warn"); return; }
  paperState.selected = p;
  paperState.rewrite = null;
  document.querySelectorAll(".paper-para-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.id === pid);
  });
  $("paper-original").textContent = p.text || "";
  pwClearDiff();
  $("paper-rewrite-meta").textContent = `${p.id} · 第 ${p.page || "-"} 页 · ${(p.text || "").length} 字`;
  $("paper-rewrite-warning").classList.add("hidden");
  $("btn-paper-adopt").classList.add("hidden");
  pwSyncInstructionWithParagraph(pid);
}

// ---------- 全局改写方针 ----------
async function pwLoadGuideline() {
  if (!paperState.paperId) return;
  const pid = paperState.paperId;
  const seq = paperState.loadSeq;
  try {
    const r = await api(`/api/paper/${encodeURIComponent(paperState.paperId)}/rewrite/guideline`);
    if (pid !== paperState.paperId || seq !== paperState.loadSeq) return;
    pwRenderGuideline(r);
  } catch (e) {
    if (pid !== paperState.paperId || seq !== paperState.loadSeq) return;
    pwRenderGuideline({ guideline: null });
  }
}

function pwRenderGuideline(r) {
  const g = r && r.guideline;
  const body = $("paper-guide-body");
  const toggle = $("paper-guide-toggle");
  const status = $("paper-guide-status");
  const btn = $("btn-paper-guide");
  if (!g) {
    paperState.guideline = null;
    body.classList.add("hidden");
    body.innerHTML = "";
    toggle.classList.add("hidden");
    status.classList.remove("stale");
    status.textContent = "先分析全文术语、重复句式与章节过渡，再逐段改写。";
    btn.textContent = "生成方针";
    return;
  }
  paperState.guideline = g;
  btn.textContent = "重新生成";
  toggle.classList.remove("hidden");
  status.classList.toggle("stale", !!(r && r.stale));
  status.textContent = r && r.stale
    ? `方针基于 v${g.version || 0} 生成，当前已是 v${r.current_version || 0}，建议重新生成。`
    : `方针基于 v${g.version || 0} 生成 · ${g.generated_at || ""}`;

  const parts = [];
  if (g.summary) {
    parts.push(`<p class="paper-guide-summary">${esc(g.summary)}</p>`);
  }
  if ((g.terminology || []).length) {
    parts.push(`<div class="paper-guide-group"><b>术语统一</b><ul>${g.terminology.map((x) =>
      `<li><span>${esc(x.term)}</span> → <span>${esc(x.preferred)}</span>${x.reason ? `（${esc(x.reason)}）` : ""}</li>`
    ).join("")}</ul></div>`);
  }
  if ((g.repeated_patterns || []).length) {
    parts.push(`<div class="paper-guide-group"><b>跨段重复句式</b><ul>${g.repeated_patterns.map((x) => {
      const examples = (x.examples || []).length ? `；示例：${esc(x.examples.join(" / "))}` : "";
      const advice = x.advice ? `；${esc(x.advice)}` : "";
      return `<li>${esc(x.pattern)}${examples}${advice}</li>`;
    }).join("")}</ul></div>`);
  }
  if ((g.transitions || []).length) {
    parts.push(`<div class="paper-guide-group"><b>过渡衔接</b><ul>${g.transitions.map((x) =>
      `<li>${esc(x)}</li>`
    ).join("")}</ul></div>`);
  }
  if ((g.style_rules || []).length) {
    parts.push(`<div class="paper-guide-group"><b>全篇统一规则</b><ul>${g.style_rules.map((x) =>
      `<li>${esc(x)}</li>`
    ).join("")}</ul></div>`);
  }
  body.innerHTML = parts.join("");
  body.classList.remove("hidden");
}

function pwGuideProgress(percent, stage) {
  pwTaskProgress("paper-guide", percent, stage);
  if (stage) {
    $("paper-guide-status").textContent = stage;
  }
}

function pwGuideProgressHide() {
  pwTaskProgressHide("paper-guide");
  $("btn-paper-guide-cancel").disabled = false;
}

function pwGuideFinish() {
  if (paperState.guideTimer) { clearTimeout(paperState.guideTimer); paperState.guideTimer = null; }
  paperState.guideTaskId = "";
  paperState.guidePaperId = "";
  pwGuideProgressHide();
  $("btn-paper-guide").disabled = false;
}

function pwPollGuideline(taskId, pid) {
  pwWatchTask(pid, taskId, {
    statusUrl: `/api/paper/${encodeURIComponent(pid)}/rewrite/guideline/${encodeURIComponent(taskId)}`,
    interval: 1200,
    timerKey: "guideTimer",
    isCurrent: () => paperState.guideTaskId === taskId && paperState.guidePaperId === pid,
    onView: (t) => {
      const logs = t.logs || [];
      const stage = logs.length ? logs[logs.length - 1] : "正在生成方针…";
      pwGuideProgress(t.progress, stage);
      if (t.status === "done") {
        const same = paperState.paperId === pid;
        pwGuideFinish();
        if (same && t.guideline) {
          pwRenderGuideline({ guideline: t.guideline, cached: false, stale: false });
        }
        pwRefreshQuota();
        pwAlert("全局改写方针已生成，逐段改写时会自动套用。", "ok", 5);
        return true;
      }
      if (t.status === "cancelled") {
        pwGuideFinish();
        $("paper-guide-status").textContent = "已取消生成，本次结果不保存。";
        pwAlert("已取消生成全局改写方针。", "warn", 5);
        return true;
      }
      if (t.status === "error") {
        pwGuideFinish();
        $("paper-guide-status").textContent = "方针生成失败，请稍后重试。";
        pwAlert(t.error || "方针生成失败", "err", 5);
        return true;
      }
      return false;
    },
    onError: (e) => {
      pwGuideFinish();
      $("paper-guide-status").textContent = "查询方针进度失败，请稍后重试。";
      pwAlert("查询方针进度失败：" + e.message, "err", 5);
    },
  });
}

async function pwGenerateGuideline() {
  if (!paperState.paperId) { pwAlert("请先上传或选择论文", "warn"); return; }
  if (paperState.guideTaskId) return;
  const btn = $("btn-paper-guide");
  const status = $("paper-guide-status");
  btn.disabled = true;
  $("btn-paper-guide-cancel").disabled = false;
  status.classList.remove("stale");
  status.textContent = "正在准备生成…";
  pwGuideProgress(0, "正在提交任务…");
  try {
    const r = await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/rewrite/guideline`, {});
    if (!r.task_id) throw new Error("未返回方针生成任务 ID");
    paperState.guideTaskId = r.task_id;
    paperState.guidePaperId = paperState.paperId;
    pwPollGuideline(r.task_id, paperState.guidePaperId);
  } catch (e) {
    pwGuideFinish();
    status.textContent = "方针生成失败，请稍后重试。";
    pwAlert(e.message, "err", 5);
  }
}

async function pwCancelGuideline() {
  const pid = paperState.guidePaperId;
  const taskId = paperState.guideTaskId;
  if (!pid || !taskId) return;
  const btn = $("btn-paper-guide-cancel");
  btn.disabled = true;
  try {
    await postJSON(`/api/paper/${encodeURIComponent(pid)}/rewrite/guideline/${encodeURIComponent(taskId)}/cancel`, {});
    $("paper-guide-stage").textContent = "正在取消…";
  } catch (e) {
    btn.disabled = false;
    pwAlert("取消失败：" + e.message, "err", 5);
  }
}

// ---------- 改写 ----------
function pwRewriteBusy(busy) {
  $("btn-paper-rewrite").disabled = busy;
  const cancel = $("btn-paper-rewrite-cancel");
  cancel.classList.toggle("hidden", !busy);
  cancel.disabled = false;
  if (busy) $("paper-rewrite-progress").classList.remove("hidden");
}

async function pwRewrite() {
  if (!paperState.paperId) { pwAlert("请先上传论文", "warn"); return; }
  if (!paperState.selected) { pwAlert("请先在左侧选择一个段落", "warn"); return; }
  const limit = pwRewriteLimit();
  if ((paperState.selected.text || "").length > limit) {
    pwAlert(`该段超过 ${limit} 字，请先在原文中拆分后再改写。`, "warn");
    return;
  }
  if (paperState.rewriteTaskId) return;
  if (paperState.batchTaskId) {
    pwAlert("批量改写正在进行中，请等待完成或先取消批量任务。", "warn");
    return;
  }
  paperState.rewritePaperId = paperState.paperId;
  paperState.rewriteParaId = paperState.selected.id;
  pwRewriteBusy(true);
  pwTaskProgress("paper-rewrite", 0, "正在提交改写任务…");
  $("paper-rewrite-meta").textContent = "生成中...";
  $("paper-rewrite-warning").classList.add("hidden");
  $("btn-paper-adopt").classList.add("hidden");
  try {
    const r = await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/rewrite`, {
      paragraph_id: paperState.selected.id,
      mode: $("paper-mode").value || "deai",
      instruction: $("paper-instruction").value.trim(),
      use_guideline: !!(paperState.guideline && $("paper-guide-use").checked),
    });
    if (!r.task_id) throw new Error("未返回改写任务 ID");
    paperState.rewriteTaskId = r.task_id;
    pwPollRewrite(r.task_id, paperState.rewritePaperId);
  } catch (e) {
    paperState.rewriteTaskId = "";
    paperState.rewritePaperId = "";
    paperState.rewriteParaId = "";
    pwRewriteBusy(false);
    pwTaskProgressHide("paper-rewrite");
    $("paper-rewrite-meta").textContent = "";
    pwAlert(e.message, "err", 5);
  }
}

function pwPollRewrite(taskId, pid) {
  pwWatchTask(pid, taskId, {
    statusUrl: `/api/paper/${encodeURIComponent(pid)}/rewrite/${taskId}`,
    interval: 1000,
    timerKey: "rewriteTimer",
    isCurrent: () => paperState.paperId === pid && paperState.rewriteTaskId === taskId,
    onView: async (t) => {
      const lastLog = (t.logs || []).slice(-1)[0];
      pwTaskProgress("paper-rewrite", t.progress || 0, lastLog || "模型正在改写…");
      if (t.status === "done") {
        const result = t.result || {};
        paperState.rewriteTaskId = "";
        paperState.rewritePaperId = "";
        paperState.rewriteParaId = "";
        if (!paperState.selected || paperState.selected.id !== result.paragraph_id) {
          await pwSelectParagraph(result.paragraph_id).catch(() => {});
        }
        paperState.rewrite = result;
        pwRenderDiff(result);
        pwRewriteBusy(false);
        pwTaskProgress("paper-rewrite", 100, "改写完成");
        setTimeout(() => pwTaskProgressHide("paper-rewrite"), 800);
        pwRefreshQuota();
        return true;
      }
      if (t.status === "cancelled") {
        paperState.rewriteTaskId = "";
        paperState.rewritePaperId = "";
        paperState.rewriteParaId = "";
        pwRewriteBusy(false);
        pwTaskProgressHide("paper-rewrite");
        $("paper-rewrite-meta").textContent = "已取消改写，本次结果不保存。";
        pwAlert("已取消改写。", "warn", 5);
        return true;
      }
      if (t.status === "error") {
        paperState.rewriteTaskId = "";
        paperState.rewritePaperId = "";
        paperState.rewriteParaId = "";
        pwRewriteBusy(false);
        pwTaskProgressHide("paper-rewrite");
        $("paper-rewrite-meta").textContent = "";
        pwAlert("改写失败：" + (t.error || "未知错误"), "err", 5);
        return true;
      }
      return false;
    },
    onError: (e) => {
      paperState.rewriteTaskId = "";
      paperState.rewritePaperId = "";
      paperState.rewriteParaId = "";
      pwRewriteBusy(false);
      pwTaskProgressHide("paper-rewrite");
      pwAlert("查询改写进度失败：" + e.message, "err", 5);
    },
  });
}

async function pwCancelRewrite() {
  const pid = paperState.rewritePaperId || paperState.paperId;
  const taskId = paperState.rewriteTaskId;
  if (!pid || !taskId) return;
  const btn = $("btn-paper-rewrite-cancel");
  btn.disabled = true;
  try {
    await postJSON(`/api/paper/${encodeURIComponent(pid)}/rewrite/${encodeURIComponent(taskId)}/cancel`, {});
    $("paper-rewrite-stage").textContent = "正在取消，等待当前调用返回…";
  } catch (e) {
    btn.disabled = false;
    pwAlert("取消失败：" + e.message, "err", 5);
  }
}

function pwClearDiff() {
  paperState.diff = null;
  $("paper-diff").innerHTML = "";
  $("paper-diff-tools").classList.add("hidden");
  $("paper-diff-hint").textContent = "";
}

function pwGroupDiff(diff) {
  // 连续的 insert/delete 视为一处改动，用户可以逐处决定保留或还原
  const segs = [];
  const groups = [];
  let current = null;
  for (const d of diff || []) {
    if (d.type === "equal") {
      current = null;
      segs.push({ type: "equal", text: d.text || "" });
      continue;
    }
    if (!current) {
      current = { type: "change", ci: groups.length, keep: true, parts: [] };
      groups.push(current);
      segs.push(current);
    }
    current.parts.push(d);
  }
  for (const g of groups) {
    g.original = g.parts.filter((x) => x.type === "delete").map((x) => x.text || "").join("");
    g.revised = g.parts.filter((x) => x.type === "insert").map((x) => x.text || "").join("");
  }
  return { segs, groups };
}

function pwSyncDiffUI() {
  const d = paperState.diff;
  if (!d) return;
  document.querySelectorAll("#paper-diff .diff-change").forEach((el) => {
    const g = d.groups[Number(el.dataset.ci)];
    if (g) el.classList.toggle("is-reverted", !g.keep);
  });
  const kept = d.groups.filter((g) => g.keep).length;
  $("paper-diff-count").textContent = `共 ${d.groups.length} 处改动，已保留 ${kept} 处`;
  $("paper-diff-tools").classList.toggle("hidden", !d.groups.length);
}

function pwSetAllDiff(keep) {
  if (!paperState.diff) return;
  paperState.diff.groups.forEach((g) => { g.keep = keep; });
  pwSyncDiffUI();
}

// 按用户逐处保留/还原的结果拼出最终要保存的段落
function pwBuildRevised() {
  const d = paperState.diff;
  if (!d) return (paperState.rewrite && paperState.rewrite.revised) || "";
  return d.segs
    .map((s) => s.type === "equal" ? s.text : (s.keep ? s.revised : s.original))
    .join("");
}

function pwRewriteLanguageError(r) {
  // Mirror the backend's language categories, including for results created
  // before the running server was restarted after an upgrade.
  const language = (text) => {
    const cjk = (String(text || "").match(/[\u4e00-\u9fff]/g) || []).length;
    const latin = (String(text || "").match(/[a-zA-Z]/g) || []).length;
    if (!cjk && !latin) return "";
    const ratio = cjk / (cjk + latin);
    return ratio >= 0.3 ? "zh" : ratio <= 0.1 ? "en" : "mixed";
  };
  const original = language(r.original);
  const revised = language(r.revised);
  return original && original !== "mixed" && original !== revised
    ? "改写结果改变了原文语言，已阻止采纳。请重新生成；若刚更新项目，请先重启服务。"
    : "";
}

function pwRenderDiff(r) {
  const error = pwRewriteLanguageError(r);
  if (error) {
    paperState.rewrite = null;
    pwClearDiff();
    $("btn-paper-adopt").classList.add("hidden");
    $("paper-rewrite-warning").textContent = error;
    $("paper-rewrite-warning").classList.remove("hidden");
    return;
  }
  paperState.diff = pwGroupDiff(r.diff || []);
  const { segs, groups } = paperState.diff;
  $("paper-diff").innerHTML = segs.map((s) => {
    if (s.type === "equal") return `<span>${esc(s.text)}</span>`;
    const del = s.original ? `<del>${esc(s.original)}</del>` : "";
    const ins = s.revised ? `<ins>${esc(s.revised)}</ins>` : "";
    return `<span class="diff-change" data-ci="${s.ci}" title="点击切换：保留改写 / 还原原文">${del}${ins}</span>`;
  }).join("");
  pwSyncDiffUI();
  $("paper-diff-hint").textContent = groups.length
    ? "点击彩色片段可逐处切换；「采纳并保存」只写入当前保留的内容。"
    : "";

  const before = Number(r.ai_score_before || 0);
  const after = Number(r.ai_score_after || 0);
  const arrow = after < before ? "↓" : after > before ? "↑" : "→";
  const pid = r.paragraph_id || (paperState.selected && paperState.selected.id) || "";
  const hasVersion = r.source_version !== undefined && r.source_version !== null;
  const current = Number((paperState.paper && paperState.paper.current_version) || 0);
  const versionNote = hasVersion
    ? ` · 基于 v${r.source_version}${Number(r.source_version) !== current ? `（当前 v${current}）` : ""}`
    : "";
  $("paper-rewrite-meta").textContent =
    `${pid} · AI 味风险 ${before} ${arrow} ${after} · 原文 ${(r.original || "").length} 字 → 改写 ${(r.revised || "").length} 字`
    + (r.guideline_used ? " · 已套用全局方针" : "")
    + versionNote;
  const staleNote = hasVersion && Number(r.source_version) !== current
    ? `该改写基于 v${r.source_version} 生成，论文当前为 v${current}；只要本段正文未变仍可采纳。`
    : "";
  if (r.warning || staleNote) {
    $("paper-rewrite-warning").textContent = [staleNote, r.warning].filter(Boolean).join(" ");
    $("paper-rewrite-warning").classList.remove("hidden");
  } else {
    $("paper-rewrite-warning").classList.add("hidden");
  }
  $("btn-paper-adopt").classList.remove("hidden");
  if ((r.reasons || []).length) {
    $("paper-rewrite-meta").textContent += " · " + r.reasons.slice(0, 3).join("；");
  }
}

async function pwAdopt() {
  if (!paperState.paperId || !paperState.rewrite || !paperState.selected) return;
  const r = paperState.rewrite;
  if (r.paragraph_id && r.paragraph_id !== paperState.selected.id) {
    pwAlert("改写结果属于另一个段落，请先切回该段落再采纳。", "warn");
    return;
  }
  const revised = pwBuildRevised();
  const languageError = pwRewriteLanguageError({ original: r.original, revised });
  if (languageError) { pwAlert(languageError, "err", 5); return; }
  if (!revised.trim()) { pwAlert("当前没有可保存的改写内容", "warn"); return; }
  if (revised.trim() === (r.original || "").trim()) {
    pwAlert("当前未保留任何改写，请至少保留一处改动后再保存。", "warn");
    return;
  }
  const btn = $("btn-paper-adopt");
  const paperId = paperState.paperId;
  const paragraphId = paperState.selected.id;
  const operation = ++paperState.adoptionSeq;
  let loadSeq = paperState.loadSeq;
  const current = () => paperState.paperId === paperId
    && paperState.loadSeq === loadSeq && paperState.adoptionSeq === operation;
  btn.disabled = true;
  const payload = {
    paragraph_id: paragraphId,
    revised: revised,
  };
  if (r.source_version !== undefined && r.source_version !== null) {
    payload.expected_version = Number(r.source_version);
  }
  if (typeof r.original === "string") {
    payload.expected_original = r.original;
  }
  try {
    const res = await postJSON(`/api/paper/${encodeURIComponent(paperId)}/apply`, payload);
    if (!current() || paperState.rewrite !== r || paperState.selected?.id !== paragraphId) return;
    paperState.rewrite = null;
    paperState.diff = null;
    if ((paperState.batchResults || []).length) {
      paperState.batchResults = paperState.batchResults.filter(
        (x) => x.paragraph_id !== res.paragraph_id
      );
    }
    paperState.batchIds.delete(res.paragraph_id);
    await pwLoadList();
    if (!current()) return;
    const reloading = pwLoadPaper(paperId, { restoreStep: 5 });
    loadSeq = paperState.loadSeq;
    await reloading;
    if (!current()) return;
    await pwSelectParagraph(res.paragraph_id).catch(() => {});
    if (!current()) return;
    pwRenderBatchResults();
    pwSetStep(5, true);
    pwAlert(`已采纳并保存为 v${res.version}。如需回退，可删除后重新上传，或继续逐段优化。`, "ok");
  } catch (e) {
    if (!current()) return;
    if (e && e.status === 409) {
      // 保留当前差异对比，只刷新论文版本信息，便于用户重新生成或复制内容
      const body = e.payload || {};
      const detail = await api(`/api/paper/${encodeURIComponent(paperId)}`).catch(() => null);
      if (!current()) return;
      if (detail) {
        paperState.paper = detail;
        const archived = detail.archived_count ? `，已归档 ${detail.archived_count} 个` : "";
        $("paper-version").textContent = `当前版本 v${detail.current_version || 0}（共 ${detail.version_count || 1} 个版本${archived}）`;
      }
      await pwLoadList().catch(() => {});
      if (!current()) return;
      const cur = body.current_version != null ? body.current_version : "?";
      pwAlert(`论文已更新（当前 v${cur}）：${e.message} 当前差异对比仍保留在页面中。`, "warn", 5);
    } else {
      pwAlert("采纳失败：" + (e && e.message ? e.message : "未知错误"), "err", 5);
    }
  } finally {
    if (current()) btn.disabled = false;
  }
}

async function pwAdoptBatch() {
  const items = paperState.batchResults || [];
  if (!paperState.paperId || !items.length) return;
  const languageError = items.map(pwRewriteLanguageError).find(Boolean);
  if (languageError) { pwAlert(languageError, "err", 5); return; }
  if (paperState.batchTaskId) {
    pwAlert("批量改写仍在进行中，请等待完成或先取消任务。", "warn");
    return;
  }
  const btn = $("btn-paper-batch-adopt");
  const paperId = paperState.paperId;
  const operation = ++paperState.adoptionSeq;
  let loadSeq = paperState.loadSeq;
  const current = () => paperState.paperId === paperId
    && paperState.loadSeq === loadSeq && paperState.adoptionSeq === operation;
  btn.disabled = true;
  const payload = {
    items: items.map((r) => {
      const item = { paragraph_id: r.paragraph_id, revised: r.revised };
      if (r.source_version !== undefined && r.source_version !== null) {
        item.expected_version = Number(r.source_version);
      }
      if (typeof r.original === "string") item.expected_original = r.original;
      return item;
    }),
  };
  try {
    const res = await postJSON(
      `/api/paper/${encodeURIComponent(paperId)}/apply-batch`, payload
    );
    if (!current() || paperState.batchResults !== items) return;
    const adopted = new Set(res.paragraph_ids || items.map((x) => x.paragraph_id));
    paperState.batchResults = items.filter((x) => !adopted.has(x.paragraph_id));
    adopted.forEach((pid) => paperState.batchIds.delete(pid));
    paperState.rewrite = null;
    paperState.diff = null;
    await pwLoadList();
    if (!current()) return;
    const reloading = pwLoadPaper(paperId, { restoreStep: 5 });
    loadSeq = paperState.loadSeq;
    await reloading;
    if (!current()) return;
    pwRenderBatchResults();
    pwSetStep(5, true);
    pwAlert(`已批量采纳 ${adopted.size} 段，保存为 v${res.version}。`, "ok", 5);
  } catch (e) {
    if (!current()) return;
    if (e && e.status === 409) {
      const body = e.payload || {};
      const detail = await api(`/api/paper/${encodeURIComponent(paperId)}`).catch(() => null);
      if (!current()) return;
      if (detail) {
        paperState.paper = detail;
        const archived = detail.archived_count ? `，已归档 ${detail.archived_count} 个` : "";
        $("paper-version").textContent = `当前版本 v${detail.current_version || 0}（共 ${detail.version_count || 1} 个版本${archived}）`;
      }
      await pwLoadList().catch(() => {});
      if (!current()) return;
      const cur = body.current_version != null ? body.current_version : "?";
      pwAlert(`批量采纳未保存（当前 v${cur}）：${e.message}`, "warn", 5);
    } else {
      pwAlert("批量采纳失败：" + (e && e.message ? e.message : "未知错误"), "err", 5);
    }
  } finally {
    if (current()) pwSyncBatchAdoptButton();
  }
}

function pwExportFilename(resp, format, fallbackName) {
  const fallback = fallbackName || (format === "summary" ? "论文修改说明.docx" : `optimized.${format}`);
  const cd = resp.headers.get("Content-Disposition") || "";
  const m = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  if (m) {
    try { return decodeURIComponent(m[1].trim()); } catch (e) {}
  }
  const plain = /filename="?([^";]+)"?/i.exec(cd);
  return plain ? plain[1].trim() : fallback;
}

async function pwTrackedFormatMode() {
  // 原文是 docx 时优先在原文件上写修订；字体不符合论文规范时先询问用户
  try {
    const check = await api(`/api/paper/${encodeURIComponent(paperState.paperId)}/format-check`);
    if (!check || !check.has_original_docx) return "auto";
    if (check.compliant) return "original";
    const issues = (check.issues || []).map((x) => "· " + x).join("\n");
    const req = check.required || {};
    const spec = [
      `中文 ${req.font_body || "-"} / 英文 ${req.font_latin || "-"}`,
      `字号 ${req.font_size_body_pt != null ? req.font_size_body_pt : "-"} 磅`,
      `行距 ${req.line_spacing != null ? req.line_spacing : "-"} 倍`,
      `段前 ${req.space_before_pt != null ? req.space_before_pt : "-"} 磅 / 段后 ${req.space_after_pt != null ? req.space_after_pt : "-"} 磅`,
      req.first_line_indent_chars ? `首行缩进 ${req.first_line_indent_chars} 字符` : "无首行缩进",
      req.page_break_before_heading ? "每章另起一页" : "不强制章节分页",
    ].join("；");
    const adjust = confirm(
      `原文格式与论文规范不一致：\n${issues}\n\n规范：${spec}\n\n` +
      "点击“确定”按论文格式调整后再导出；点击“取消”完全保留原文格式导出。"
    );
    return adjust ? "thesis" : "original";
  } catch (e) {
    return "auto";
  }
}

async function pwExport(format = "docx", tracked = false) {
  if (!paperState.paperId) { pwAlert("请先上传论文", "warn"); return; }
  try {
    const mode = tracked ? await pwTrackedFormatMode() : "";
    const query = `format=${format}`
      + (tracked ? `&tracked=true&format_mode=${encodeURIComponent(mode)}` : "");
    const resp = await fetch(`/api/paper/${encodeURIComponent(paperState.paperId)}/export?${query}`);
    if (!resp.ok) {
      let detail = "";
      try { detail = (await resp.json()).detail || ""; } catch (e) {}
      throw new Error(detail || `HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = pwExportFilename(resp, format, tracked ? `${pwPaperStem()}_修订版.docx` : "");
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    if (tracked) {
      pwAlert(
        mode === "thesis"
          ? "修订版已导出：正文按论文格式调整，修订标记可在 Word 中逐条接受或拒绝。"
          : "修订版已导出：保留原文封面、目录、图片与字体，可在 Word 中逐条接受或拒绝修订。",
        "ok", 5
      );
    } else if (format === "summary") {
      pwAlert("《论文修改说明》已导出，可直接随修改稿一起提交。", "ok", 5);
    }
  } catch (e) {
    pwAlert("导出失败：" + e.message, "err");
  }
}

// ---------- 备份 / 恢复 / 版本整理 ----------
function pwPaperStem() {
  const p = paperState.paper || {};
  const raw = String(p.filename || p.title || "论文").split(/[\\/]/).pop();
  return (raw.replace(/\.[^.]+$/, "") || "论文").replace(/[\\/:*?"<>|]+/g, "_").slice(0, 80);
}

async function pwBackup() {
  if (!paperState.paperId) { pwAlert("请先上传或选择论文", "warn"); return; }
  try {
    const resp = await fetch(`/api/paper/${encodeURIComponent(paperState.paperId)}/backup`);
    if (!resp.ok) {
      let detail = "";
      try { detail = (await resp.json()).detail || ""; } catch (e) {}
      throw new Error(detail || `HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = pwExportFilename(resp, "zip", `${pwPaperStem()}_备份.zip`);
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    pwAlert("备份已下载：包含论文原文、需求、诊断、版本与已归档版本。", "ok", 5);
  } catch (e) {
    pwAlert("备份失败：" + e.message, "err");
  }
}

async function pwRestoreBackup(file) {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch("/api/paper/restore", { method: "POST", body: form });
  if (!resp.ok) {
    let detail = "";
    try { detail = (await resp.json()).detail || ""; } catch (e) {}
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  const result = await resp.json();
  await pwLoadList();
  await pwLoadPaper(result.paper_id);
  pwAlert(`备份已恢复为《${result.title || result.filename || result.paper_id}》，原论文未被修改。`, "ok");
}

async function pwCleanupVersions() {
  if (!paperState.paperId) { pwAlert("请先上传或选择论文", "warn"); return; }
  const raw = window.prompt("除原始版本 v0 和当前版本外，保留最近几个版本？", "5");
  if (raw === null) return;
  const keep = Number(String(raw).trim());
  if (!Number.isInteger(keep) || keep < 0 || keep > 100) {
    pwAlert("请输入 0-100 之间的整数。", "warn");
    return;
  }
  if (!window.confirm(
    "较早版本会压缩归档到本机，仍可在历史版本接口中读取；原始版本 v0 和当前版本始终保留。继续？"
  )) return;
  const res = await postJSON(
    `/api/paper/${encodeURIComponent(paperState.paperId)}/versions/cleanup`,
    { keep_recent: keep }
  );
  const archived = (res.archived_versions || []).length;
  if (paperState.paper) {
    paperState.paper.version_count = res.version_count;
    paperState.paper.archived_count = res.archived_count;
  }
  const archivedNote = res.archived_count ? `，已归档 ${res.archived_count} 个` : "";
  $("paper-version").textContent = `当前版本 v${res.current_version}（共 ${res.version_count} 个版本${archivedNote}）`;
  pwAlert(archived
    ? `已归档 ${archived} 个较早版本，保留原始版本、当前版本和最近 ${keep} 个版本。`
    : "没有可整理的旧版本。", "ok", 5);
}

// ---------- 删除 ----------
async function pwDelete() {
  if (!paperState.paperId) { pwAlert("请先选择论文", "warn"); return; }
  const title = (paperState.paper && paperState.paper.title) || paperState.paperId;
  if (!window.confirm(`确定删除《${title}》吗？论文原文、诊断与版本记录都会一并删除，且不可恢复。`)) return;
  await api(`/api/paper/${encodeURIComponent(paperState.paperId)}`, { method: "DELETE" });
  pwClearLastState();
  pwResetPaper();
  await pwLoadList();
  pwAlert("论文已删除。", "ok");
}

// ---------- 论文问答 ----------
function pwAskOpen(open) {
  const dock = $("paper-ask-dock");
  dock.classList.toggle("open", open);
  $("btn-ask-toggle").setAttribute("aria-expanded", String(open));
  if (open) {
    // 等宽度过渡启动后再聚焦，避免输入框在折叠状态下抢焦点
    setTimeout(() => $("paper-ask-input").focus(), 60);
  } else {
    $("paper-ask-input").blur();
  }
}

function pwRenderAskBasis(basis, evidence) {
  const items = [];
  const seen = new Set();
  const info = new Map();
  const add = (id, kind, meta) => {
    const key = `${kind}:${id}`;
    if (!id || seen.has(key)) return;
    seen.add(key);
    items.push({ id, kind });
    if (meta) info.set(key, meta);
  };
  (evidence || []).forEach((item) => {
    if (!item || !item.id) return;
    const kind = item.kind === "table" ? "table" : "paragraph";
    add(String(item.id).trim(), kind, item);
  });
  (basis || []).forEach((id) => {
    const value = String(id || "").trim();
    if (!value) return;
    add(value, value.startsWith("t_") ? "table" : "paragraph");
  });
  if (!items.length) return "";
  return `<div class="ask-basis"><span>依据</span>${items.map((item) => {
    const table = item.kind === "table";
    const meta = info.get(`${item.kind}:${item.id}`) || {};
    let label = item.id;
    if (table) {
      const caption = String(meta.caption || "").trim();
      label = caption ? `${item.id} ${caption.slice(0, 24)}` : `${item.id} 表格`;
    }
    const text = table ? "查看表格" : "查看原文段落";
    return `<button type="button" class="chip basis-source-link${table ? " basis-table" : ""}" data-ask-basis="${esc(item.id)}" data-ask-kind="${item.kind}" aria-label="${text} ${esc(item.id)}" title="${text}">${esc(label)}</button>`;
  }).join("")}</div>`;
}

function pwCloseTable() {
  const modal = $("paper-table-modal");
  if (modal) modal.classList.add("hidden");
}

async function pwOpenTable(tableId) {
  if (!paperState.paperId || !tableId) return;
  let tables = paperState.tables || [];
  if (!tables.some((t) => t && t.id === tableId)) {
    try {
      const data = await api(`/api/paper/${encodeURIComponent(paperState.paperId)}/tables`);
      paperState.tables = data.tables || [];
      tables = paperState.tables;
    } catch (e) {
      pwAlert("加载表格失败：" + e.message, "err");
      return;
    }
  }
  const table = tables.find((t) => t && t.id === tableId);
  if (!table) {
    pwAlert(`未找到表格 ${tableId}，可能是旧版论文数据，请重新上传后定位。`, "warn");
    return;
  }
  const page = table.page ? `第 ${table.page} 页` : "页码未知";
  const meta = [table.id, page, table.section || "", table.source === "pdf" ? "PDF 识别" : "Word 读取"];
  $("paper-table-title").textContent = table.caption || `表格 ${table.id}`;
  $("paper-table-meta").textContent = meta.filter(Boolean).join(" · ");
  const rows = Array.isArray(table.rows) ? table.rows : [];
  const headerRows = Math.max(0, Math.min(Number(table.header_rows) || 0, rows.length));
  let html = "";
  if (rows.length) {
    const cells = (row, tag) => (row || []).map((cell) =>
      `<${tag}>${esc(String(cell == null ? "" : cell))}</${tag}>`).join("");
    const head = rows.slice(0, headerRows).map((row) => `<tr>${cells(row, "th")}</tr>`).join("");
    const body = rows.slice(headerRows).map((row) => `<tr>${cells(row, "td")}</tr>`).join("");
    html = `<table class="paper-table">${head ? `<thead>${head}</thead>` : ""}<tbody>${body}</tbody></table>`;
  } else if (table.fallback_text) {
    html = `<p class="muted">未能识别表格结构，以下为原文中保留的表格文字：</p><pre class="paper-table-fallback">${esc(table.fallback_text)}</pre>`;
  } else {
    html = '<p class="muted">该表格没有可显示的行列数据。</p>';
  }
  if (table.units) html += `<p class="paper-table-note">单位：${esc(table.units)}</p>`;
  (table.footnotes || []).forEach((note) => {
    html += `<p class="paper-table-note">${esc(note)}</p>`;
  });
  if (table.status === "fallback" && table.note) {
    html += `<p class="paper-table-note stale">${esc(table.note)}</p>`;
  }
  $("paper-table-view").innerHTML = html;
  $("paper-table-modal").classList.remove("hidden");
}

async function pwAskOpenBasis(id, kind) {
  if (!paperState.paperId || !id) return;
  if (kind === "table" || String(id).startsWith("t_")) {
    await pwOpenTable(id);
    return;
  }
  pwAskOpen(true);
  await pwGotoParagraph(id);
  if (!paperState.selected || paperState.selected.id !== id) return;
  $("paper-original").scrollIntoView({ behavior: "smooth", block: "center" });
}

async function pwAsk() {
  if ($("btn-paper-ask").disabled) return;
  if (!paperState.paperId) {
    pwAlert("请先上传论文，再围绕该论文提问。", "warn");
    pwSetStep(1);
    return;
  }
  pwAskOpen(true);
  const q = $("paper-ask-input").value.trim();
  if (!q) return;
  const pid = paperState.paperId;
  const seq = ++paperState.askSeq;
  const isCurrent = () => pid === paperState.paperId && seq === paperState.askSeq;
  const btn = $("btn-paper-ask");
  btn.disabled = true;
  $("paper-ask-answer").classList.remove("hidden");
  $("paper-ask-answer").innerHTML = '<span class="muted">思考中...</span>';
  try {
    const r = await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/ask`, { question: q });
    if (!isCurrent()) return;
    if (r.rejected) {
      $("paper-ask-answer").innerHTML = `<p class="muted">${esc(r.answer)}</p>`;
    } else {
      const basis = pwRenderAskBasis(r.basis, r.evidence);
      $("paper-ask-answer").innerHTML = `<div class="markdown">${renderMarkdown(r.answer)}</div>${basis}`;
      $("paper-ask-input").value = "";
      pwRefreshQuota();
    }
  } catch (e) {
    if (!isCurrent()) return;
    $("paper-ask-answer").innerHTML = `<span class="paper-error">${esc(e.message)}</span>`;
  } finally {
    if (isCurrent()) btn.disabled = false;
  }
}

// app.js 在 init 中调用；加载顺序在 app.js 之后时由 load 事件兜底
window.initPaperPage = initPaperPage;
