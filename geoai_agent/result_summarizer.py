from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .dataset_catalog import POI_LABELS
from .llm_client import LLMClientError, create_text_response
from .task_workspace import TaskWorkspace


MAIN_ROAD_NOTICE = (
    "道路口径仅包括 OSM motorway、trunk、primary、secondary 及其连接道路，"
    "不含 tertiary、residential、unclassified、living_street 等支路和生活道路。"
)


def _first(gdf, field: str, default: str = "") -> str:
    if field not in gdf.columns:
        return default
    values = gdf[field].dropna().astype(str)
    return values.iloc[0] if not values.empty else default


def extract_workflow_statistics(
    plan: dict,
    workspace: TaskWorkspace,
) -> dict[str, Any] | None:
    import geopandas as gpd

    workflow = plan.get("workflow", {})
    steps = workflow.get("steps", [])
    if not steps:
        return None
    output = steps[-1].get("params", {}).get("OUTPUT")
    if not output:
        return None
    path = workspace.resolve(output)
    if not path.exists():
        return None
    gdf = gpd.read_file(path)
    name = workflow.get("workflow")
    common = {
        "result_file": str(path),
        "region_name": plan.get("region_name", ""),
        "data_requirements": plan.get("data_requirements", []),
        "data_notice": "OpenStreetMap is community-maintained data and may differ from official statistics.",
    }
    road_metadata: dict[str, Any] = {}
    poi_step = next(
        (step for step in steps if step.get("tool") == "download_osm_pois"),
        None,
    )
    if poi_step:
        poi_output = poi_step.get("params", {}).get("OUTPUT")
        poi_path = workspace.resolve(poi_output) if poi_output else None
        if poi_path and poi_path.exists():
            poi_layer = gpd.read_file(poi_path)
            poi_source = _first(poi_layer, "data_source", "osm_overpass")
            if poi_source == "osm_local_pbf_snapshot":
                snapshot = _first(poi_layer, "snapshot_modified_at", "未知日期")
                common["poi_data_notice"] = (
                    f"POI 数据来自项目内置 OSM 本地快照（快照时间 {snapshot}），"
                    "用于稳定复现实验，不代表实时官方统计。"
                )
            elif _first(poi_layer, "tile_download_status") == "partial":
                requested = _first(poi_layer, "tiles_requested", "unknown")
                failed = _first(poi_layer, "tiles_failed", "unknown")
                common["poi_data_notice"] = (
                    f"POI 数据采用分块获取：请求 {requested} 个分块，"
                    f"其中 {failed} 个获取失败；已使用达到完整度阈值的"
                    "缓存与实时分块继续计算，数量和密度应按部分覆盖解读。"
                )
    road_step = next(
        (
            step for step in steps
            if step.get("tool") in {"download_osm_roads", "download_osm_roads_in_area"}
        ),
        None,
    )
    if road_step:
        road_output = road_step.get("params", {}).get("OUTPUT")
        road_path = workspace.resolve(road_output) if road_output else None
        if road_path and road_path.exists():
            road_layer = gpd.read_file(road_path)
            road_source = _first(road_layer, "data_source", "osm_overpass")
            road_metadata["road_data_source"] = road_source
            if road_source == "osm_shortbread_vector_tiles":
                zoom = _first(road_layer, "tile_zoom", "11")
                road_metadata["road_data_notice"] = (
                    f"道路数据来自 OSM 官方 Shortbread z{zoom} 矢量瓦片；"
                    "瓦片道路经过制图泛化，长度结果为工程近似值。"
                )
                if _first(road_layer, "tile_download_status") == "partial":
                    requested = _first(road_layer, "tiles_requested", "unknown")
                    failed = _first(road_layer, "tiles_failed", "unknown")
                    road_metadata["road_data_notice"] += (
                        f"本次请求 {requested} 个瓦片，其中 {failed} 个获取失败；"
                        "已用达到完整度阈值的其余瓦片继续计算，"
                        "结果需按部分覆盖解读。"
                    )
            elif road_source == "osm_local_pbf_snapshot":
                snapshot = _first(road_layer, "snapshot_modified_at", "未知日期")
                road_metadata["road_data_notice"] = (
                    f"道路来自项目内置 OSM 本地快照（快照时间 {snapshot}）；"
                    "结果可复现但不是实时路网。"
                )
    water_step = next(
        (step for step in steps if step.get("tool") == "download_osm_water"),
        None,
    )
    if water_step:
        water_output = water_step.get("params", {}).get("OUTPUT")
        water_path = workspace.resolve(water_output) if water_output else None
        if water_path and water_path.exists():
            water_layer = gpd.read_file(water_path)
            if _first(water_layer, "data_source") == "osm_local_pbf_snapshot":
                snapshot = _first(water_layer, "snapshot_modified_at", "未知日期")
                common["water_data_notice"] = (
                    f"水系来自项目内置 OSM 本地快照（快照时间 {snapshot}），"
                    "不代表实时官方水系数据。"
                )
    if name == "dynamic_road_length_around_poi":
        if not {"road_length", "road_count"}.issubset(gdf.columns):
            return None
        road_length_m = float(gdf["road_length"].sum())
        return {
            **common,
            "result_type": "road_length_around_poi",
            "poi_type": plan.get("poi_type"),
            "distance_meters": plan.get("distance_meters"),
            "road_length_m": round(road_length_m, 2),
            "road_length_km": round(road_length_m / 1000, 2),
            "road_count": int(gdf["road_count"].sum()),
            "counting_method": "dissolved_union_buffer",
            "road_scope": "main",
            "road_scope_notice": MAIN_ROAD_NOTICE,
            **road_metadata,
        }
    if name == "dynamic_administrative_area":
        if "area_sq_km" not in gdf.columns:
            return None
        return {
            **common,
            "result_type": "administrative_area",
            "area_sq_km": round(float(gdf["area_sq_km"].sum()), 2),
            "data_source": _first(gdf, "data_source", "osm_nominatim"),
        }
    if name == "dynamic_university_count":
        if "point_count" not in gdf.columns:
            return None
        return {
            **common,
            "result_type": "university_count",
            "point_count": int(gdf["point_count"].max()),
            "data_source": _first(gdf, "point_data_source", "osm_overpass"),
        }
    if name == "dynamic_poi_count":
        if "point_count" not in gdf.columns:
            return None
        poi_type = plan.get("poi_type", "")
        return {
            **common,
            "result_type": "poi_count",
            "poi_type": poi_type,
            "poi_label": POI_LABELS.get(poi_type, poi_type),
            "point_count": int(gdf["point_count"].max()),
            "data_source": _first(gdf, "point_data_source", "osm_overpass"),
        }
    if name == "dynamic_poi_service_area":
        if "coverage_sq_km" not in gdf.columns:
            return None
        poi_type = plan.get("poi_type", "")
        return {
            **common,
            "result_type": "poi_service_area",
            "poi_type": poi_type,
            "poi_label": POI_LABELS.get(poi_type, poi_type),
            "distance_meters": plan.get("distance_meters", 0),
            "coverage_sq_km": round(float(gdf["coverage_sq_km"].sum()), 2),
        }
    if name == "dynamic_poi_density":
        required = {"point_count", "density_per_sq_km", "area_sq_km"}
        if not required.issubset(gdf.columns):
            return None
        poi_type = plan.get("poi_type", "")
        return {
            **common,
            "result_type": "poi_density",
            "poi_type": poi_type,
            "poi_label": POI_LABELS.get(poi_type, poi_type),
            "grid_count": int(len(gdf)),
            "point_count": int(gdf["point_count"].sum()),
            "max_density_per_sq_km": round(float(gdf["density_per_sq_km"].max()), 2),
        }
    if name == "dynamic_poi_nearest_neighbor":
        if "nearest_neighbor_m" not in gdf.columns:
            return None
        poi_type = plan.get("poi_type", "")
        distances = gdf["nearest_neighbor_m"].dropna()
        return {
            **common,
            "result_type": "poi_nearest_neighbor",
            "poi_type": poi_type,
            "poi_label": POI_LABELS.get(poi_type, poi_type),
            "feature_count": int(len(distances)),
            "minimum_distance_m": round(float(distances.min()), 2),
            "mean_distance_m": round(float(distances.mean()), 2),
            "median_distance_m": round(float(distances.median()), 2),
            "maximum_distance_m": round(float(distances.max()), 2),
        }
    if name == "dynamic_service_gap_analysis":
        required = {"uncovered_sq_km", "covered_sq_km", "coverage_rate_pct"}
        if not required.issubset(gdf.columns):
            return None
        poi_type = plan.get("poi_type", "")
        row = gdf.iloc[0]
        return {
            **common,
            "result_type": "service_gap_analysis",
            "poi_type": poi_type,
            "poi_label": POI_LABELS.get(poi_type, poi_type),
            "distance_meters": plan.get("distance_meters", 0),
            "uncovered_sq_km": round(float(row["uncovered_sq_km"]), 2),
            "covered_sq_km": round(float(row["covered_sq_km"]), 2),
            "coverage_rate_pct": round(float(row["coverage_rate_pct"]), 2),
        }
    if name == "dynamic_multi_ring_service_analysis":
        required = {"distance_m", "coverage_sq_km", "coverage_rate_pct", "marginal_gain_sq_km"}
        if not required.issubset(gdf.columns):
            return None
        poi_type = plan.get("poi_type", "")
        rings = [
            {
                "distance_m": int(row.distance_m),
                "coverage_sq_km": round(float(row.coverage_sq_km), 2),
                "coverage_rate_pct": round(float(row.coverage_rate_pct), 2),
                "marginal_gain_sq_km": round(float(row.marginal_gain_sq_km), 2),
            }
            for row in gdf.sort_values("distance_m").itertuples()
        ]
        return {
            **common,
            "result_type": "multi_ring_service_analysis",
            "poi_type": poi_type,
            "poi_label": POI_LABELS.get(poi_type, poi_type),
            "rings": rings,
            "ring_count": len(rings),
        }
    if name == "dynamic_poi_road_accessibility":
        if "nearest_road_m" not in gdf.columns:
            return None
        poi_type = plan.get("poi_type", "")
        distances = gdf["nearest_road_m"].dropna()
        return {
            **common,
            "result_type": "poi_road_accessibility",
            "poi_type": poi_type,
            "poi_label": POI_LABELS.get(poi_type, poi_type),
            "feature_count": int(len(distances)),
            "minimum_distance_m": round(float(distances.min()), 2),
            "mean_distance_m": round(float(distances.mean()), 2),
            "maximum_distance_m": round(float(distances.max()), 2),
            "road_scope": "main",
            "road_scope_notice": MAIN_ROAD_NOTICE,
            **road_metadata,
        }
    if name == "dynamic_road_density":
        required = {"road_length_km", "density_km_per_sq_km", "area_sq_km"}
        if not required.issubset(gdf.columns):
            return None
        return {
            **common,
            "result_type": "road_density",
            "grid_count": int(len(gdf)),
            "road_length_km": round(float(gdf["road_length_km"].sum()), 2),
            "max_density_km_per_sq_km": round(
                float(gdf["density_km_per_sq_km"].max()), 2
            ),
            "road_scope": "main",
            "road_scope_notice": MAIN_ROAD_NOTICE,
            **road_metadata,
        }
    if name == "fixture_adjacent_regions":
        if "region_name" not in gdf.columns:
            return None
        return {
            **common,
            "result_type": "adjacent_regions",
            "adjacent_count": int(len(gdf)),
            "adjacent_names": sorted(gdf["region_name"].dropna().astype(str).tolist()),
            "data_source": _first(gdf, "data_source", "bundled_gadm_4_1_fixture"),
            "data_notice": "该邻接结果来自随项目分发的 GADM 4.1 学术测试夹具，可能与现行官方区划不同。",
        }
    if name == "dynamic_multi_criteria_site_selection":
        required = {"rank", "site_score", "area_sq_km"}
        if not required.issubset(gdf.columns):
            return None
        best = gdf.sort_values("rank").iloc[0]
        return {
            **common,
            "result_type": "multi_criteria_site_selection",
            "candidate_count": int(len(gdf)),
            "best_score": round(float(best["site_score"]), 2),
            "best_area_sq_km": round(float(best["area_sq_km"]), 4),
            "best_road_distance_m": round(float(best.get("road_distance_m", 0)), 2),
            "best_facility_distance_m": round(
                float(best.get("facility_distance_m", 0)), 2
            ),
            "road_scope": "main",
            "road_scope_notice": MAIN_ROAD_NOTICE,
            **road_metadata,
            "data_notice": (
                "候选地块来自规则网格和 OSM 道路/高校数据的工程筛选，"
                "仅用于方案预选，不能替代规划、地籍、环境和现场调查。"
            ),
        }
    if name == "dynamic_advanced_site_selection":
        required = {
            "rank", "site_score", "area_sq_km", "road_distance_m",
            "transit_distance_m", "water_distance_m", "facility_distance_m",
        }
        if not required.issubset(gdf.columns):
            return None
        best = gdf.sort_values("rank").iloc[0]
        return {
            **common,
            "result_type": "advanced_site_selection",
            "candidate_count": int(len(gdf)),
            "best_score": round(float(best["site_score"]), 2),
            "best_area_sq_km": round(float(best["area_sq_km"]), 4),
            "best_road_distance_m": round(float(best["road_distance_m"]), 2),
            "best_transit_distance_m": round(float(best["transit_distance_m"]), 2),
            "best_water_distance_m": round(float(best["water_distance_m"]), 2),
            "best_facility_distance_m": round(float(best["facility_distance_m"]), 2),
            "road_scope": "main",
            "road_scope_notice": MAIN_ROAD_NOTICE,
            **road_metadata,
            "data_notice": (
                "候选地块基于规则网格和 OSM 道路、轨道站点、高校、水域数据筛选，"
                "仅用于方案预选，不能替代规划、用地、环境与现场调查。"
            ),
        }
    return None


def build_deterministic_answer(user_query: str, stats: dict[str, Any]) -> str:
    result_type = stats["result_type"]
    if result_type == "road_length_around_poi":
        return (
            f"已完成分析：{user_query}\n"
            f"基于 OpenStreetMap 当前收录的高校/学院要素，将 {stats['distance_meters']} 米缓冲区"
            f"合并后统计，{stats['region_name']}高校周边主要道路总长度约为 "
            f"{stats['road_length_km']} 公里，共涉及 {stats['road_count']} 条主要道路要素。"
            "重叠缓冲区内的道路未重复计数。\n"
            f"结果文件：{stats['result_file']}\n"
            f"说明：{stats['road_scope_notice']}"
            f"{stats.get('road_data_notice', '')}OSM 为社区维护数据，结果不等同于官方统计。"
        )
    if result_type == "administrative_area":
        return (
            f"已完成分析：{user_query}\n{stats['region_name']}面积约为 "
            f"{stats['area_sq_km']} 平方公里。结果文件：{stats['result_file']}\n"
            "边界来自 OpenStreetMap Nominatim，可能与官方口径存在差异。"
        )
    if result_type == "university_count":
        return (
            f"已完成分析：{user_query}\n在 {stats['region_name']}边界内检索到 "
            f"{stats['point_count']} 个 OSM 高校/学院要素。结果文件：{stats['result_file']}\n"
            "该数值是 OSM 要素数，不等同于官方高校数量。"
        )
    if result_type == "adjacent_regions":
        names = "、".join(stats["adjacent_names"])
        return (
            f"已完成分析：{user_query}\n与 {stats['region_name']} 相邻的区域共 "
            f"{stats['adjacent_count']} 个：{names}。结果文件：{stats['result_file']}\n"
            "说明：结果基于项目内 GADM 4.1 学术测试夹具，可能与现行官方行政区划不同。"
        )
    if result_type == "multi_criteria_site_selection":
        return (
            f"已完成分析：{user_query}\n共生成并保留 {stats['candidate_count']} 个候选地块，"
            f"排名第一的综合得分为 {stats['best_score']}，面积约 "
            f"{stats['best_area_sq_km']} 平方公里，距最近主要道路约 "
            f"{stats['best_road_distance_m']} 米，距最近高校约 "
            f"{stats['best_facility_distance_m']} 米。\n"
            f"结果文件：{stats['result_file']}\n{stats['road_scope_notice']}\n"
            f"{stats.get('road_data_notice', '')}\n{stats['data_notice']}"
        )
    if result_type == "poi_count":
        return (
            f"已完成分析：{user_query}\n"
            f"在 {stats['region_name']} 边界内检索到 {stats['point_count']} 个 "
            f"OSM {stats['poi_label']}要素。\n结果文件：{stats['result_file']}\n"
            "说明：这是 OpenStreetMap 要素数量，不等同于官方机构统计。"
            f"{stats.get('poi_data_notice', '')}"
        )
    if result_type == "poi_service_area":
        return (
            f"已完成分析：{user_query}\n"
            f"以 {stats['region_name']} 的 OSM {stats['poi_label']}为中心建立 "
            f"{stats['distance_meters']} 米服务范围，边界内覆盖面积约 "
            f"{stats['coverage_sq_km']} 平方公里。\n结果文件：{stats['result_file']}\n"
            "说明：该结果是几何缓冲区覆盖，不代表真实出行时间或道路可达范围。"
        )
    if result_type == "poi_density":
        return (
            f"已完成分析：{user_query}\n"
            f"{stats['region_name']} 共生成 {stats['grid_count']} 个分析网格，"
            f"统计到 {stats['point_count']} 个 OSM {stats['poi_label']}要素；"
            f"最高密度约为 {stats['max_density_per_sq_km']} 个/平方公里。\n"
            f"结果文件：{stats['result_file']}\n"
            f"{stats.get('poi_data_notice', '')}"
        )
    if result_type == "poi_nearest_neighbor":
        return (
            f"已完成分析：{user_query}\n"
            f"对 {stats['region_name']} 的 {stats['feature_count']} 个 OSM "
            f"{stats['poi_label']}要素计算设施最近邻直线距离：最小 "
            f"{stats['minimum_distance_m']} 米、平均 {stats['mean_distance_m']} 米、"
            f"中位数 {stats['median_distance_m']} 米、最大 {stats['maximum_distance_m']} 米。\n"
            f"结果文件：{stats['result_file']}\n"
            "说明：结果为投影平面直线距离，可用于识别设施过密或服务稀疏区域，"
            "不代表道路出行距离。"
        )
    if result_type == "service_gap_analysis":
        return (
            f"已完成分析：{user_query}\n"
            f"以 {stats['region_name']} 的 OSM {stats['poi_label']}为中心建立 "
            f"{stats['distance_meters']} 米服务范围，覆盖率约为 "
            f"{stats['coverage_rate_pct']}%；已覆盖约 {stats['covered_sq_km']} 平方公里，"
            f"服务盲区约 {stats['uncovered_sq_km']} 平方公里。\n"
            f"结果文件：{stats['result_file']}\n"
            "说明：盲区按欧氏缓冲区计算，不代表真实道路出行时间。"
        )
    if result_type == "multi_ring_service_analysis":
        rows = "；".join(
            f"{item['distance_m']}米覆盖{item['coverage_sq_km']}平方公里"
            f"（{item['coverage_rate_pct']}%）"
            for item in stats["rings"]
        )
        return (
            f"已完成分析：{user_query}\n"
            f"{stats['region_name']} OSM {stats['poi_label']}多级服务圈结果：{rows}。\n"
            f"结果文件：{stats['result_file']}\n"
            "说明：各圈层为累计欧氏缓冲范围，marginal_gain_sq_km 字段表示扩大服务半径后的新增覆盖。"
        )
    if result_type == "poi_road_accessibility":
        return (
            f"已完成分析：{user_query}\n"
            f"对 {stats['region_name']} 的 {stats['feature_count']} 个 OSM "
            f"{stats['poi_label']}要素计算最近主要道路直线距离：最小 "
            f"{stats['minimum_distance_m']} 米，平均 {stats['mean_distance_m']} 米，"
            f"最大 {stats['maximum_distance_m']} 米。\n结果文件：{stats['result_file']}\n"
            f"说明：{stats['road_scope_notice']}"
            f"{stats.get('road_data_notice', '')}这里是投影坐标下的直线距离，不是路网行驶距离。"
        )
    if result_type == "road_density":
        return (
            f"已完成分析：{user_query}\n"
            f"{stats['region_name']} 共生成 {stats['grid_count']} 个主要道路密度网格，"
            f"网格内主要道路累计约 {stats['road_length_km']} 公里，最高主要道路密度约 "
            f"{stats['max_density_km_per_sq_km']} 公里/平方公里。\n"
            f"结果文件：{stats['result_file']}\n{stats['road_scope_notice']}\n"
            f"{stats.get('road_data_notice', '')}"
        )
    if result_type == "advanced_site_selection":
        return (
            f"已完成分析：{user_query}\n"
            f"综合主干路、地铁站、高校、水域避让与边界安全距离，共保留 "
            f"{stats['candidate_count']} 个候选地块。排名第一的得分为 "
            f"{stats['best_score']}，面积约 {stats['best_area_sq_km']} 平方公里；"
            f"距主干路 {stats['best_road_distance_m']} 米、距地铁站 "
            f"{stats['best_transit_distance_m']} 米、距水域 "
            f"{stats['best_water_distance_m']} 米。\n结果文件：{stats['result_file']}\n"
            f"{stats['road_scope_notice']}\n{stats.get('road_data_notice', '')}\n"
            f"{stats['data_notice']}"
        )
    raise ValueError(f"Unsupported result type: {result_type}")


def build_llm_summary_messages(user_query: str, stats: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是GIS Agent结果总结节点。只能引用可信统计JSON，禁止重新计算或编造。"
                "必须说明区域、数据来源、关键统计值、结果文件，并准确引用 data_notice。"
                "如果存在 road_scope_notice，必须原样说明道路统计口径。"
                "如果存在 road_data_notice，也必须原样说明道路来源和近似性。"
                "如果存在 poi_data_notice，也必须原样说明 POI 分块完整度。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户任务：{user_query}\n可信统计JSON：\n"
                f"{json.dumps(stats, ensure_ascii=False, indent=2)}\n请用简洁中文回答。"
            ),
        },
    ]


def summarize_workflow_result(
    user_query: str,
    plan: dict,
    workspace: TaskWorkspace,
    *,
    use_llm: bool = True,
) -> dict[str, Any] | None:
    stats = extract_workflow_statistics(plan, workspace)
    if stats is None:
        return None
    deterministic = build_deterministic_answer(user_query, stats)
    stats["deterministic_answer"] = deterministic
    if not use_llm:
        stats["answer"] = deterministic
        stats["answer_source"] = "deterministic"
        return stats
    try:
        stats["answer"] = create_text_response(build_llm_summary_messages(user_query, stats))
        stats["answer_source"] = "llm"
    except LLMClientError as exc:
        stats["answer"] = deterministic
        stats["answer_source"] = "deterministic_fallback"
        stats["llm_error"] = str(exc)
    return stats
