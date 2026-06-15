# The Twelve-Factor App

## URL

https://12factor.net/

## 属于哪类

发布规则

## 资料主要内容

The Twelve-Factor App 是一套面向服务型应用的发布、运行和运维原则。它强调应用应该显式声明依赖，把配置与代码分离，通过环境变量管理配置，区分构建、发布、运行阶段，把日志当作事件流，并通过端口绑定对外提供服务。

## 对 ReleaseGuard Agent 的作用

它适合作为 ReleaseGuard Agent 第一阶段发布前检查的总纲，尤其适合支撑配置检查、依赖声明检查、日志检查、部署入口检查和 Web 服务入口检查。

## 可抽取的检查规则

- rule_id: RG-CONFIG-001
- rule_name: 检查项目是否提供环境变量示例文件
- checker: ConfigChecker
- rule_source: The Twelve-Factor App - Config；该资料直接支持“配置应与代码分离并存放在环境中”，`.env.example` 是 ReleaseGuard 的落地约定，需要人工确认
- risk_level: medium
- detection_target: 项目根目录是否存在 `.env.example`
- why_dangerous: 如果项目没有环境变量示例，新成员、部署环境和 CI/CD 很难知道发布前必须准备哪些配置，容易导致运行时缺少配置或错误配置
- evidence: `.env.example` 是否存在；如果不存在，记录项目根目录文件列表
- recommendation: 在项目根目录提供 `.env.example`，只写变量名和安全示例值，不写真实密钥
- implementation_difficulty: easy
- phase: phase-1

- rule_id: RG-CONFIG-002
- rule_name: 检查代码中是否疑似硬编码配置
- checker: ConfigChecker
- rule_source: The Twelve-Factor App - Config
- risk_level: high
- detection_target: Python 源码中是否出现疑似环境相关配置硬编码，例如 `DATABASE_URL = "..."`、`SECRET_KEY = "..."`、`API_KEY = "..."`
- why_dangerous: 配置写死在代码里会导致不同环境难以隔离，也可能把敏感信息提交到仓库
- evidence: 命中的文件路径、行号、变量名或疑似配置片段
- recommendation: 使用环境变量读取配置，并把需要的变量写入 `.env.example`
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-DEPS-001
- rule_name: 检查项目是否显式声明依赖
- checker: DependencyChecker
- rule_source: The Twelve-Factor App - Dependencies
- risk_level: high
- detection_target: 项目根目录是否存在 `requirements.txt`、`pyproject.toml`、`Pipfile` 或类似依赖声明文件
- why_dangerous: 没有依赖声明会导致发布环境无法稳定复现，测试和部署结果不可预测
- evidence: 命中的依赖声明文件；如果不存在，记录项目根目录文件列表
- recommendation: 至少提供 `requirements.txt`；后续可升级为 `pyproject.toml` 或锁文件机制
- implementation_difficulty: easy
- phase: phase-1

- rule_id: RG-LOG-001
- rule_name: 检查项目是否存在基础日志使用痕迹
- checker: LoggingChecker
- rule_source: The Twelve-Factor App - Logs；资料支持把日志视为事件流，但“基础日志配置”作为检查规则需要人工确认
- risk_level: medium
- detection_target: 是否存在 `logging`、`structlog`、`loguru` 等日志使用痕迹，或是否完全依赖 `print`
- why_dangerous: 缺少日志会让发布后的问题排查、错误定位和运行观察变困难
- evidence: 日志相关 import、配置文件、日志调用位置；或未发现日志使用的说明
- recommendation: 使用 Python `logging` 或成熟日志库，并确保日志输出适合被部署环境收集
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-DEPLOY-001
- rule_name: 检查项目是否说明启动命令
- checker: DeploymentChecker
- rule_source: The Twelve-Factor App - Processes / Build, release, run；资料支持应用以进程运行，但具体启动命令说明属于 ReleaseGuard 落地规则，需要人工确认
- risk_level: medium
- detection_target: `README.md`、Dockerfile、脚本或配置中是否存在可识别启动命令，例如 `uvicorn`、`flask run`、`python -m`
- why_dangerous: 没有启动命令说明会让发布、部署、验收和故障恢复依赖人工猜测
- evidence: 命中的启动命令文本、文件路径和行号
- recommendation: 在 README 或部署配置中明确写出本地启动和生产启动命令
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-PORT-001
- rule_name: 检查 Web 项目是否有端口绑定或服务入口线索
- checker: WebEntryChecker
- rule_source: The Twelve-Factor App - Port binding
- risk_level: medium
- detection_target: FastAPI / Flask 项目中是否存在端口配置、`uvicorn` 启动入口、`flask run` 说明或 Web app 实例
- why_dangerous: Web 服务如果缺少清晰入口和端口线索，发布平台难以正确启动和路由流量
- evidence: 命中的端口、启动命令、app 实例或入口文件
- recommendation: 明确服务入口和端口配置方式，优先通过环境变量配置端口
- implementation_difficulty: medium
- phase: phase-1

## 暂不实现的内容

- 完整的 build / release / run 阶段建模。
- dev/prod parity 的深度分析。
- 进程并发模型检查。
- backing services 的完整依赖拓扑分析。

## 我还需要人工确认的问题

- `.env.example` 是否作为 ReleaseGuard Agent 的第一阶段强制规则，还是只作为建议规则。
- 启动命令说明应该优先从 README、Dockerfile、脚本，还是框架入口中识别。
- 日志检查第一阶段是只做存在性检查，还是同时检查是否输出到 stdout/stderr。
