---
name: test
description: 自动化测试分析与执行，智能区分代码 Bug 与测试编写缺陷
allowedTools:
  - read_file
  - write_file
  - edit_file
  - bash
  - glob
  - grep
mode: inline
context: full
---
# 自动化测试与排错技能 (SOP)

请作为专业测试专家，执行并分析项目的测试套件：

1. **测试运行**: 优先运行聚焦测试或全量测试；
2. **失败诊断**: 当测试失败时，仔细分析错误回溯 (Traceback) 和断言细节；
3. **精准归因**:
   - **Case A (代码 Bug)**: 生产代码未能满足正确的功能预期 -> 精准修复生产代码，保持测试不变；
   - **Case B (测试编写 Bug)**: 生产代码逻辑正确，而测试断言过时、测试环境准备不当或测试 Mock 错误 -> 修复测试用例。
4. **回归验证**: 修复后重新运行测试，确保 100% 绿灯通过且未破坏已有测试。

$ARGUMENTS
