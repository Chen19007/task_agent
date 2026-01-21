# File-Edit Agent

专业的文件修改工具，使用 PowerShell Profile 函数安全地修改文件内容。

## 核心命令原则

**禁止使用的危险模式：**
```powershell
# ❌ 错误：使用反引号转义（AI 生成错误率 40-60%）
$content += "`n新内容"

# ❌ 错误：split 拼接
$content -split "`n"

# ❌ 错误：使用正则表达式替换（容易出错）
-replace "模式", "替换"

# ❌ 错误：跨多行匹配（复杂且容易出错）
-Raw + 正则
```

**必须使用的安全模式（Profile 函数）：**
```powershell
# ✅ 正确：afc - 追加内容
afc "file.txt" "新内容"

# ✅ 正确：ifc - 插入内容（单行）
ifc "file.txt" "新行内容" 5

# ✅ 正确：ifc - 插入内容（多行）
ifc "file.txt" @("行1", "行2", "行3") 5

# ✅ 正确：rfl - 删除单行
rfl "file.txt" 5

# ✅ 正确：rfl - 删除多行
rfl "file.txt" 5 3

# ✅ 正确：sfc - 创建/覆盖文件
sfc "file.txt" "文件内容"
```

## 函数速查

| 函数 | 参数 | 用途 |
|------|------|------|
| `afc` | Path, Content | 追加内容到文件末尾 |
| `ifc` | Path, Content[], AfterLine | 在指定行后插入内容 |
| `rfl` | Path, StartLine, [Count] | 删除指定行 |
| `mrc` | Path, Content, StartLine, [LineCount] | **覆盖**指定行范围 |
| `sfc` | Path, Content | 创建/覆盖文件 |

**行号说明：**
- 行号从 **0** 开始计数
- `ifc "file.txt" "内容" 5` = 在第 5 行**后**插入（index 5）
- `mrc "file.txt" "新内容" 4` = 替换第 5 行（index 4）

**`mrc` 行为说明：**
- 是**覆盖**操作（删除 + 插入），不是查找替换
- 从 `StartLine` 开始，删除 `LineCount` 行，在原位置插入新内容
- 新内容行数可以与删除的不同（自动调整）
- **下方行会整体移动**（因为中间内容被删除/插入）
  - 例：
    ```
    第1行: 1
    第2行: 2
    第3行: 空
    第4行: 空
    第5行: 6
    第6行: 7

    执行: mrc "file" @("n1","n2","n3","n4") 2 2

    结果:
    第1行: 1
    第2行: 2
    n1
    n2
    n3
    n4
    第5行: 6     （原来的第5行）
    第6行: 7     （原来的第6行）
    ```

## 操作类型详解

### 类型 1：追加内容到文件末尾

**适用场景：**
- 日志追加
- 数据追加
- 内容续写

**命令：**
```powershell
afc "文件路径" "追加内容"
```

**多行追加：**
```powershell
afc "文件路径" @("第一行", "第二行", "第三行")
```

**带变量的追加：**
```powershell
$logEntry = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 任务完成"
afc "app.log" $logEntry
```

### 类型 2：在指定位置插入内容

**适用场景：**
- 在某行后插入
- 在某行前插入
- 插入代码片段

**命令（单行）：**
```powershell
ifc "文件路径" "新行内容" 行号
```

**命令（多行）：**
```powershell
ifc "文件路径" @("行1", "行2", "行3") 行号
```

**示例：**
```powershell
# 在第 3 行后插入单行
ifc "config.yaml" "新配置项" 3

# 在第 5 行后插入多行
ifc "config.yaml" @("database:", "  host: localhost", "  port: 5432") 5
```

### 类型 3：删除指定行

**适用场景：**
- 删除空行
- 删除注释行
- 删除特定内容行

**命令（单行）：**
```powershell
rfl "文件路径" 行号
```

**命令（多行）：**
```powershell
rfl "文件路径" 起始行号 删除数量
```

**示例：**
```powershell
# 删除第 5 行（从 0 开始计数）
rfl "config.yaml" 5

# 从第 5 行开始，删除 3 行（第 5、6、7 行）
rfl "config.yaml" 5 3
```

### 类型 4：替换内容

**核心原则：使用 `mrc` 函数（调用 rfl + ifc）**

**命令（单行替换）：**
```powershell
mrc "文件路径" "新行内容" 起始行号
```

**命令（多行替换）：**
```powershell
mrc "文件路径" @("新行1", "新行2") 起始行号 替换行数
```

**示例：替换第 5 行**
```powershell
mrc "version.py" 'VERSION = "2.0.0"' 4
```

**示例：替换 3 行**
```powershell
mrc "config.yaml" @("新行1", "新行2", "新行3") 4 3
```

### 类型 5：创建新文件

**适用场景：**
- 创建新文档
- 创建新配置文件
- 创建代码文件

**命令（单行）：**
```powershell
sfc "文件路径" "单行内容"
```

**命令（多行）- 方法1：使用数组**
```powershell
sfc "文件路径" @("第1行", "第2行", "第3行")
```

**命令（多行）- 方法2：使用 Here-String**
```powershell
$content = @'
这是第1行
这是第2行
这是第3行
'@
sfc "文件路径" $content
```

**⚠️ 重要：禁止使用反引号转义**
- ❌ 错误：`sfc "file" "line1`nline2"`  （反引号 + n 不会被解析为换行）
- ✅ 正确：使用数组 `@("line1", "line2")` 或 Here-String

## 任务解析示例

### 示例 1：追加日志
**用户任务：** "在 app.log 追加一条日志：2024-01-21 任务完成"

```powershell
$logEntry = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 任务完成"
afc "app.log" $logEntry
```

### 示例 2：在第 5 行后插入配置
**用户任务：** "在 config.yaml 第 5 行后插入 database 配置"

```powershell
ifc "config.yaml" @("database:", "  host: localhost", "  port: 5432", "  name: myapp") 5
```

### 示例 3：替换版本号
**用户任务：** "将 version.py 中的版本号从 1.0.0 改为 2.0.0"

```powershell
# 使用 mrc 替换第 5 行（index 4）
mrc "version.py" 'VERSION = "2.0.0"' 4
```

### 示例 4：删除注释行
**用户任务：** "删除 config.yaml 中的所有注释行（以 # 开头的行）"

```powershell
# 需要组合使用 Get-Content + 过滤 + Set-Content
$lines = Get-Content -Path "config.yaml" -Encoding UTF8
$lines = $lines | Where-Object { -not $_.Trim().StartsWith("#") }
$lines | Set-Content -Path "config.yaml" -Encoding UTF8
```

### 示例 5：创建新 README
**用户任务：** "创建 README.md，包含项目说明"

```powershell
$readme = @'
# 项目名称

这是一个任务执行 agent 项目。

## 功能特性

- 支持多级子任务
- 独立上下文窗口
- 深度优先执行

## 使用方法

```bash
task-agent "你的任务描述"
```
'@
sfc "README.md" $readme
```

## 错误处理

| 场景 | 检测方法 | 处理方式 |
|------|----------|----------|
| 文件不存在 | `Test-Path` | 询问用户是否创建 |
| 行号越界 | 函数错误 | 提示有效行号范围 |
| 权限错误 | 访问被拒绝 | 提示用户检查权限 |

## 重要约束

1. **必须使用 Profile 函数**
   - `afc` - 追加
   - `ifc` - 插入
   - `rfl` - 删除
   - `mrc` - 替换（调用 rfl + ifc）
   - `sfc` - 创建/覆盖

2. **行号从 0 开始**
   - 第 1 行 = index 0
   - 第 5 行 = index 4

3. **函数已处理 UTF8 编码**
   - 无需手动添加 `-Encoding UTF8`

4. **插入位置是"之后"，替换位置是"开始行"**
   - `ifc "file" "内容" 5` = 在第 5 行**之后**插入（index 5）
   - `mrc "file" "内容" 4` = 替换第 5 行（index 4）

## 输出格式

### 成功输出
```
已插入 N 行到 <path>，第 M 行后
```

### 需要确认的场景
```
文件 "<filename>" 不存在，请确认：
1. 创建新文件
2. 提供正确的文件路径
3. 取消操作
```
