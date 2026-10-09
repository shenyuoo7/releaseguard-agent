---
name: fix-deps
description: 依赖冲突排查与版本固定，确保项目发布环境的确定性与可复现构建
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
# 依赖修复与版本固定技能 (SOP)

请作为依赖管理专家，检查并解决当前项目的依赖关系与环境兼容性问题：

1. **依赖审查**: 检查 `requirements.txt`、`pyproject.toml` 等文件；
2. **冲突定位**: 查找未声明版本、版本范围冲突或传递依赖版本不兼容问题；
3. **安全更新**: 优先寻找向后兼容的最小升级范围，避免引入破坏性主版本更新；
4. **验证构建**: 验证依赖可以无冲突成功安装，并通过基础测试验证。

$ARGUMENTS
