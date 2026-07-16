from __future__ import annotations

import re
from typing import Any

from .dataset_catalog import POI_LABELS


REGION_PATTERN = re.compile(
    r"([\u4e00-\u9fff]{2,16}?(?:\u7279\u522b\u884c\u653f\u533a|\u81ea\u6cbb\u533a|\u81ea\u6cbb\u5dde|\u5e02|\u7701|\u533a|\u53bf|\u5dde|\u76df))"
)
REFERENCE_WORDS = (
    "\u5b83",
    "\u8be5\u5730\u533a",
    "\u8fd9\u4e2a\u5730\u533a",
    "\u8fd9\u91cc",
    "\u5176\u4e2d",
    "\u8fd9\u91cc\u9762",
    "\u4e0a\u8ff0\u533a\u57df",
)
LEADING_WORDS = (
    "\u8bf7\u5e2e\u6211",
    "\u5e2e\u6211",
    "\u6211\u60f3\u627e",
    "\u8bf7\u95ee",
    "\u8bf7\u67e5\u627e",
    "\u8bf7",
    "\u518d\u7edf\u8ba1",
    "\u7edf\u8ba1",
    "\u8ba1\u7b97",
    "\u67e5\u8be2",
    "\u5206\u6790",
    "\u544a\u8bc9\u6211",
    "\u770b\u770b",
    "\u5bfb\u627e",
    "\u5728",
    "\u5bf9",
    "\u4ee5",
    "\u6362\u6210",
    "\u6539\u4e3a",
    "\u6539\u6210",
    "\u8c03\u6574\u4e3a",
    "\u628a\u533a\u57df\u6362\u6210",
    "\u628a\u533a\u57df\u6539\u4e3a",
    "\u628a\u5730\u533a\u6362\u6210",
    "\u628a\u5730\u533a\u6539\u4e3a",
    "\u518d\u770b",
)

POI_KEYWORDS = {
    "subway_station": ("\u5730\u94c1\u7ad9", "\u5730\u94c1", "\u8f68\u9053\u7ad9"),
    "university": ("\u9ad8\u6821", "\u5927\u5b66", "\u5b66\u9662"),
    "school": ("\u4e2d\u5c0f\u5b66", "\u5b66\u6821"),
    "hospital": ("\u533b\u9662",),
    "clinic": ("\u8bca\u6240", "\u95e8\u8bca"),
    "pharmacy": ("\u836f\u5e97", "\u836f\u623f"),
    "park": ("\u516c\u56ed", "\u7eff\u5730"),
    "police": ("\u6d3e\u51fa\u6240", "\u516c\u5b89", "\u8b66\u52a1"),
    "fire_station": ("\u6d88\u9632\u7ad9", "\u6d88\u9632\u961f"),
    "supermarket": ("\u8d85\u5e02",),
    "charging_station": ("\u5145\u7535\u7ad9", "\u5145\u7535\u6869"),
}


def _clean_region(candidate: str) -> str:
    value = candidate.strip()
    changed = True
    while changed:
        changed = False
        for prefix in LEADING_WORDS:
            if value.startswith(prefix) and len(value) > len(prefix) + 1:
                value = value[len(prefix) :]
                changed = True
                break
    return value


def extract_region(query: str) -> str:
    candidates = []
    for match in REGION_PATTERN.finditer(query):
        candidate = _clean_region(match.group(1))
        if candidate.endswith(("\u57ce\u5e02", "\u5730\u5e02")) or candidate in {
            "\u54ea\u4e9b\u5e02",
            "\u54ea\u4e2a\u5e02",
        }:
            continue
        candidates.append(candidate)
    return min(candidates, key=len) if candidates else ""


def extract_poi_type(query: str, default: str = "university") -> str:
    compact = "".join(query.split())
    for poi_type, keywords in POI_KEYWORDS.items():
        if any(keyword in compact for keyword in keywords):
            return poi_type
    return default


def extract_distance_meters(query: str, default: int = 0) -> int:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(\u516c\u91cc|\u5343\u7c73|km|KM|\u7c73|m)", query)
    if not match:
        return default
    value = float(match.group(1))
    if match.group(2) in {"\u516c\u91cc", "\u5343\u7c73", "km", "KM"}:
        value *= 1000
    return max(1, int(round(value)))


def classify_task(query: str) -> str:
    compact = "".join(query.split())
    has_poi = any(
        any(keyword in compact for keyword in values)
        for values in POI_KEYWORDS.values()
    )
    if any(word in compact for word in ("服务盲区", "覆盖盲区", "未覆盖区域", "覆盖缺口")):
        return "service_gap_analysis"
    if any(word in compact for word in ("多级服务圈", "多圈层", "分级服务区", "多尺度服务区")):
        return "multi_ring_service_analysis"
    if any(word in compact for word in ("最近邻", "设施间距", "平均间距", "相互距离")):
        return "poi_nearest_neighbor"
    selection_words = ("\u9009\u5740", "\u5019\u9009\u5730\u5757", "\u5efa\u8bbe\u65b0\u6821\u533a", "\u65b0\u6821\u533a")
    advanced_words = ("\u6c34\u57df", "\u5730\u94c1", "\u4e3b\u5e72\u8def", "\u65b0\u6821\u533a")
    if any(word in compact for word in selection_words) and any(
        word in compact for word in advanced_words
    ):
        return "advanced_site_selection"
    if any(word in compact for word in ("\u591a\u6761\u4ef6\u9009\u5740", "\u7efc\u5408\u9009\u5740", "\u5019\u9009\u5730\u5757", "\u9009\u5740\u5206\u6790")):
        return "multi_criteria_site_selection"
    if any(word in compact for word in (
        "\u76f8\u90bb", "\u90bb\u63a5", "\u5468\u56f4\u6709\u54ea\u4e9b\u5e02", "\u5468\u8fb9\u6709\u54ea\u4e9b\u5e02",
        "\u5468\u56f4\u6709\u54ea\u4e9b\u57ce\u5e02", "\u5468\u8fb9\u6709\u54ea\u4e9b\u57ce\u5e02",
    )):
        return "adjacent_regions"
    if "\u9053\u8def\u5bc6\u5ea6" in compact or "\u8def\u7f51\u5bc6\u5ea6" in compact:
        return "road_density"
    if "\u9053\u8def" in compact and any(word in compact for word in ("\u957f\u5ea6", "\u591a\u957f", "\u603b\u957f")):
        return "road_length_around_poi"
    if any(word in compact for word in ("\u670d\u52a1\u8303\u56f4", "\u8986\u76d6\u8303\u56f4", "\u8f90\u5c04\u8303\u56f4", "\u7f13\u51b2\u533a")):
        return "poi_service_area"
    if any(word in compact for word in ("\u9053\u8def\u53ef\u8fbe\u6027", "\u8ddd\u9053\u8def", "\u6700\u8fd1\u9053\u8def")) or (
        has_poi
        and "\u9053\u8def" in compact
        and "\u8ddd\u79bb" in compact
        and any(word in compact for word in ("\u6700\u8fd1", "\u4e3b\u8981", "\u4e3b\u5e72"))
    ):
        return "poi_road_accessibility"
    if any(word in compact for word in ("\u5bc6\u5ea6", "\u70ed\u529b", "\u7a7a\u95f4\u5206\u5e03", "\u5206\u5e03\u60c5\u51b5")):
        return "poi_density"
    if any(word in compact for word in ("\u9762\u79ef", "\u591a\u5927")):
        return "administrative_area"
    if has_poi and any(word in compact for word in ("\u6570\u91cf", "\u591a\u5c11", "\u51e0\u4e2a", "\u8981\u7d20\u6570", "\u7edf\u8ba1")):
        return "university_count" if extract_poi_type(query) == "university" else "poi_count"
    return "unknown"


def canonical_query(
    task_type: str,
    region: str,
    original: str,
    *,
    poi_type: str | None = None,
    distance_meters: int | None = None,
) -> str:
    poi_type = poi_type or extract_poi_type(original)
    poi_label = POI_LABELS.get(poi_type, poi_type)
    distance = (
        distance_meters
        if distance_meters is not None
        else extract_distance_meters(original)
    )
    if task_type == "adjacent_regions":
        return f"\u67e5\u8be2{region}\u76f8\u90bb\u7684\u5730\u7ea7\u884c\u653f\u533a"
    if task_type == "administrative_area":
        return f"\u8ba1\u7b97{region}\u7684\u9762\u79ef"
    if task_type in {"university_count", "poi_count"}:
        return f"\u7edf\u8ba1{region}\u8303\u56f4\u5185\u7684{poi_label}\u6570\u91cf"
    if task_type == "road_length_around_poi":
        return f"\u7edf\u8ba1{region}{poi_label}\u5468\u8fb9{distance or 1000}\u7c73\u4e3b\u8981\u9053\u8def\u603b\u957f\u5ea6"
    if task_type == "poi_service_area":
        return f"\u5206\u6790{region}{poi_label}{distance or 1000}\u7c73\u670d\u52a1\u8986\u76d6\u8303\u56f4"
    if task_type == "poi_density":
        return f"\u5206\u6790{region}{poi_label}\u7a7a\u95f4\u5bc6\u5ea6\u5206\u5e03"
    if task_type == "poi_nearest_neighbor":
        return f"分析{region}{poi_label}最近邻距离和设施间距"
    if task_type == "service_gap_analysis":
        return f"分析{region}{poi_label}{distance or 1000}米服务覆盖盲区"
    if task_type == "multi_ring_service_analysis":
        return f"分析{region}{poi_label}500米、1000米和2000米多级服务圈"
    if task_type == "poi_road_accessibility":
        return f"\u5206\u6790{region}{poi_label}\u5230\u6700\u8fd1\u4e3b\u8981\u9053\u8def\u7684\u8ddd\u79bb"
    if task_type == "road_density":
        return f"\u8ba1\u7b97{region}\u4e3b\u8981\u9053\u8def\u7f51\u5bc6\u5ea6"
    if task_type == "multi_criteria_site_selection":
        return f"\u5bf9{region}\u8fdb\u884c\u591a\u6761\u4ef6\u9009\u5740\uff0c\u7efc\u5408\u9ad8\u6821\u53ef\u8fbe\u6027\u3001\u4e3b\u8981\u9053\u8def\u53ef\u8fbe\u6027\u548c\u8fb9\u754c\u5b89\u5168\u8ddd\u79bb"
    if task_type == "advanced_site_selection":
        return (
            f"\u5bf9{region}\u8fdb\u884c\u65b0\u6821\u533a\u9009\u5740\uff0c\u8ddd\u4e3b\u5e72\u8def\u4e0d\u8d85\u8fc7"
            f"{distance or 1000}\u7c73\uff0c\u907f\u5f00\u6c34\u57df\u5e76\u9760\u8fd1\u5730\u94c1\u7ad9"
        )
    return original


SUPPORTED_CONTEXT_TASKS = {
    "adjacent_regions",
    "administrative_area",
    "university_count",
    "poi_count",
    "road_length_around_poi",
    "poi_service_area",
    "poi_density",
    "poi_nearest_neighbor",
    "service_gap_analysis",
    "multi_ring_service_analysis",
    "poi_road_accessibility",
    "road_density",
    "multi_criteria_site_selection",
    "advanced_site_selection",
}


def resolve_conversation_context(query: str, memory: dict[str, Any] | None) -> dict[str, Any]:
    memory = dict(memory or {})
    explicit_region = extract_region(query)
    task_type = classify_task(query)
    adjustment_words = (
        "改为", "改成", "换成", "调整为", "范围改", "距离改", "再分析", "继续分析",
    )
    previous_task = str(memory.get("previous_task_type") or "")
    if (
        task_type == "unknown"
        and previous_task in SUPPORTED_CONTEXT_TASKS
        and any(word in query for word in adjustment_words + REFERENCE_WORDS)
    ):
        task_type = previous_task
    has_reference = any(word in query for word in REFERENCE_WORDS)
    inherited_region = str(memory.get("current_region") or "")
    region = explicit_region or inherited_region
    previous_poi = str(memory.get("previous_poi_type") or "university")
    poi_type = extract_poi_type(query, previous_poi)
    explicit_distance = extract_distance_meters(query)
    previous_distance = int(memory.get("previous_distance_meters") or 0)
    distance_meters = explicit_distance or previous_distance

    if has_reference and not explicit_region and not inherited_region:
        return {
            "action": "clarify",
            "task_type": task_type,
            "region_name": "",
            "resolved_query": query,
            "clarification": "\u8bf7\u8bf4\u660e\u201c\u8fd9\u91cc\u201d\u6216\u201c\u5b83\u201d\u5177\u4f53\u6307\u54ea\u4e2a\u884c\u653f\u533a\u57df\uff0c\u4f8b\u5982\uff1a\u5357\u4eac\u5e02\u3002",
            "resolution_source": "missing_reference",
        }
    if task_type in SUPPORTED_CONTEXT_TASKS and not region:
        return {
            "action": "clarify",
            "task_type": task_type,
            "region_name": "",
            "resolved_query": query,
            "clarification": "\u8bf7\u8865\u5145\u9700\u8981\u5206\u6790\u7684\u884c\u653f\u533a\u57df\uff0c\u4f8b\u5982\uff1a\u5357\u4eac\u5e02\u3002",
            "resolution_source": "missing_region",
        }

    return {
        "action": "execute",
        "task_type": task_type,
        "region_name": region,
        "resolved_query": canonical_query(
            task_type,
            region,
            query,
            poi_type=poi_type,
            distance_meters=distance_meters,
        ) if region else query,
        "clarification": "",
        "resolution_source": "explicit" if explicit_region else "memory" if region else "none",
    }
