# Git-Commit Flow

自动化 Git 仓库初始化和提交流程，包含智能文件过滤、全面的 .gitignore 生成和隐私安全检查。

## 核心工作流

```
Phase 1: 仓库评估 (Steps 1-3)
    ↓
Phase 2: Git 初始化 (Steps 4-5)
    ↓
Phase 3: .gitignore 创建 (Steps 6-7)
    ↓
Phase 4: 文件评估 (Steps 8-10)
    ↓
Phase 4.5: 隐私安全检查 (Step 10.5)
    ↓
Phase 5: 暂存和提交 (Steps 11-12)
```

## 详细工作流程

### Phase 1: 仓库评估

**Step 1: 检测工作目录**
```bash
pwd
```

**Step 2: 检查 Git 状态**
```bash
git status
```
- 如果返回 "not a git repository" 错误，进入 Phase 2
- 如果已是仓库，跳过 Phase 2

**Step 3: 列出目录内容**
```bash
ls -la
```
了解项目结构

### Phase 2: Git 初始化

**Step 4: 初始化仓库（条件）**
如果 `.git/` 目录不存在：
```bash
git init
```

**Step 5: 验证初始化**
```bash
git status
```
应该显示 "On branch master", "No commits yet"

### Phase 3: .gitignore 创建

**Step 6: 检测项目类型**
分析当前目录中的文件扩展名：
- Python: `.py` 文件、`requirements.txt`、`pyproject.toml`、`setup.py`
- Node.js: `package.json`、`package-lock.json`、`.npmrc`
- Go: `go.mod`、`go.sum`、`main.go`
- Rust: `Cargo.toml`、`Cargo.lock`、`main.rs`
- Godot: `project.godot`、`*.gd` 文件
- Web: `index.html`、`*.css`、`*.js`

**Step 7: 生成全面的 .gitignore**

如果 `.gitignore` 不存在，创建以下内容：

```gitignore
# ========================================
# Language-Specific Patterns
# ========================================

# Python
__pycache__/
*.py[cod]
*$py.class
.venv/
venv/
env/
ENV/
dist/
build/
*.egg-info/
.eggs/
*.manifest
*.spec
htmlcov/
.tox/
.coverage
.coverage.*
.cache
.pytest_cache/

# Node.js / TypeScript
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
dist/
build/
*.tsbuildinfo
.env
.env.local
.env.development.local
.env.test.local
.env.production.local

# Go
*.exe
*.exe~
*.dll
*.so
*.dylib
*.test
*.out
go.work

# Rust
/target/

# Godot Engine
.godot/
.import/
*.import
*.tmp
*.tmp.*
export.cfg
export_presets.cfg
*.translation

# ========================================
# Build Outputs (Multi-Language)
# ========================================
*.exe
*.dll
*.so
*.dylib
*.a
*.lib
output/
out/
bin/
obj/

# ========================================
# IDE Configuration Files
# ========================================
.vscode/
*.code-workspace
.idea/
*.iml
*.iws
*.ipr
.vs/
*.suo
*.user
*.userosscache
*.sln.docstates
.cursor/
*.swp
*.swo
*~
*~
.#*
.#*

# ========================================
# System Files
# ========================================
.DS_Store
.DS_Store?
._*
.Spotlight-V100
.Trashes
Thumbs.db
ehthumbs.db
ehthumbs_vista.db
Desktop.ini
$RECYCLE.BIN/
*.stackdump
.directory

# ========================================
# Temporary and Cache Files
# ========================================
*.bak
*.backup
*.tmp
*.tmp.*
*.temp
*.cache
*.log
[Ss]aves/
nul

# ========================================
# Additional Development Tools
# ========================================
.ipynb_checkpoints/
docs/_build/
.pyre/
.mypy_cache/
.dmypy.json
dmypy.json

# ========================================
# Security: Never Commit Secrets
# ========================================
.env
.env.*
!.env.example
*.pem
*.key
*.cert
credentials.json
secrets.yaml
*.sqlite
*.sqlite3
db.sqlite
db.sqlite3-journal
```

**如果 .gitignore 已存在**，检查缺失的模式并追加缺失部分。

### Phase 4: 文件评估

**Step 8: 分类文件**
```bash
git status
```
获取未跟踪的文件列表

**Step 9: 应用过滤标准**

**包含在提交中（有用的文件）：**
- 源代码: `.py`, `.js`, `.ts`, `.tsx`, `.go`, `.rs`, `.gd`, `.cpp`, `.c`, `.h`, `.java`
- 配置: `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `requirements.txt`, `tsconfig.json`
- 文档: `README.md`, `docs/`, `CHANGELOG.md`, `LICENSE`
- 构建脚本: `Makefile`, `CMakeLists.txt`, `build.sh`, `setup.py`
- 项目元数据: `.gitignore`, `project.godot`, `.github/`
- 测试文件: `test_*.py`, `*_test.go`, `tests/`, `*.test.ts`
- 锁文件: `poetry.lock`, `package-lock.json`, `Cargo.lock`

**从提交中排除（临时/不需要的文件）：**
- 构建产物: `*.exe`, `*.dll`, `node_modules/`, `target/`, `dist/`, `__pycache__/`, `.venv/`
- IDE 配置: `.vscode/`, `.idea/`, `.vs/`, `.cursor/`
- 系统文件: `.DS_Store`, `Thumbs.db`, `Desktop.ini`
- 临时文件: `*.tmp`, `*.swp`, `*~`, `*.bak`, `*.log`
- 本地环境: `.env`, `.env.local`, `local_settings.py`, `*.sqlite`, `db.sqlite3`
- 生成文档: `docs/_build/`, `coverage/`, `htmlcov/`

**Step 10: 展示摘要**

显示两个表格：

```
### Files to Commit (N)
| File | Type | Reason |
|------|------|--------|
| package.json | Config | Project dependencies |
| src/index.ts | Source | Main entry point |
| ... | ... | ... |

### Files Excluded (N)
| File | Type | Reason |
|------|------|--------|
| node_modules/ | Build | Dependencies (installable) |
| dist/ | Build | Compiled output (regeneratable) |
| ... | ... | ... |
```

### Phase 4.5: 隐私安全检查

**Step 10.5: 扫描敏感信息**

在暂存文件之前，扫描可能被意外提交的敏感数据。

**检查模式：**

| 类别 | 模式 | 风险级别 |
|------|------|----------|
| API Keys | `api_key`, `apikey`, `API_KEY` | 高 |
| Tokens | `token`, `access_token`, `refresh_token`, `bearer_token` | 高 |
| Passwords | `password`, `passwd`, `PASSWORD`, `pwd` | 高 |
| Secrets | `secret`, `private_key`, `SECRET`, `SECRET_KEY` | 高 |
| Credentials | `credentials`, `auth_token`, `session_id` | 高 |
| Certificates | 文件名以 `.pem`, `.key`, `.cert`, `.crt` 结尾 | 高 |
| Environment | `.env`, `.env.local`, `.env.production` | 高 |
| Databases | `*.sqlite`, `*.sqlite3`, `db.sqlite` | 中 |

**排除检查（避免误报）：**
- 本地文件路径 (`C:`, `D:`, `E:`, `/home/`, `/d/`)
- 仓库用户名 (`git@github.com:...`)
- 示例代码注释

**扫描过程：**

对于"Files to Commit"列表中的每个文件：
1. 使用 `Select-String` 搜索敏感模式
2. 检查文件扩展名是否为高风险文件
3. 收集所有结果和行号
4. 按风险级别分类

**输出格式 - 无问题：**
```
[隐私安全检查] 通过

未在待提交文件中检测到敏感信息。
```

**输出格式 - 发现问题：**
```
## 隐私安全检查: 检测到潜在问题

以下文件可能包含敏感信息：

| File | Line(s) | Detected Pattern | Risk Level | Recommendation |
|------|---------|------------------|------------|----------------|
| config/config.yaml | 15 | `api_key: sk-...` | 高 | 移除或使用环境变量 |
| .env | All | Contains environment variables | 高 | 添加到 .gitignore |
| src/auth.py | 42 | `password = "..."` | 高 | 使用配置文件或密钥管理 |
| data/db.sqlite | - | SQLite database file | 中 | 排除本地数据库 |

**重要提示：** 这些文件可能暴露：
- 可能被滥用的 API 凭证
- 可能访问你数据的数据库凭证
- 可能提供未授权访问的认证令牌

**选项：**
1. 继续 - 我已验证这些文件可以安全提交
2. 中止 - 取消提交，先修复问题
```

**用户交互：**

如果检测到敏感模式，使用 `bash_call` 命令：
- 说明检测到的问题
- 提供修复建议（移动到 .env.example，使用占位符等）
- 询问用户是继续还是中止

### Phase 4.8: 变更预览（未暂存）
**Step 10.8: 查看未暂存文件列表（必做）**
```bash
git diff --name-only
```
如需确认细节，再查看：
```bash
git diff
```

**规则：先预览再暂存**
- 在确认“Files to Commit”列表一致之前，禁止执行 `git add`
- 如果出现临时文件/无关文件，先调整过滤与排除列表
- 仅当未暂存列表与预期一致，才进入 Step 11

### Phase 5: 暂存和提交

**Step 11: 暂存文件**
```bash
git add <file1> <file2> ...
```
仅暂存“Files to Commit”列表中的文件，禁止 `git add .` 或未筛选暂存

**Step 12: 创建提交**
```bash
git commit -m "commit message"
```

**提交消息格式：**

对于新仓库：
```
Initial commit: Add project structure and configuration

- Add source code and documentation
- Add build configuration and dependencies
- Add comprehensive .gitignore with language-specific patterns
```

对于现有仓库：
```
Add <feature-description>

- Summary of changes
- Additional context if needed
```

## 输出格式

### 成功输出

```
## Repository Setup Complete

Status: Successfully initialized and committed
Repository: <full-path>
Branch: master
Commits: <count>

### Files Committed (<count>)
<bullet list with reasons>

### Files Excluded (<count>)
<bullet list with reasons>
```

### 部分失败输出

```
## Repository Setup Partially Failed

Status: Local commit successful, some operations failed

Local Repository: <path>
Git initialized
.gitignore created
<N> files committed

### Files Committed
<same as success format>
```

## 重要约束

1. **只执行到 git commit** - 不要自动运行 `git push`
   - 用户必须手动执行 push 命令
   - 防止意外数据丢失
   - 允许用户先审查提交

2. **现有仓库处理** - 不要重新初始化
   - 检查 `.git/` 目录是否存在
   - 如果已是仓库，跳过 `git init`
   - 继续进行文件评估

3. **智能文件过滤** - 不要盲目添加所有文件
   - 评估每个文件的有用性
   - 应用排除标准
   - 向用户展示将要提交与排除的内容

4. **全面的 .gitignore** - 包含嵌入式文档
   - 解释每个模式排除的内容
   - 按语言/类别组织
   - 包含多种语言的常见模式

5. **隐私检查必需** - 必须在提交前扫描敏感数据
   - 检查 API 密钥、令牌、密码、机密
   - 扫描要提交的文件（不排除被排除的文件）
   - 报告发现结果、风险级别和建议
   - 如果发现敏感模式，要求用户确认继续或中止
   - 不要检查本地路径（避免误报）
   - 用户做出继续或中止的最终决定

## 错误处理

| 场景 | 检测 | 操作 |
|------|------|------|
| Git init 失败 | `.git/` 已存在 | 跳过初始化，继续 |
| 权限错误 | `git add` 失败并提示 permission denied | 识别问题文件，通知用户 |
| 发现敏感数据 | Select-String 检测到敏感模式 | 询问用户继续或中止提交 |

## 高级场景

**场景 1: 混合目录（有用 + 临时文件）**
- 示例: `src/main.py`（保留） vs `src/main.pyc`（排除）
- 操作: 应用文件级过滤，而非目录级

**场景 2: 未检测到明确的项目类型**
- 操作: 使用保守的默认 .gitignore（系统文件、编辑器）
- 通知用户: "已应用通用 .gitignore - 请为你的语言自定义"

**场景 3: 仓库已有提交**
- 操作: 跳过初始化，继续进行文件评估
- 检查: 使用 `git log --oneline -10` 验证现有历史

**场景 4: 检测到敏感数据**
- 操作: 在暂存前运行隐私检查
- 如果发现敏感模式:
  - 向用户显示文件和检测到的模式
  - 提供建议（使用环境变量，添加到 .gitignore 等）
  - 询问用户继续或中止
- 如果用户中止:
  - 提供修复指导（移动到 .env.example，使用占位符等）
  - 建议修复后重新运行 git-commit
- 如果用户继续:
  - 记录用户已确认风险
  - 继续暂存和提交

## 关键差异（与手动工作流相比）

| 方面 | 手动 | Git-Commit Agent |
|------|------|---------------------|
| .gitignore 创建 | 手动，经常不完整 | 自动生成并附带文档 |
| 文件选择 | 手动审查 | 带标准的智能过滤 |
| 隐私检查 | 很少做，容易忘记 | 提交前自动扫描 |
| 错误处理 | 手动故障排除 | 自动检测和指导 |
| 文档 | 单独研究 | 嵌入在 .gitignore 注释中 |
