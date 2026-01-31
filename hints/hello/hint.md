本 hint 提供一个用于验证的 PowerShell 函数。

可用函数：
- Get-HelloUser：返回 "hello <Windows用户名>"
- Print-Context：返回环境变量上下文对象（AgentStartDir/AgentProjectDir/HintModuleDir）

调用示例：
<ps_call> Get-HelloUser </ps_call>
<ps_call> Print-Context </ps_call>
