# pytest Good Integration Practices

## URL

https://docs.pytest.org/en/stable/explanation/goodpractices.html

## 属于哪类

Python工程

## 资料主要内容

pytest 官方文档介绍了测试项目的组织方式、虚拟环境建议、测试发现规则、测试配置方式、`src` layout 项目的导入建议，以及通过 `python -m pytest` 运行测试的方式。pytest 默认会发现 `test_*.py` 或 `*_test.py` 文件，并收集以 `test` 开头的测试函数或方法。

## 对 ReleaseGuard Agent 的作用

它可以直接作为 TestChecker 的核心依据，帮助 ReleaseGuard Agent 判断一个 Python 项目是否具备基本测试结构、测试配置和可执行测试命令。

## 可抽取的检查规则

- rule_id: RG-TEST-001
- rule_name: 检查项目是否存在 tests 目录
- checker: TestChecker
- rule_source: pytest Good Integration Practices
- risk_level: medium
- detection_target: 项目根目录是否存在 `tests/`
- why_dangerous: 没有测试目录通常意味着项目缺少系统化测试入口，发布前质量无法被稳定验证
- evidence: `tests/` 是否存在；如果不存在，记录项目根目录文件列表
- recommendation: 创建 `tests/` 目录，并按单元测试、集成测试或端到端测试组织测试文件
- implementation_difficulty: easy
- phase: phase-1

- rule_id: RG-TEST-002
- rule_name: 检查是否存在 pytest 可发现的测试文件
- checker: TestChecker
- rule_source: pytest Good Integration Practices
- risk_level: high
- detection_target: 是否存在 `test_*.py` 或 `*_test.py`
- why_dangerous: 没有符合 pytest 发现规则的测试文件会导致测试命令无法收集有效测试
- evidence: 匹配到的测试文件列表；或未匹配到任何测试文件的说明
- recommendation: 按 pytest 默认规则命名测试文件，例如 `test_check_result.py`
- implementation_difficulty: easy
- phase: phase-1

- rule_id: RG-TEST-003
- rule_name: 检查是否存在 pytest 可收集的测试函数或方法
- checker: TestChecker
- rule_source: pytest Good Integration Practices
- risk_level: high
- detection_target: 测试文件中是否存在以 `test` 开头的函数或方法
- why_dangerous: 只有测试文件但没有可收集测试用例时，测试命令可能显示 collected 0 items，发布前验证失效
- evidence: 测试函数数量、测试文件路径；或 collected 0 的输出
- recommendation: 在测试文件中编写以 `test_` 开头的测试函数
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-TEST-004
- rule_name: 检查 pytest 是否能成功收集测试
- checker: TestChecker
- rule_source: pytest Good Integration Practices
- risk_level: high
- detection_target: `python -m pytest --collect-only` 是否能成功运行
- why_dangerous: 如果测试收集阶段失败，说明导入、配置或测试结构已经存在问题，发布前无法进入真正测试执行
- evidence: 命令退出码、收集到的测试数量、错误输出摘要
- recommendation: 修复导入路径、测试命名或 pytest 配置，确保测试能被收集
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-TEST-005
- rule_name: 检查 pytest 测试是否能运行通过
- checker: TestChecker
- rule_source: pytest Good Integration Practices
- risk_level: critical
- detection_target: `python -m pytest` 是否成功退出
- why_dangerous: 测试失败说明项目当前状态不满足发布前质量门槛
- evidence: 命令退出码、失败测试数量、失败摘要
- recommendation: 修复失败测试或明确跳过原因，重新运行测试直到通过
- implementation_difficulty: medium
- phase: phase-1

- rule_id: RG-TEST-006
- rule_name: 检查项目是否存在 pytest 配置
- checker: TestConfigChecker
- rule_source: pytest Good Integration Practices
- risk_level: medium
- detection_target: 是否存在 `pytest.ini`、`pytest.toml`、`pyproject.toml` 或其他 pytest 支持的配置入口
- why_dangerous: 缺少测试配置可能导致测试发现、导入路径和命令行为依赖本机环境，影响可复现性
- evidence: 命中的配置文件路径；如果不存在，记录未发现测试配置
- recommendation: 在项目根目录提供清晰的 pytest 配置，至少说明测试路径和导入策略
- implementation_difficulty: easy
- phase: phase-1

- rule_id: RG-TEST-007
- rule_name: 检查 src layout 项目是否有可复现的导入配置
- checker: TestConfigChecker
- rule_source: pytest Good Integration Practices；`pythonpath` 配置是 ReleaseGuard 当前落地方式之一，是否优先推荐 editable install 需要人工确认
- risk_level: medium
- detection_target: 如果项目使用 `src/` layout，检查 pytest 配置、可编辑安装说明或打包配置是否能让测试导入源码包
- why_dangerous: `src/` layout 没有导入配置时，测试可能在本地偶然通过，但在 CI 或新环境中失败
- evidence: `src/` 是否存在；pytest 配置中的 `pythonpath`；或项目安装说明
- recommendation: 配置 pytest 的导入路径，或提供可编辑安装流程，例如 `python -m pip install -e .`
- implementation_difficulty: medium
- phase: phase-1

## 暂不实现的内容

- pytest 插件生态检查。
- coverage 覆盖率阈值检查。
- flaky test 识别。
- 分层测试质量评估。

## 我还需要人工确认的问题

- 第一阶段是否要求所有 Python 项目必须有 `tests/`，还是允许小项目只给 warning。
- `src` layout 项目中，优先推荐 `pythonpath = src` 还是可编辑安装。
- 是否需要把 collected 0 items 视为 failed，还是 warning。
