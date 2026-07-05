from __future__ import annotations

import re
from typing import Any


REGION_PATTERN = re.compile(
    r"([\u4e00-\u9fff]{2,12}(?:特别行政区|自治区|自治州|市|省|区|县|州|盟))"
    r"(?=的|内|范围|周边|附近|相邻|面积|有|里|中|$)"
)
REFERENCE_WORDS = ("它", "该地区", "这个地区", "这里", "其中", "这里面", "上述区域")
LEADING_WORDS = (
    "请帮我", "帮我", "请问", "请", "再统计", "统计", "计算", "查询", "分析", "告诉我", "看看",
)


def _clean_region(candidate: str) -> str:
    value = candidate.strip()
    changed = True
    while changed:
        changed = False
        for prefix in LEADING_WORDS:
            if value.startswith(prefix) and len(value) > len(prefix) + 1:
                value = value[len(prefix):]
                changed = True
                break
    return value


def extract_region(query: str) -> str:
    for match in REGION_PATTERN.finditer(query):
        candidate = _clean_region(match.group(1))
        if candidate.endswith(("城市", "地市")) or candidate in {"哪些市", "哪个市"}:
            continue
        return candidate
    return ""


def classify_task(query: str) -> str:
    compact = "".join(query.split())
    if any(word in compact for word in (
        "相邻", "邻接", "周围有哪些市", "周边有哪些市", "周围有哪些城市", "周边有哪些城市",
    )):
        return "adjacent_regions"
    if any(word in compact for word in ("面积", "多大")):
        return "administrative_area"
    if any(word in compact for word in ("高校", "大学", "学院")) and any(
        word in compact for word in ("数量", "多少", "几个", "要素数")
    ):
        return "university_count"
    if "道路" in compact and any(word in compact for word in ("长度", "多长", "总长")):
        return "road_length_around_poi"
    return "unknown"


def canonical_query(task_type: str, region: str, original: str) -> str:
    if task_type == "adjacent_regions":
        return f"查询{region}相邻的地级行政区"
    if task_type == "administrative_area":
        return f"计算{region}的面积"
    if task_type == "university_count":
        return f"统计{region}范围内的高校数量"
    if task_type == "road_length_around_poi":
        return original if region in original else f"{region}：{original}"
    return original


def resolve_conversation_context(query: str, memory: dict[str, Any] | None) -> dict[str, Any]:
    memory = dict(memory or {})
    explicit_region = extract_region(query)
    task_type = classify_task(query)
    has_reference = any(word in query for word in REFERENCE_WORDS)
    inherited_region = str(memory.get("current_region") or "")
    region = explicit_region or inherited_region

    if has_reference and not explicit_region and not inherited_region:
        return {
            "action": "clarify",
            "task_type": task_type,
            "region_name": "",
            "resolved_query": query,
            "clarification": "请说明“这里”或“它”具体指哪个行政区域，例如：南京市。",
            "resolution_source": "missing_reference",
        }
    if task_type in {
        "adjacent_regions", "administrative_area", "university_count", "road_length_around_poi",
    } and not region:
        return {
            "action": "clarify",
            "task_type": task_type,
            "region_name": "",
            "resolved_query": query,
            "clarification": "请补充需要分析的行政区域，例如：南京市。",
            "resolution_source": "missing_region",
        }

    return {
        "action": "execute",
        "task_type": task_type,
        "region_name": region,
        "resolved_query": canonical_query(task_type, region, query) if region else query,
        "clarification": "",
        "resolution_source": "explicit" if explicit_region else "memory" if region else "none",
    }
