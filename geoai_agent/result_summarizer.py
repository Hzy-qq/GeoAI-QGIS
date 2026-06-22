from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import warnings

from .llm_client import LLMClientError, create_text_response


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def get_final_output_path(workflow: dict, project_root: Path = PROJECT_ROOT) -> Path | None:
    steps = workflow.get("steps", [])
    if not steps:
        return None
    output_path = steps[-1].get("params", {}).get("OUTPUT")
    if not output_path:
        return None
    return project_root / output_path


def summarize_road_length(result_path: Path) -> dict[str, Any] | None:
    if result_path is None or not result_path.exists():
        return None

    try:
        import geopandas as gpd
    except ImportError:
        return None

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        gdf = gpd.read_file(result_path)

    if "road_length" not in gdf.columns or "road_count" not in gdf.columns:
        return None

    road_length_m = float(gdf["road_length"].sum())
    road_count = int(gdf["road_count"].sum())
    return {
        "result_file": str(result_path),
        "road_length_m": round(road_length_m, 2),
        "road_length_km": round(road_length_m / 1000, 2),
        "road_count": road_count,
    }


def build_road_length_answer(
    user_query: str,
    distance_meters: int | None,
    summary: dict[str, Any],
) -> str:
    distance_text = f"{distance_meters} 米" if distance_meters else "指定范围"
    return (
        f"已完成分析：{user_query}\n"
        f"在 places 周边 {distance_text} 范围内，道路总长度约为 "
        f"{summary['road_length_km']} 公里，共统计到 {summary['road_count']} 条道路要素。\n"
        f"结果文件已保存到：{summary['result_file']}"
    )


def build_llm_summary_messages(
    user_query: str,
    distance_meters: int | None,
    summary: dict[str, Any],
) -> list[dict[str, str]]:
    distance_text = f"{distance_meters} 米" if distance_meters else "未显式指定"
    statistics = {
        "result_file": summary["result_file"],
        "road_length_m": summary["road_length_m"],
        "road_length_km": summary["road_length_km"],
        "road_count": summary["road_count"],
        "distance_meters": distance_meters,
    }
    statistics_text = json.dumps(statistics, ensure_ascii=False, indent=2)
    return [
        {
            "role": "system",
            "content": (
                "你是一个 GIS Agent 的结果总结节点。"
                "你只负责把工具已经计算出的统计结果，整理成简洁、准确的中文回答。"
                "不要重新计算，不要编造不存在的数据。"
                "回答中必须包含分析是否完成、缓冲距离、道路总长度、道路要素数量、结果文件路径。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户原始任务：{user_query}\n"
                f"缓冲距离：{distance_text}\n"
                f"工具统计结果 JSON：\n{statistics_text}\n\n"
                "请基于这些真实统计结果，输出给用户看的最终中文回答。"
            ),
        },
    ]


def generate_llm_road_length_answer(
    user_query: str,
    distance_meters: int | None,
    summary: dict[str, Any],
    *,
    model: str | None = None,
) -> str:
    messages = build_llm_summary_messages(user_query, distance_meters, summary)
    return create_text_response(messages, model=model)


def summarize_workflow_result(
    user_query: str,
    workflow: dict,
    distance_meters: int | None = None,
    *,
    use_llm: bool = True,
) -> dict[str, Any] | None:
    result_path = get_final_output_path(workflow)
    summary = summarize_road_length(result_path) if result_path else None
    if summary is None:
        return None

    deterministic_answer = build_road_length_answer(user_query, distance_meters, summary)
    summary["deterministic_answer"] = deterministic_answer

    if not use_llm:
        summary["answer"] = deterministic_answer
        summary["answer_source"] = "deterministic"
        return summary

    try:
        summary["answer"] = generate_llm_road_length_answer(
            user_query,
            distance_meters,
            summary,
        )
        summary["answer_source"] = "llm"
    except LLMClientError as exc:
        summary["answer"] = deterministic_answer
        summary["answer_source"] = "deterministic_fallback"
        summary["llm_error"] = str(exc)

    return summary
