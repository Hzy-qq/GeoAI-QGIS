# 动态数据任务 {#task-guides-dynamic-data}

对于“统计南京市所有大学周边500米道路长度”，Planner 必须提取 region_name=南京市、
poi_type=university、distance_meters=500，并声明 administrative_boundary、
university_pois、road_network 三类数据需求。程序先读取持久化缓存和南京标准化离线包，
离线包未覆盖时读取江苏 OSM PBF；只有本地数据无法覆盖时，才使用 Nominatim、OSM 官方
Shortbread 矢量瓦片或 Overpass。LLM 不得提供 URL。获取后先校验数据，再将大学点自动
转换到本地 UTM 坐标系。

所有大学缓冲区必须先 dissolve，再裁剪和统计道路，避免重叠区域重复计算。道路必须与缓冲区
使用相同投影坐标系。本地 PBF/离线包结果必须记录 snapshot_modified_at；矢量瓦片按分析区
相交范围下载并裁剪瓦片缓冲重叠；显式 Overpass 模式按
高校点所在经纬网格分块，失败批次会四分重试。OSM 要素数量不等同于官方高校数量，最终回答
必须说明数据口径；矢量瓦片经过制图泛化，道路长度属于工程近似值。

道路口径仅包括 motorway、trunk、primary、secondary 及连接线，不统计 tertiary、
unclassified、residential、living_street、footway、path、steps 和 service。

# 原生 Tool Calling {#task-guides-tool-calling}

DeepSeek 原生 Tool Calling 用于调用 submit_gis_plan。模型提交的 arguments 首先解析为 JSON，
然后由 workflow_schema 进行工具白名单、参数、路径、顺序和业务规则二次校验。模型只提出调用，
Executor 才能执行。若原生调用失败，可配置降级为 JSON Planner，但 trace 必须记录 planner_mode。

# 失败恢复与执行预算 {#task-guides-reliability}

网络超时、429 和 5xx 属于 transient error，采用有限重试、指数退避和端点熔断；POI 分块
成功结果会持久化，重试时只补失败分块。参数或 Schema 错误属于
plan_recoverable，可反馈 Planner 后重规划；未知工具、越界路径和无 CRS 数据属于 permanent error。
每个任务限制规划次数、工具调用次数、QGIS 超时、网络响应体大小、要素数量和总截止时间。

# 面积和高校计数 {#task-guides-other-tasks}

行政区面积任务从缓存/离线包/本地 PBF/受控网络级联获取边界，自动选择 UTM 后计算平方公里。
高校计数任务以相同级联获取 OSM amenity=university/college 要素并在边界内计数。两类任务
都必须经过 Evaluator，回答必须披露本地快照或网络数据口径。
# Adjacent regions

For `adjacent_regions`, load the bundled neighbor-boundary fixture, validate polygon geometry,
select the named target region, then calculate touching polygons. Never infer adjacency from
the language model. Report the fixture version and its non-official data limitation.

# 通用 POI 与密度分析 {#task-guides-multifunction}

通用 POI 支持高校、学校、医院、诊所、药店、地铁站、公园、公安机构、消防站、超市和
充电站。可对这些设施执行边界内计数、投影后的缓冲服务区、规则网格点密度，以及到最近
主要道路的直线距离分析。服务区必须 dissolve 并裁剪到行政区边界；密度与距离必须先转换到
合适的投影坐标系。服务区不是路网等时圈，最近道路距离也不是实际行驶距离。

道路密度以规则网格中裁剪后的主要道路长度除以网格有效面积，输出 km/km²。道路口径仅含
OSM motorway、trunk、primary、secondary 及其连接道路，不统计支路和生活道路。全域道路
任务优先使用允许的数据目录与缓存，禁止由 LLM 自行构造外部 URL。

# 高级多条件选址 {#task-guides-advanced-site-selection}

高级选址先生成规则网格，再使用主干路、地铁站、高校、水域和行政区边界进行硬约束与排序。
水域相交或未达到最小避让距离的网格直接淘汰；超过主干路和地铁阈值的网格也直接淘汰。
其余网格按道路、轨道交通、高校和边界内部安全距离加权评分。结果只用于初筛，不能替代
用地性质、环境评价、地质灾害、权属和现场调查。

# 多轮调整 {#task-guides-conversation-followup}

会话记忆分别保存 current_region、previous_task_type、previous_poi_type 和
previous_distance_meters。用户说“把范围改为2公里”时，Resolver 继承上一轮区域、任务与
设施类型，只覆盖距离；用户说“换成公园”时只覆盖设施类型。每轮仍需重新经过 Schema、
工具执行和 Evaluator，不能直接复用上一轮数值回答。

# 最终版空间公平性分析 {#task-guides-final-spatial}

服务盲区分析先将行政区和设施点转换到同一米制投影坐标系，对设施做 dissolve 缓冲，
再用行政区几何减去覆盖几何，输出 uncovered_sq_km、covered_sq_km 和
coverage_rate_pct。它是欧氏距离覆盖，不是基于真实道路时间的等时圈。

多级服务圈固定比较 500、1000 和 2000 米累计覆盖范围，同时计算每次扩大半径带来的
marginal_gain_sq_km。设施最近邻分析为每个 POI 找到另一个最近 POI，输出最小、平均、
中位数和最大直线距离，可用于识别设施过度集中或稀疏，但不能解释为路网出行距离。

三类工作流都必须先校验数据、自动投影、使用任务工作区路径，并在回答中披露 OSM 数据口径。
