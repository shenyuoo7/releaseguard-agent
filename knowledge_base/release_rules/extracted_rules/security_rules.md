# Security Baseline Rules

本文件汇总第一阶段 SecurityBaselineChecker 可使用的安全基线规则。注意：这些规则参考 OWASP ASVS，但当前还没有完成具体 ASVS 版本号和要求编号映射，报告中必须标记待确认来源。

## Rules

| rule_id | rule_name | checker | source | priority | phase |
|---|---|---|---|---|---|
| RG-SEC-001 | 检查是否疑似硬编码敏感信息 | SecurityBaselineChecker | OWASP ASVS，需要人工确认具体编号 | high | phase-1 |
| RG-SEC-002 | 检查 Flask 是否开启 debug=True | SecurityBaselineChecker | OWASP ASVS，需要补充 Flask 直接依据 | high | phase-1 |
| RG-SEC-003 | 检查是否存在过宽 CORS 配置 | SecurityBaselineChecker | OWASP ASVS，需要人工确认具体编号 | high | phase-1 |
| RG-SEC-004 | 检查危险命令执行用法 | SecurityBaselineChecker | OWASP ASVS，需要人工确认具体编号 | medium | phase-1 |
| RG-SEC-005 | 检查是否缺少生产安全配置说明 | SecurityBaselineChecker | OWASP ASVS，需要人工确认 | medium | phase-1 |
| RG-SEC-006 | 检查安全报告是否记录 ASVS 版本和要求编号 | SecurityReportChecker | OWASP ASVS | medium | phase-2 |

## 第一阶段落地方式

- 使用关键词和简单文本扫描识别疑似敏感信息。
- 对 evidence 做脱敏处理。
- 识别 Flask `debug=True`。
- 识别常见 CORS 通配符配置。
- 识别 `os.system` 和 `subprocess(..., shell=True)`。

## 需要人工确认

- 每条安全规则对应的 ASVS 版本和控制编号。
- 敏感信息扫描的误报处理策略。
- evidence 脱敏格式。
