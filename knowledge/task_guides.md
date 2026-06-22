# GeoAI Task Guides

## Road length statistics around places

Supported user intents:
- 统计 places 周边 500 米范围内的道路长度
- 计算 places 附近 1.5km 内道路总长度
- 帮我统计附近路网总里程
- 计算缓冲区内 roads 的 length

Default distance:
- If the user asks for nearby or surrounding road length but does not specify a distance, use 1000 meters.

Distance conversion:
- 1 km = 1000 meters
- 1.5 km = 1500 meters
- 2 km = 2000 meters

Required workflow:
1. `buffer`
2. `clip`
3. `sum_line_lengths`

For distance N, use output paths:
- `outputs/places_buffer_Nm.gpkg`
- `outputs/roads_clip_Nm.gpkg`
- `outputs/buffer_with_road_length_Nm.gpkg`

For `sum_line_lengths`, use:
- `LEN_FIELD`: `road_length`
- `COUNT_FIELD`: `road_count`

## Unsupported tasks

The current prototype does not support:
- building count
- school count
- pure buffer-only requests
- POI statistics
- raster analysis

For unsupported tasks, return:
- `supported`: false
- `distance_meters`: 0
- `workflow`: `{"workflow": "unsupported", "steps": []}`
- `reason`: short Chinese explanation

