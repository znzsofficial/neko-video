"""Bounded text presentation. Platform evidence stays separate from instructions."""


def bounded(text, limit):
    marker = "\n[内容过长，已截断]"
    return text if len(text) <= limit else text[: limit - len(marker)] + marker


def duration(value):
    try:
        seconds = max(0, int(value or 0))
        return f"{seconds // 60}:{seconds % 60:02d}"
    except (ValueError, TypeError):
        return "未知"


def render(info, *, injected=False):
    platform = "B站" if info["platform"] == "bilibili" else "抖音"
    lines = [
        f"[{platform}视频信息] {info.get('title', '')}",
        f"作者: {info.get('author', '未知')} / 时长: {duration(info.get('duration'))}",
    ]
    if info.get("summary"):
        lines += ["[平台AI总结]", info["summary"]]
    else:
        lines.append("AI总结: 暂无")
    if info.get("description") and info["description"] != info.get("title"):
        lines.append("简介: " + info["description"])
    if info.get("chapter_abstract"):
        lines.append("章节概述: " + info["chapter_abstract"])
    for chapter in info.get("chapters", [])[:40]:
        lines.append(
            f"- {chapter.get('time', '')} {chapter.get('desc', '')}: {chapter.get('detail', '')}"
        )
    for highlight in info.get("highlights", [])[:30]:
        lines.append(f"- {highlight.get('time', '')} {highlight.get('title', '')}")
    if not injected and info.get("url"):
        lines.append("链接: " + info["url"])
    return bounded("\n".join(lines), 6000 if injected else 12000)
