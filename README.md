# GeoAI

`GeoAI` is a LangGraph + Chroma based GeoAgent prototype for GIS spatial analysis.

It converts a natural-language GIS request into a structured QGIS workflow,
validates the workflow, executes QGIS Processing tools, extracts real spatial
statistics, and asks the LLM to generate a Chinese final answer.

## Workflow

```text
User query
  -> Chroma RAG retrieval from knowledge base
  -> LLM planner
  -> workflow JSON
  -> schema validation
  -> QGIS Processing execution
  -> GeoPackage output
  -> statistics extraction
  -> LLM result summarizer
  -> trace JSON
```

## Structure

```text
GeoAI/
  data/
    processed/
      places.gpkg
      roads.gpkg
  evals/
    eval_cases.json
  knowledge/
    qgis_tools.md
    task_guides.md
    workflow_examples.jsonl
  outputs/
    chroma/
    hf_cache/
    langgraph_chroma_agent_trace.json
  scripts/
    check_llm_config.py
    build_chroma_store.py
    retrieve_chroma_knowledge.py
    evaluate_chroma_rag_planner.py
    run_langgraph_chroma_task.py
  geoai_agent/
    chroma_store.py
    chroma_rag_planner.py
    langgraph_agent.py
    llm_client.py
    llm_planner.py
    workflow_schema.py
    tool_registry.py
    executor.py
    qgis_runner.py
    result_summarizer.py
```

## Setup

Install dependencies:

```powershell
pip install -r requirements-advanced.txt
```

Create `.env` from `.env.example`, then set your LLM API key and QGIS command.

Check LLM configuration:

```powershell
python scripts/check_llm_config.py
```

Build the Chroma vector store:

```powershell
python scripts/build_chroma_store.py
```

Test retrieval:

```powershell
python scripts/retrieve_chroma_knowledge.py "计算点要素周围1公里范围内的道路长度"
```

Evaluate the Chroma RAG planner:

```powershell
python scripts/evaluate_chroma_rag_planner.py
```

Run the full LangGraph + Chroma Agent:

```powershell
python scripts/run_langgraph_chroma_task.py "统计 places 周边 700 米范围内道路的长度"
```

## Interview Talking Point

This project uses LangGraph to organize an Agent workflow with five nodes:
retrieval, planning, validation, execution, and summarization. Chroma stores
embedding vectors for QGIS tool docs, task guides, workflow examples, and eval
cases. The LLM generates structured workflow JSON, the schema validator checks
tool and parameter legality, QGIS performs the actual spatial analysis, and the
LLM only verbalizes trusted statistics produced by QGIS/GeoPandas.
