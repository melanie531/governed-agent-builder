# Create Agent 自助构建与部署

日期：2026-09-13
账户：`820242898417` / `us-west-2`
分支：`feat/create-agent-journey`

## 确认后的范围

只保留 Research、Knowledge Q&A 两个模板。Research 合并原来的 Research brief 和 Web research，支持研究公开资料和总结用户提供的材料。

Business user 使用平台发布的 Foundation Harness，配置模型、prompt、MCP server、skills 和输出格式。模型、MCP server、其下的工具、skills 都由后端 AI Catalog 提供；创建页与 Catalog 页使用同一个 `/api/catalog` 数据源。模板只保存推荐能力的引用，不在前端或 runtime 内写死能力清单。

构建时选择 MCP server。每个连接默认只展示用途、状态和数据处理说明；展开 Tool permissions 才能查看该 server 下的批准工具并收窄权限。构建时不执行工具，agent 运行时才通过 AgentCore Gateway 调用。保存后的工具列表是明确的授权快照，新发现的工具不会自动加入。

## 用户流程

1. 选择模板。
2. 填名称、选择模型与 MCP server、选择 skills、编辑 prompt 和输出格式。
3. 可选 evaluation dataset：下载 JSON、使用 synthetic sample、上传或粘贴。空白和 `[]` 均跳过评估；非空无效数据必须修正。限制 20 cases / 32 KiB。
4. 点击 Deploy to AgentCore 保存不可变版本并排队部署，也可以只保存草稿。
5. 详情页分别展示 deployment 与 evaluation；部署成功后可 Try agent。刷新页面恢复状态。修改配置创建新版本。

没有 dataset 时不会创建 evaluation job，也不会调用 Evaluate。提供 dataset 时先部署并独立健康检查，再按 case 调用真实 Runtime 并执行 AgentCore on-demand evaluation。评估质量未达标或评估服务失败都不会把已部署的 Runtime 标成部署失败。

## 执行与权限

- Foundation v2 使用 Bedrock Converse 执行已批准模型，模型 ID 与参数支持来自 Catalog binding。已验证发布目标为 Claude Haiku 4.5 和 GPT-6 Astra。
- 本项目创建 `gab-journey-tools` Gateway，AWS_IAM 入站认证。Tavily 使用远程 MCP target，API key 保存在 AgentCore Identity；Knowledge 使用 Gateway Lambda target，返回明确标注的 synthetic Aurora 文档。
- 保存、部署、每个 worker 阶段和普通调用都检查 business membership、owner、workspace、Catalog grant、server/tool 归属与精确版本。撤销 MCP server 后，其下的旧工具选择也失效。
- Foundation ZIP、domain manifest 和执行证据存入私有、版本化 S3。保存时固定 Foundation artifact 版本与摘要；客户端不能提供 Runtime ARN、角色、Gateway URL 或模型执行 binding。
- 每个 agent version 创建一个 IAM-only AgentCore Runtime，使用平台拥有的代码与项目角色。当前为 managed PUBLIC network；不是客户 VPC 内部署。应用 worker 才能调用本项目 namespace 下的 Runtime。
- Runtime 对工具名和输入 schema 再次校验，限制模型/工具次数、输入与证据大小。实际 OTel spans 写入私有 S3 和专用 CloudWatch log group。
- 评估调用原生 `Builtin.Correctness`，提交真实 session spans 和参考答案。保留 AWS request ID、分数、解释和被忽略的参考字段。引用/JSON/required terms 是额外结构检查。
- 外部付费调用在数据库事务外执行，先持久化 claim。幂等保存和部署防止双击重复创建；Evaluate 没有原生幂等键，失去确定结果时标记 UNKNOWN，不自动重放。

## 发布与测试

`examples/journey/` 是平台首次发布输入。平台发布脚本把它写入后端 Catalog；应用运行时不读取这些文件。Skill 的平台管理 API 支持无需重新部署应用即可发布新内容和版本。业务用户只读取有权限的目录记录。

发布命令和具体组件见 [部署说明](../create-agent-journey.md)。本地测试使用只存在于 `tests/` 的 cloud adapter，云端包不包含该 adapter。浏览器测试必须区分本地状态验证与真实 Cognito / Runtime / Gateway / Evaluate 验收。

验收矩阵：两个模板 × 无 dataset / synthetic dataset；选择不同模型；MCP 权限展开/收窄；刷新恢复；Try agent；修改后保存新版本；重复提交；权限撤销；过期 Catalog 版本；不确定付费请求不自动重放。云端检查必须记录实际 Runtime、工具调用、model ID 和原生 evaluation request ID。
