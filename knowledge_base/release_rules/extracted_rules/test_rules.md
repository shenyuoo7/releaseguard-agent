# Test Rules

本文件汇总第一阶段 TestChecker 和 TestConfigChecker 可直接使用的规则。

## Rules

| rule_id | rule_name | checker | source | priority | phase |
|---|---|---|---|---|---|
| RG-TEST-001 | 检查项目是否存在 tests 目录 | TestChecker | pytest Good Integration Practices | high | phase-1 |
| RG-TEST-002 | 检查是否存在 pytest 可发现的测试文件 | TestChecker | pytest Good Integration Practices | high | phase-1 |
| RG-TEST-003 | 检查是否存在 pytest 可收集的测试函数或方法 | TestChecker | pytest Good Integration Practices | high | phase-1 |
| RG-TEST-004 | 检查 pytest 是否能成功收集测试 | TestChecker | pytest Good Integration Practices | high | phase-1 |
| RG-TEST-005 | 检查 pytest 测试是否能运行通过 | TestChecker | pytest Good Integration Practices | high | phase-1 |
| RG-TEST-006 | 检查项目是否存在 pytest 配置 | TestConfigChecker | pytest Good Integration Practices | medium | phase-1 |
| RG-TEST-007 | 检查 src layout 项目是否有可复现的导入配置 | TestConfigChecker | pytest Good Integration Practices | medium | phase-1 |

## 第一阶段落地方式

- 先做 `tests/` 目录存在性检查。
- 再做测试文件命名规则扫描。
- 再通过 `python -m pytest --collect-only` 获取可收集测试数量。
- 最后执行 `python -m pytest`，把退出码和失败摘要作为 evidence。

## 需要人工确认

- collected 0 items 应该是 failed 还是 warning。
- `src` layout 的导入规则优先采用 pytest `pythonpath`，还是要求项目支持 editable install。
