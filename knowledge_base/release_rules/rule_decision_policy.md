# Rule Decision Policy

本文件定义 ReleaseGuard Agent 规则库的专业默认判断方式。

目标不是把所有规则都说成“官方强制要求”，而是把规则来源和工程判断分清楚：

- 哪些规则是资料直接支持的。
- 哪些规则是 ReleaseGuard 为发布前检查制定的默认策略。
- 哪些规则有价值，但还需要补充更强来源映射。
- 哪些规则暂时不进入第一阶段实现。

## 支持级别

### source-backed

含义：

该规则可以被当前资料直接支撑。

适合写法：

- 报告中可以写“依据来源：某官方文档 / 某标准”。
- 代码中可以把 `rule_source` 设置为明确资料名称。
- 如果规则失败，可以作为明确发布风险。

例子：

- pytest 默认发现 `test_*.py` 和 `*_test.py`。
- Dockerfile 必须从 `FROM` 开始，允许前面有 parser directive、注释或全局 `ARG`。
- FastAPI Testing 文档展示 `TestClient(app)` 和 `assert response.status_code == 200`。

### releaseguard-default

含义：

该规则不是资料逐字强制要求，但它是 ReleaseGuard 基于资料、发布风险和真实工程经验做出的默认发布策略。

适合写法：

- 报告中应写“ReleaseGuard 默认发布策略”。
- 不应说“官方文档强制要求”。
- 可以进入第一阶段实现，但要在 `rule_source` 中保留来源和策略边界。

例子：

- `.env.example` 是 ReleaseGuard 对 Twelve-Factor Config 原则的落地方式。
- 缺少健康检查接口先作为 ReleaseGuard 默认发布风险。
- Dockerfile 存在时，要求有 `CMD` 或 `ENTRYPOINT` 作为可运行镜像的默认策略。

### needs-source-mapping

含义：

该规则有工程价值，也适合自动检查，但当前还没有精确映射到某个官方条款、标准编号或框架文档。

适合写法：

- 可以先实现为安全基线或质量基线。
- 报告中不能声称完整合规。
- 后续应补充具体来源、版本号或规则编号。

例子：

- OWASP ASVS 相关安全规则在没有具体编号前，只能说“参考 ASVS 安全基线”。
- Flask `debug=True` 需要补充 Flask 官方部署或安全文档作为直接依据。
- CORS 通配符风险需要补充框架或安全资料。

### deferred

含义：

规则目前不适合第一阶段实现。

常见原因：

- 需要运行容器或外部服务。
- 需要复杂 AST / 数据流分析。
- 需要完整安全审计。
- 需要 Agent / RAG / eval 基础设施成熟后再做。

## 阻断策略

ReleaseGuard 第一阶段使用四种阻断策略：

### block

含义：

该问题应阻止发布。

适用情况：

- 测试执行失败。
- Dockerfile 存在但基础结构无效。
- 明确发现真实敏感信息。
- Web 项目入口无法识别且项目声明要发布服务。

### warn

含义：

该问题需要修复或确认，但第一阶段不直接阻断发布。

适用情况：

- 缺少健康检查接口。
- 缺少基础日志痕迹。
- Dockerfile 风格问题。
- 缺少生产安全说明。

### info

含义：

只是提供质量建议或上下文信息。

适用情况：

- Dockerfile 指令大小写风格。
- 规则来源编号还需要增强。

### conditional

含义：

是否阻断取决于项目上下文。

适用情况：

- 缺少 Dockerfile：如果项目声明容器化发布，则 block；否则 warn 或 skipped。
- 缺少 `.env.example`：如果代码使用环境变量或配置变量，则 block；否则 warn。
- CORS 通配符：如果明确是生产配置，则 block；如果只是开发样例，则 warn。

## 第一阶段默认判断原则

第一阶段优先实现可以产生明确 evidence 的规则：

- 文件是否存在。
- 配置是否存在。
- 命令是否能运行。
- 入口是否能识别。
- 文本扫描是否能给出文件路径和行号。

第一阶段避免实现：

- 需要完整安全审计的规则。
- 需要运行真实外部服务的规则。
- 需要复杂语义理解的规则。
- 不能给出清楚 evidence 的规则。

## 报告措辞原则

报告必须区分三种话术：

- `source-backed`: “依据官方文档 / 标准，该项应满足……”
- `releaseguard-default`: “根据 ReleaseGuard 默认发布策略，建议满足……”
- `needs-source-mapping`: “该项为 ReleaseGuard 安全/质量基线，后续需要补充更精确来源映射……”

这样做的原因：

- 对老板和面试官透明。
- 对开发者公平。
- 对后续 RAG 和报告生成更稳定。
- 不把工程经验伪装成官方标准。
