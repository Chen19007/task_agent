# Web-Search Flow

联网搜索和信息查询工具，通过 MCP Proxy 调用 Google 搜索和网页抓取服务。

## 工具使用

### Google 搜索

使用 `<bash_call>` 调用 `web-search` 进行搜索：

```bash
web-search google_search "搜索关键词" --num 10 --gl us --hl en
```

**参数说明**：
- `google_search`: 工具名称
- `"搜索关键词"`: 要搜索的内容（用双引号包裹）
- `--num 10`: 返回结果数量（默认10，最大10）
- `--gl us`: 国家/区域代码（us=美国, cn=中国）
- `--hl en`: 语言代码（en=英文, zh=中文）

**完整示例**：
```bash
web-search google_search "Python 教程" --num 5 --gl cn --hl zh
```

### 网页抓取

抓取网页内容进行分析：

```bash
web-search scrape <url> [--includeMarkdown]
```

**示例**：
```bash
web-search scrape "https://example.com" --includeMarkdown
```

## 工作流程

### Phase 1: 理解用户查询

分析用户的搜索需求：
- 确定搜索关键词
- 选择合适的搜索参数（语言、区域）
- 如果需要更多信息，先向用户询问

### Phase 2: 执行搜索

使用 `<bash_call>` 调用搜索工具：

**典型搜索场景**：
```bash
web-search google_search "Claude Code 官方文档" --num 10 --gl us --hl zh
```

### Phase 3: 解析结果

**输出格式**（JSON）：
```json
{
  "success": true,
  "tool": "google_search",
  "searchParameters": {...},
  "answerBox": {
    "title": "直接答案",
    "snippet": "答案摘要",
    "link": "链接"
  },
  "organic": [
    {
      "title": "结果标题",
      "link": "https://...",
      "snippet": "摘要内容",
      "position": 1
    }
  ],
  "relatedSearches": [{"query": "相关搜索词"}],
  "credits": 1
}
```

### Phase 4: 格式化输出

将搜索结果整理成易读的格式：

```
## 搜索结果

### 直接答案
[如果有 AnswerBox，显示直接答案]

### 相关结果 (N)
| # | 标题 | 链接 | 摘要 |
|---|------|------|------|
| 1 | 结果1 | URL | 摘要... |
| 2 | 结果2 | URL | 摘要... |

### 相关搜索
- 相关词1
- 相关词2
```

### Phase 5: 抓取详情（可选）

如果需要深入了解某个结果，使用 `<bash_call>` 抓取网页：

```bash
web-search scrape "https://目标链接" --includeMarkdown
```

## 任务完成返回的内容

```
<return>
# 搜索完成

## 查询: "xxx"

### 直接答案
[答案内容]

### 找到 N 条相关结果
| # | 标题 | 链接 |
|---|------|------|
| 1 | xxx | https://... |

### 相关搜索词
- xxx
- xxx

## 抓取的网页内容摘要
[如果抓取了网页]
</return>
```

## 重要约束

1. **搜索关键词优化**：
   - 使用具体、明确的关键词
   - 必要时添加修饰词（如"官方文档"、"最新版本"）
   - 中文查询使用 `--gl cn --hl zh`

2. **结果验证**：
   - 优先使用官方或权威来源
   - 检查链接是否有效
   - 注意信息的时效性

3. **避免过度抓取**：
   - 只抓取与任务相关的网页
   - 抓取前确认 URL 有效
   - 注意版权和网站使用条款

4. **错误处理**：
   - 搜索失败时尝试调整关键词
   - 网页无法访问时跳过该链接
   - 向用户报告错误并建议替代方案

## 使用场景

| 场景 | 示例 |
|------|------|
| 技术文档查询 | "Django 官方文档 REST framework" |
| 最新资讯 | "Claude Code 2025 新功能" |
| 问题解决 | "Python asyncio TimeoutError 处理" |
| 资源收集 | "免费 Python 教程推荐" |
| 深入研究 | 抓取官方文档页面分析详细用法 |

## 错误处理

| 错误类型 | 处理方式 |
|----------|----------|
| 无搜索结果 | 尝试不同的关键词或更通用的搜索 |
| 网页无法访问 | 跳过该链接，使用其他来源 |
| 参数错误 | 检查参数格式，使用 `--help` 查看帮助 |
