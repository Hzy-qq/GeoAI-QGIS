# 动态数据任务 {#task-guides-dynamic-data}

对于“统计南京市所有大学周边500米道路长度”，Planner 必须提取 region_name=南京市、
poi_type=university、distance_meters=500，并声明 administrative_boundary、
university_pois、road_network 三类数据需求。程序根据数据目录选择 Nominatim 与 Overpass，
LLM 不得提供 URL。下载后先校验数据，再将大学点自动转换到本地 UTM 坐标系。

所有大学缓冲区必须先 dissolve，再裁剪和统计道路，避免重叠区域重复计算。道路必须与缓冲区
使用相同投影坐标系。道路下载按高校点所在经纬网格分块，避免一次请求整座城市路网；下载结果
按 OSM way ID 去重后再精确裁剪。OSM 要素数量不等同于官方高校数量，最终回答必须说明数据口径。

道路口径包括 motorway、trunk、primary、secondary、tertiary、unclassified、residential、
living_street 及连接线，不包括 footway、path、steps、construction 和 service。

# 原生 Tool Calling {#task-guides-tool-calling}

DeepSeek 原生 Tool Calling 用于调用 submit_gis_plan。模型提交的 arguments 首先解析为 JSON，
然后由 workflow_schema 进行工具白名单、参数、路径、顺序和业务规则二次校验。模型只提出调用，
Executor 才能执行。若原生调用失败，可配置降级为 JSON Planner，但 trace 必须记录 planner_mode。

# 失败恢复与执行预算 {#task-guides-reliability}

网络超时、429 和 5xx 属于 transient error，最多重试一次；参数或 Schema 错误属于
plan_recoverable，可反馈 Planner 后重规划；未知工具、越界路径和无 CRS 数据属于 permanent error。
每个任务限制规划次数、工具调用次数、QGIS 超时、网络响应体大小、要素数量和总截止时间。

# 面积和高校计数 {#task-guides-other-tasks}

行政区面积任务动态下载边界，自动选择 UTM 后计算平方公里。高校计数任务动态下载边界与
OSM amenity=university/college 点要素，并在边界内计数。两类任务都必须经过 Evaluator。
