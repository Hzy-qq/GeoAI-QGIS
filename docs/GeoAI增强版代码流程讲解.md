# GeoAI 增强版代码流程讲解

这份文档只解释当前保留的增强版项目：**LangGraph + Chroma + Embedding + QGIS + LLM**。

## 1. 一句话说明项目

GeoAI 是一个 GIS Agent 原型：用户输入自然语言空间分析任务后，系统先用 Chroma 检索相关知识，再让 LLM 生成 QGIS 工作流 JSON，经过 schema 校验后调用 QGIS Processing 执行，最后读取真实 GeoPackage 结果，并让 LLM 生成中文回答。

完整链路是：

```text
用户问题
  -> Chroma 检索知识库
  -> LLM planner 生成 workflow JSON
  -> workflow_schema 校验
  -> executor 调用 QGIS
  -> result_summarizer 读取 GeoPackage 统计结果
  -> LLM summarizer 生成最终中文回答
  -> 保存 trace
```

## 2. 入口脚本

### `scripts/build_chroma_store.py`

作用：构建 Chroma 向量库。

核心流程：

```python
documents = load_knowledge_documents()
result = build_chroma_store(documents, reset=True)
```

它会读取：

```text
knowledge/qgis_tools.md
knowledge/task_guides.md
knowledge/workflow_examples.jsonl
evals/eval_cases.json
```

然后用 `sentence-transformers` 模型生成 embedding，存入：

```text
outputs/chroma/
```

### `scripts/retrieve_chroma_knowledge.py`

作用：单独测试 Chroma 检索效果。

示例：

```powershell
python scripts/retrieve_chroma_knowledge.py "计算点要素周围1公里范围内的道路长度"
```

它会调用：

```python
retrieve_chroma_context(query, top_k=4)
```

返回和问题最相关的工具说明、任务指南、workflow 示例或 eval case。

### `scripts/evaluate_chroma_rag_planner.py`

作用：测试 “Chroma 检索 + LLM planner” 是否能生成正确 workflow。

它不会执行 QGIS，只检查 planner 输出是否正确，包括：

```text
supported 是否正确
distance_meters 是否正确
workflow 工具链是否正确
schema 是否合法
```

如果所有 case 通过，会输出：

```text
Chroma RAG planner eval: 7/7 passed
```

### `scripts/run_langgraph_chroma_task.py`

作用：完整增强版 Agent 入口。

示例：

```powershell
python scripts/run_langgraph_chroma_task.py "统计 places 周边 700 米范围内道路的长度"
```

它会调用：

```python
trace = run_langgraph_agent(user_query, top_k=4)
```

最终输出：

```text
LangGraph planner generated workflow: road_length_statistics
Retriever: chroma
Workflow success: True
Step 1 buffer success: True
Step 2 clip success: True
Step 3 sum_line_lengths success: True
最终回答：...
```

并保存 trace：

```text
outputs/langgraph_chroma_agent_trace.json
```

## 3. LangGraph 编排层

核心文件：

```text
geoai_agent/langgraph_agent.py
```

这个文件使用官方 LangGraph 的 `StateGraph`。

### 3.1 状态对象

```python
class LangGraphAgentState(TypedDict, total=False):
    user_query: str
    top_k: int
    retrieved_context: str
    retrieved_docs: list[dict[str, Any]]
    plan: dict[str, Any] | None
    workflow: dict[str, Any] | None
    validation_error: str | None
    execution_trace: dict[str, Any] | None
    summary: dict[str, Any] | None
    success: bool
```

它表示 Agent 运行过程中的共享状态。每个节点读取 state 的一部分，再返回新的字段。

### 3.2 节点顺序

```text
retrieve -> plan -> validate -> execute -> summarize
```

对应代码：

```python
graph.add_node("retrieve", retrieve_node)
graph.add_node("plan", planner_node)
graph.add_node("validate", validator_node)
graph.add_node("execute", executor_node)
graph.add_node("summarize", summarizer_node)
```

连接关系：

```python
graph.add_edge("retrieve", "plan")
graph.add_edge("plan", "validate")
graph.add_edge("validate", "execute")
graph.add_edge("execute", "summarize")
graph.add_edge("summarize", END)
```

### 3.3 每个节点做什么

| 节点 | 函数 | 作用 |
| --- | --- | --- |
| retrieve | `retrieve_node()` | 从 Chroma 检索相关知识 |
| plan | `planner_node()` | 调用 LLM 生成 workflow JSON |
| validate | `validator_node()` | 校验 workflow 是否合法 |
| execute | `executor_node()` | 调用 QGIS 执行 workflow |
| summarize | `summarizer_node()` | 读取结果并让 LLM 生成中文回答 |

## 4. Chroma 与 Embedding 检索层

核心文件：

```text
geoai_agent/chroma_store.py
```

### 4.1 主要职责

这个文件负责：

```text
配置 HuggingFace 缓存
加载 sentence-transformers 模型
创建 Chroma PersistentClient
构建 Chroma collection
写入知识文档
查询 top_k 相关文档
对道路长度任务做领域重排
```

### 4.2 embedding 模型

默认模型：

```text
BAAI/bge-small-zh-v1.5
```

可以通过 `.env` 修改：

```text
EMBEDDING_MODEL=你的模型名或本地路径
```

### 4.3 为什么要 `SentenceTransformerEmbeddingFunction`

Chroma 需要一个 embedding function，把文本转成向量。项目中封装了：

```python
class SentenceTransformerEmbeddingFunction:
```

它提供 Chroma 新版需要的接口：

```python
name()
__call__()
embed_query()
embed_documents()
```

其中：

```text
embed_documents() 用于构建向量库时处理知识文档
embed_query() 用于检索时处理用户问题
```

### 4.4 检索返回什么

`retrieve_chroma_context()` 返回两个值：

```python
return context_text, results
```

其中：

```text
context_text：拼接后的文本，会传给 LLM planner
results：结构化检索结果，会写入 trace
```

`results` 中每条包含：

```text
id
score
similarity
distance
text
metadata
retriever
```

### 4.5 领域重排

Chroma 先根据 embedding 相似度返回结果。项目又通过 `chroma_domain_boost()` 做了一层道路长度任务的领域重排。

例如用户问道路长度时，系统会提高这些文档的分数：

```text
workflow_examples.jsonl 中的道路长度成功案例
task_guides.md 中的 road length guide
qgis_tools.md 中的 sum_line_lengths 工具说明
相关 eval case
```

这样 planner 更容易看到正确流程：

```text
buffer -> clip -> sum_line_lengths
```

## 5. 知识库加载层

核心文件：

```text
geoai_agent/knowledge_loader.py
```

它把原始知识资料转成统一格式：

```python
{
    "id": "...",
    "text": "...",
    "metadata": {...}
}
```

主要函数：

| 函数 | 作用 |
| --- | --- |
| `chunk_markdown()` | 按 Markdown 标题切分 `.md` 文档 |
| `load_workflow_examples()` | 读取历史成功 workflow 示例 |
| `load_eval_cases()` | 读取 eval cases |
| `load_knowledge_documents()` | 汇总所有知识文档 |

## 6. Planner 层

核心文件：

```text
geoai_agent/llm_planner.py
geoai_agent/chroma_rag_planner.py
```

### 6.1 `llm_planner.py`

它负责让 LLM 生成结构化 workflow JSON。

核心函数：

```python
plan_workflow_with_llm(user_query, extra_context=context)
```

其中 `extra_context` 就是 Chroma 检索出来的知识。

Planner 的 system prompt 会告诉模型：

```text
只能生成 JSON
不能执行代码
不能发明工具
当前支持道路长度统计任务
工具链必须是 buffer -> clip -> sum_line_lengths
公里要转换成米
没写距离时默认 1000 米
```

### 6.2 `chroma_rag_planner.py`

这个文件把 Chroma 检索和 LLM planner 串起来：

```python
context, retrieved = retrieve_chroma_context(user_query, top_k=top_k)
plan = plan_workflow_with_llm(user_query, extra_context=context)
```

它主要用于 eval 脚本测试 planner。

## 7. Schema 校验层

核心文件：

```text
geoai_agent/workflow_schema.py
```

它负责检查 LLM 输出是否安全、合法、可执行。

主要校验：

```text
supported 是否为 bool
distance_meters 是否为正整数
workflow 是否存在
steps 是否为列表
tool 是否在 tool_registry 中
必填参数是否齐全
是否包含未知参数
```

这一步很重要，因为 LLM 可能会生成格式错误或发明工具。只有通过 schema 校验的 workflow 才会进入 QGIS 执行阶段。

## 8. 工具注册层

核心文件：

```text
geoai_agent/tool_registry.py
```

它定义当前 Agent 能调用哪些工具。

当前工具链：

| 工具名 | QGIS 算法 | 作用 |
| --- | --- | --- |
| `buffer` | `native:buffer` | 对 places 生成缓冲区 |
| `clip` | `native:clip` | 裁剪 roads |
| `sum_line_lengths` | `native:sumlinelengths` | 统计缓冲区内道路长度 |

LLM 只能使用这里注册过的工具。

## 9. QGIS 执行层

核心文件：

```text
geoai_agent/executor.py
geoai_agent/qgis_runner.py
```

### 9.1 `executor.py`

它按 workflow 顺序执行每一步：

```python
for index, task in enumerate(workflow["steps"], start=1):
    result = execute_task(task)
```

它不理解自然语言，只执行已经校验过的结构化任务。

### 9.2 `qgis_runner.py`

它真正调用 QGIS 命令：

```python
qgis_process run native:buffer --
qgis_process run native:clip --
qgis_process run native:sumlinelengths --
```

QGIS 命令路径来自 `.env`：

```text
QGIS_PROCESS_CMD=F:\QGIS\bin\qgis_process-qgis-ltr.bat
```

## 10. 结果总结层

核心文件：

```text
geoai_agent/result_summarizer.py
```

它不是让 LLM 直接计算道路长度，而是：

```text
读取 QGIS 输出 GeoPackage
用 GeoPandas 统计 road_length 和 road_count
把真实统计结果 JSON 交给 LLM
LLM 生成中文回答
```

这能避免模型幻觉。

如果 LLM 总结失败，系统会退回模板回答：

```text
answer_source = deterministic_fallback
```

如果 LLM 总结成功：

```text
answer_source = llm
```

## 11. LLM 客户端

核心文件：

```text
geoai_agent/llm_client.py
```

它负责：

```text
读取 .env
获取 LLM_API_KEY
获取 LLM_MODEL
调用 OpenAI-compatible chat/completions 接口
解析 JSON 或文本结果
```

两个关键函数：

| 函数 | 用途 |
| --- | --- |
| `create_json_response()` | planner 阶段，要求模型输出 JSON |
| `create_text_response()` | summarizer 阶段，要求模型输出自然语言 |

## 12. Trace 文件

完整 Agent 运行后会保存：

```text
outputs/langgraph_chroma_agent_trace.json
```

里面包含：

```text
user_query
retrieved_docs
plan
validation_error
workflow
execution_trace
summary
success
```

这个 trace 可以回答面试官常问的问题：

```text
模型参考了哪些知识？
模型生成了什么 workflow？
schema 校验是否通过？
QGIS 每一步是否成功？
最终统计值来自哪里？
最终回答是 LLM 生成还是模板兜底？
```

## 13. 面试讲法

可以这样概括：

> 这个项目使用 LangGraph 构建 GeoAI Agent 工作流，将流程拆分为检索、规划、校验、执行和总结五个节点。RAG 部分使用 Chroma 和 sentence-transformers 构建知识库向量检索，把 QGIS 工具说明、任务指南、历史 workflow 示例和 eval case 注入 planner 上下文。LLM 只负责生成结构化 workflow JSON 和最终中文表达，workflow 会经过本地 schema 校验后才交给 QGIS 执行，真实空间统计由 QGIS 和 GeoPandas 完成，从而降低 LLM 幻觉风险。

再补一句工程亮点：

> 项目把 LLM planner、schema validator、QGIS executor、result summarizer 解耦，既能通过 eval 测试 planner 质量，也能通过 trace 追踪每次 Agent 运行过程，方便调试和扩展新的 GIS 任务类型。
