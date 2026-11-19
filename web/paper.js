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
  sections: [],
  paragraphs: [],
  diagTimer: null,
  simTimer: null,
};

const PAPER_DIM_LABELS = {
  level: "论文层次",
  feasibility: "内容可实现性",
  format: "正文格式",
  deai: "正文去 AI 味",
};

// ---------- 通用 ----------
function pwAlert(msg, kind) {
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
  return `今日 LLM 配额：剩余 ${q.remaining} / 共 ${q.limit} 次`;
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
  $("btn-paper-delete").addEventListener("click", () => pwDelete().catch(pwError));
  $("btn-paper-confirm").addEventListener("click", () => pwConfirm().catch(pwError));
  $("btn-paper-diagnose").addEventListener("click", () => pwStartDiagnose().catch(pwError));
  $("btn-paper-similar").addEventListener("click", () => pwStartSimilar(false).catch(pwError));
  $("btn-paper-similar-refresh").addEventListener("click", () => pwStartSimilar(true).catch(pwError));
  $("paper-section-filter").addEventListener("change", () => pwLoadParagraphs().catch(pwError));
  $("btn-paper-rewrite").addEventListener("click", () => pwRewrite().catch(pwError));
  $("btn-paper-adopt").addEventListener("click", () => pwAdopt().catch(pwError));
  $("btn-paper-export").addEventListener("click", () => pwExport());
  $("btn-paper-ask").addEventListener("click", () => pwAsk().catch(pwError));
  $("paper-ask-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") pwAsk().catch(pwError);
  });
  $("paper-steps").addEventListener("click", (e) => {
    const btn = e.target.closest(".paper-step");
    if (btn) pwSetStep(Number(btn.dataset.step));
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
  if (fromUrl) {
    await pwLoadPaper(fromUrl).catch(pwError);
  } else if (paperState.paperId) {
    await pwLoadPaper(paperState.paperId).catch(pwError);
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
  $("paper-diff").innerHTML = "";
  $("paper-rewrite-meta").textContent = "";
  $("paper-rewrite-warning").classList.add("hidden");
  $("btn-paper-adopt").classList.add("hidden");
  $("paper-version").textContent = "";
  $("paper-req-status").textContent = "";
  $("paper-diag-logs").classList.add("hidden");
  $("paper-ask-answer").classList.add("hidden");
  pwSetStep(1, true);
  pwHideAlert();
}

async function pwLoadPaper(paperId) {
  const p = await api(`/api/paper/${encodeURIComponent(paperId)}`);
  pwClearTimers();
  paperState.paperId = paperId;
  paperState.paper = p;
  paperState.selected = null;
  paperState.rewrite = null;
  $("paper-select").value = paperId;
  $("paper-diff").innerHTML = "";
  $("paper-original").textContent = "在左侧选择一个正文段落。";
  $("paper-rewrite-meta").textContent = "";
  $("paper-rewrite-warning").classList.add("hidden");
  $("btn-paper-adopt").classList.add("hidden");
  $("paper-similar-list").innerHTML = "";
  $("paper-ask-answer").classList.add("hidden");
  $("paper-ask-input").value = "";

  pwFillRequirement(p.requirement || {});
  const confirmed = !!(p.requirement && p.requirement.confirmed);
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
    : "需求未确认：请填写并确认后再诊断";

  $("paper-version").textContent = `当前版本 v${p.current_version || 0}（共 ${p.version_count || 1} 个版本）`;
  $("paper-upload-result").innerHTML =
    `<p><b>${esc(p.title || p.filename || paperId)}</b></p>
     <p class="muted">${esc(p.filename || "")} · ${p.pages || 0} 页 · ${p.chars || 0} 字符 · ${p.paragraph_count || 0} 段</p>`;

  pwSetStep(confirmed ? 3 : 2, true);
  await pwLoadParagraphs();
  await pwLoadSimilar();
}

// ---------- 上传 ----------
async function pwUpload() {
  const f = $("paper-file").files[0];
  if (!f) { pwAlert("请先选择 PDF 文件", "warn"); return; }
  if (!/\.pdf$/i.test(f.name)) { pwAlert("仅支持 PDF 文件（.pdf）", "err"); return; }
  const fd = new FormData();
  fd.append("file", f);
  const btn = $("btn-paper-upload");
  btn.disabled = true;
  $("paper-upload-result").innerHTML = '<span class="muted">正在上传并解析...</span>';
  try {
    const r = await api("/api/paper/upload", { method: "POST", body: fd });
    paperState.paperId = r.paper_id;
    $("paper-upload-result").innerHTML =
      `<p><b>解析完成：${esc(r.title)}</b></p>
       <p class="muted">${r.pages} 页 · ${r.chars} 字符 · ${r.section_count} 个章节 · ${r.paragraph_count} 段</p>`;
    $("paper-file").value = "";
    await pwLoadList();
    await pwLoadPaper(r.paper_id);
    pwAlert("上传成功，请确认诊断需求。", "ok");
  } catch (e) {
    $("paper-upload-result").innerHTML = "";
    pwAlert(e.message, "err");
  } finally {
    btn.disabled = false;
  }
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
  $("paper-dim-cards").innerHTML = '<span class="muted">诊断中，通常需要 30~90 秒...</span>';
  try {
    const r = await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/diagnose`, { dimensions: [] });
    pwPollDiagnose(r.task_id);
  } catch (e) {
    btn.disabled = false;
    pwAlert(e.message, "err");
  }
}

function pwPollDiagnose(taskId) {
  const pid = paperState.paperId;
  const tick = async () => {
    if (paperState.paperId !== pid) return;
    try {
      const t = await api(`/api/paper/${encodeURIComponent(pid)}/diagnose/${taskId}`);
      $("paper-diag-logs").textContent = (t.logs || []).join("\n");
      if (t.status === "done") {
        $("btn-paper-diagnose").disabled = false;
        if (t.report) pwRenderDiagnosis(t.report.dimensions || {}, t.report.total_score);
        if (t.report && t.report.errors && Object.keys(t.report.errors).length) {
          pwAlert("部分维度诊断失败：" + Object.values(t.report.errors).join("；") + "。可再次点击诊断重试。", "warn");
        } else {
          pwAlert("诊断完成。可查看右侧问题列表，或继续相似论文与段落改写。", "ok");
        }
        pwRefreshQuota();
        pwLoadPaper(pid).catch(() => {});
        return;
      }
      if (t.status === "error") {
        $("btn-paper-diagnose").disabled = false;
        pwAlert("诊断失败：" + (t.error || "未知错误"), "err");
        return;
      }
      paperState.diagTimer = setTimeout(tick, 1200);
    } catch (e) {
      $("btn-paper-diagnose").disabled = false;
      pwAlert("查询诊断进度失败：" + e.message, "err");
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
  const order = ["level", "feasibility", "format", "deai"];
  order.forEach((key) => {
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
    if (key === "deai" && d.level) {
      extra += `<p class="muted">模板化程度：${esc(d.level)}</p>`;
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
  ["level", "feasibility", "format", "deai"].forEach((key) => {
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
  try {
    const r = await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/similar`, {
      limit: 10,
      refresh: !!refresh,
    });
    pwPollSimilar(r.task_id);
  } catch (e) {
    btn.disabled = false;
    $("paper-similar-status").textContent = "";
    pwAlert(e.message, "err");
  }
}

function pwPollSimilar(taskId) {
  const pid = paperState.paperId;
  const tick = async () => {
    if (paperState.paperId !== pid) return;
    try {
      const t = await api(`/api/paper/${encodeURIComponent(pid)}/similar/${taskId}`);
      $("paper-similar-status").textContent = t.stage || "检索中...";
      if (t.status === "done") {
        $("btn-paper-similar").disabled = false;
        $("btn-paper-similar-refresh").disabled = false;
        $("paper-similar-status").textContent = "";
        pwRenderSimilar(t.report || { items: [] });
        if (t.report && t.report.error) pwAlert(t.report.error, "warn");
        return;
      }
      if (t.status === "error") {
        $("btn-paper-similar").disabled = false;
        $("btn-paper-similar-refresh").disabled = false;
        $("paper-similar-status").textContent = "";
        pwAlert("相似论文检索失败：" + (t.error || "未知错误"), "err");
        return;
      }
      paperState.simTimer = setTimeout(tick, 1200);
    } catch (e) {
      $("btn-paper-similar").disabled = false;
      $("btn-paper-similar-refresh").disabled = false;
      pwAlert("查询检索进度失败：" + e.message, "err");
    }
  };
  tick();
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
    return;
  }
  box.innerHTML = list.map((p) => {
    const sec = p.section ? ` · ${esc(p.section)}` : "";
    const type = p.type === "body" ? "" : `<span class="chip">${esc(p.type)}</span>`;
    return `<button class="paper-para-item${paperState.selected && paperState.selected.id === p.id ? " active" : ""}" data-id="${esc(p.id)}">
      <span class="para-tag">${esc(p.id)} · 第 ${p.page || "-"} 页${sec} ${type}</span>
      <span class="para-text">${esc((p.text || "").slice(0, 120))}</span>
      <span class="para-len">${(p.text || "").length} 字</span>
    </button>`;
  }).join("");
  box.querySelectorAll(".paper-para-item").forEach((btn) => {
    btn.addEventListener("click", () => pwSelectParagraph(btn.dataset.id).catch(pwError));
  });
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
  $("paper-diff").innerHTML = "";
  $("paper-rewrite-meta").textContent = `${p.id} · 第 ${p.page || "-"} 页 · ${(p.text || "").length} 字`;
  $("paper-rewrite-warning").classList.add("hidden");
  $("btn-paper-adopt").classList.add("hidden");
}

// ---------- 改写 ----------
async function pwRewrite() {
  if (!paperState.paperId) { pwAlert("请先上传论文", "warn"); return; }
  if (!paperState.selected) { pwAlert("请先在左侧选择一个段落", "warn"); return; }
  if ((paperState.selected.text || "").length > 800) {
    pwAlert("该段超过 800 字，请先在原文中拆分后再改写。", "warn");
    return;
  }
  const btn = $("btn-paper-rewrite");
  btn.disabled = true;
  $("paper-rewrite-meta").textContent = "生成中...";
  $("paper-rewrite-warning").classList.add("hidden");
  $("btn-paper-adopt").classList.add("hidden");
  try {
    const r = await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/rewrite`, {
      paragraph_id: paperState.selected.id,
      mode: $("paper-mode").value || "deai",
      instruction: $("paper-instruction").value.trim(),
    });
    paperState.rewrite = r;
    pwRenderDiff(r);
    pwRefreshQuota();
  } catch (e) {
    $("paper-rewrite-meta").textContent = "";
    pwAlert(e.message, "err");
  } finally {
    btn.disabled = false;
  }
}

function pwRenderDiff(r) {
  const diff = r.diff || [];
  $("paper-diff").innerHTML = diff.map((d) => {
    const text = esc(d.text || "");
    if (d.type === "insert") return `<ins>${text}</ins>`;
    if (d.type === "delete") return `<del>${text}</del>`;
    return `<span>${text}</span>`;
  }).join("");
  const before = Number(r.ai_score_before || 0);
  const after = Number(r.ai_score_after || 0);
  const arrow = after < before ? "↓" : after > before ? "↑" : "→";
  $("paper-rewrite-meta").textContent =
    `${paperState.selected.id} · AI 味风险 ${before} ${arrow} ${after} · 原文 ${(r.original || "").length} 字 → 改写 ${(r.revised || "").length} 字`;
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
  if (!r.revised) { pwAlert("改写内容为空，无法采纳", "warn"); return; }
  const btn = $("btn-paper-adopt");
  btn.disabled = true;
  try {
    const res = await postJSON(`/api/paper/${encodeURIComponent(paperState.paperId)}/apply`, {
      paragraph_id: paperState.selected.id,
      revised: r.revised,
    });
    paperState.rewrite = null;
    await pwLoadPaper(paperState.paperId);
    await pwSelectParagraph(res.paragraph_id).catch(() => {});
    pwSetStep(5, true);
    pwAlert(`已采纳并保存为 v${res.version}。如需回退，可删除后重新上传，或继续逐段优化。`, "ok");
  } finally {
    btn.disabled = false;
  }
}

async function pwExport() {
  if (!paperState.paperId) { pwAlert("请先上传论文", "warn"); return; }
  try {
    const resp = await fetch(`/api/paper/${encodeURIComponent(paperState.paperId)}/export?format=docx`);
    if (!resp.ok) {
      let detail = "";
      try { detail = (await resp.json()).detail || ""; } catch (e) {}
      throw new Error(detail || `HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "optimized.docx";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
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
  pwResetPaper();
  await pwLoadList();
  pwAlert("论文已删除。", "ok");
}

// ---------- 论文问答 ----------
async function pwAsk() {
  if (!paperState.paperId) {
    pwAlert("请先上传论文，再围绕该论文提问。", "warn");
    pwSetStep(1);
    return;
  }
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
