本 hint 提供一个用于验证的 Bash 函数。

可用函数：
- get_hello_user：返回 "hello <Linux用户名>"
- print_context：返回环境变量上下文（AGENT_START_DIR/AGENT_PROJECT_DIR/HINT_MODULE_DIR）

调用示例：
<bash_call> get_hello_user </bash_call>
<bash_call> print_context </bash_call>
