# Model Discovery 模块交付报告（增量更新）

- 日期：2026-09-13（UTC）
- 基线：melanie531/governed-agent-builder @ ff6d73b21d53d07a1a4d3026146904f6f37f6f45
- 分支：feature/model-discovery-module（独立 worktree /home/ec2-user/work/agent-studio-model-discovery）
- 所有权：仅新增 `backend/model_discovery.py`、`tests/test_model_discovery.py` 与本报告目录。不修改任何现有 backend/frontend/infra 文件（Requests 工人拥有 main.tsx 与现有 backend；Models UI 工人拥有 AICatalog.tsx/helper/tests）。

## 状态

- [x] RED：首个失败测试（ImportError: cannot import name 'model_discovery'；第二波 15 failed；投毒测试 1 failed）
- [x] GREEN：模块实现，`tests/test_model_discovery.py` 27 passed
- [x] 全量回归：`tests/` 1136 passed, 1 failed —— 唯一失败 `test_web_research.py::test_react_renders_malicious_report_as_text`
      为 worktree 环境缺 `frontend/node_modules`（esbuild ERR_MODULE_NOT_FOUND）；同一测试在 /tmp/gab 主
      checkout（有 node_modules）上 1 passed。环境差异，非本改动回归（本分支未触碰任何现有文件）。
- [ ] 提交并 push 独立分支
- [x] 集成状态说明（见下）

## 设计契约（与现有 live_catalog 公共契约对齐、但不接线）

- 发现（discovery）≠ Gateway 接入 ≠ 授权 ≠ 执行就绪。模块输出的每条记录固定
  `execution_ready=False`、`execution_binding={'status':'unverified',...}`、`integration_ready=False`、
  `discovery_only=True`，仅是“官方审核记录驱动的近期模型发现目录”。
- 输入是 owner 核实过的官方审核记录（vendor / model name / capabilities / 正式发布日期 / 来源 URL / 审核人）。
  **本模块不内置任何正式模型名单**：官方清单与发布日期由哥哥核实中，生产数据待注入；测试使用明确标注的 synthetic 记录。
- 滚动 6 个日历月窗口（UTC date，可注入 today），日历月回退带日钳制（如 8/31 → 2/28），不是 180 天简化。
- fail loud：缺失/非法发布日期、未来日期、缺来源 URL、不明供应商、重复 model id → 抛错或显式 excluded 原因，绝不降级为假目录。
- 供应商路由限制：Anthropic(Claude)/OpenAI 仅 bedrock-runtime；Google(Gemini) external；Amazon Nova / Mantle 明确拒绝。
- 历史绑定不删除：窗口过滤只作用于“新发现列表”；已有 draft/binding 引用的旧模型走独立 lookup，
  永不因窗口滚动被删除，未知引用保留为显式 unverified 条目而非静默丢弃。

## 测试证据

- RED#1：`pytest tests/test_model_discovery.py` → collection ERROR
  `ImportError: cannot import name 'model_discovery' from 'backend'`（模块不存在）。
- GREEN#1（窗口/日期切片）：8 passed。
- RED#2（来源 URL/供应商/重复 ID/输出契约/历史绑定 lookup）：15 failed, 11 passed，全部因功能缺失。
- GREEN#2：26 passed。
- RED#3（投毒输入 `execution_ready=True` 泄漏进 lookup.resolve）：1 failed —— 证实
  `base.update(record, ...)` 会让输入覆盖就绪标志；修复后固定标志二次覆盖。
- GREEN#3：`tests/test_model_discovery.py` 27 passed（0.04s）。
- 全量：`/tmp/gab/.venv/bin/python -m pytest tests/ -q` → 1136 passed, 1 failed（环境性，见状态节）。
- 测试数据全部为 `synthetic=True`、名称 `synthetic-*` 的显式合成记录，未断言任何真实模型名/日期。

## 集成状态（重要）

- **当前仅模块级交付，尚未接入 `/api/catalog` / `configured_catalog()` / LiveCatalog。**
- 待办（不在本任务范围）：
  1. 官方核实的模型审核记录数据（哥哥提供，含正式发布日期与来源 URL）。
  2. 父任务/owner 决策后再把 `RecentModelDiscovery` 以新的 provider source 形式接入
     `configured_catalog`（类似 provider_metadata 的注入方式），并补齐 exposure/approval 治理。
  3. 不部署；本分支交审。
