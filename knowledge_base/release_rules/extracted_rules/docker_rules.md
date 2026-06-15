# Docker Rules

本文件汇总第一阶段 DockerChecker 和 DockerStyleChecker 可使用的规则。

## Rules

| rule_id | rule_name | checker | source | priority | phase |
|---|---|---|---|---|---|
| RG-DOCKER-001 | 检查项目是否存在 Dockerfile | DockerChecker | Dockerfile reference | high | phase-1 |
| RG-DOCKER-002 | 检查 Dockerfile 是否包含 FROM 指令 | DockerChecker | Dockerfile reference | high | phase-1 |
| RG-DOCKER-003 | 检查 FROM 是否出现在合理位置 | DockerChecker | Dockerfile reference | high | phase-1 |
| RG-DOCKER-004 | 检查 Dockerfile 是否包含 WORKDIR | DockerChecker | Dockerfile reference | medium | phase-1 |
| RG-DOCKER-005 | 检查 Dockerfile 是否包含 COPY 或 ADD | DockerChecker | Dockerfile reference | medium | phase-1 |
| RG-DOCKER-006 | 检查 Dockerfile 是否包含依赖安装步骤 | DockerChecker | Dockerfile reference | medium | phase-1 |
| RG-DOCKER-007 | 检查 Dockerfile 是否包含 CMD 或 ENTRYPOINT | DockerChecker | Dockerfile reference | high | phase-1 |
| RG-DOCKER-008 | 检查 Dockerfile 指令是否使用统一大写风格 | DockerStyleChecker | Dockerfile reference | low | phase-1 |

## 第一阶段落地方式

- 先查找根目录 `Dockerfile`。
- 使用逐行文本解析 Dockerfile 指令。
- 先实现存在性和基础结构检查。
- 不在第一阶段做镜像构建和安全扫描。

## 需要人工确认

- 缺少 Dockerfile 是否对所有项目都算 failed。
- 是否识别 `docker/Dockerfile`。
- Docker 指令大小写问题是否只作为 info 或 low。
