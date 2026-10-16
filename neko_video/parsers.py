"""Pure official-response parsers. No credentials, HTTP clients, or SDK calls."""

import json
import re

from .presentation import duration


def object_after(text, marker):
    offset = text.find(marker)
    if offset < 0:
        return {}
    start = text.find("{", offset + len(marker))
    if start < 0:
        return {}
    raw = text[start : start + 200000]
    for candidate in [raw, raw.replace('\\"', '"')]:
        try:
            value, _ = json.JSONDecoder().raw_decode(candidate)
            if isinstance(value, dict):
                return value
        except ValueError:
            continue
    return {}


def basic_douyin(text):
    data = object_after(text, "window._ROUTER_DATA")
    loader = data.get("loaderData") or {}
    page = loader.get("video_(id)/page") or loader.get("note_(id)/page") or {}
    entries = (page.get("videoInfoRes") or {}).get("item_list") or []
    if not entries or not isinstance(entries[0], dict):
        return None
    item = entries[0]
    milliseconds = (item.get("video") or {}).get("duration") or 0
    return {
        "title": str(item.get("desc") or "")[:2000],
        "description": str(item.get("desc") or "")[:200],
        "author": str((item.get("author") or {}).get("nickname") or "未知")[:120],
        "duration": milliseconds // 1000 if milliseconds > 1000 else milliseconds,
    }


def chapter_douyin(text):
    data = object_after(text, '"chapterInfo"')
    chapters = []
    for entry in (data.get("list") or [])[:40]:
        if not isinstance(entry, dict):
            continue
        chapters.append(
            {
                "time": duration((entry.get("timestamp") or 0) // 1000),
                "desc": str(entry.get("desc") or "")[:1000],
                "detail": str(entry.get("detail") or "")[:2000],
            }
        )
    return {"chapters": chapters, "chapter_abstract": str(data.get("chapterAbstract") or "")[:2000]}


def clean_html(text):
    return re.sub(r"<[^>]+>", "", text).strip()


def generation_douyin(payload):
    summary, highlights = [], []
    if payload.get("status_code") != 0:
        return {"summary": "", "highlights": []}
    for card in (payload.get("business_data") or [])[:100]:
        if not isinstance(card, dict):
            continue
        for entry in ((card.get("data") or {}).get("bytesync_data") or [])[:100]:
            try:
                data = json.loads(entry) if isinstance(entry, str) else entry
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            if card.get("card_id") == "ai_chat_message_lynx":
                for span in ((data.get("display") or {}).get("generation_spans") or [])[:100]:
                    value = (span.get("text") or {}).get("content")
                    if isinstance(value, str):
                        summary.append(value[:6000])
            elif card.get("card_id") == "ask_ai_high_light_clip":
                for item in (data.get("data") or [])[:30]:
                    if isinstance(item, dict) and item.get("title"):
                        highlights.append(
                            {
                                "time": duration(item.get("startTime")),
                                "title": str(item["title"])[:1000],
                            }
                        )
    return {"summary": clean_html("".join(summary))[:6000], "highlights": highlights[:30]}


def stream_douyin(raw):
    tokens = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line[5:].strip())
            for entry in (payload.get("data") or [])[:100]:
                display = (entry.get("display") or {}).get("display") or {}
                for span in (display.get("generation_spans") or [])[:100]:
                    value = (span.get("text") or {}).get("content")
                    if isinstance(value, str):
                        tokens.append(value[:6000])
        except (ValueError, AttributeError, TypeError):
            continue
    text = clean_html("".join(tokens))[:6000]
    refusals = (
        "无法为您提供",
        "无法对您提到",
        "无法确定",
        "无法总结",
        "无法进行总结",
        "无法定位",
        "信息不足",
        "过于模糊",
        "缺乏可供识别",
        "无法唯一确定",
        "建议直接观看",
        "建议补充视频",
    )
    return "" if any(phrase in text[:150] for phrase in refusals) else text
