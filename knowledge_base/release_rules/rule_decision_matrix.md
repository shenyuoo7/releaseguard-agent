# Rule Decision Matrix

本文件把此前“需要人工确认”的点整理成 ReleaseGuard Agent 的专业默认裁决。

## Config / Dependency / Release

| concept | default_decision | support_level | blocking_policy | reason |
|---|---|---|---|---|
| `.env.example` | 作为 ReleaseGuard 第一阶段默认发布规则 | releaseguard-default | conditional | Twelve-Factor 支持配置从代码分离并存放在环境中；`.env.example` 是工程落地方式。若发现环境变量或配置变量但缺少示例文件，阻断；否则警告。 |
| 硬编码配置 | 第一阶段实现文本扫描 | source-backed | conditional | Twelve-Factor Config 直接支持配置不应写死在代码里。普通配置为 warning，高风险 secret-like 配置为 block。 |
| 依赖声明文件 | Python 项目必须有依赖声明入口 | source-backed | block | Twelve-Factor Dependencies 支持显式声明依赖。第一阶段接受 `requirements.txt` 或 `pyproject.toml`。 |
| 基础日志痕迹 | 第一阶段做 warning，不阻断 | releaseguard-default | warn | Twelve-Factor Logs 支持日志作为事件流，但“检查日志配置”是 ReleaseGuard 的工程落地。 |
| 启动命令说明 | Web 服务项目应有启动线索 | releaseguard-default | conditional | 服务型项目发布前必须知道如何启动。若 FastAPI / Flask 项目找不到入口或启动命令，阻断；generic Python 先警告。 |
| 端口绑定或服务入口 | Web 项目需要可识别入口 | source-backed | conditional | Twelve-Factor Port Binding 支持服务通过端口暴露。第一阶段先识别 app 实例、启动命令和端口线索。 |

## Test

| concept | default_decision | support_level | blocking_policy | reason |
|---|---|---|---|---|
| `tests/` 目录 | Python 项目发布前应有测试目录 | releaseguard-default | warn | pytest 支持多种布局，但 ReleaseGuard 第一阶段把 `tests/` 作为清晰测试入口。 |
| pytest 测试文件命名 | 必须能发现 `test_*.py` 或 `*_test.py` | source-backed | block | pytest 文档直接说明默认测试发现规则。 |
| pytest 测试函数命名 | 必须能收集到测试函数或方法 | source-backed | block | pytest 文档直接说明收集 `test` 前缀函数或方法。 |
| `python -m pytest --collect-only` | 应成功收集测试 | source-backed | block | 收集失败说明测试结构、导入或配置有问题。 |
| `python -m pytest` | 应运行通过 | source-backed | block | 发布前测试失败应阻断。 |
| pytest 配置文件 | 建议存在 | releaseguard-default | warn | pytest 支持配置；ReleaseGuard 用它保证测试可复现。 |
| `src` layout 导入配置 | 使用 `src/` 时必须有可复现导入策略 | source-backed | conditional | pytest 文档说明可以用 `PYTHONPATH` 或 `pythonpath` 配置。若测试导入失败，阻断；否则警告。 |

## FastAPI

| concept | default_decision | support_level | blocking_policy | reason |
|---|---|---|---|---|
| FastAPI 依赖声明 | 使用 FastAPI 时必须声明依赖 | releaseguard-default | block | FastAPI 文档展示使用方式；依赖声明要求来自 ReleaseGuard 发布可复现策略。 |
| `FastAPI()` app 实例 | 第一阶段优先识别显式 app 实例 | source-backed | block | FastAPI Testing 文档展示 `app = FastAPI()`。没有 app 入口时，后续 API 检查无法执行。 |
| `TestClient` | 建议 FastAPI 项目有 TestClient 测试 | source-backed | warn | FastAPI 官方文档说明可以直接用 pytest 和 TestClient 测试应用。 |
| `TestClient(app)` | API 测试应绑定 app | source-backed | warn | 文档展示把 FastAPI app 传给 TestClient。 |
| 状态码断言 | API 测试应至少断言状态码 | source-backed | warn | FastAPI Testing 示例使用 `assert response.status_code == 200`。 |
| `/health` 接口 | 作为 ReleaseGuard 默认发布策略 | releaseguard-default | warn | 不是 FastAPI Testing 强制要求，但对发布平台和健康检查有工程价值；后续补健康检查资料后再升级。 |

## Docker

| concept | default_decision | support_level | blocking_policy | reason |
|---|---|---|---|---|
| Dockerfile 是否必须存在 | 仅在项目声明容器化发布时强制 | releaseguard-default | conditional | Dockerfile 文档说明其作用，但不要求所有项目必须容器化。 |
| `FROM` | Dockerfile 存在时必须有，并且位置合理 | source-backed | block | Docker 文档说明有效 Dockerfile 必须以 `FROM` 开始，允许前置 parser directive、注释或全局 `ARG`。 |
| `WORKDIR` | 建议存在 | releaseguard-default | warn | Docker 文档定义 `WORKDIR`，ReleaseGuard 把它作为可读性和路径稳定性的默认策略。 |
| `COPY` 或 `ADD` | 应存在复制应用内容或依赖文件的指令 | releaseguard-default | warn | 对应用镜像通常需要复制代码或依赖文件，但存在特殊镜像构建方式。 |
| 依赖安装 `RUN` | Python 应用镜像应有依赖安装步骤 | releaseguard-default | warn | 对 Python 应用发布有工程价值，但具体命令不由 Dockerfile reference 强制。 |
| `CMD` 或 `ENTRYPOINT` | 服务镜像应提供默认启动命令 | releaseguard-default | conditional | 对可直接运行的发布镜像应阻断；对基础镜像或构建镜像可警告。 |
| 指令大写 | 作为风格建议 | source-backed | info | Docker 文档说明指令不区分大小写，但约定使用大写。 |

## Security

| concept | default_decision | support_level | blocking_policy | reason |
|---|---|---|---|---|
| 硬编码 secret | 第一阶段必须检查 | needs-source-mapping | block | ASVS 提供 Web 安全控制测试基础；具体 secret 规则编号后续映射。发现真实 secret-like 值时阻断，evidence 必须脱敏。 |
| Flask `debug=True` | 生产发布前必须关闭 | needs-source-mapping | block | 这是成熟 Flask/生产安全实践；需要补充 Flask 官方直接依据。 |
| 宽松 CORS | 生产配置中禁止通配符 | needs-source-mapping | conditional | 有安全风险，但需补充具体 CORS / ASVS 映射。生产配置阻断，开发样例警告。 |
| `os.system` / `subprocess(shell=True)` | 第一阶段检查危险调用 | source-backed | conditional | ASVS 页面给出了 OS command injection 编号示例 `v5.0.0-1.2.5`。若发现外部输入进入命令执行，阻断；否则警告。 |
| 生产安全配置说明 | 建议存在 | releaseguard-default | warn | 对发布移交有工程价值，但不是 ASVS 直接文件要求。 |
| ASVS 版本和编号记录 | 安全类规则后续必须补齐 | source-backed | info | OWASP ASVS 说明要求引用格式应包含版本和要求编号。 |

## 默认执行顺序

第一阶段实现优先级：

1. Dependency / config / test 规则。
2. FastAPI 项目识别和 FastAPI 测试线索。
3. Dockerfile 基础结构。
4. SecurityBaseline 的低复杂度文本扫描。
5. 健康检查和启动命令增强。

## 老板视角总结

这些默认裁决的作用是让 ReleaseGuard Agent 不停在“每条规则都等确认”的状态。我们先用专业默认值推进工程实现，同时保留透明边界：

- 能引用官方资料的，明确引用。
- 属于 ReleaseGuard 产品策略的，明确标记。
- 还没找到精确编号的，先实现为基线，不做合规夸大。
