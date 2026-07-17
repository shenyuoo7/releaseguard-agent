# ReleaseGuard 极简使用

日常使用只需要双击：

`ReleaseGuard.bat`

## 第一次使用

确认项目根目录已有 `.venv`，然后双击启动。浏览器会自动打开
`http://127.0.0.1:8000/`。要使用真实 AI，点击“配置 AI”，选择 Provider，
填写 Base URL、Model 和 API Key，再点击“测试连接”。只有这个按钮会主动
发起真实模型请求。

## 检查项目

点击“选择项目文件夹”，选择要审查的项目。基础扫描不调用模型；AI 智能审查
必须先测试连接成功。点击“开始审查”后，页面会显示当前阶段并自动进入结果页。

## 启动网页

双击 `ReleaseGuard.bat` 就是启动网页，不需要打开 PowerShell 或 Swagger。
关闭 ReleaseGuard 服务窗口即可停止本地服务。

## 查看结果

风险总结、修复计划、确定性事实和规则证据都直接显示在结果页。需要归档时再点击
“下载完整 Markdown”或“下载 JSON”。本地副本保存在 `outputs/runs/`。

## 三个常见错误

1. **找不到 `.venv`**：运行 `ReleaseGuard.bat -Action RepairEnvironment`。
2. **AI 未配置或连接失败**：回到“配置 AI”，检查 Key、Base URL、Model 和网络。
3. **端口 8000 被占用**：关闭占用端口的程序；若已是 ReleaseGuard，会直接复用。

高级兼容菜单仍可通过 `ReleaseGuard.bat -Action Menu` 打开。
