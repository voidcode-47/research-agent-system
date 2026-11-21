# Spec：AI 生成率趋势 + 段落风险热力图

## 背景

论文诊断的 `deai` 维度已经产出整体 AI 生成率（`ai_rate`）与段落级风险（`detect()` 返回的
`paragraphs`），但当前只持久化整体结果，用户看不到：

- 同一论文多次诊断之间 AI 生成率是否下降；
- 哪些正文段落风险最高、应优先改写。

本规格只覆盖这两个可观测能力，复用现有诊断链路与前端原生 JS，不引入新依赖、不改模块大结构。

## 目标

1. 每次诊断成功后追加一条轻量历史快照，形成 AI 生成率趋势；去重并限制总量。
2. `deai` 结果持久化逐段风险，详情接口返回，前端绘制可点击的段落风险热力条。
3. 旧版 `deai` 结果（无 `paragraph_risks`）保持兼容：不显示热力条，不影响现有渲染。

## 数据设计

### 历史快照

存储位置：`data/papers/{paper_id}/analysis/history.json`（JSON 数组，原子写入）。

单条记录字段（仅写入非空值）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `dimension` | str | `level` / `feasibility` / `format` / `deai` |
| `version` | int | 诊断所用正文版本 |
| `generated_at` | str | 维度结果时间戳 |
| `score` | number | 该维度得分 |
| `ai_rate` | int | 仅 deai |
| `ai_score` | int | 仅 deai |
| `paragraph_risk` | dict | 仅 deai，`{high, medium}` 计数 |

规则：

- 去重键：`(dimension, version, generated_at, score)`；重复记录直接跳过。
- 总量上限 200 条，仅保留最近 200 条。
- 读取接口只返回最近 `limit` 条（详情接口用 40），损坏或缺失时返回空列表，不报错。
- 只在 `save_analysis_if_current` 真正写盘成功后追加，确保过期诊断不会污染趋势。

### 逐段风险

在 `_diagnose_deai` 返回值新增 `paragraph_risks`：

```json
{
  "index": 3,
  "paragraph_id": "p_xxx",
  "page": 5,
  "risk": "high",
  "risk_points": 6,
  "hits": ["理论依据式起笔", "段末总结"]
}
```

- 顺序与 `detect()` 的正文段落顺序一致。
- `paragraph_id` / `page` 通过 `body_items[index]` 映射；映射失败时回退到
  `_locate_sentence(excerpt)`，仍失败则为空字符串 / 0。
- `hits` 最多保留 6 条，控制详情接口体积。

## 实现清单

### 后端

1. `paper/store.py`
   - 新增常量 `_HISTORY_NAME = "history.json"`、`_HISTORY_LIMIT = 200`。
   - 新增 `append_analysis_history(paper_id, record)`：在 `_LOCK` 内读取、去重、截断、原子写入。
   - 新增 `load_analysis_history(paper_id, limit=40)`：容错读取最近 N 条。
   - `save_analysis_if_current` 写盘成功后调用 `append_analysis_history`（RLock 可重入）。
2. `paper/analysis.py`
   - `_diagnose_deai` 内先构建 `index -> (page, paragraph_id)` 映射，风险问题列表复用该映射。
   - 新增 `paragraph_risks` 字段并写入返回结果。
3. `api/paper.py`
   - `api_paper_detail` 返回新增 `analysis_history: store.load_analysis_history(paper_id, limit=40)`。

### 前端

4. `web/index.html`
   - 在 `#paper-dim-cards` 之后新增 `<div id="paper-trend" class="paper-trend hidden"></div>`。
5. `web/paper.js`
   - `pwRefreshPaper` 调用 `pwRenderDiagnosis(p.analysis_results, p.total_score, p.analysis_history)`。
   - `pwRenderDiagnosis(dims, total, history)` 末尾调用 `pwRenderTrend(history, dims.deai)`。
   - 新增 `pwRenderTrend(history, deai)`：
     - 过滤 `dimension === "deai"` 且 `ai_rate` 为数字的历史；
     - 至少 2 个点绘制内联 SVG 折线（viewBox `320x72`），并显示最新值与相对上一次的变化；
     - 少于 2 个点显示「再完成一次诊断后可查看趋势」；
     - 有 `paragraph_risks` 时绘制热力条：每段一个 `.heat-cell`，类名 `risk-low/medium/high`，
       `data-pid` 指向段落，`title` 展示序号、风险分与命中特征；
     - 显示高/中风险段数与图例；无任何 deai 数据时保持 `hidden`。
   - 初始化时在 `#paper-trend` 上做事件委托：点击 `.heat-cell[data-pid]` 调用 `pwGotoParagraph`。
6. `web/style.css`
   - 新增 `.paper-trend`、趋势 SVG 与热力条样式，沿用现有浅色变量，不引入深色主题。

### 测试

7. `tests/test_paper_api.py`
   - 扩展端到端诊断用例：断言 `deai.paragraph_risks` 结构、详情 `analysis_history`
     包含 4 条记录且 deai 最新一条与结果一致。
   - 新增历史去重与上限用例（直接调用 store 函数）。
8. `tests/test_web.cjs`
   - 新增用例：传入 `paragraph_risks` 与至少 2 条 deai 历史时，`#paper-trend`
     包含 `.heat-cell` 与 `trend-svg`；无 deai 数据时保持 `hidden`。

## 验收标准

- 诊断完成后，详情接口的 `analysis_results.deai.paragraph_risks` 中每段都有
  `paragraph_id`、`risk`、`risk_points`（无法映射的段落为空 ID）。
- 同一论文连续诊断两次后，`analysis_history` 中 deai 记录为 2 条；重复保存同一条记录不增加。
- 历史文件最多 200 条，读取接口最多返回 40 条。
- 前端在至少有 2 个 deai 历史点时显示折线；热力条颜色区分高/中/低风险，点击可定位段落。
- 旧论文（deai 结果无 `paragraph_risks`）渲染不报错，仅不显示热力条。
- `.venv\Scripts\python.exe -m pytest -q`、`node --test tests/test_web.cjs`、
  `.venv\Scripts\python.exe -m ruff check . --isolated --select F,E9 --no-cache` 全部通过。

## 非目标

- 不实现批量改写、参考文献校验、Word 修订导出（后续独立规格）。
- 不调整诊断维度权重、评分口径或提示词。
- 不引入数据库、前端框架或图表库。
