from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .llm_client import LLMClientError, create_text_response
from .task_workspace import TaskWorkspace


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
    return None


def build_deterministic_answer(user_query: str, stats: dict[str, Any]) -> str:
    result_type = stats["result_type"]
    if result_type == "road_length_around_poi":
        return (
            f"已完成分析：{user_query}\n"
            f"基于 OpenStreetMap 当前收录的高校/学院要素，将 {stats['distance_meters']} 米缓冲区"
            f"合并后统计，{stats['region_name']}高校周边道路总长度约为 "
            f"{stats['road_length_km']} 公里，共涉及 {stats['road_count']} 条道路要素。"
            "重叠缓冲区内的道路未重复计数。\n"
            f"结果文件：{stats['result_file']}\n"
            "说明：OSM 为社区维护数据，结果不等同于官方高校或道路统计。"
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
    raise ValueError(f"Unsupported result type: {result_type}")


def build_llm_summary_messages(user_query: str, stats: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是GIS Agent结果总结节点。只能引用可信统计JSON，禁止重新计算或编造。"
                "必须说明区域、数据来源、关键统计值、结果文件，并准确引用 data_notice。"
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
