# Dockerfile reference

## URL

https://docs.docker.com/reference/dockerfile/

## 属于哪类

Docker

## 资料主要内容

Dockerfile reference 说明 Dockerfile 是包含镜像构建指令的文本文件，Docker 会读取这些指令构建镜像。文档定义了 `FROM`、`WORKDIR`、`COPY`、`ADD`、`RUN`、`CMD`、`ENTRYPOINT` 等常见指令，并说明 Dockerfile 指令不区分大小写，但通常使用大写以便区分参数。

## 对 ReleaseGuard Agent 的作用

它可以作为 DockerChecker 第一阶段规则依据，用于检查项目是否具备可构建镜像的基本文件和基本 Dockerfile 结构。第一阶段只做存在性和基础结构检查，不做完整容器安全审计。

## 可抽取的检查规则

- rule_id: RG-DOCKER-001
- rule_name: 检查项目是否存在 Dockerfile
- checker: DockerChecker
- rule_source: Dockerfile reference；文档说明 Dockerfile 的作用，但并不要求所有项目必须提供 Dockerfile，是否强制需要人工确认
- risk_level: medium
- detection_target: 项目根目录或常见部署目录是否存在 `Dockerfile`
- why_dangerous: 如果项目目标是容器化发布但没有 Dockerfile，发布流水线无法构建镜像
- evidence: `Dockerfile` 路径；如果不存在，记录查找过的位置
- recommendation: 如果项目需要容器化发布，请提供 Dockerfile；如果不需要，应在报告中说明发布方式
- implementation_difficulty: easy
- phase: phase-1

- rule_id: RG-DOCKER-002
- rule_name: 检查 Dockerfile 是否包含 FROM 指令
- checker: DockerChecker
- rule_source: Dockerfile reference - FROM
- risk_level: high
- detection_target: Dockerfile 中是否存在 `FROM`
- why_dangerous: 缺少基础镜像指令时，Dockerfile 无法形成明确构建基础
- evidence: `FROM` 指令所在行；或未发现 `FROM`
- recommendation: 为 Dockerfile 添加明确基础镜像，例如 Python 官方镜像
- implementation_difficulty: easy
- phase: phase-1

- rule_id: RG-DOCKER-003
- rule_name: 检查 FROM 是否出现在合理位置
- checker: DockerChecker
- rule_source: Dockerfile reference - FROM；需要考虑 parser directive、注释和允许出现在 FROM 前的 ARG
- risk_level: high
- detection_target: Dockerfile 第一条主要构建指令是否为 `FROM`，或仅有允许的前置 `ARG`
- why_dangerous: `FROM` 位置异常可能导致 Dockerfile 不符合预期构建语义
- evidence: 第一条非注释指令、`FROM` 行号、前置指令列表
- recommendation: 将 `FROM` 放在 Dockerfile 的基础阶段开头；如需前置 `ARG`，保持清晰和最小化
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-DOCKER-004
- rule_name: 检查 Dockerfile 是否包含 WORKDIR
- checker: DockerChecker
- rule_source: Dockerfile reference - WORKDIR；文档定义该指令，但没有强制所有 Dockerfile 必须使用，作为 ReleaseGuard 规则需要人工确认
- risk_level: low
- detection_target: Dockerfile 中是否存在 `WORKDIR`
- why_dangerous: 缺少工作目录会让后续 `COPY`、`RUN`、`CMD` 的相对路径行为不够清晰
- evidence: `WORKDIR` 指令行；或未发现 `WORKDIR`
- recommendation: 在 Dockerfile 中设置明确工作目录，例如 `/app`
- implementation_difficulty: easy
- phase: phase-1

- rule_id: RG-DOCKER-005
- rule_name: 检查 Dockerfile 是否包含 COPY 或 ADD
- checker: DockerChecker
- rule_source: Dockerfile reference - COPY / ADD；是否必须使用取决于镜像构建方式，需要人工确认
- risk_level: medium
- detection_target: Dockerfile 中是否存在 `COPY` 或 `ADD`
- why_dangerous: 对大多数应用镜像来说，如果没有复制项目文件或依赖文件，镜像可能无法包含应用代码
- evidence: `COPY` 或 `ADD` 指令行；或未发现相关指令
- recommendation: 使用 `COPY` 明确复制依赖文件和应用代码；避免不必要的 `ADD`
- implementation_difficulty: easy
- phase: phase-1

- rule_id: RG-DOCKER-006
- rule_name: 检查 Dockerfile 是否包含依赖安装步骤
- checker: DockerChecker
- rule_source: Dockerfile reference - RUN；具体依赖安装命令属于 Python 项目发布约定，需要人工确认
- risk_level: medium
- detection_target: Dockerfile 中是否存在 `RUN` 指令，且包含 `pip install`、`python -m pip install` 或类似依赖安装命令
- why_dangerous: 如果镜像没有安装依赖，容器启动后可能因缺少包而失败
- evidence: 依赖安装相关 `RUN` 指令；或未发现依赖安装步骤
- recommendation: 在 Dockerfile 中使用可复现的依赖安装命令，并优先使用 `python -m pip install`
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-DOCKER-007
- rule_name: 检查 Dockerfile 是否包含 CMD 或 ENTRYPOINT
- checker: DockerChecker
- rule_source: Dockerfile reference - CMD / ENTRYPOINT；文档定义指令，是否强制作为发布规则需要人工确认
- risk_level: high
- detection_target: Dockerfile 中是否存在 `CMD` 或 `ENTRYPOINT`
- why_dangerous: 没有默认启动命令的镜像可能无法被发布平台直接运行
- evidence: `CMD` 或 `ENTRYPOINT` 指令行；或未发现启动指令
- recommendation: 为镜像提供明确默认启动命令，例如启动 FastAPI 或 Flask 服务
- implementation_difficulty: easy
- phase: phase-1

- rule_id: RG-DOCKER-008
- rule_name: 检查 Dockerfile 指令是否使用统一大写风格
- checker: DockerStyleChecker
- rule_source: Dockerfile reference
- risk_level: low
- detection_target: Dockerfile 指令是否统一使用大写，例如 `FROM`、`WORKDIR`、`COPY`、`RUN`
- why_dangerous: 指令大小写不统一通常不会导致构建失败，但会降低 Dockerfile 可读性和团队协作质量
- evidence: 非大写指令的行号和原始文本
- recommendation: 统一使用大写 Dockerfile 指令
- implementation_difficulty: easy
- phase: phase-1

## 暂不实现的内容

- 多阶段构建质量检查。
- 镜像安全扫描。
- root 用户、权限、镜像体积优化等深度审计。
- Docker Compose 和 Kubernetes 配置检查。

## 我还需要人工确认的问题

- 第一阶段是否把缺少 Dockerfile 视为 failed，还是只在项目声明容器化发布时 failed。
- Dockerfile 是否需要支持非根目录位置，例如 `docker/Dockerfile`。
- 是否要求 Python 项目统一使用 `python -m pip install`。
