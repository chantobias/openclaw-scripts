#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""香港分區交通狀況地圖 - 眼鏡妹

啟動本機網頁，按「區域」（香港島、九龍、新界）同「地區」（18 區）
查看運輸署策略性道路探測器的即時車速，以及特別交通消息。

數據來源（公開、毋須 API 金鑰）：
    探測器位置：
        https://static.data.gov.hk/td/traffic-data-strategic-major-roads/info/traffic_speed_volume_occ_info.csv
    即時車速、佔用率、交通量（約每分鐘）：
        https://resource.data.one.gov.hk/td/traffic-detectors/rawSpeedVol-all.xml
    特別交通消息：
        https://www.td.gov.hk/tc/special_news/trafficnews.xml
        https://resource.data.one.gov.hk/td/en/specialtrafficnews.xml

用法：
    python traffic_map.py
    python traffic_map.py --port 8765
    python traffic_map.py --no-browser
    python traffic_map.py --once --region nt --district north
    python traffic_map.py --self-test
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


USER_AGENT = "Mozilla/5.0 (compatible; TrafficMap/1.0)"
LOCATIONS_URL = (
    "https://static.data.gov.hk/td/traffic-data-strategic-major-roads/"
    "info/traffic_speed_volume_occ_info.csv"
)
SPEED_URL = "https://resource.data.one.gov.hk/td/traffic-detectors/rawSpeedVol-all.xml"
NEWS_URLS = (
    "https://www.td.gov.hk/tc/special_news/trafficnews.xml",
    "https://resource.data.one.gov.hk/td/en/specialtrafficnews.xml",
)
ONE_NEWS_NS = {"td": "http://data.one.gov.hk/td"}

JAM_SPEED = 20.0
SLOW_SPEED = 40.0
JAM_OCC = 35.0
SLOW_OCC = 18.0
EMPTY_OCC = 10.0

STATUS_LABEL = {
    "smooth": "暢通",
    "slow": "緩慢",
    "jam": "擠塞",
    "unknown": "無數據",
    "closed": "封閉",
    "clear": "已解封",
}

REGIONS: tuple[tuple[str, str], ...] = (
    ("hk", "香港島"),
    ("kln", "九龍"),
    ("nt", "新界"),
)

# id, region, 中文名, 運輸署 CSV 用的英文地區名
DISTRICTS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("central-western", "hk", "中西區", ("Central & Western", "Central and Western")),
    ("wan-chai", "hk", "灣仔區", ("Wan Chai",)),
    ("eastern", "hk", "東區", ("Eastern",)),
    ("southern", "hk", "南區", ("Southern",)),
    ("yau-tsim-mong", "kln", "油尖旺區", ("Yau Tsim Mong",)),
    ("sham-shui-po", "kln", "深水埗區", ("Sham Shui Po",)),
    ("kowloon-city", "kln", "九龍城區", ("Kowloon City",)),
    ("wong-tai-sin", "kln", "黃大仙區", ("Wong Tai Sin",)),
    ("kwun-tong", "kln", "觀塘區", ("Kwun Tong",)),
    ("kwai-tsing", "nt", "葵青區", ("Kwai Tsing",)),
    ("tsuen-wan", "nt", "荃灣區", ("Tsuen Wan",)),
    ("tuen-mun", "nt", "屯門區", ("Tuen Mun",)),
    ("yuen-long", "nt", "元朗區", ("Yuen Long",)),
    ("north", "nt", "北區", ("North",)),
    ("tai-po", "nt", "大埔區", ("Tai Po",)),
    ("sha-tin", "nt", "沙田區", ("Sha Tin",)),
    ("sai-kung", "nt", "西貢區", ("Sai Kung",)),
    ("islands", "nt", "離島區", ("Islands",)),
)

# 消息正文未必寫出完整路名，用地名輔助對應地區。
DISTRICT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "central-western": ("中環", "上環", "西環", "西營盤", "石塘咀", "金鐘", "山頂", "半山", "干諾道", "正街", "第二街"),
    "wan-chai": ("灣仔", "銅鑼灣", "跑馬地", "大坑", "渣甸山"),
    "eastern": ("北角", "鰂魚涌", "太古", "西灣河", "筲箕灣", "柴灣", "小西灣", "杏花邨", "天后"),
    "southern": ("香港仔", "黃竹坑", "鴨脷洲", "赤柱", "淺水灣", "深水灣", "薄扶林", "華富", "田灣"),
    "yau-tsim-mong": ("油麻地", "尖沙咀", "旺角", "大角咀", "佐敦", "柯士甸", "海泓道", "麗翔道"),
    "sham-shui-po": ("深水埗", "長沙灣", "荔枝角", "石硤尾", "美孚", "南昌", "蘇屋"),
    "kowloon-city": ("紅磡", "土瓜灣", "馬頭圍", "九龍塘", "何文田", "啟德", "九龍城", "宋皇臺"),
    "wong-tai-sin": ("黃大仙", "鑽石山", "新蒲崗", "樂富", "慈雲山", "橫頭磡", "竹園"),
    "kwun-tong": ("觀塘", "牛頭角", "九龍灣", "藍田", "油塘", "秀茂坪", "鯉魚門", "順利", "四順"),
    "kwai-tsing": ("葵涌", "葵芳", "青衣", "荔景", "醉酒灣", "貨櫃碼頭"),
    "tsuen-wan": ("荃灣", "梨木樹", "汀九", "深井"),
    "tuen-mun": ("屯門", "蝴蝶邨", "兆康", "藍地", "掃管笏"),
    "yuen-long": ("元朗", "天水圍", "錦田", "八鄉", "洪水橋", "流浮山", "屏山"),
    "north": ("上水", "粉嶺", "古洞", "沙頭角", "打鼓嶺", "文錦渡", "彩園", "聯和墟", "和合石", "華明", "百和路"),
    "tai-po": ("大埔", "太和", "大尾篤", "林村"),
    "sha-tin": ("沙田", "大圍", "火炭", "馬鞍山", "石門", "車公廟", "小瀝源", "顯徑"),
    "sai-kung": ("西貢", "將軍澳", "坑口", "寶琳", "調景嶺", "清水灣", "康城"),
    "islands": ("東涌", "大嶼山", "長洲", "坪洲", "梅窩", "愉景灣", "赤鱲角", "機場"),
}

REGION_BY_ID = {item[0]: {"id": item[0], "name": item[1]} for item in REGIONS}
DISTRICT_BY_ID = {
    item[0]: {"id": item[0], "region": item[1], "name": item[2], "aliases": item[3]}
    for item in DISTRICTS
}
DISTRICT_ID_BY_EN = {
    alias: item[0] for item in DISTRICTS for alias in item[3]
}
GENERIC_ROAD_KEYS = {"路", "公路", "大道", "大橋", "隧道", "天橋", "橋", "道"}
GENERIC_LANDMARKS = {"馬場", "公園", "天橋", "迴旋處", "交匯處", "隧道", "公路", "大橋"}


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read()


def split_road(road_tc: str) -> tuple[str, str]:
    """由「路名近地標 - 方向」拆出路名同地標。"""
    text = " ".join((road_tc or "").split())
    landmark = ""
    if "近" in text:
        left, right = text.split("近", 1)
        road = left.strip(" -－")
        landmark = right
        for sep in (" - ", "－", "（", "(", " — "):
            if sep in landmark:
                landmark = landmark.split(sep, 1)[0]
        landmark = landmark.strip(" -－")
    else:
        road = text
        for sep in (" - ", "－", "（", "(", " — "):
            if sep in road:
                road = road.split(sep, 1)[0]
        road = road.strip()
    if landmark in GENERIC_ROAD_KEYS or len(landmark) < 2:
        landmark = ""
    return road, landmark


def classify(speed: float | None, occupancy: float | None, volume: float | None) -> str:
    if speed is None:
        return "unknown"
    volume = 0.0 if volume is None else volume
    occupancy = 0.0 if occupancy is None else occupancy
    if volume <= 0 and occupancy < EMPTY_OCC:
        return "smooth"
    if speed < JAM_SPEED or occupancy >= JAM_OCC:
        return "jam"
    if speed < SLOW_SPEED or occupancy >= SLOW_OCC:
        return "slow"
    return "smooth"


def _parse_csv(text: str) -> list[dict[str, str]]:
    import csv
    import io

    return list(csv.DictReader(io.StringIO(text)))


def load_locations(raw: bytes | None = None) -> list[dict[str, Any]]:
    if raw is None:
        raw = fetch_bytes(LOCATIONS_URL)
    rows = _parse_csv(raw.decode("utf-8-sig"))
    locations: list[dict[str, Any]] = []
    skipped: set[str] = set()
    for row in rows:
        district_en = (row.get("District") or "").strip()
        district_id = DISTRICT_ID_BY_EN.get(district_en)
        if not district_id:
            skipped.add(district_en or "(空白)")
            continue
        try:
            lat = float(row["Latitude"])
            lng = float(row["Longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(lat) and math.isfinite(lng)):
            continue
        road_tc = (row.get("Road_TC") or "").strip()
        road_key, landmark = split_road(road_tc)
        meta = DISTRICT_BY_ID[district_id]
        locations.append(
            {
                "id": (row.get("AID_ID_Number") or "").strip(),
                "district_id": district_id,
                "district_name": meta["name"],
                "region": meta["region"],
                "region_name": REGION_BY_ID[meta["region"]]["name"],
                "name": road_tc,
                "road_key": road_key,
                "landmark": landmark,
                "lat": lat,
                "lng": lng,
                "direction": (row.get("Direction") or "").strip(),
            }
        )
    if skipped:
        print("略過未能對應的地區：" + "、".join(sorted(skipped)), file=sys.stderr)
    return locations


def _lane_float(lane: ET.Element, tag: str) -> float | None:
    value = lane.findtext(tag)
    if value is None or value.strip() == "":
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return number


def parse_speeds(raw: bytes) -> tuple[str, dict[str, dict[str, Any]]]:
    root = ET.fromstring(raw)
    date = (root.findtext("date") or "").strip()
    periods = root.findall("periods/period")
    period = periods[-1] if periods else None
    stamp = date
    if period is not None:
        end = (period.findtext("period_to") or "").strip()
        if end:
            stamp = f"{date} {end}".strip()
    speeds: dict[str, dict[str, Any]] = {}
    detectors = period.findall("detectors/detector") if period is not None else []
    for detector in detectors:
        detector_id = (detector.findtext("detector_id") or "").strip()
        if not detector_id:
            continue
        weighted = 0.0
        weight = 0.0
        plain: list[float] = []
        occupancies: list[float] = []
        volume = 0.0
        for lane in detector.findall("lanes/lane"):
            if (lane.findtext("valid") or "").strip().upper() != "Y":
                continue
            speed = _lane_float(lane, "speed")
            if speed is None or speed < 0:
                continue
            occ = _lane_float(lane, "occupancy")
            vol = _lane_float(lane, "volume")
            if vol is None or vol < 0:
                vol = 0.0
            plain.append(speed)
            volume += vol
            weighted += speed * vol
            weight += vol
            if occ is not None and occ >= 0:
                occupancies.append(occ)
        if not plain:
            speeds[detector_id] = {
                "speed": None,
                "occupancy": None,
                "volume": 0,
                "status": "unknown",
            }
            continue
        avg_speed = weighted / weight if weight > 0 else sum(plain) / len(plain)
        avg_occ = sum(occupancies) / len(occupancies) if occupancies else 0.0
        speeds[detector_id] = {
            "speed": round(avg_speed, 1),
            "occupancy": round(avg_occ, 1),
            "volume": int(round(volume)),
            "status": classify(avg_speed, avg_occ, volume),
        }
    return stamp, speeds


def child_text(element: ET.Element, name: str) -> str:
    """讀取直接子元素文字。標籤可能帶 XML namespace。"""
    for child in list(element):
        tag = child.tag.split("}", 1)[-1]
        if tag == name and child.text:
            return " ".join(child.text.split())
    return ""


def _compact(text: str) -> str:
    return "".join((text or "").split())


def parse_news(documents: list[tuple[str, bytes]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for source, raw in documents:
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue
        nodes = root.findall("td:message", ONE_NEWS_NS) or root.findall("message")
        for node in nodes:
            text = child_text(node, "ChinText") or child_text(node, "CONTENT_CN") or child_text(node, "INCIDENT_DETAIL_CN")
            if not text:
                continue
            lat = _lane_number(child_text(node, "LATITUDE"))
            lng = _lane_number(child_text(node, "LONGITUDE"))
            messages.append(
                {
                    "id": child_text(node, "msgID") or child_text(node, "ID") or child_text(node, "INCIDENT_NUMBER") or _compact(text)[:40],
                    "text": text,
                    "time": child_text(node, "ReferenceDate") or child_text(node, "ANNOUNCEMENT_DATE"),
                    "location": child_text(node, "LOCATION_CN"),
                    "landmark": child_text(node, "NEAR_LANDMARK_CN"),
                    "lat": lat,
                    "lng": lng,
                }
            )
    deduped: list[dict[str, Any]] = []
    seen: list[str] = []
    for message in messages:
        key = _compact(message["text"])
        if not key:
            continue
        replaced = False
        for index, previous in enumerate(seen):
            short, long = (key, previous) if len(key) <= len(previous) else (previous, key)
            same = short == long or (len(short) >= 30 and long.startswith(short))
            if not same:
                continue
            if len(key) > len(previous):
                seen[index] = key
                deduped[index] = message
            replaced = True
            break
        if not replaced:
            seen.append(key)
            deduped.append(message)
    return deduped


def _lane_number(value: str | None) -> float | None:
    if value is None or not str(value).strip():
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return number


def incident_kind(text: str) -> str:
    compact = _compact(text)
    if "解封" in compact or "re-opened" in text.lower():
        return "clear"
    if "擠塞" in compact or "congest" in text.lower():
        return "jam"
    if "封閉" in compact or "closed" in text.lower():
        return "closed"
    if "繁忙" in compact or "busy" in text.lower():
        return "slow"
    return "info"


def _nearest_detector(
    lat: float,
    lng: float,
    locations: list[dict[str, Any]],
    limit_km: float = 3.0,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_km = limit_km
    for item in locations:
        km = _haversine_km(lat, lng, item["lat"], item["lng"])
        if km < best_km:
            best = item
            best_km = km
    return best


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlng / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def _place_incident(
    message: dict[str, Any],
    district_id: str,
    locations: list[dict[str, Any]],
    points: dict[str, list[dict[str, Any]]],
    landmark_points: dict[str, list[dict[str, Any]]],
    coord: tuple[float, float] | None,
) -> dict[str, Any]:
    lat: float | None = None
    lng: float | None = None
    located_by = ""
    if coord is not None and district_id:
        lat, lng = coord
        located_by = "座標"
    else:
        chosen = landmark_points.get(district_id) or points.get(district_id) or []
        if chosen:
            lat = sum(item["lat"] for item in chosen) / len(chosen)
            lng = sum(item["lng"] for item in chosen) / len(chosen)
            located_by = "地標" if landmark_points.get(district_id) else "路名"
        elif district_id:
            pool = [item for item in locations if item["district_id"] == district_id]
            if pool:
                lat = sum(item["lat"] for item in pool) / len(pool)
                lng = sum(item["lng"] for item in pool) / len(pool)
                located_by = "地區"
    meta = DISTRICT_BY_ID.get(district_id)
    return {
        "id": message.get("id") or "",
        "text": message.get("text") or "",
        "time": message.get("time") or "",
        "kind": incident_kind(message.get("text") or ""),
        "district_id": district_id,
        "district_name": meta["name"] if meta else "",
        "region": meta["region"] if meta else "",
        "region_name": REGION_BY_ID[meta["region"]]["name"] if meta else "",
        "lat": None if lat is None else round(float(lat), 6),
        "lng": None if lng is None else round(float(lng), 6),
        "located_by": located_by,
    }


def match_incident(message: dict[str, Any], locations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text = " ".join(
        part
        for part in (message.get("text"), message.get("location"), message.get("landmark"))
        if part
    )
    compact = _compact(text)
    scores: dict[str, int] = defaultdict(int)
    points: dict[str, list[dict[str, Any]]] = defaultdict(list)
    landmark_points: dict[str, list[dict[str, Any]]] = defaultdict(list)

    seen_roads: set[tuple[str, str]] = set()
    seen_landmarks: set[tuple[str, str]] = set()
    for item in locations:
        road = item.get("road_key") or ""
        if len(road) < 3 or road in GENERIC_ROAD_KEYS:
            continue
        if road not in compact:
            continue
        key = (item["district_id"], road)
        if key not in seen_roads:
            scores[item["district_id"]] += 10
            seen_roads.add(key)
        points[item["district_id"]].append(item)
        landmark = item.get("landmark") or ""
        if len(landmark) >= 2 and landmark not in GENERIC_LANDMARKS and landmark in compact:
            pair = (item["district_id"], landmark)
            if pair not in seen_landmarks:
                scores[item["district_id"]] += 15
                seen_landmarks.add(pair)
            landmark_points[item["district_id"]].append(item)

    for district_id, words in DISTRICT_KEYWORDS.items():
        if any(word and word in compact for word in words):
            scores[district_id] += 6

    coord: tuple[float, float] | None = None
    winners: list[str]
    lat = message.get("lat")
    lng = message.get("lng")
    if isinstance(lat, float) and isinstance(lng, float):
        nearest = _nearest_detector(lat, lng, locations)
        if nearest:
            winners = [nearest["district_id"]]
            coord = (lat, lng)
        else:
            winners = []
    else:
        winners = []
    if not winners and scores:
        best = max(scores.values())
        winners = [key for key, value in scores.items() if value == best]
    if not winners:
        winners = [""]
    return [
        _place_incident(message, district_id, locations, points, landmark_points, coord)
        for district_id in winners
    ]


def normalize_selection(region: str, district: str) -> tuple[str, str]:
    if region not in REGION_BY_ID:
        region = "all"
    if district not in DISTRICT_BY_ID:
        district = "all"
    if district != "all" and region != "all":
        if DISTRICT_BY_ID[district]["region"] != region:
            district = "all"
    return region, district


def _in_selection(item: dict[str, Any], region: str, district: str) -> bool:
    if district != "all":
        return item.get("district_id") == district
    if region != "all":
        return item.get("region") == region
    return True


def _summary(detectors: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {key: 0 for key in ("smooth", "slow", "jam", "unknown")}
    speed_sum = 0.0
    speed_n = 0
    for item in detectors:
        status = item.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
        if item.get("speed") is not None and status != "unknown":
            speed_sum += float(item["speed"])
            speed_n += 1
    known = counts["smooth"] + counts["slow"] + counts["jam"]
    return {
        "total": len(detectors),
        "smooth": counts["smooth"],
        "slow": counts["slow"],
        "jam": counts["jam"],
        "unknown": counts["unknown"],
        "avgSpeed": None if speed_n == 0 else round(speed_sum / speed_n, 1),
        "jamRatio": 0 if known == 0 else round(counts["jam"] / known, 3),
    }


def _slow_roads(detectors: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    """列出實質緩慢或擠塞的路段。

    停住的車流交通量通常很低，不能用交通量加權去代表整條路，
    否則暢通路段會把擠塞點的車速拉高。
    """
    groups: dict[str, dict[str, Any]] = {}
    rank = {"jam": 0, "slow": 1}
    for item in detectors:
        if item.get("status") not in rank or item.get("speed") is None:
            continue
        name = item.get("road_key") or item.get("name") or item["id"]
        group_key = f"{item.get('district_id') or ''}|{name}"
        group = groups.setdefault(group_key, {"name": name, "samples": []})
        group["samples"].append(item)
    rows: list[dict[str, Any]] = []
    for group in groups.values():
        worst = min(
            group["samples"],
            key=lambda item: (
                rank[item["status"]],
                float(item["speed"]),
                -float(item.get("occupancy") or 0),
            ),
        )
        label = group["name"]
        landmark = worst.get("landmark") or ""
        if landmark:
            label = f"{group['name']}近{landmark}"
        rows.append(
            {
                "name": label,
                "district_name": worst.get("district_name") or "",
                "speed": worst["speed"],
                "status": worst["status"],
                "count": len(group["samples"]),
            }
        )
    rows.sort(key=lambda row: (rank[row["status"]], row["speed"]))
    return rows[:limit]


def build_payload(
    locations: list[dict[str, Any]],
    speeds: dict[str, dict[str, Any]],
    updated: str,
    news: list[dict[str, Any]],
    region: str,
    district: str,
    error: str = "",
) -> dict[str, Any]:
    region, district = normalize_selection(region, district)
    incidents: list[dict[str, Any]] = []
    for item in news:
        incidents.extend(match_incident(item, locations))

    enriched: list[dict[str, Any]] = []
    for item in locations:
        reading = speeds.get(item["id"]) or {
            "speed": None,
            "occupancy": None,
            "volume": 0,
            "status": "unknown",
        }
        enriched.append({**item, **reading})

    def pack(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item["id"],
            "name": item["name"],
            "road_key": item["road_key"],
            "lat": item["lat"],
            "lng": item["lng"],
            "speed": item["speed"],
            "occupancy": item["occupancy"],
            "volume": item["volume"],
            "status": item["status"],
            "district_id": item["district_id"],
            "district_name": item["district_name"],
            "region": item["region"],
            "region_name": item["region_name"],
        }

    scoped = [item for item in enriched if _in_selection(item, region, "all")]
    selected = [item for item in scoped if _in_selection(item, region, district)]
    breakdown = []
    for meta in DISTRICT_BY_ID.values():
        if region != "all" and meta["region"] != region:
            continue
        members = [item for item in enriched if item["district_id"] == meta["id"]]
        active_news = [
            item
            for item in incidents
            if item["district_id"] == meta["id"] and item["kind"] in ("jam", "slow", "closed")
        ]
        row = _summary(members)
        row.update(
            {
                "id": meta["id"],
                "name": meta["name"],
                "region": meta["region"],
                "region_name": REGION_BY_ID[meta["region"]]["name"],
                "incidents": len(active_news),
            }
        )
        breakdown.append(row)

    selected_incidents = [item for item in incidents if _in_selection(item, region, district)]
    kind_rank = {"jam": 0, "closed": 1, "slow": 2, "clear": 3, "info": 4}
    selected_incidents.sort(
        key=lambda item: (kind_rank.get(item["kind"], 9), item.get("district_name") or "")
    )
    return {
        "ok": not error,
        "error": error,
        "updated": updated,
        "region": region,
        "district": district,
        "regions": [{"id": item[0], "name": item[1]} for item in REGIONS],
        "districts": [
            {"id": item[0], "name": item[2], "region": item[1]} for item in DISTRICTS
        ],
        "summary": _summary(selected),
        "breakdown": breakdown,
        "slowRoads": _slow_roads(selected),
        "detectors": [pack(item) for item in selected],
        "incidents": selected_incidents,
    }


def format_summary(payload: dict[str, Any]) -> str:
    region_name = "全港"
    if payload["region"] in REGION_BY_ID:
        region_name = REGION_BY_ID[payload["region"]]["name"]
    district_name = "全部地區"
    if payload["district"] in DISTRICT_BY_ID:
        district_name = DISTRICT_BY_ID[payload["district"]]["name"]
    summary = payload["summary"]
    avg = "—" if summary["avgSpeed"] is None else f"{summary['avgSpeed']} km/h"
    lines = [
        f"香港交通狀況　{region_name} / {district_name}",
        f"資料時間：{payload.get('updated') or '未能取得'}",
        (
            f"探測器 {summary['total']}　暢通 {summary['smooth']}　"
            f"緩慢 {summary['slow']}　擠塞 {summary['jam']}　"
            f"無數據 {summary['unknown']}　平均車速 {avg}"
        ),
    ]
    if payload.get("error"):
        lines.append(f"錯誤：{payload['error']}")
    if payload["slowRoads"]:
        lines.append("車速較低路段：")
        for road in payload["slowRoads"]:
            lines.append(
                f"  {STATUS_LABEL.get(road['status'], road['status'])}　"
                f"{road['name']}（{road['district_name']}）{road['speed']} km/h"
            )
    if payload["incidents"]:
        lines.append("特別交通消息：")
        for item in payload["incidents"]:
            where = item["district_name"] or "未對應地區"
            lines.append(f"  [{STATUS_LABEL.get(item['kind'], item['kind'])}] {where}　{item['text']}")
    else:
        lines.append("特別交通消息：所選範圍暫無對應消息")
    return "\n".join(lines)


class TrafficStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._locations: list[dict[str, Any]] | None = None
        self._locations_at = 0.0
        self._speeds: dict[str, dict[str, Any]] | None = None
        self._updated = ""
        self._speeds_at = 0.0
        self._news: list[dict[str, Any]] | None = None
        self._news_at = 0.0

    def snapshot(self, region: str, district: str) -> dict[str, Any]:
        error = ""
        try:
            locations = self.locations()
        except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as exc:
            return build_payload([], {}, "", [], region, district, error=f"未能下載探測器位置：{exc}")
        try:
            updated, speeds = self.speeds()
        except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as exc:
            updated, speeds = "", {}
            error = f"未能下載即時車速：{exc}"
        try:
            news = self.news()
        except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as exc:
            news = []
            extra = f"未能下載特別交通消息：{exc}"
            error = f"{error} {extra}".strip()
        return build_payload(locations, speeds, updated, news, region, district, error=error)

    def locations(self) -> list[dict[str, Any]]:
        with self._lock:
            cached = self._locations
            fresh = cached is not None and time.time() - self._locations_at < 6 * 3600
        if fresh and cached is not None:
            return cached
        loaded = load_locations()
        with self._lock:
            self._locations = loaded
            self._locations_at = time.time()
        return loaded

    def speeds(self) -> tuple[str, dict[str, dict[str, Any]]]:
        with self._lock:
            cached = self._speeds
            updated = self._updated
            fresh = cached is not None and time.time() - self._speeds_at < 30
        if fresh and cached is not None:
            return updated, cached
        stamp, loaded = parse_speeds(fetch_bytes(SPEED_URL))
        with self._lock:
            self._speeds = loaded
            self._updated = stamp
            self._speeds_at = time.time()
        return stamp, loaded

    def news(self) -> list[dict[str, Any]]:
        with self._lock:
            cached = self._news
            fresh = cached is not None and time.time() - self._news_at < 45
        if fresh and cached is not None:
            return cached
        documents: list[tuple[str, bytes]] = []
        errors: list[str] = []
        for url in NEWS_URLS:
            try:
                documents.append((url, fetch_bytes(url)))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                errors.append(f"{url} ({exc})")
        loaded = parse_news(documents)
        if not documents and errors:
            raise urllib.error.URLError("；".join(errors))
        with self._lock:
            self._news = loaded
            self._news_at = time.time()
        return loaded


STORE = TrafficStore()


class TrafficHandler(BaseHTTPRequestHandler):
    server_version = "TrafficMap/1.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)
            return
        if parsed.path == "/api/traffic":
            query = parse_qs(parsed.query)
            region = (query.get("region") or ["all"])[0]
            district = (query.get("district") or ["all"])[0]
            payload = STORE.snapshot(region, district)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body, no_store=True)
            return
        self._send(404, "text/plain; charset=utf-8", "找不到頁面".encode("utf-8"))

    def _send(self, status: int, content_type: str, body: bytes, no_store: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


PAGE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>香港分區交通狀況</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
  :root {
    --bg: #e7eef3;
    --panel: #ffffff;
    --ink: #1b2833;
    --muted: #5d6d7c;
    --line: #d5e0e8;
    --header: #14324c;
    --smooth: #1f9d55;
    --slow: #d99700;
    --jam: #d64545;
    --unknown: #8b98a5;
    --closed: #9a3412;
    --clear: #3d7a63;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    font-family: "PingFang HK", "Microsoft JhengHei", "Noto Sans TC", sans-serif;
    color: var(--ink);
    background: var(--bg);
  }
  header {
    background: var(--header);
    color: white;
    padding: 14px 20px;
    display: flex;
    justify-content: space-between;
    gap: 16px;
    align-items: center;
  }
  header h1 { font-size: 20px; margin: 0 0 4px; font-weight: 650; }
  header p { margin: 0; color: #d5e4ef; font-size: 13px; }
  header .meta { text-align: right; font-size: 13px; color: #d5e4ef; }
  button, select { font: inherit; }
  main { display: flex; height: calc(100% - 72px); min-height: 520px; }
  aside {
    width: 360px;
    flex: none;
    background: var(--panel);
    border-right: 1px solid var(--line);
    overflow: auto;
    padding: 16px;
  }
  .map-wrap { flex: 1; min-width: 0; position: relative; }
  #map { height: 100%; width: 100%; background: #d7e2ea; }
  label { display: block; font-size: 13px; color: var(--muted); margin: 12px 0 6px; }
  select, .refresh {
    width: 100%;
    border: 1px solid var(--line);
    background: white;
    border-radius: 8px;
    padding: 8px 10px;
  }
  .refresh { margin-top: 12px; cursor: pointer; color: var(--header); }
  .cards { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 14px; }
  .card { border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px; }
  .card b { display: block; font-size: 18px; }
  .card span { color: var(--muted); font-size: 12px; }
  .smooth { color: var(--smooth); }
  .slow { color: var(--slow); }
  .jam { color: var(--jam); }
  .unknown { color: var(--unknown); }
  .closed { color: var(--closed); }
  .clear { color: var(--clear); }
  .info { color: #3b6d8c; }
  h2 { font-size: 14px; margin: 18px 0 8px; }
  .district-row, .road, .incident {
    width: 100%;
    text-align: left;
    border: 1px solid var(--line);
    background: white;
    border-radius: 10px;
    padding: 8px 10px;
    margin-bottom: 8px;
    cursor: pointer;
  }
  .district-row.active { border-color: #14324c; box-shadow: inset 3px 0 0 #14324c; }
  .row-top, .road, .incident { display: flex; justify-content: space-between; gap: 8px; }
  .name { font-weight: 650; }
  .sub { color: var(--muted); font-size: 12px; }
  .bar { display: flex; height: 8px; border-radius: 99px; overflow: hidden; background: #eef2f5; margin-top: 6px; }
  .bar i { display: block; height: 100%; }
  .legend {
    position: absolute; z-index: 500; left: 12px; bottom: 12px;
    background: white; border-radius: 10px; padding: 8px 10px;
    box-shadow: 0 4px 16px rgba(20, 40, 60, 0.15); font-size: 12px;
  }
  .legend div { display: flex; align-items: center; gap: 6px; margin: 3px 0; }
  .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  .banner {
    background: #fff4f4; color: #8f2d2d; border: 1px solid #f0c8c8;
    border-radius: 8px; padding: 8px 10px; margin-top: 12px; font-size: 13px;
  }
  .empty { color: var(--muted); font-size: 13px; }
  .incident p, .road p { margin: 4px 0 0; }
  .badge { font-size: 12px; font-weight: 650; white-space: nowrap; }
  @media (max-width: 800px) {
    main { flex-direction: column; height: auto; }
    aside { width: auto; border-right: 0; border-bottom: 1px solid var(--line); }
    #map { height: 68vh; }
  }
</style>
</head>
<body>
<header>
  <div>
    <h1>香港分區交通狀況</h1>
    <p>先選區域，再選地區。地圖顯示該範圍的策略性道路車速同特別交通消息。</p>
  </div>
  <div class="meta" id="updated">正在讀取資料…</div>
</header>
<main>
  <aside>
    <label for="region">區域</label>
    <select id="region"></select>
    <label for="district">地區</label>
    <select id="district"></select>
    <button class="refresh" id="refresh" type="button">重新整理</button>
    <div id="error"></div>
    <div class="cards" id="cards"></div>
    <h2>地區概況</h2>
    <div id="breakdown"></div>
    <h2>車速較低路段</h2>
    <div id="roads"></div>
    <h2>特別交通消息</h2>
    <div id="incidents"></div>
  </aside>
  <section class="map-wrap">
    <div id="map"></div>
    <div class="legend">
      <div><i class="dot" style="background:#1f9d55"></i>暢通</div>
      <div><i class="dot" style="background:#d99700"></i>緩慢</div>
      <div><i class="dot" style="background:#d64545"></i>擠塞</div>
      <div><i class="dot" style="background:#8b98a5"></i>無數據</div>
    </div>
  </section>
</main>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const STATUS = {smooth:"暢通", slow:"緩慢", jam:"擠塞", unknown:"無數據", closed:"封閉", clear:"已解封", info:"消息"};
const COLOR = {smooth:"#1f9d55", slow:"#d99700", jam:"#d64545", unknown:"#8b98a5", closed:"#9a3412", clear:"#3d7a63", info:"#3b6d8c"};
const state = { region: "all", district: "all", map: null, detectors: null, incidents: null, ready: false };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));
}

function params() {
  const query = new URLSearchParams(location.search);
  state.region = query.get("region") || "all";
  state.district = query.get("district") || "all";
}

function remember() {
  const url = new URL(location.href);
  url.searchParams.set("region", state.region);
  url.searchParams.set("district", state.district);
  history.replaceState(null, "", url);
}

function setupMap() {
  state.map = L.map("map", { preferCanvas: true }).setView([22.35, 114.15], 11);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap &copy; CARTO"
  }).addTo(state.map);
  state.detectors = L.layerGroup().addTo(state.map);
  state.incidents = L.layerGroup().addTo(state.map);
}

function fillSelects(data) {
  const region = document.getElementById("region");
  const district = document.getElementById("district");
  const regionValue = data.region || "all";
  const districtValue = data.district || "all";
  region.innerHTML = '<option value="all">全部區域</option>' + data.regions.map((item) =>
    `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join("");
  const visible = data.districts.filter((item) => regionValue === "all" || item.region === regionValue);
  district.innerHTML = '<option value="all">全部地區</option>' + visible.map((item) =>
    `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join("");
  region.value = regionValue;
  district.value = visible.some((item) => item.id === districtValue) ? districtValue : "all";
  state.region = region.value;
  state.district = district.value;
}

function renderSummary(summary) {
  const avg = summary.avgSpeed == null ? "—" : summary.avgSpeed;
  document.getElementById("cards").innerHTML = [
    ["探測器", summary.total, ""],
    ["平均車速", avg, ""],
    ["暢通", summary.smooth, "smooth"],
    ["緩慢", summary.slow, "slow"],
    ["擠塞", summary.jam, "jam"],
    ["無數據", summary.unknown, "unknown"]
  ].map(([label, value, cls]) =>
    `<div class="card"><b class="${cls}">${esc(value)}</b><span>${esc(label)}</span></div>`).join("");
}

function bar(row) {
  const known = Math.max(row.total, 1);
  return `<div class="bar">
    <i style="width:${row.smooth / known * 100}%;background:#1f9d55"></i>
    <i style="width:${row.slow / known * 100}%;background:#d99700"></i>
    <i style="width:${row.jam / known * 100}%;background:#d64545"></i>
    <i style="width:${row.unknown / known * 100}%;background:#8b98a5"></i>
  </div>`;
}

function renderBreakdown(rows) {
  const box = document.getElementById("breakdown");
  if (!rows.length) {
    box.innerHTML = '<p class="empty">沒有地區資料</p>';
    return;
  }
  box.innerHTML = rows.map((row) => {
    const avg = row.avgSpeed == null ? "—" : `${row.avgSpeed} km/h`;
    const news = row.incidents ? `　消息 ${row.incidents}` : "";
    const active = row.id === state.district ? " active" : "";
    return `<button class="district-row${active}" type="button" data-region="${esc(row.region)}" data-district="${esc(row.id)}">
      <div class="row-top"><span class="name">${esc(row.name)}</span><span class="sub">${esc(avg)}${esc(news)}</span></div>
      ${bar(row)}
    </button>`;
  }).join("");
  box.querySelectorAll(".district-row").forEach((button) => {
    button.addEventListener("click", () => {
      state.region = button.dataset.region;
      state.district = button.dataset.district;
      remember();
      load(true);
    });
  });
}

function renderRoads(roads) {
  const box = document.getElementById("roads");
  if (!roads.length) {
    box.innerHTML = '<p class="empty">所選範圍未見明顯緩慢或擠塞路段</p>';
    return;
  }
  box.innerHTML = roads.map((road) =>
    `<div class="road"><div><b>${esc(road.name)}</b><p class="sub">${esc(road.district_name)}　相關探測器 ${esc(road.count)}</p></div>
     <span class="badge ${esc(road.status)}">${esc(STATUS[road.status] || road.status)} ${esc(road.speed)} km/h</span></div>`).join("");
}

function renderIncidents(items) {
  const box = document.getElementById("incidents");
  if (!items.length) {
    box.innerHTML = '<p class="empty">所選範圍暫無對應的特別交通消息</p>';
    return;
  }
  box.innerHTML = items.map((item, index) =>
    `<button class="incident" type="button" data-index="${index}">
      <div><b>${esc(item.district_name || "未對應地區")}</b><p>${esc(item.text)}</p>
      <p class="sub">${esc(item.time || "")}${item.located_by ? "　位置依據：" + esc(item.located_by) : ""}</p></div>
      <span class="badge ${esc(item.kind)}">${esc(STATUS[item.kind] || item.kind)}</span>
    </button>`).join("");
  box.querySelectorAll(".incident").forEach((button) => {
    button.addEventListener("click", () => {
      const item = items[Number(button.dataset.index)];
      if (item.lat != null && item.lng != null) state.map.setView([item.lat, item.lng], 15);
    });
  });
}

function draw(data, fit) {
  state.detectors.clearLayers();
  state.incidents.clearLayers();
  const bounds = [];
  data.detectors.forEach((item) => {
    const color = COLOR[item.status] || COLOR.unknown;
    const marker = L.circleMarker([item.lat, item.lng], {
      radius: item.status === "jam" ? 8 : 6,
      color: "#ffffff",
      weight: 1,
      fillColor: color,
      fillOpacity: 0.92
    });
    const speed = item.speed == null ? "—" : `${item.speed} km/h`;
    const occ = item.occupancy == null ? "—" : `${item.occupancy}%`;
    marker.bindPopup(
      `<b>${esc(item.name)}</b><br>${esc(item.region_name)} ${esc(item.district_name)}<br>` +
      `狀況：${esc(STATUS[item.status] || item.status)}<br>車速：${esc(speed)}<br>` +
      `佔用率：${esc(occ)}<br>交通量：${esc(item.volume)}`
    );
    marker.addTo(state.detectors);
    bounds.push([item.lat, item.lng]);
  });
  data.incidents.forEach((item) => {
    if (item.lat == null || item.lng == null || item.kind === "clear" || item.kind === "info") return;
    const marker = L.circleMarker([item.lat, item.lng], {
      radius: 11,
      color: COLOR[item.kind] || COLOR.jam,
      weight: 3,
      fillColor: "#ffffff",
      fillOpacity: 0.95
    });
    marker.bindPopup(`<b>特別交通消息</b><br>${esc(item.district_name)}<br>${esc(item.text)}`);
    marker.addTo(state.incidents);
    bounds.push([item.lat, item.lng]);
  });
  if (fit && bounds.length) {
    state.map.fitBounds(bounds, { padding: [28, 28], maxZoom: 14 });
  }
}

async function load(fit) {
  const updated = document.getElementById("updated");
  updated.textContent = "正在讀取資料…";
  const url = `/api/traffic?region=${encodeURIComponent(state.region)}&district=${encodeURIComponent(state.district)}`;
  try {
    const response = await fetch(url, { cache: "no-store" });
    const data = await response.json();
    fillSelects(data);
    document.getElementById("error").innerHTML = data.error ? `<div class="banner">${esc(data.error)}</div>` : "";
    updated.textContent = data.updated ? `資料時間 ${data.updated}` : "未能取得資料時間";
    renderSummary(data.summary);
    renderBreakdown(data.breakdown);
    renderRoads(data.slowRoads);
    renderIncidents(data.incidents);
    draw(data, fit);
  } catch (error) {
    updated.textContent = "讀取失敗";
    document.getElementById("error").innerHTML = `<div class="banner">${esc(error.message || error)}</div>`;
  }
}

params();
setupMap();
document.getElementById("region").addEventListener("change", (event) => {
  state.region = event.target.value;
  state.district = "all";
  remember();
  load(true);
});
document.getElementById("district").addEventListener("change", (event) => {
  state.district = event.target.value;
  remember();
  load(true);
});
document.getElementById("refresh").addEventListener("click", () => load(false));
load(true);
setInterval(() => load(false), 60000);
</script>
</body>
</html>
"""


def serve(host: str, port: int, open_browser: bool) -> None:
    httpd = ThreadingHTTPServer((host, port), TrafficHandler)
    url = f"http://{host}:{port}/"
    print(f"交通地圖已啟動：{url}")
    print("按 Ctrl+C 結束")
    if open_browser and (os.environ.get("DISPLAY") or sys.platform != "linux"):
        webbrowser.open(url)
    elif open_browser:
        print("目前環境沒有圖形介面，請用瀏覽器開啟上面的網址。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已結束")
    finally:
        httpd.server_close()


def run_self_test() -> None:
    assert split_road("粉嶺公路近彩園邨 - 南行") == ("粉嶺公路", "彩園邨")
    assert split_road("香港仔海旁道近福群大樓 - 東行")[0] == "香港仔海旁道"
    assert classify(80, 5, 12) == "smooth"
    assert classify(12, 8, 6) == "jam"
    assert classify(0, 0, 0) == "smooth"
    assert classify(32, 8, 4) == "slow"
    assert classify(55, 40, 3) == "jam"
    assert classify(None, None, None) == "unknown"
    assert incident_kind("較早前封閉的行車線現已解封。") == "clear"
    assert incident_kind("現時上址交通擠塞。") == "jam"
    assert incident_kind("慢線現已封閉。") == "closed"

    speed_xml = """<?xml version="1.0" encoding="utf-8"?>
    <raw_speed_volume_list><date>2026-09-28</date><periods><period>
      <period_from>09:16:00</period_from><period_to>09:16:30</period_to>
      <detectors>
        <detector><detector_id>A1</detector_id><lanes>
          <lane><speed>10</speed><occupancy>40</occupancy><volume>4</volume><valid>Y</valid></lane>
          <lane><speed>80</speed><occupancy>1</occupancy><volume>0</volume><valid>N</valid></lane>
        </lanes></detector>
        <detector><detector_id>A2</detector_id><lanes>
          <lane><speed>70</speed><occupancy>4</occupancy><volume>8</volume><valid>Y</valid></lane>
        </lanes></detector>
      </detectors>
    </period></periods></raw_speed_volume_list>
    """.encode("utf-8")
    stamp, speeds = parse_speeds(speed_xml)
    assert stamp == "2026-09-28 09:16:30"
    assert speeds["A1"]["status"] == "jam"
    assert speeds["A2"]["status"] == "smooth"

    locations = [
        {
            "id": "N1",
            "district_id": "north",
            "district_name": "北區",
            "region": "nt",
            "region_name": "新界",
            "name": "粉嶺公路近彩園邨 - 南行",
            "road_key": "粉嶺公路",
            "landmark": "彩園邨",
            "lat": 22.501,
            "lng": 114.128,
            "direction": "South",
        },
        {
            "id": "T1",
            "district_id": "tsuen-wan",
            "district_name": "荃灣區",
            "region": "nt",
            "region_name": "新界",
            "name": "荃灣路近荃灣公園 - 東行",
            "road_key": "荃灣路",
            "landmark": "荃灣公園",
            "lat": 22.371,
            "lng": 114.113,
            "direction": "East",
        },
    ]
    matched = match_incident(
        {"id": "1", "text": "因交通意外，粉嶺公路(往九龍方向)近彩園邨的全線現已封閉。", "time": "", "lat": None, "lng": None, "location": "", "landmark": ""},
        locations,
    )
    assert len(matched) == 1, matched
    assert matched[0]["district_id"] == "north", matched
    assert matched[0]["kind"] == "closed"
    assert matched[0]["lat"] is not None
    tied = match_incident(
        {"id": "2", "text": "馬灣至中環及荃灣的渡輪班次有所調整。", "time": "", "lat": None, "lng": None, "location": "", "landmark": ""},
        [],
    )
    assert {item["district_id"] for item in tied} == {"central-western", "tsuen-wan"}, tied
    assert tied[0]["kind"] == "info"

    payload = build_payload(
        locations,
        {"N1": {"speed": 15, "occupancy": 40, "volume": 4, "status": "jam"}},
        "2026-09-28 09:16:30",
        [{"id": "1", "text": "粉嶺公路近彩園邨全線封閉，交通擠塞。", "time": "09:16", "lat": None, "lng": None, "location": "", "landmark": ""}],
        "nt",
        "north",
    )
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["jam"] == 1
    assert payload["detectors"][0]["district_name"] == "北區"
    assert payload["incidents"][0]["district_id"] == "north"
    assert normalize_selection("hk", "north") == ("hk", "all")
    assert "北區" in format_summary(payload)
    slow = _slow_roads(
        [
            {
                "road_key": "粉嶺公路",
                "landmark": "彩園邨",
                "district_name": "北區",
                "status": "smooth",
                "speed": 80,
                "volume": 30,
                "occupancy": 4,
            },
            {
                "road_key": "粉嶺公路",
                "landmark": "古洞",
                "district_name": "北區",
                "status": "jam",
                "speed": 8,
                "volume": 2,
                "occupancy": 70,
            },
        ]
    )
    assert slow[0]["speed"] == 8
    assert slow[0]["status"] == "jam"
    assert "古洞" in slow[0]["name"]

    news_xml = """<?xml version="1.0"?>
    <body xmlns="http://data.one.gov.hk/td">
      <message><msgID>1</msgID><ChinText>粉嶺公路近彩園邨封閉。</ChinText><ReferenceDate>a</ReferenceDate></message>
      <message><msgID>2</msgID><ChinText>荃灣路近荃灣公園交通繁忙。</ChinText><ReferenceDate>b</ReferenceDate></message>
    </body>
    """.encode("utf-8")
    parsed = parse_news([("https://resource.data.one.gov.hk/td/en/specialtrafficnews.xml", news_xml)])
    assert len(parsed) == 2, parsed
    assert {item["text"] for item in parsed} == {"粉嶺公路近彩園邨封閉。", "荃灣路近荃灣公園交通繁忙。"}
    print("self-test 通過")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="香港分區交通狀況地圖")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--once", action="store_true", help="只印出文字摘要，不啟動網頁")
    parser.add_argument("--region", default="all", help="hk、kln、nt 或 all")
    parser.add_argument("--district", default="all", help="地區代號，例如 north、sha-tin")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        run_self_test()
        return 0
    if args.once:
        payload = STORE.snapshot(args.region, args.district)
        print(format_summary(payload))
        return 0 if payload.get("ok") or payload["summary"]["total"] else 1
    try:
        serve(args.host, args.port, open_browser=not args.no_browser)
    except OSError as exc:
        print(f"無法啟動伺服器：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
