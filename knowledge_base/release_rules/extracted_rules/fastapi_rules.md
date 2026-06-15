# FastAPI Rules

本文件汇总第一阶段 FastAPIDetector、FastAPITestChecker 和 FastAPIHealthChecker 可使用的规则。

## Rules

| rule_id | rule_name | checker | source | priority | phase |
|---|---|---|---|---|---|
| RG-FASTAPI-001 | 检查项目是否声明 FastAPI 依赖 | FastAPIDetector | FastAPI Testing | high | phase-1 |
| RG-FASTAPI-002 | 检查代码中是否存在 FastAPI app 实例 | FastAPIDetector | FastAPI Testing | high | phase-1 |
| RG-FASTAPI-003 | 检查是否使用 FastAPI TestClient | FastAPITestChecker | FastAPI Testing | medium | phase-1 |
| RG-FASTAPI-004 | 检查是否存在针对 FastAPI app 的测试文件 | FastAPITestChecker | FastAPI Testing | high | phase-1 |
| RG-FASTAPI-005 | 检查 FastAPI 测试是否能被 pytest 运行 | FastAPITestChecker | FastAPI Testing | high | phase-1 |
| RG-FASTAPI-006 | 检查 FastAPI 测试是否包含基础状态码断言 | FastAPITestChecker | FastAPI Testing | medium | phase-1 |
| RG-FASTAPI-007 | 检查是否存在健康检查接口 | FastAPIHealthChecker | 需要补充健康检查资料依据 | medium | phase-1 |

## 第一阶段落地方式

- 从依赖文件中识别 `fastapi`。
- 从源码文本中识别 `FastAPI()`。
- 从测试文件中识别 `TestClient`、`TestClient(app)` 和状态码断言。
- 对 `/health` 类接口先做路由字符串识别，但必须在报告中标记规则依据待补充。

## 需要人工确认

- 是否支持 FastAPI 工厂函数模式。
- 是否把 `/health` 缺失作为 failed，还是 warning。
- 是否要求 FastAPI 项目必须包含至少一个 TestClient 测试。
