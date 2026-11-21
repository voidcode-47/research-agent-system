# Tasks：AI 生成率趋势 + 段落风险热力图

## 1. 存储层历史快照

- [ ] `paper/store.py` 增加 `_HISTORY_NAME`、`_HISTORY_LIMIT` 与 `_history_path`。
- [ ] 实现 `append_analysis_history`：去重键 `(dimension, version, generated_at, score)`，
      仅保留最近 200 条，`_LOCK` 内原子写入。
- [ ] 实现 `load_analysis_history(paper_id, limit=40)`：缺失/损坏返回 `[]`。
- [ ] `save_analysis_if_current` 成功写盘后追加历史。

验证：`store.append_analysis_history` 连续写入相同记录只保留 1 条；写入 205 条后仅剩 200 条。

## 2. 诊断结果逐段风险

- [ ] `paper/analysis.py` 的 `_diagnose_deai` 构建 `index -> (page, paragraph_id)` 映射。
- [ ] 风险问题列表复用映射，避免重复定位。
- [ ] 返回值新增 `paragraph_risks`（index / paragraph_id / page / risk / risk_points / hits≤6）。

验证：`pytest` 端到端诊断用例可读取到 `deai["paragraph_risks"]`。

## 3. 详情接口

- [ ] `api/paper.py` 的 `api_paper_detail` 返回 `analysis_history`（最近 40 条）。

验证：诊断后 `GET /api/paper/{id}` 的 `analysis_history` 含 4 个维度记录。

## 4. 前端趋势与热力图

- [ ] `web/index.html` 在 `#paper-dim-cards` 后新增 `#paper-trend` 容器。
- [ ] `web/paper.js` 的 `pwRefreshPaper` 传入 `p.analysis_history`。
- [ ] `pwRenderDiagnosis` 接收第三个参数并调用 `pwRenderTrend`。
- [ ] 实现 `pwRenderTrend`：SVG 折线（≥2 个点）、变化提示、热力条、图例、空态隐藏。
- [ ] `pwInit` 注册 `#paper-trend` 事件委托，点击热力格调用 `pwGotoParagraph`。
- [ ] `web/style.css` 增加趋势与热力条样式（浅色、无深色主题）。

验证：`node --test tests/test_web.cjs` 新增用例通过。

## 5. 测试与回归

- [ ] `tests/test_paper_api.py` 扩展端到端诊断断言（`paragraph_risks` + `analysis_history`）。
- [ ] `tests/test_paper_api.py` 新增历史去重与上限用例。
- [ ] `tests/test_web.cjs` 新增趋势与热力条渲染用例。
- [ ] 运行 `.venv\Scripts\python.exe -m pytest -q` 全量通过。
- [ ] 运行 `node --test tests/test_web.cjs` 通过。
- [ ] 运行 `.venv\Scripts\python.exe -m ruff check . --isolated --select F,E9 --no-cache` 通过。

## 6. 约束

- 不提交/推送代码，保留工作区原有未提交改动。
- 不引入第三方依赖，不新增深色主题，不改动诊断评分口径。
- 代码注释使用中文；仅在逻辑不直观处补充。
