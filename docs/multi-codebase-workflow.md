# 多代码库 + 文档混合场景：Graphify 最佳实践

> 场景：你不仅有 md 笔记/书籍，还有 Linux 内核、lwIP、RT-Thread 等大型代码库。
> 问题：放在一起太大（耗时、耗 token）；分开又怕跨库关系丢失。怎么办？

---

## 核心矛盾

| 方案 | 优势 | 问题 |
|------|------|------|
| 全部放一个图 | 概念自动关联，跨库发现 | 文件数爆炸（Linux kernel 6 万+文件），LLM token 极其昂贵 |
| 完全分离 | 各自独立，成本可控 | 概念之间零连接，跨库关系丢失 |

**推荐方案：分层图 + 桥接层**——兼顾成本与连接。

---

## 第一层：每个代码库独立建图（纯 AST，零 LLM 成本）

Graphify 对纯代码库使用 tree-sitter AST 提取，**完全免费**（不调用 LLM）。`skill.md` 明确指出：

> Fast path: If detection found zero docs, papers, and images (code-only corpus), skip Part B entirely and go straight to Part C. AST handles code — there is nothing for semantic subagents to do.

分别对每个代码库运行：

```bash
/graphify linux-kernel/     # → linux-kernel/graphify-out/
/graphify lwip/             # → lwip/graphify-out/
/graphify rt-thread/        # → rt-thread/graphify-out/
```

每个库各自生成 `graph.json`、`GRAPH_REPORT.md`、`graph.html`。

### 用 `.graphifyignore` 控制 Linux kernel 的范围

Linux kernel 太大时，在根目录放一个 `.graphifyignore` 过滤无关子系统：

```gitignore
# .graphifyignore — 放在 linux-kernel/ 根目录
drivers/gpu/
drivers/media/
sound/
Documentation/translations/
arch/arm64/       # 排除不需要的架构，只保留你关心的
arch/mips/
```

Graphify 的 `detect.py` 实现了完整的 `.graphifyignore` 机制，支持 gitignore 风格的 glob 模式，并且会向上遍历父目录查找规则文件。

---

## 第二层：文档/书籍单独建图（需要 LLM，但量可控）

```
0-raw/                 → /graphify 0-raw/            → 0-raw/graphify-out/
  books/
    understanding-linux-kernel/
    lwip-protocol-stack-design/
    rt-thread-manual/
  notes/
    my-notes.md
```

LLM 成本只花在文档语义提取上。文档数量远少于代码文件（几十到几百个 md），完全可控。

**关键：** 同目录下的文件会被分到同一个 chunk，LLM 在同一 chunk 内就能发现跨书的 `semantically_similar_to` 关系。

---

## 第三层：桥接图（小成本，大价值）

创建一个手动桥接文档 `bridge.md`，放在 `0-raw/` 中一起处理：

```markdown
# bridge.md — 跨库概念映射

## 调度器
- Linux: `kernel/sched/core.c` → `schedule()`, `__schedule()`, CFS 公平调度
- RT-Thread: `src/scheduler.c` → `rt_schedule()`, 优先级抢占式调度
- 概念：两者都实现任务调度，但 Linux 用 CFS，RT-Thread 用纯优先级抢占

## TCP/IP 协议栈
- Linux: `net/ipv4/tcp.c` → 完整 TCP 实现
- lwIP: `src/core/tcp.c` → 精简 TCP 实现，面向嵌入式
- 概念：lwIP 是 Linux TCP 栈的轻量替代，API 不同但核心状态机相同

## 内存管理
- Linux: `mm/slab.c`, `mm/slub.c` → slab allocator
- RT-Thread: `src/mempool.c`, `src/memheap.c` → 简化版内存池
- lwIP: `src/core/memp.c` → 固定大小内存池
```

Graphify 处理此文件时，LLM 会自动生成 `conceptually_related_to` 和 `semantically_similar_to` 边，将跨库概念连接起来。

---

## 查询时用多 MCP Server 聚合

Graphify 的 MCP server 每次加载一个 `graph.json`。配置多个实例即可同时查询所有图：

```json
{
  "mcpServers": {
    "graphify-linux": {
      "command": "python3",
      "args": ["-m", "graphify.serve", "/path/to/linux-kernel/graphify-out/graph.json"]
    },
    "graphify-lwip": {
      "command": "python3",
      "args": ["-m", "graphify.serve", "/path/to/lwip/graphify-out/graph.json"]
    },
    "graphify-rtthread": {
      "command": "python3",
      "args": ["-m", "graphify.serve", "/path/to/rt-thread/graphify-out/graph.json"]
    },
    "graphify-docs": {
      "command": "python3",
      "args": ["-m", "graphify.serve", "/path/to/0-raw/graphify-out/graph.json"]
    }
  }
}
```

Claude/Agent 可以同时查询所有四个图。文档图中的桥接概念告诉 Agent "schedule 在 Linux 里对应什么、在 RT-Thread 里对应什么"，然后去对应的代码图中深入查询。

---

## 成本分析

| 部分 | 预估文件数 | LLM tokens | 方式 |
|------|-----------|------------|------|
| Linux kernel（过滤后） | ~5,000–15,000 | **0** | 纯 AST |
| lwIP | ~200 | **0** | 纯 AST |
| RT-Thread | ~500 | **0** | 纯 AST |
| 文档/书籍 | ~50–200 | **中等** | 语义提取 |
| 桥接文档 | 1–5 | **极少** | 语义提取 |

### 节省手段

- **增量更新 `--update`**：只重新提取变化的文件，缓存命中的跳过。
- **提取缓存**：已处理文件的结果缓存在 `graphify-out/cache/` 中，下次运行自动命中，不消耗 LLM。
- **代码变更零 LLM**：纯代码文件变更时自动检测为 `code_only`，跳过语义提取。

---

## 关于"双链太多"的担心

实际上不会太多。Graphify 内置三层去重：

1. **同文件内**（AST 层）：每个提取器跟踪 `seen_ids` 集合，同一文件中相同实体只出现一次。
2. **跨文件**（build 层）：NetworkX `add_node()` 幂等——同 ID 的节点只保留最后一次的属性。
3. **语义合并**（merge 层）：缓存与新结果合并时按 `node["id"]` 去重。

边的数量由实际的 call/import/reference 关系决定，不会产生笛卡尔积。Linux kernel 的 30,000 个函数之间只有**实际存在**的调用关系才会产生边。

---

## 操作步骤总结

1. **每个代码库**：添加 `.graphifyignore` 过滤无关子系统 → `/graphify <path>` → 零 LLM 成本
2. **文档目录**：集中放在 `0-raw/` → `/graphify 0-raw/` → LLM 成本可控
3. **桥接文档**：手动写 `bridge.md` 映射跨库概念（10–20 分钟），放入 `0-raw/` 一起处理
4. **配置多 MCP server**：查询时自动聚合全部图
5. **增量更新**：代码变了用 `--update`（零 LLM），新文档用 `--update`（仅处理增量）

---

## 目录结构示例

```
workspace/
├── linux-kernel/
│   ├── .graphifyignore          # 过滤不需要的子系统
│   ├── kernel/
│   ├── net/
│   ├── mm/
│   └── graphify-out/            # 独立的图
│       ├── graph.json
│       ├── graph.html
│       └── GRAPH_REPORT.md
├── lwip/
│   └── graphify-out/
├── rt-thread/
│   └── graphify-out/
└── 0-raw/
    ├── bridge.md                # 跨库概念桥接
    ├── books/
    │   ├── understanding-linux-kernel/
    │   ├── lwip-protocol-stack-design/
    │   └── rt-thread-manual/
    ├── notes/
    └── graphify-out/            # 文档+桥接的图
        ├── graph.json
        ├── graph.html
        └── GRAPH_REPORT.md
```

这个结构让你：
- 代码图免费维护，随时 `--update`
- 文档图按需更新，成本集中可控
- 桥接层手动维护，一次编写长期受益
- 多图并行查询，跨库关系不丢失
