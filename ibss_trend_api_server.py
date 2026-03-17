import html
import math
import os
import re
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="IBSS Trend API", version="0.1.0")

BILIBILI_SEARCH_URL = "https://api.bilibili.com/x/web-interface/search/type"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class TrendRequest(BaseModel):
    time_window: str = "7d"
    target_audience: str = "IBSS students"
    content_focus: str = "AI literacy"
    query_seed: str = ""
    demo_mode: str = "false"
    limit: int = 8


MOCK_TRENDS = [
    {
        "title": "AI Agent 工具爆火，大学生到底该怎么用？",
        "source": "mock_bilibili",
        "url": "https://www.bilibili.com",
        "summary": "围绕 AI agent 的能力、效率和误区持续升温，适合转化为 AI literacy 选题。",
        "hot_score": 95,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "raw_tags": ["AI", "Agent", "AI literacy"],
    },
    {
        "title": "Python 自动化又火了：不会代码也能搭工作流吗？",
        "source": "mock_bilibili",
        "url": "https://www.bilibili.com",
        "summary": "从低代码和 Python 自动化切入，适合给学生做 workflow automation 入门内容。",
        "hot_score": 90,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "raw_tags": ["Python", "automation", "workflow"],
    },
    {
        "title": "求职季 AI 简历工具刷屏，但学生最该学的不是一键生成",
        "source": "mock_bilibili",
        "url": "https://www.bilibili.com",
        "summary": "把 AI 求职热点转化为 employability 和 human review 的教育内容。",
        "hot_score": 88,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "raw_tags": ["AI求职", "employability"],
    },
]


def clean_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<.*?>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def focus_queries(content_focus: str, query_seed: str) -> List[str]:
    mapping = {
        "AI literacy": ["AI", "AI工具", "人工智能", "大学生AI", "Agent"],
        "Python": ["Python", "Python入门", "自动化脚本", "编程入门"],
        "workflow automation": ["工作流", "自动化", "智能体", "效率工具"],
        "employability": ["AI求职", "AI办公", "简历AI", "学生就业"],
    }
    queries = mapping.get(content_focus, ["AI", "Python", "自动化"])
    if query_seed:
        queries = [query_seed] + queries
    deduped = []
    seen = set()
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            deduped.append(q)
    return deduped[:5]


def parse_time_window(value: str) -> int:
    if value == "3d":
        return 3
    return 7


def hot_score(item: Dict[str, Any], lookback_days: int) -> int:
    play = int(item.get("play") or 0)
    like = int(item.get("like") or 0)
    comment = int(item.get("review") or 0)
    pub_ts = int(item.get("pubdate") or 0)

    recency_bonus = 0
    if pub_ts:
        age_days = max((datetime.now(timezone.utc).timestamp() - pub_ts) / 86400, 0)
        recency_bonus = max(0, 30 - int(age_days * (30 / max(lookback_days, 1))))

    score = (
        min(play / 10000, 35)
        + min(like / 2000, 25)
        + min(comment / 300, 15)
        + recency_bonus
    )
    return int(min(100, math.floor(score)))


def fetch_bilibili_candidates(keyword: str, page: int = 1) -> List[Dict[str, Any]]:
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://search.bilibili.com/",
    }
    params = {
        "search_type": "video",
        "keyword": keyword,
        "page": page,
        "page_size": 10,
        "order": "pubdate",
    }
    response = requests.get(BILIBILI_SEARCH_URL, headers=headers, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    return data.get("data", {}).get("result", []) or []


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/trends")
def get_trends(req: TrendRequest) -> Dict[str, Any]:
    if str(req.demo_mode).lower() == "true":
        return {"trends": MOCK_TRENDS[: req.limit]}

    lookback_days = parse_time_window(req.time_window)
    queries = focus_queries(req.content_focus, req.query_seed)

    trends: List[Dict[str, Any]] = []
    seen_titles = set()

    for query in queries:
        try:
            results = fetch_bilibili_candidates(query)
        except Exception:
            continue

        for item in results:
            title = clean_html(item.get("title", ""))
            if not title:
                continue
            if title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())

            summary = clean_html(item.get("description", ""))
            bvid = item.get("bvid") or ""
            url = f"https://www.bilibili.com/video/{bvid}" if bvid else "https://www.bilibili.com"
            tags = [req.content_focus, query]
            trends.append(
                {
                    "title": title,
                    "source": "bilibili",
                    "url": url,
                    "summary": summary,
                    "hot_score": hot_score(item, lookback_days),
                    "published_at": int(item.get("pubdate") or 0),
                    "raw_tags": tags,
                }
            )

    trends.sort(key=lambda x: x.get("hot_score", 0), reverse=True)

    if not trends:
        return {"trends": MOCK_TRENDS[: req.limit]}

    return {"trends": trends[: req.limit]}
