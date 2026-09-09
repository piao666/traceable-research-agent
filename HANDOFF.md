# 交接文档：R10 Real Runtime 本地验收完成、待发布

> 本文档用于跨会话交接。移动中的发布状态以远端 `feature/improvements` HEAD
> 及其对应 GitHub Actions 结果为准，避免把旧提交号误写成当前分支 HEAD。

## 一、当前状态

- **分支**：`feature/improvements`
- **当前已发布基线**：`304064158c7ffebbe9678863cf7d75f74054b910`
- **当前阶段**：`R10 — Real Runtime`
- **发布状态**：R10.0a 与 R10 Real Runtime 实现及本地门禁已全部收口；尚未提交或推送
- **项目边界**：单实例、本地优先；不内置多租户、RAG 或向量数据库

## 二、已发布能力（截至 3040641）

- 计划式与 ReAct 调研执行、人工计划审批、恢复与重试。
- Run、Trace、Evidence、Citation、Report 的可追溯持久化与导出。
- 共享预算、子任务关联、失败原因保真和有界恢复。
- 信源治理、网页正文缓存、PDF 阅读、学术检索和 Skill 工作流。
- FastAPI、React/Vite 与 Streamlit 界面，以及 Docker 配置和本地持久化。
- R9 增加研究目标检查、数据口径约束、来源 ID 复用、重复抓取抑制和旧结果完整性标记。

本轮关键提交（文档提交本身以分支 HEAD 为准）：

```text
3040641 2026-09-08 fix: strengthen research recovery and simplify execution UI
de91b9d 2026-09-07 docs: finalize repository repair handoff
adc76a9 2026-09-07 test: stabilize feature branch CI gates
```

## 三、本轮待发布修复

当前修复范围不含 `LICENSE`：

1. 将 `finalization_reserve` 从根研究阶段的终止原因改为最终质量门／报告软切换信号。
2. 用语言感知的保守 Token 估算替换 UTF-8 字节计数，并在有 provider usage 时回填真实值。
3. 按场景、产品数量、比较维度和需求矩阵扩展动态步骤；核心搜索／抓取工具采用任务感知额度。
4. 为技术比较建立产品 × 维度 Research Contract、覆盖矩阵、缺口提示和完成门禁。
5. 模糊产品比较无明确学术意图时禁用 arXiv／Crossref／OpenAlex／Semantic Scholar，避免实体漂移。
6. 提示上下文混合已抓取与待抓取来源，去除重复 snippet；完整队列从 Trace 重建，不再写入 plan_json。
7. 新建研究页移除 Planned/ReAct 选择，默认采用后端动态路由；检索策略默认自动推断。
8. 校正综合 Smoke 与本地评估中已过期的成功预期、限流模拟和工具名称。
9. 官方离线测试入口改用一次性 SQLite，并强制 Offline Profile、清空外部密钥且传给子进程，避免碰触部署数据或意外调用真实 Provider。
10. 增加 `deep`、`standard`、`offline` 三档 `RESEARCH_PROFILE`，显式环境变量仍可覆盖档位默认值；Deep 档扩大共享安全上限。
11. 增加通用 `openai_compatible` Provider，保留 qwen／deepseek 别名，并统一 usage 与错误分类。
12. 认证、权限、限流、超时、模型不存在、上下文溢出和结构化输出错误进入 ReAct Trace 与有界恢复；不记录响应正文或密钥。
13. 新增脱敏 Runtime Capability 模型及显式 `POST /api/runtime/preflight`，验证真实 LLM、Tavily 和静态网页抓取，模拟搜索不能冒充成功。
14. 新建研究页仅显示中文的研究环境简要状态和“验证真实连接”操作，不恢复内部预算／恢复板块或 Planned/ReAct 选择。
15. `.env.example` 收敛为最小真实 Deep 配置，`.env.example.full` 保存高级覆盖项，`.env.example.offline` 隔离模拟环境；新增带显式付费确认保护的 `scripts/validate_real_runtime.py`。
16. Planner 分解、Deepening Critic 以及 Planned／ReAct／Deepening 报告合成均记录稳定、脱敏的 LLM 错误分类；报告失败统一进入 Run 失败收口。
17. 真实预检失败后，前端明确显示模型、搜索与网页抓取“验证失败”，不再误显示为尚未验证。

## 四、验证结果

- Python `compileall` 与 `git diff --check` 通过。
- 后端完整离线 pytest：收集 651 项，649 通过、2 条件跳过、0 失败；7 条第三方弃用警告；0 次外部网络尝试。
- R10／恢复／完整性／深化专项：153 通过，另有 24 个子测试通过。
- 前端：11 个测试文件、104 项通过；类型检查、Lint 和生产构建通过。
- 综合项目 Smoke：18/18 通过；本地评估 78/80 通过，余下 2 项为明确标注的真实网络依赖，0 硬失败。
- Docker 静态配置 Smoke 通过；当前环境没有 Docker CLI，未实际构建／启动。
- 未调用真实收费模型／搜索服务，未执行 Windows 浏览器人工验收。

## 五、下一步与未验证项

- 审阅当前差异后提交并推送到 `feature/improvements`，确认功能分支 CI 通过。
- 在 Windows 预览目录拉取新提交，重新构建 `api web`，先在新建研究页执行真实连接验证，再运行 `scripts/validate_real_runtime.py --confirm-real-calls --run-task` 完成真实厂商验收。
- 真实 LLM／Tavily 验收通过后进入 R11 抓取路由；R12 Intelligence、R13 本地可靠性和 R14 真实验收依次推进。
- `LICENSE` 按用户要求暂不处理。

## 六、工程注意事项

1. `TASK.md`、`docs/`、`.env`、`workspace/` 运行产物均为本地内容，不得提交。
2. 文件读取必须保持路径边界、逐文件 HITL 和长度／体积上限。
3. 外部系统写操作不得通过 MCP 暴露；本地持久化副作用必须在工具元数据中如实声明。
4. 所有工具执行继续保留 Trace；失败、拒绝和预算耗尽不得被报告生成覆盖。
5. 不要把固定数据或 mock 回归描述成真实联网验收。

(End of file)
