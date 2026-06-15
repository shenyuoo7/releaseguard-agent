# FastAPI Testing

## URL

https://fastapi.tiangolo.com/tutorial/testing/#fastapi-app-file

## 属于哪类

FastAPI

## 资料主要内容

FastAPI 官方测试文档展示了如何在应用文件中创建 `app = FastAPI()`，如何使用 `from fastapi.testclient import TestClient` 构建测试客户端，如何编写以 `test_` 开头的测试函数，并用普通 `assert` 检查响应状态码和响应内容。

## 对 ReleaseGuard Agent 的作用

它可以作为 FastAPIChecker 和 FastAPI 测试检查的第一批依据，帮助识别 FastAPI 项目、FastAPI app 实例、TestClient 测试，以及基础接口响应断言。

## 可抽取的检查规则

- rule_id: RG-FASTAPI-001
- rule_name: 检查项目是否声明 FastAPI 依赖
- checker: FastAPIDetector
- rule_source: FastAPI Testing；文档使用 FastAPI，但“依赖声明检查”是 ReleaseGuard 的项目识别落地规则，需要人工确认
- risk_level: medium
- detection_target: `requirements.txt`、`pyproject.toml` 等依赖文件中是否包含 `fastapi`
- why_dangerous: 如果代码使用 FastAPI 但依赖文件没有声明，部署环境可能无法安装正确依赖
- evidence: 命中的依赖文件和依赖行；或代码中存在 FastAPI 使用但依赖文件未声明
- recommendation: 在依赖声明文件中加入 `fastapi`，并根据运行方式补充 `uvicorn` 等运行依赖
- implementation_difficulty: easy
- phase: phase-1

- rule_id: RG-FASTAPI-002
- rule_name: 检查代码中是否存在 FastAPI app 实例
- checker: FastAPIDetector
- rule_source: FastAPI Testing
- risk_level: high
- detection_target: Python 文件中是否存在 `FastAPI()` 调用和 app 变量线索
- why_dangerous: 没有可识别 app 实例时，ReleaseGuard 难以确认服务入口，也难以执行 API 健康检查或测试分析
- evidence: 命中的文件路径、变量名和 `FastAPI()` 调用位置
- recommendation: 明确创建 FastAPI app 实例，例如 `app = FastAPI()`
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-FASTAPI-003
- rule_name: 检查是否使用 FastAPI TestClient
- checker: FastAPITestChecker
- rule_source: FastAPI Testing
- risk_level: medium
- detection_target: 测试文件中是否存在 `from fastapi.testclient import TestClient`
- why_dangerous: 没有 TestClient 测试时，API 路由是否可调用、状态码是否正确很难在发布前被自动验证
- evidence: 命中的测试文件路径和 import 行
- recommendation: 为关键 API 添加基于 `TestClient` 的测试
- implementation_difficulty: easy
- phase: phase-1

- rule_id: RG-FASTAPI-004
- rule_name: 检查是否存在针对 FastAPI app 的测试文件
- checker: FastAPITestChecker
- rule_source: FastAPI Testing
- risk_level: high
- detection_target: 测试文件是否导入 FastAPI app，并创建 `TestClient(app)`
- why_dangerous: 只有通用测试而没有 API 测试时，Web 服务发布后可能出现路由不可用但测试仍通过的情况
- evidence: `TestClient(app)` 调用、app 导入路径、测试文件路径
- recommendation: 添加至少一个针对 FastAPI app 的请求测试
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-FASTAPI-005
- rule_name: 检查 FastAPI 测试是否能被 pytest 运行
- checker: FastAPITestChecker
- rule_source: FastAPI Testing
- risk_level: high
- detection_target: 包含 FastAPI TestClient 的测试是否能被 `python -m pytest` 收集并运行
- why_dangerous: FastAPI 测试无法运行会让 API 发布质量门槛失效
- evidence: pytest 输出、FastAPI 测试文件数量、失败摘要
- recommendation: 修复 app 导入路径、测试命名、依赖安装或应用初始化问题
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-FASTAPI-006
- rule_name: 检查 FastAPI 测试是否包含基础状态码断言
- checker: FastAPITestChecker
- rule_source: FastAPI Testing
- risk_level: medium
- detection_target: FastAPI 测试中是否存在类似 `assert response.status_code == 200` 的断言
- why_dangerous: 如果测试只发送请求但不检查状态码，接口失败可能无法被测试发现
- evidence: 命中的状态码断言行；或未发现状态码断言
- recommendation: 至少为关键路由断言 HTTP 状态码和关键响应字段
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-FASTAPI-007
- rule_name: 检查是否存在健康检查接口
- checker: FastAPIHealthChecker
- rule_source: FastAPI Testing 不强制要求 `/health`；该规则目前属于 ReleaseGuard 内部发布规则，需要人工确认和补充健康检查资料依据
- risk_level: medium
- detection_target: 路由中是否存在 `/health`、`/healthz`、`/ready` 或类似健康检查路径
- why_dangerous: 没有健康检查接口时，部署平台和发布流程难以判断服务是否已正确启动
- evidence: 命中的路由路径、文件路径和行号；或未发现健康检查路由
- recommendation: 添加轻量健康检查接口，并在发布检查中调用它
- implementation_difficulty: medium
- phase: phase-1

## 暂不实现的内容

- 深度路由覆盖率分析。
- OpenAPI schema 完整性检查。
- 依赖注入 override 的复杂测试检查。
- 异步数据库和外部服务 mock 质量检查。

## 我还需要人工确认的问题

- `/health` 是否作为第一阶段必备规则，还是只作为 warning。
- FastAPI 项目入口是否只识别 `app = FastAPI()`，还是同时识别工厂函数模式。
- 是否要求至少一个 `TestClient` 测试，还是只检测是否存在 API 测试线索。
