# Config And Release Baseline Rules

本文件汇总第一阶段可落地的配置、依赖、日志和发布入口类规则。依赖规则暂时放在这里，是因为本轮只要求建立 `config_rules.md`，后续可以拆成独立的 `dependency_rules.md`。

## Rules

| rule_id | rule_name | checker | source | priority | phase |
|---|---|---|---|---|---|
| RG-CONFIG-001 | 检查项目是否提供环境变量示例文件 | ConfigChecker | The Twelve-Factor App | high | phase-1 |
| RG-CONFIG-002 | 检查代码中是否疑似硬编码配置 | ConfigChecker | The Twelve-Factor App | high | phase-1 |
| RG-DEPS-001 | 检查项目是否显式声明依赖 | DependencyChecker | The Twelve-Factor App | high | phase-1 |
| RG-LOG-001 | 检查项目是否存在基础日志使用痕迹 | LoggingChecker | The Twelve-Factor App | medium | phase-1 |
| RG-DEPLOY-001 | 检查项目是否说明启动命令 | DeploymentChecker | The Twelve-Factor App | high | phase-1 |
| RG-PORT-001 | 检查 Web 项目是否有端口绑定或服务入口线索 | WebEntryChecker | The Twelve-Factor App | medium | phase-1 |

## 第一阶段落地方式

- 使用文件存在性检查识别 `.env.example` 和依赖声明文件。
- 使用简单文本扫描识别疑似硬编码配置。
- 使用 README、Dockerfile、脚本和源码文本识别启动命令。
- 使用 FastAPI / Flask app 入口、端口配置和启动命令识别 Web 服务入口线索。

## 需要人工确认

- `.env.example` 是否设为强制规则。
- 依赖规则后续是否拆分到 `dependency_rules.md`。
- 日志规则第一阶段是否只做 warning。
