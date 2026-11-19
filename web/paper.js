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
  askDockBound: false,
  askAbort: null,
};

const PAPER_DIM_LABELS = {
  level: "论文层次",
  feasibility: "内容可实现性",
  format: "正文格式",
  deai: "AI 率",
};

const PAPER_DIM_ORDER = ["level", "feasibility", "format", "deai"];

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

function pwStepPanels() {
  return Array.from(document.querySelectorAll(".paper-step-panel"));
}

function pwSetStep(n, force) {
  if (!force && n > paperState.maxStep) return;
  paperState.step = n;
  document.querySelectorAll(".paper-step").forEach((b) => {
    const s = Number(b.dataset.step);
    b.classList.toggle("active", s === n);
    b.classList.toggle("done", s < paperState.maxStep);
  });
  pwStepPanels().forEach((p) => p.classList.toggle("active", p.id === `paper-step-${n}`));
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
  return `今日 LLM 配额：剩余 ${q.remaining} / 共 ${q.limit} 次`;
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
}

// ---------- 通用任务进度 ----------
const PAPER_STAGE_LABELS = {
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

function pwDiagProgress(percent, stage) {
  const box = $("paper-diag-progress");
  if (!box) return;
  box.classList.remove("hidden");
  const p = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
  $("paper-diag-fill").style.width = p + "%";
  $("paper-diag-percent").textContent = p + "%";
  if (stage) $("paper-diag-stage").textContent = stage;
}

function pwDiagProgressHide() {
  const box = $("paper-diag-progress");
  if (box) box.classList.add("hidden");
  const fill = $("paper-diag-fill");
  if (fill) fill.style.width = "0%";
  const pct = $("paper-diag-percent");
  if (pct) pct.textContent = "0%";
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
  $("paper-select").addEventListener("change", (e) => {
    if (e.target.value) pwLoadPaper(e.target.value).catch(pwError);
    else pwResetPaper();
  });
  $("btn-paper-refresh").addEventListener("click", () => {
    pwLoadList().catch(pwError);
    if (paperState.paperId) pwLoadPaper(paperState.paperId).catch(pwError);
  });
  $("btn-paper-new").addEventListener("click", () => pwNewPaper());
  $("btn-paper-delete").addEventListener("click", () => pwDelete().catch(pwError));
  $("btn-paper-confirm").addEventListener("click", () => pwConfirm().catch(pwError));
  $("btn-paper-upload-cancel").addEventListener("click", () => pwCancelUpload().catch(pwError));
  $("btn-paper-diagnose").addEventListener("click", () => pwStartDiagnose().catch(pwError));
  $("btn-paper-cancel").addEventListener("click", () => pwCancelDiagnose().catch(pwError));
  $("btn-paper-similar").addEventListener("click", () => pwStartSimilar(false).catch(pwError));
  $("btn-paper-similar-refresh").addEventListener("click", () => pwStartSimilar(true).catch(pwError));
  $("btn-paper-similar-cancel").addEventListener("click", () => pwCancelSimilar().catch(pwError));
  $("paper-section-filter").addEventListener("change", () => pwLoadParagraphs().catch(pwError));
  $("btn-paper-rewrite").addEventListener("click", () => pwRewrite().catch(pwError));
  $("btn-paper-rewrite-cancel").addEventListener("click", () => pwCancelRewrite().catch(pwError));
  $("btn-paper-batch").addEventListener("click", () => pwBatchRewrite().catch(pwError));
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
  $("btn-paper-ask").addEventListener("click", () => pwAsk().catch(pwError));
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
      if (e.key === "Escape" && $("paper-ask-dock").classList.contains("open")) {
        pwAskOpen(false);
      }
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
    const item = e.target.closest(".issue-item");
    if (!item || !item.dataset.pid) return;
    pwGotoParagraph(item.dataset.pid).catch(pwError);
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
}

function pwError(e) {
  console.error(e);
  pwAlert(e && e.message ? e.message : "操作失败，请稍后重试", "err");
}

// ---------- 列表 / 详情 ----------
async function pwLoadList() {
  const data = await api("/api/paper");
  const sel = $("paper-select");
  const keep = paperState.paperId || sel.value;
  sel.innerHTML = '<option value="">选择论文...</option>';
  (data.papers || []).forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.id;
    const title = p.title || p.filename || p.id;
    opt.textContent = `${title}（v${p.current_version || 0} · ${p.paragraph_count || 0} 段）`;
    sel.appendChild(opt);
  });
  if (keep && Array.from(sel.options).some((x) => x.value === keep)) sel.value = keep;
}

function pwResetPaper() {
  pwClearTimers();
  paperState.paperId = "";
  paperState.paper = null;
  paperState.selected = null;
  paperState.rewrite = null;
  paperState.guideline = null;
  paperState.guideTaskId = "";
  paperState.guidePaperId = "";
  pwGuideProgressHide();
  $("btn-paper-guide").disabled = false;
  paperState.maxStep = 1;
  paperState.paragraphs = [];
  paperState.sections = [];
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
  pwDiagProgressHide();
  pwDiagCancelReset();
  pwTaskProgressHide("paper-upload");
  pwTaskProgressHide("paper-similar");
  pwTaskProgressHide("paper-rewrite");
  pwTaskProgressHide("paper-batch");
  $("paper-batch-results").innerHTML = "";
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
  const p = await api(`/api/paper/${encodeURIComponent(paperId)}`);
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
    paperState.batchResults = [];
    paperState.batchErrors = [];
    paperState.batchIds = new Set();
    paperState.batchPaperId = "";
    paperState.batchTaskId = "";
    $("paper-batch-results").innerHTML = "";
    $("btn-paper-batch-cancel").disabled = false;
    pwTaskProgressHide("paper-batch");
  }
  if (paperState.diagPaperId && paperState.diagPaperId !== paperId) {
    paperState.diagPaperId = "";
    paperState.diagTaskId = "";
    pwDiagProgressHide();
    pwDiagCancelReset();
  }
  paperState.paperId = paperId;
  paperState.paper = p;
  paperState.selected = null;
  paperState.rewrite = null;
  paperState.guideline = null;
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
    pwRenderDiagnosis(p.analysis_results, p.total_score);
  } else {
    $("paper-dim-cards").innerHTML = "";
    $("paper-total-score").textContent = "";
    $("paper-issues").innerHTML = "";
  }
  const analyzed = p.analyzed || {};
  const done = Object.keys(analyzed).filter((k) => analyzed[k]).length;
  $("paper-req-status").textContent = confirmed
    ? `需求已确认 · 当前版本 v${p.current_version || 0} · 已完成 ${done}/4 个维度`
    : (p.suggestion
      ? "已根据论文内容自动预填，请核对后点击「确认需求」"
      : "需求未确认：请填写并确认后再诊断");

  $("paper-version").textContent = `当前版本 v${p.current_version || 0}（共 ${p.version_count || 1} 个版本）`;
  $("paper-upload-result").innerHTML =
    `<p><b>${esc(p.title || p.filename || paperId)}</b></p>
     <p class="muted">${esc(p.filename || "")} · ${p.pages || 0} 页 · ${p.chars || 0} 字符 · ${p.paragraph_count || 0} 段 · ${p.source_type === "docx" ? "Word" : "PDF"}</p>`;
  pwEmphasizeExport(p.source_type === "docx" ? "docx" : "pdf");

  const step = opts.restoreStep && opts.restoreStep <= paperState.maxStep
    ? opts.restoreStep
    : (confirmed ? 3 : 2);
  pwSetStep(step, true);
  await pwLoadParagraphs();
  await pwLoadGuideline();
  await pwLoadSimilar();
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
    const tick = async () => {
      try {
        const t = await api(`/api/paper/upload/${encodeURIComponent(taskId)}`);
        const pct = 20 + Math.round((Number(t.progress) || 0) * 0.8);
        pwTaskProgress("paper-upload", pct, t.stage || "正在解析论文…");
        if (t.status === "done") { resolve(t.report || {}); return; }
        if (t.status === "cancelled") {
          const err = new Error("已取消上传");
          err.cancelled = true;
          reject(err);
          return;
        }
        if (t.status === "error") { reject(new Error(t.error || "论文解析失败")); return; }
        paperState.uploadTimer = setTimeout(tick, 700);
      } catch (e) {
        reject(e);
      }
    };
    tick();
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
  $("paper-deai-strength").value = deai.strength || "medium";
  const keep = deai.keep || ["数据", "术语", "引用"];
  document.querySelectorAll("#paper-keep input").forEach((el) => {
    el.checked = keep.includes(el.value);
  });
  $("paper-banned").value = (deai.banned_phrases || []).join("、");
  $("paper-notes").value = req.notes || "";
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
  pwDiagProgress(0, "正在准备论文与模型…");
  $("paper-dim-cards").innerHTML = '<span class="muted">诊断中，通常需要 30~90 秒...</span>';
  try {
    const r = await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/diagnose`, { dimensions: [] });
    paperState.diagTaskId = r.task_id;
    pwPollDiagnose(r.task_id);
  } catch (e) {
    btn.disabled = false;
    paperState.diagPaperId = "";
    paperState.diagTaskId = "";
    pwDiagProgressHide();
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
  const tick = async () => {
    if (paperState.paperId !== pid) return;
    try {
      const t = await api(`/api/paper/${encodeURIComponent(pid)}/diagnose/${taskId}`);
      const logLines = t.logs || [];
      $("paper-diag-logs").textContent = logLines.join("\n");
      pwDiagProgress(
        typeof t.progress === "number" ? t.progress : 0,
        logLines[logLines.length - 1] || "正在诊断…"
      );
      if (t.status === "done") {
        $("btn-paper-diagnose").disabled = false;
        pwDiagProgress(100, "诊断完成");
        paperState.diagPaperId = "";
        paperState.diagTaskId = "";
        pwDiagCancelReset();
        setTimeout(() => {
          if (!paperState.diagPaperId) pwDiagProgressHide();
        }, 1500);
        if (t.report) pwRenderDiagnosis(t.report.dimensions || {}, t.report.total_score);
        if (t.report && t.report.errors && Object.keys(t.report.errors).length) {
          pwAlert("部分维度诊断失败：" + Object.values(t.report.errors).join("；") + "。可再次点击诊断重试。", "warn", 3);
        } else {
          pwAlert("诊断完成。可查看右侧问题列表，或继续相似论文与段落改写。", "ok", 3);
        }
        pwRefreshQuota();
        pwLoadPaper(pid).catch(() => {});
        return;
      }
      if (t.status === "cancelled") {
        $("btn-paper-diagnose").disabled = false;
        paperState.diagPaperId = "";
        paperState.diagTaskId = "";
        pwDiagCancelReset();
        pwDiagProgressHide();
        pwAlert("诊断已取消，已完成维度的结果会保留。可重新点击「开始诊断」。", "warn", 3);
        pwLoadPaper(pid).catch(() => {});
        return;
      }
      if (t.status === "error") {
        $("btn-paper-diagnose").disabled = false;
        paperState.diagPaperId = "";
        paperState.diagTaskId = "";
        pwDiagCancelReset();
        pwDiagProgressHide();
        pwAlert("诊断失败：" + (t.error || "未知错误"), "err", 3);
        return;
      }
      paperState.diagTimer = setTimeout(tick, 1200);
    } catch (e) {
      $("btn-paper-diagnose").disabled = false;
      paperState.diagPaperId = "";
      paperState.diagTaskId = "";
      pwDiagCancelReset();
      pwDiagProgressHide();
      pwAlert("查询诊断进度失败：" + e.message, "err", 3);
    }
  };
  tick();
}

function pwScoreClass(score) {
  if (score >= 80) return "good";
  if (score >= 60) return "warn";
  return "bad";
}

function pwRenderDiagnosis(dims, total) {
  const wrap = $("paper-dim-cards");
  wrap.innerHTML = "";
  PAPER_DIM_ORDER.forEach((key) => {
    const d = dims[key];
    if (!d) return;
    const label = PAPER_DIM_LABELS[key] || key;
    const score = Number(d.score || 0);
    const stale = d.stale ? '<span class="paper-stale">已过期</span>' : "";
    const issues = Array.isArray(d.issues) ? d.issues : [];
    const card = document.createElement("div");
    card.className = "paper-dim-card " + pwScoreClass(score);
    let extra = "";
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
  pwRenderIssues(dims);
}

function pwRenderIssues(dims) {
  const box = $("paper-issues");
  const rows = [];
  PAPER_DIM_ORDER.forEach((key) => {
    const d = dims[key];
    if (!d) return;
    (d.issues || []).forEach((it) => {
      const sev = ["high", "medium", "low"].includes(it.severity) ? it.severity : "medium";
      const title = it.rule_id || it.issue || PAPER_DIM_LABELS[key];
      const page = it.page ? `第 ${it.page} 页` : "";
      const loc = it.paragraph_id || "";
      rows.push(
        `<div class="issue-item sev-${sev}" data-pid="${esc(loc)}" data-page="${esc(it.page || "")}">
           <div class="issue-head">
             <span class="sev-tag">${sev === "high" ? "高" : sev === "medium" ? "中" : "低"}</span>
             <b>${esc(title)}</b>
             <span class="muted">${esc(PAPER_DIM_LABELS[key])} ${esc(page)}</span>
           </div>
           ${it.evidence ? `<blockquote>${esc(it.evidence)}</blockquote>` : ""}
           ${it.suggestion ? `<p class="issue-sug">建议：${esc(it.suggestion)}</p>` : ""}
           ${it.basis ? `<p class="muted">依据：${esc(it.basis)}</p>` : ""}
         </div>`
      );
    });
  });
  box.innerHTML = rows.length
    ? `<div class="section-title">问题清单（点击带段落的条目可定位改写）</div>${rows.join("")}`
    : '<p class="muted">暂无问题条目。</p>';
}

async function pwGotoParagraph(pid) {
  await pwLoadParagraphs();
  await pwSelectParagraph(pid);
  pwSetStep(5, true);
}

// ---------- 相似论文 ----------
async function pwLoadSimilar() {
  if (!paperState.paperId) return;
  try {
    const data = await api(`/api/paper/${encodeURIComponent(paperState.paperId)}/similar`);
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
  const tick = async () => {
    if (paperState.paperId !== pid) return;
    try {
      const t = await api(`/api/paper/${encodeURIComponent(pid)}/similar/${taskId}`);
      $("paper-similar-status").textContent = pwStageText(t.stage) || "检索中...";
      pwTaskProgress("paper-similar", t.progress || 0, t.stage || "检索中…");
      if (t.status === "done") {
        paperState.similarTaskId = "";
        pwSimilarBusy(false);
        pwTaskProgress("paper-similar", 100, "检索完成");
        setTimeout(() => pwTaskProgressHide("paper-similar"), 800);
        pwRenderSimilar(t.report || { items: [] });
        if (t.report && t.report.error) pwAlert(t.report.error, "warn", 4);
        return;
      }
      if (t.status === "cancelled") {
        paperState.similarTaskId = "";
        pwSimilarBusy(false);
        pwTaskProgressHide("paper-similar");
        pwAlert("已取消相似论文检索。", "warn", 4);
        return;
      }
      if (t.status === "error") {
        paperState.similarTaskId = "";
        pwSimilarBusy(false);
        pwTaskProgressHide("paper-similar");
        pwAlert("相似论文检索失败：" + (t.error || "未知错误"), "err", 4);
        return;
      }
      paperState.simTimer = setTimeout(tick, 1000);
    } catch (e) {
      paperState.similarTaskId = "";
      pwSimilarBusy(false);
      pwTaskProgressHide("paper-similar");
      pwAlert("查询检索进度失败：" + e.message, "err", 4);
    }
  };
  tick();
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
    const link = it.url || (it.doi ? `https://doi.org/${it.doi}` : "");
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
  const section = $("paper-section-filter").value;
  const qs = section ? `?section=${encodeURIComponent(section)}` : "";
  try {
    const data = await api(`/api/paper/${encodeURIComponent(paperState.paperId)}/paragraphs${qs}`);
    paperState.sections = data.sections || [];
    paperState.paragraphs = data.paragraphs || [];
    pwFillSectionFilter();
    pwRenderParagraphs();
  } catch (e) {
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
    const selectable = p.type === "body" && (p.text || "").length <= 800;
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
  const tick = async () => {
    if (paperState.paperId !== pid) return;
    try {
      const t = await api(`/api/paper/${encodeURIComponent(pid)}/rewrite/${taskId}`);
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
            + (failed ? `，失败 ${failed} 段` : "") + "。请逐段查看差异后采纳。", "ok", 5);
          pwRefreshQuota();
        }
        pwTaskProgress("paper-batch", 100, t.status === "cancelled" ? "已取消" : "批量改写完成");
        setTimeout(() => pwTaskProgressHide("paper-batch"), 900);
        return;
      }
      if (t.status === "error") {
        paperState.batchTaskId = "";
        pwTaskProgressHide("paper-batch");
        $("btn-paper-batch").disabled = false;
        pwAlert("批量改写失败：" + (t.error || "未知错误"), "err", 5);
        return;
      }
      paperState.batchTimer = setTimeout(tick, 1200);
    } catch (e) {
      paperState.batchTaskId = "";
      pwTaskProgressHide("paper-batch");
      $("btn-paper-batch").disabled = false;
      pwAlert("查询批量改写进度失败：" + e.message, "err", 5);
    }
  };
  tick();
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

function pwRenderBatchResults() {
  const box = $("paper-batch-results");
  const items = paperState.batchResults || [];
  const errors = paperState.batchErrors || [];
  if (!items.length && !errors.length) { box.innerHTML = ""; return; }
  const rows = items.map((r) => {
    const before = Number(r.ai_score_before || 0);
    const after = Number(r.ai_score_after || 0);
    return `<div class="batch-item">
      <div class="batch-meta"><b>${esc(r.paragraph_id)} · 已完成</b>AI 味 ${before} → ${after} · ${(r.revised || "").length} 字</div>
      <button class="btn btn-ghost" data-batch-pid="${esc(r.paragraph_id)}">查看差异</button>
    </div>`;
  });
  const errRows = errors.map((e) => `<div class="batch-item err">
      <div class="batch-meta"><b>${esc(e.paragraph_id)} · 失败</b>${esc(e.error || "未知错误")}</div>
    </div>`);
  box.innerHTML = `<div class="muted">批量结果（可单独查看并按段采纳）</div>${rows.concat(errRows).join("")}`;
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
}

// ---------- 全局改写方针 ----------
async function pwLoadGuideline() {
  if (!paperState.paperId) return;
  try {
    const r = await api(`/api/paper/${encodeURIComponent(paperState.paperId)}/rewrite/guideline`);
    pwRenderGuideline(r);
  } catch (e) {
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
  const box = $("paper-guide-progress");
  box.classList.remove("hidden");
  const p = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
  $("paper-guide-fill").style.width = p + "%";
  $("paper-guide-percent").textContent = p + "%";
  if (stage) {
    $("paper-guide-stage").textContent = stage;
    $("paper-guide-status").textContent = stage;
  }
}

function pwGuideProgressHide() {
  $("paper-guide-progress").classList.add("hidden");
  $("paper-guide-fill").style.width = "0%";
  $("paper-guide-percent").textContent = "0%";
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
  const tick = async () => {
    try {
      const t = await api(`/api/paper/${encodeURIComponent(pid)}/rewrite/guideline/${encodeURIComponent(taskId)}`);
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
        return;
      }
      if (t.status === "cancelled") {
        pwGuideFinish();
        $("paper-guide-status").textContent = "已取消生成，本次结果不保存。";
        pwAlert("已取消生成全局改写方针。", "warn", 5);
        return;
      }
      if (t.status === "error") {
        pwGuideFinish();
        $("paper-guide-status").textContent = "方针生成失败，请稍后重试。";
        pwAlert(t.error || "方针生成失败", "err", 5);
        return;
      }
      paperState.guideTimer = setTimeout(tick, 1200);
    } catch (e) {
      pwGuideFinish();
      $("paper-guide-status").textContent = "查询方针进度失败，请稍后重试。";
      pwAlert("查询方针进度失败：" + e.message, "err", 5);
    }
  };
  tick();
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
  if ((paperState.selected.text || "").length > 800) {
    pwAlert("该段超过 800 字，请先在原文中拆分后再改写。", "warn");
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
  const tick = async () => {
    if (paperState.paperId !== pid || paperState.rewriteTaskId !== taskId) return;
    try {
      const t = await api(`/api/paper/${encodeURIComponent(pid)}/rewrite/${taskId}`);
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
        return;
      }
      if (t.status === "cancelled") {
        paperState.rewriteTaskId = "";
        paperState.rewritePaperId = "";
        paperState.rewriteParaId = "";
        pwRewriteBusy(false);
        pwTaskProgressHide("paper-rewrite");
        $("paper-rewrite-meta").textContent = "已取消改写，本次结果不保存。";
        pwAlert("已取消改写。", "warn", 5);
        return;
      }
      if (t.status === "error") {
        paperState.rewriteTaskId = "";
        paperState.rewritePaperId = "";
        paperState.rewriteParaId = "";
        pwRewriteBusy(false);
        pwTaskProgressHide("paper-rewrite");
        $("paper-rewrite-meta").textContent = "";
        pwAlert("改写失败：" + (t.error || "未知错误"), "err", 5);
        return;
      }
      paperState.rewriteTimer = setTimeout(tick, 1000);
    } catch (e) {
      paperState.rewriteTaskId = "";
      paperState.rewritePaperId = "";
      paperState.rewriteParaId = "";
      pwRewriteBusy(false);
      pwTaskProgressHide("paper-rewrite");
      pwAlert("查询改写进度失败：" + e.message, "err", 5);
    }
  };
  tick();
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

function pwRenderDiff(r) {
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
  $("paper-rewrite-meta").textContent =
    `${pid} · AI 味风险 ${before} ${arrow} ${after} · 原文 ${(r.original || "").length} 字 → 改写 ${(r.revised || "").length} 字`
    + (r.guideline_used ? " · 已套用全局方针" : "");
  if (r.warning) {
    $("paper-rewrite-warning").textContent = r.warning;
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
  if (!revised.trim()) { pwAlert("当前没有可保存的改写内容", "warn"); return; }
  if (revised.trim() === (r.original || "").trim()) {
    pwAlert("当前未保留任何改写，请至少保留一处改动后再保存。", "warn");
    return;
  }
  const btn = $("btn-paper-adopt");
  btn.disabled = true;
  try {
    const res = await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/apply`, {
      paragraph_id: paperState.selected.id,
      revised: revised,
    });
    paperState.rewrite = null;
    paperState.diff = null;
    if ((paperState.batchResults || []).length) {
      paperState.batchResults = paperState.batchResults.filter(
        (x) => x.paragraph_id !== res.paragraph_id
      );
    }
    paperState.batchIds.delete(res.paragraph_id);
    await pwLoadPaper(paperState.paperId, { restoreStep: 5 });
    await pwSelectParagraph(res.paragraph_id).catch(() => {});
    pwRenderBatchResults();
    pwSetStep(5, true);
    pwAlert(`已采纳并保存为 v${res.version}。如需回退，可删除后重新上传，或继续逐段优化。`, "ok");
  } finally {
    btn.disabled = false;
  }
}

function pwExportFilename(resp, format) {
  const fallback = format === "summary" ? "论文修改说明.docx" : `optimized.${format}`;
  const cd = resp.headers.get("Content-Disposition") || "";
  const m = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  if (m) {
    try { return decodeURIComponent(m[1].trim()); } catch (e) {}
  }
  const plain = /filename="?([^";]+)"?/i.exec(cd);
  return plain ? plain[1].trim() : fallback;
}

async function pwExport(format = "docx") {
  if (!paperState.paperId) { pwAlert("请先上传论文", "warn"); return; }
  try {
    const resp = await fetch(`/api/paper/${encodeURIComponent(paperState.paperId)}/export?format=${format}`);
    if (!resp.ok) {
      let detail = "";
      try { detail = (await resp.json()).detail || ""; } catch (e) {}
      throw new Error(detail || `HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = pwExportFilename(resp, format);
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    if (format === "summary") {
      pwAlert("《论文修改说明》已导出，可直接随修改稿一起提交。", "ok", 5);
    }
  } catch (e) {
    pwAlert("导出失败：" + e.message, "err");
  }
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

async function pwAsk() {
  if (!paperState.paperId) {
    pwAlert("请先上传论文，再围绕该论文提问。", "warn");
    pwSetStep(1);
    return;
  }
  pwAskOpen(true);
  const q = $("paper-ask-input").value.trim();
  if (!q) return;
  const btn = $("btn-paper-ask");
  btn.disabled = true;
  $("paper-ask-answer").classList.remove("hidden");
  $("paper-ask-answer").innerHTML = '<span class="muted">思考中...</span>';
  try {
    const r = await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/ask`, { question: q });
    if (r.rejected) {
      $("paper-ask-answer").innerHTML = `<p class="muted">${esc(r.answer)}</p>`;
    } else {
      const basis = (r.basis || []).length
        ? `<div class="ask-basis">依据：${(r.basis || []).map((b) => `<span class="chip">${esc(b)}</span>`).join("")}</div>`
        : "";
      $("paper-ask-answer").innerHTML = `<div class="markdown">${renderMarkdown(r.answer)}</div>${basis}`;
      $("paper-ask-input").value = "";
      pwRefreshQuota();
    }
  } catch (e) {
    $("paper-ask-answer").innerHTML = `<span class="paper-error">${esc(e.message)}</span>`;
  } finally {
    btn.disabled = false;
  }
}

// app.js 在 init 中调用；加载顺序在 app.js 之后时由 load 事件兜底
window.initPaperPage = initPaperPage;
