# ReleaseGuard Agent Rule Index

本文件汇总第一批从公开资料抽取的规则素材，并加入专业默认裁决。

字段说明：

- `support_level`: 规则依据强度，取值为 `source-backed`、`releaseguard-default`、`needs-source-mapping`。
- `blocking_policy`: 第一阶段默认阻断策略，取值为 `block`、`warn`、`info`、`conditional`。
- `evidence_type`: checker 应该生成的主要证据类型。

| rule_id | rule_name | checker | source | support_level | priority | blocking_policy | evidence_type | phase |
|---|---|---|---|---|---|---|---|---|
| RG-CONFIG-001 | 检查项目是否提供环境变量示例文件 | ConfigChecker | The Twelve-Factor App + ReleaseGuard default policy | releaseguard-default | high | conditional | file_exists | phase-1 |
| RG-CONFIG-002 | 检查代码中是否疑似硬编码配置 | ConfigChecker | The Twelve-Factor App | source-backed | high | conditional | matched_lines | phase-1 |
| RG-DEPS-001 | 检查项目是否显式声明依赖 | DependencyChecker | The Twelve-Factor App | source-backed | high | block | file_exists | phase-1 |
| RG-LOG-001 | 检查项目是否存在基础日志使用痕迹 | LoggingChecker | The Twelve-Factor App + ReleaseGuard default policy | releaseguard-default | medium | warn | matched_lines | phase-1 |
| RG-DEPLOY-001 | 检查项目是否说明启动命令 | DeploymentChecker | The Twelve-Factor App + ReleaseGuard default policy | releaseguard-default | high | conditional | matched_lines | phase-1 |
| RG-PORT-001 | 检查 Web 项目是否有端口绑定或服务入口线索 | WebEntryChecker | The Twelve-Factor App | source-backed | medium | conditional | matched_lines | phase-1 |
| RG-TEST-001 | 检查项目是否存在 tests 目录 | TestChecker | pytest Good Integration Practices + ReleaseGuard default policy | releaseguard-default | high | warn | directory_exists | phase-1 |
| RG-TEST-002 | 检查是否存在 pytest 可发现的测试文件 | TestChecker | pytest Good Integration Practices | source-backed | high | block | file_glob_matches | phase-1 |
| RG-TEST-003 | 检查是否存在 pytest 可收集的测试函数或方法 | TestChecker | pytest Good Integration Practices | source-backed | high | block | collected_tests | phase-1 |
| RG-TEST-004 | 检查 pytest 是否能成功收集测试 | TestChecker | pytest Good Integration Practices | source-backed | high | block | command_result | phase-1 |
| RG-TEST-005 | 检查 pytest 测试是否能运行通过 | TestChecker | pytest Good Integration Practices | source-backed | high | block | command_result | phase-1 |
| RG-TEST-006 | 检查项目是否存在 pytest 配置 | TestConfigChecker | pytest Good Integration Practices + ReleaseGuard default policy | releaseguard-default | medium | warn | file_exists | phase-1 |
| RG-TEST-007 | 检查 src layout 项目是否有可复现的导入配置 | TestConfigChecker | pytest Good Integration Practices | source-backed | medium | conditional | config_value | phase-1 |
| RG-FASTAPI-001 | 检查项目是否声明 FastAPI 依赖 | FastAPIDetector | FastAPI Testing + ReleaseGuard dependency policy | releaseguard-default | high | block | dependency_line | phase-1 |
| RG-FASTAPI-002 | 检查代码中是否存在 FastAPI app 实例 | FastAPIDetector | FastAPI Testing | source-backed | high | block | matched_lines | phase-1 |
| RG-FASTAPI-003 | 检查是否使用 FastAPI TestClient | FastAPITestChecker | FastAPI Testing | source-backed | medium | warn | matched_lines | phase-1 |
| RG-FASTAPI-004 | 检查是否存在针对 FastAPI app 的测试文件 | FastAPITestChecker | FastAPI Testing | source-backed | high | warn | matched_lines | phase-1 |
| RG-FASTAPI-005 | 检查 FastAPI 测试是否能被 pytest 运行 | FastAPITestChecker | FastAPI Testing | source-backed | high | block | command_result | phase-1 |
| RG-FASTAPI-006 | 检查 FastAPI 测试是否包含基础状态码断言 | FastAPITestChecker | FastAPI Testing | source-backed | medium | warn | matched_lines | phase-1 |
| RG-FASTAPI-007 | 检查是否存在健康检查接口 | FastAPIHealthChecker | ReleaseGuard default policy; stronger source needed | releaseguard-default | medium | warn | route_match | phase-1 |
| RG-DOCKER-001 | 检查项目是否存在 Dockerfile | DockerChecker | Dockerfile reference + ReleaseGuard container policy | releaseguard-default | high | conditional | file_exists | phase-1 |
| RG-DOCKER-002 | 检查 Dockerfile 是否包含 FROM 指令 | DockerChecker | Dockerfile reference | source-backed | high | block | docker_instruction | phase-1 |
| RG-DOCKER-003 | 检查 FROM 是否出现在合理位置 | DockerChecker | Dockerfile reference | source-backed | high | block | docker_instruction_order | phase-1 |
| RG-DOCKER-004 | 检查 Dockerfile 是否包含 WORKDIR | DockerChecker | Dockerfile reference + ReleaseGuard default policy | releaseguard-default | medium | warn | docker_instruction | phase-1 |
| RG-DOCKER-005 | 检查 Dockerfile 是否包含 COPY 或 ADD | DockerChecker | Dockerfile reference + ReleaseGuard default policy | releaseguard-default | medium | warn | docker_instruction | phase-1 |
| RG-DOCKER-006 | 检查 Dockerfile 是否包含依赖安装步骤 | DockerChecker | Dockerfile reference + ReleaseGuard Python image policy | releaseguard-default | medium | warn | docker_instruction | phase-1 |
| RG-DOCKER-007 | 检查 Dockerfile 是否包含 CMD 或 ENTRYPOINT | DockerChecker | Dockerfile reference + ReleaseGuard runnable image policy | releaseguard-default | high | conditional | docker_instruction | phase-1 |
| RG-DOCKER-008 | 检查 Dockerfile 指令是否使用统一大写风格 | DockerStyleChecker | Dockerfile reference | source-backed | low | info | docker_instruction_style | phase-1 |
| RG-SEC-001 | 检查是否疑似硬编码敏感信息 | SecurityBaselineChecker | OWASP ASVS general basis; exact mapping needed | needs-source-mapping | high | block | redacted_matched_lines | phase-1 |
| RG-SEC-002 | 检查 Flask 是否开启 debug=True | SecurityBaselineChecker | ReleaseGuard security baseline; Flask source needed | needs-source-mapping | high | block | matched_lines | phase-1 |
| RG-SEC-003 | 检查是否存在过宽 CORS 配置 | SecurityBaselineChecker | OWASP ASVS general basis; exact mapping needed | needs-source-mapping | high | conditional | matched_lines | phase-1 |
| RG-SEC-004 | 检查危险命令执行用法 | SecurityBaselineChecker | OWASP ASVS command injection reference | source-backed | medium | conditional | matched_lines | phase-1 |
| RG-SEC-005 | 检查是否缺少生产安全配置说明 | SecurityBaselineChecker | ReleaseGuard default policy | releaseguard-default | medium | warn | doc_section_match | phase-1 |
| RG-SEC-006 | 检查安全报告是否记录 ASVS 版本和要求编号 | SecurityReportChecker | OWASP ASVS | source-backed | medium | info | rule_metadata | phase-2 |

## 第一阶段默认优先级

建议优先实现：

1. `RG-DEPS-001`: 依赖声明文件存在性。
2. `RG-CONFIG-001`: `.env.example` 存在性。
3. `RG-TEST-001` 到 `RG-TEST-005`: 测试结构、测试收集、测试运行。
4. `RG-FASTAPI-001` 和 `RG-FASTAPI-002`: FastAPI 项目识别。
5. `RG-DOCKER-001` 到 `RG-DOCKER-003`: Dockerfile 基础结构。

## 当前仍需补充资料的规则

- `RG-FASTAPI-007`: 需要补充健康检查或部署平台实践资料，但可以先作为 ReleaseGuard 默认策略实现为 warning。
- `RG-SEC-002`: 需要补充 Flask 官方生产部署或 debug 安全文档，但可以先按安全基线阻断。
- `RG-SEC-001`、`RG-SEC-003`、`RG-SEC-005`: 需要映射到更精确的 ASVS 版本和要求编号。

## 不允许的报告表达

- 不允许说 ReleaseGuard 第一阶段完成了完整 ASVS 审计。
- 不允许把 `.env.example` 说成 Twelve-Factor 原文强制要求。
- 不允许把 `/health` 说成 FastAPI 官方强制要求。
- 不允许在 evidence 中展示完整 secret 值。
