# OWASP Application Security Verification Standard，ASVS

## URL

https://owasp.org/www-project-application-security-verification-standard/

## 属于哪类

安全

## 资料主要内容

OWASP ASVS 是 Web 应用安全验证标准，为应用安全控制测试和安全开发要求提供结构化清单。它适合为 ReleaseGuard Agent 的安全基线检查提供参考，也适合在报告中记录安全规则依据和版本信息。

## 对 ReleaseGuard Agent 的作用

它适合作为 SecurityBaselineChecker 的高层安全依据。第一阶段不做完整 ASVS 审计，只抽取发布前可以自动识别、能生成明确 evidence 的安全基线规则。

## 可抽取的检查规则

- rule_id: RG-SEC-001
- rule_name: 检查是否疑似硬编码敏感信息
- checker: SecurityBaselineChecker
- rule_source: OWASP ASVS；具体 ASVS 版本号和要求编号需要人工确认
- risk_level: critical
- detection_target: Python 源码、配置文件、样例文件中是否出现 `password`、`token`、`secret`、`api_key` 等高风险关键词和值
- why_dangerous: 硬编码敏感信息可能导致密钥泄露、权限滥用和生产系统被攻击
- evidence: 命中的文件路径、行号、关键词；报告中必须脱敏展示
- recommendation: 移除真实敏感信息，改用环境变量或安全密钥管理方案，并更新 `.env.example`
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-SEC-002
- rule_name: 检查 Flask 是否开启 debug=True
- checker: SecurityBaselineChecker
- rule_source: OWASP ASVS 可作为生产安全配置参考，但 Flask `debug=True` 的直接依据需要补充 Flask 官方部署或安全文档，需要人工确认
- risk_level: critical
- detection_target: Flask 项目中是否存在 `debug=True`、`app.run(debug=True)` 或生产配置中 debug 开启
- why_dangerous: 生产环境开启 debug 可能暴露调试信息，增加敏感信息泄露和远程利用风险
- evidence: 命中的文件路径、行号和 debug 配置片段
- recommendation: 生产环境关闭 debug，通过环境变量区分开发和生产配置
- implementation_difficulty: easy
- phase: phase-1

- rule_id: RG-SEC-003
- rule_name: 检查是否存在过宽 CORS 配置
- checker: SecurityBaselineChecker
- rule_source: OWASP ASVS 可作为 Web 安全控制参考，但具体 CORS 要求编号需要人工确认
- risk_level: high
- detection_target: FastAPI / Flask 项目中是否出现允许所有来源的 CORS 配置，例如 `allow_origins=["*"]`
- why_dangerous: 过宽 CORS 可能扩大攻击面，让不可信站点更容易调用 API
- evidence: 命中的 CORS 配置文件路径、行号和配置值
- recommendation: 按环境和业务域名配置精确 origin，避免生产环境使用通配符
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-SEC-004
- rule_name: 检查危险命令执行用法
- checker: SecurityBaselineChecker
- rule_source: OWASP ASVS 可作为输入验证和安全执行参考，但 `os.system` / `subprocess(shell=True)` 的具体标准映射需要人工确认
- risk_level: high
- detection_target: Python 源码中是否出现 `os.system`、`subprocess.*(shell=True)` 等危险命令执行模式
- why_dangerous: 如果命令参数包含外部输入，可能造成命令注入风险
- evidence: 命中的文件路径、行号和危险调用类型
- recommendation: 避免 shell 执行；使用参数列表调用 subprocess，并严格校验外部输入
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-SEC-005
- rule_name: 检查是否缺少生产安全配置说明
- checker: SecurityBaselineChecker
- rule_source: OWASP ASVS 可作为安全配置参考，但“安全配置说明文件”属于 ReleaseGuard 落地规则，需要人工确认
- risk_level: medium
- detection_target: README、docs 或配置示例中是否说明生产环境安全配置，例如 secret、debug、CORS、HTTPS、环境变量
- why_dangerous: 缺少生产安全说明会让部署人员依赖默认配置或开发配置上线
- evidence: README 或 docs 中命中的安全配置段落；或未发现相关说明
- recommendation: 在 README 或 docs 中补充生产环境安全配置要求和禁止项
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-SEC-006
- rule_name: 检查安全报告是否记录 ASVS 版本和要求编号
- checker: SecurityReportChecker
- rule_source: OWASP ASVS 的版本化和编号化结构
- risk_level: info
- detection_target: 安全类规则在报告或规则库中是否记录 ASVS 版本号和具体要求编号
- why_dangerous: 没有版本和编号会让安全发现缺少可追溯依据，后续复查和审计困难
- evidence: 规则库中的 `rule_source`、ASVS 版本号、要求编号字段
- recommendation: 后续补充 ASVS 版本和具体要求编号，避免只写笼统来源
- implementation_difficulty: medium
- phase: phase-2

## 暂不实现的内容

- 完整 ASVS 审计。
- 认证、授权、会话、加密、业务逻辑安全的深度验证。
- 动态安全扫描。
- 第三方漏洞库联动。

## 我还需要人工确认的问题

- 每条安全规则需要映射到哪个 ASVS 版本和具体要求编号。
- 第一阶段是否只做源码静态文本扫描，还是允许解析 AST。
- 报告中如何脱敏展示疑似敏感信息 evidence。
