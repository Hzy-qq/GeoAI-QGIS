# GeoAI-QGIS Final — 多轮空间分析 Agent

这是一个面向自然语言 GIS 分析的最终工程版：用户在浏览器中提出空间问题，FastAPI 将任务持久化到 MySQL，独立 Worker 通过 LangGraph 完成上下文解析、RAG 检索、计划校验、GIS 工具执行和结果总结，前端使用 SSE 展示节点级进度并在 Leaflet 中加载、下载结果图层。

## 最终能力

- 多轮会话：同一 `conversation_id` 下继承区域、POI 类型、距离和上一次结果，可继续说“改成 2 公里”“再计算其中的主要道路长度”。
- 语义 RAG：本地 BGE Embedding + Chroma，可选 BGE Reranker，为规划器检索工具说明和任务约束。
- 异步执行：FastAPI 只接收任务；Worker 使用 MySQL 任务表原子认领、执行和回写，避免长耗时 GIS 任务阻塞接口。
- Redis 辅助层：镜像 SSE 事件、维护 Worker 租约、缓存成功结果；Redis 不可用时自动退回 MySQL、JSONL 进度文件和本地心跳。
- 可视化交付：多轮聊天、任务时间线、地图图层、GeoPackage 下载和失败原因提示。
- 工程保护：Pydantic/工作流 Schema 双重校验、目录白名单、超时/重试/道路查询预算、幂等请求、过期队列清理和就绪检查。
- 稳定数据层：代码支持南京标准化离线包、可选江苏 OSM PBF 和带缓存/分块/熔断的网络源；运行数据不进入 Git，按需在本地准备。

## 支持的空间分析

1. 行政区面积与相邻行政区；
2. POI 数量、服务区、栅格密度；
3. 主要道路密度、POI 到道路最近距离、POI 周边主要道路长度；
4. 多条件与高级设施选址；
5. POI 最近邻/设施间距分析；
6. 服务覆盖盲区分析；
7. 500/1000/2000 米多环服务覆盖分析。

道路分析只获取 OSM `motorway/trunk/primary/secondary` 及其 link，排除住宅和支路。默认数据来自本地 OSM 快照，结果可复现但不是实时路网，也不是测绘成果。

## 数据获取与离线包

默认 `auto` 级联为：`outputs/data_cache` 命中 → 本地 `data/osm/nanjing` 标准化包 → 可选 `data/osm/jiangsu-latest.osm.pbf` → 受控网络回退。GitHub 仓库不包含运行数据；准备本地离线包后，南京常用任务无需访问 Nominatim、Overpass 或矢量瓦片。结果会写入数据来源与快照时间，并明确说明“非实时官方统计”。

数据准备命令见 [`data/osm/README.md`](data/osm/README.md)。下载或替换江苏 PBF 后可重建南京离线包：

```powershell
python scripts\build_osm_offline_pack.py
```

超出江苏快照范围时，网络层仍采用持久化整层/分块缓存、原子写入、端点熔断、指数退避、POI 分块续跑和有限完整度降级。具体数据口径见 [数据源说明](data/DATA_SOURCES.md)。

## 核心链路

```text
浏览器 -> POST /api/v1/tasks -> MySQL(PENDING)
                              -> Worker 原子认领
                              -> 会话 LangGraph
                                 -> 上下文解析/追问
                                 -> GIS LangGraph
                                    -> BGE/Chroma 检索
                                    -> LLM/规则规划
                                    -> Schema 校验
                                    -> QGIS/Python 工具
                                    -> 确定性质量检查
                                    -> LLM/模板总结
                              -> MySQL + Redis 缓存 + 结果图层
浏览器 <- SSE 进度 / JSON 结果 / GeoPackage 下载
```

MySQL 是任务、会话和结果的真实数据源；Redis 是可丢失、可重建的加速层，不承担最终持久化。

## 快速运行

```powershell
cd "F:\研一\codex\Agent项目\final\GeoAI-QGIS_Final"
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
docker-compose --env-file .env up -d mysql redis
python scripts\build_knowledge.py
python scripts\run_api.py
```

`.env` 至少要配置 DeepSeek API Key、QGIS `qgis_process` 路径、MySQL 密码及匹配的 `DATABASE_URL`。打开：

- 前端：`http://127.0.0.1:8000/`
- Swagger：`http://127.0.0.1:8000/docs`
- 就绪检查：`http://127.0.0.1:8000/health/ready`

`run_api.py` 默认同时启动一个常驻 Worker；生产部署可分别运行：

```powershell
python scripts\run_api.py --api-only
python scripts\run_worker.py
```

## 验证

```powershell
python scripts\evaluate.py
python scripts\evaluate.py --retrieval
python scripts\evaluate.py --check-runtime
```

- 本次离线回归：75 项测试通过。
- BGE 检索集：6 个案例，Recall@4 = 0.917，MRR = 1.000。
- 关闭业务缓存的离线数据层回放：南京边界 0.952 s、地铁站 0.089 s、主干道路 0.293 s、水系 0.206 s。
- 两条真实 LangGraph 端到端回放通过：南京地铁站密度（260 个站点要素、308 个网格）与高校周边 1 km 主干道路长度（923.84 km）；两者均通过 Evaluator，且未访问 Overpass。
- `--check-runtime` 会额外检查 MySQL、Redis、Chroma、QGIS 和 Worker，必须在服务已启动时执行。
- DeepSeek 与外部 OSM 网络刷新保持显式 opt-in，避免把供应商波动误判成代码回归；南京默认演示不以公网 OSM 为硬依赖。

完整架构、降级策略、案例和学习路线见 [最终工程说明](docs/FINAL_ARCHITECTURE_AND_GUIDE.md)，发布验证边界见 [验证报告](docs/FINAL_VALIDATION_REPORT.md)。

> 安全提示：不要提交 `.env`、模型缓存、数据库卷、任务输出以及 `docs` 下的个人简历/面试材料。
