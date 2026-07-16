# 数据获取工具 {#qgis-tools-data-acquisition}

download_region_boundary、download_osm_pois、download_osm_roads 和 download_osm_water
共用“持久化缓存 → 南京标准化离线包 → 江苏 OSM PBF → 受控网络回退”的数据层。南京离线包
包含行政区、11 类 POI、主要道路和水系；PBF 提供江苏范围的本地兜底。网络端点只能来自
dataset_catalog 白名单，并受分块缓存、超时、有限重试、指数退避、端点熔断、最大响应体、
查询面积和最大要素数量限制。

POI 网络回退按地理分块并保存成功分块，达到完整度阈值时可以带 partial 标记继续，否则明确
失败。道路仅保留 motorway、trunk、primary、secondary 及其连接道路。所有本地快照结果都要
保留 snapshot_modified_at，并在最终回答中说明可复现但不是实时官方数据。

# CRS 工具 {#qgis-tools-crs}

auto_reproject_layer 根据数据中心点经纬度自动选择 UTM 分区。南京约位于东经118度，使用
EPSG:32650。reproject_to_match 将道路转换到缓冲区的 CRS。米制 buffer 和道路长度计算禁止
直接使用 EPSG:4326。

# 空间分析工具 {#qgis-tools-spatial-analysis}

native:buffer 创建缓冲区，针对多个高校必须 DISSOLVE=true。native:clip 将道路裁剪到合并缓冲区。
native:sumlinelengths 产生 road_length 和 road_count 字段。Evaluator 会检查结果文件可读、非空、
字段存在且数值非负。

# 数据质量工具 {#qgis-tools-validation}

validate_dataset 检查文件存在、要素数、CRS、几何类型、空几何和无效几何。下载数据不能绕过该步骤。
# Conversation and adjacency tools

`load_neighbor_boundaries` copies the allowlisted bundled boundary fixture into the current
task workspace. `select_feature_by_attribute` selects the target region and
`find_adjacent_polygons` computes topology-based neighbors. All paths remain task-scoped
`workspace://` paths.
