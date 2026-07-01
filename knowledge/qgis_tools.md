# 数据获取工具 {#qgis-tools-data-acquisition}

download_region_boundary 只从 Nominatim 下载行政区 Polygon/MultiPolygon。
download_osm_pois 只从 Overpass 下载 university/college 点要素。
download_osm_roads 只从 Overpass 下载 highway 线要素。所有端点来自 dataset_catalog 白名单，
并受超时、重试、最大响应体、查询面积和最大要素数量限制。

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
