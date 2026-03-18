import html
import math
import os
import re
import json
import time
import uuid
import hmac
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="IBSS Trend + Visla Wrapper API", version="0.2.0")

# ---------------------------
# Trend endpoints (existing)
# ---------------------------
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
    return 3 if value == "3d" else 7


def hot_score(item: Dict[str, Any], lookback_days: int) -> int:
    play = int(item.get("play") or 0)
    like = int(item.get("like") or 0)
    comment = int(item.get("review") or 0)
    pub_ts = int(item.get("pubdate") or 0)

    recency_bonus = 0
    if pub_ts:
        age_days = max((datetime.now(timezone.utc).timestamp() - pub_ts) / 86400, 0)
        recency_bonus = max(0, 30 - int(age_days * (30 / max(lookback_days, 1))))

    score = min(play / 10000, 35) + min(like / 2000, 25) + min(comment / 300, 15) + recency_bonus
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
            key = title.lower()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            summary = clean_html(item.get("description", ""))
            bvid = item.get("bvid") or ""
            url = f"https://www.bilibili.com/video/{bvid}" if bvid else "https://www.bilibili.com"
            trends.append(
                {
                    "title": title,
                    "source": "bilibili",
                    "url": url,
                    "summary": summary,
                    "hot_score": hot_score(item, lookback_days),
                    "published_at": int(item.get("pubdate") or 0),
                    "raw_tags": [req.content_focus, query],
                }
            )

    trends.sort(key=lambda x: x.get("hot_score", 0), reverse=True)
    if not trends:
        return {"trends": MOCK_TRENDS[: req.limit]}
    return {"trends": trends[: req.limit]}


# ---------------------------
# Visla wrapper endpoints
# ---------------------------
VISLA_BASE_URL = os.getenv("VISLA_BASE_URL", "https://openapi.visla.us/openapi/v1").rstrip("/")
VISLA_CREDENTIAL = os.getenv("VISLA_CREDENTIAL", "").strip()
VISLA_TEAMSPACE_UUID = os.getenv("VISLA_TEAMSPACE_UUID", "").strip()
VISLA_VERIFY_SSL = os.getenv("VISLA_VERIFY_SSL", "true").strip().lower() in ("1", "true", "yes", "on")
VISLA_TIMEOUT = int(os.getenv("VISLA_HTTP_TIMEOUT", "60"))


class VideoRequest(BaseModel):
    approved_payload_json: str = ""
    approved_topic: str = ""
    approved_script: str = ""
    approved_visual_ideas: str = ""
    approved_storyboard: str = ""
    caption: str = ""
    hashtags: Any = ""
    duration_seconds: int = 45
    aspect_ratio: str = "9:16"
    video_pace: str = "normal"
    burn_subtitles: bool = True
    use_avatar: bool = False
    avatar_look_id: str = ""
    voice_id: str = ""
    teamspace_uuid_override: str = ""
    max_wait_seconds: int = 180


class StatusRequest(BaseModel):
    project_uuid: str = ""
    clip_uuid: str = ""


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(x) for x in value if x not in (None, ""))
    return str(value).strip()


def _boolify(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _split_credential(raw: str) -> tuple[str, str]:
    if "." not in raw:
        raise ValueError("VISLA_CREDENTIAL must be in `api_key.api_secret` format.")
    return raw.split(".", 1)


def _json_loads_if_needed(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = _clean_text(value)
    if not text:
        return None
    return json.loads(text)


def _normalize_hashtags(value: Any) -> List[str]:
    try:
        parsed = _json_loads_if_needed(value)
    except Exception:
        parsed = None
    tags: List[str] = []
    if isinstance(parsed, list):
        tags = [str(x).strip() for x in parsed if str(x).strip()]
    else:
        text = _clean_text(value)
        if text:
            tags = re.split(r"[，,\s]+", text)
            tags = [t.strip() for t in tags if t.strip()]
    clean: List[str] = []
    seen = set()
    for tag in tags:
        tag = tag if tag.startswith("#") else f"#{tag.lstrip('#')}"
        if tag not in seen:
            seen.add(tag)
            clean.append(tag)
    return clean


def _extract_first(obj: Any, candidate_keys: set[str], pred=None):
    queue = [obj]
    while queue:
        item = queue.pop(0)
        if isinstance(item, dict):
            for k, v in item.items():
                if k in candidate_keys and (pred is None or pred(v)):
                    return v
                if isinstance(v, (dict, list)):
                    queue.append(v)
        elif isinstance(item, list):
            queue.extend(item)
    return None


def _extract_teamspace_uuid(obj: Any) -> str:
    v = _extract_first(obj, {"teamspaceUuid", "teamSpaceUuid", "teamspace_uuid", "uuid"}, lambda x: isinstance(x, str) and len(x) >= 8)
    return str(v) if v else ""


def _extract_voice_id(obj: Any) -> str:
    v = _extract_first(obj, {"voiceId", "voice_id", "id"}, lambda x: isinstance(x, (int, str)))
    return str(v) if v is not None else ""


def _extract_project_uuid(obj: Any) -> str:
    v = _extract_first(obj, {"projectUuid", "project_uuid", "uuid"}, lambda x: isinstance(x, str) and len(x) >= 8)
    return str(v) if v else ""


def _extract_clip_uuid(obj: Any) -> str:
    v = _extract_first(obj, {"clipUuid", "clip_uuid", "uuid"}, lambda x: isinstance(x, str) and len(x) >= 8)
    return str(v) if v else ""


def _extract_status(obj: Any) -> str:
    v = _extract_first(obj, {"progressStatus", "progress_status", "clipStatus", "clip_status", "status"}, lambda x: isinstance(x, str))
    return str(v) if v else ""


def _extract_download_link(obj: Any) -> str:
    v = _extract_first(obj, {"downloadLink", "download_url", "downloadUrl", "url"}, lambda x: isinstance(x, str) and x.startswith("http"))
    return str(v) if v else ""


def _sign_headers(method: str, full_url: str, api_key: str, api_secret: str) -> Dict[str, str]:
    ts = str(int(time.time() * 1000))
    nonce = str(uuid.uuid4())
    sign_str = f"{method.upper()}|{full_url}|{ts}|{nonce}"
    signature = hmac.new(api_secret.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json; charset=utf-8",
        "accept": "*/*",
        "key": api_key,
        "ts": ts,
        "nonce": nonce,
        "sign": signature,
    }


def _visla_request(method: str, endpoint: str, api_key: str, api_secret: str, body: Optional[dict] = None, params: Optional[dict] = None) -> tuple[Any, str, int]:
    base = f"{VISLA_BASE_URL}{endpoint}"
    if params:
        query = requests.compat.urlencode(params, doseq=True)
        request_url = f"{base}?{query}"
        sign_url = base
    else:
        request_url = base
        sign_url = base

    headers = _sign_headers(method, sign_url, api_key, api_secret)
    try:
        resp = requests.request(
            method.upper(),
            request_url,
            headers=headers,
            json=body,
            timeout=VISLA_TIMEOUT,
            verify=VISLA_VERIFY_SSL,
        )
        text = resp.text
        try:
            data = resp.json()
        except Exception:
            data = {"raw": text}
        return data, text, resp.status_code
    except Exception as e:
        return {"error": str(e)}, str(e), 0


def _build_visla_script(topic: str, script: str, visual_ideas: str, storyboard: str, duration_seconds: int) -> str:
    parts = []
    if topic:
        parts.append(f"Video Title: {topic}")
    parts.append(f"Target Duration: about {duration_seconds} seconds")
    parts.append("Narration / Script:")
    parts.append(script or "")
    if visual_ideas:
        parts.append("\nVisual Ideas:")
        parts.append(visual_ideas)
    if storyboard:
        parts.append("\nStoryboard:")
        parts.append(storyboard)
    return "\n".join(parts).strip()


def _build_request_body(topic: str, script: str, visual_ideas: str, storyboard: str, duration_seconds: int, aspect_ratio: str, video_pace: str, burn_subtitles: bool, use_avatar: bool, avatar_look_id: str, voice_id: str, hashtags: List[str]) -> dict:
    visla_script = _build_visla_script(topic, script, visual_ideas, storyboard, duration_seconds)
    stock_tags: List[str] = []
    for token in [topic] + hashtags[:4]:
        text = _clean_text(token).replace("#", "")
        if text and text not in stock_tags:
            stock_tags.append(text)
    return {
        "script": visla_script,
        "target_video": {
            "aspect_ratio": aspect_ratio,
            "video_pace": video_pace,
            "burn_subtitles": burn_subtitles,
        },
        "footage_options": {
            "use_private_stocks": False,
            "use_free_stocks": True,
            "use_premium_stocks": False,
            "use_premium_stocks_getty": False,
            "stock_footage_tags": stock_tags[:5],
        },
        "bgm_options": {
            "use_free_stocks": True,
            "use_premium_stocks": False,
        },
        "avatar_options": {
            "use_avatar": use_avatar,
            "look_id": avatar_look_id or "1000148",
            "layout": "smart_composition",
        },
        "voice_options": {
            "use_voice": True,
            "voice_id": str(voice_id),
        },
    }


def _default_fail(payload: dict, input_mode: str, caption: str, hashtags_list: List[str], diagnostics: List[str], **kwargs) -> dict:
    base = {
        "video_status": "failed",
        "project_uuid": "",
        "clip_uuid": "",
        "download_url": "",
        "preview_url": "",
        "used_teamspace_uuid": "",
        "used_voice_id": "",
        "visla_request_body": "",
        "final_caption": caption,
        "hashtags_text": " ".join(hashtags_list),
        "summary_markdown": "",
        "error_message": "",
        "diagnostics_text": "\n".join(diagnostics),
        "normalized_payload_json": json.dumps(payload or {}, ensure_ascii=False, indent=2),
        "input_mode": input_mode,
    }
    base.update(kwargs)
    return base


def _normalize_payload(req: VideoRequest) -> tuple[dict, str, List[str], str, List[str]]:
    payload = None
    try:
        if _clean_text(req.approved_payload_json):
            payload = _json_loads_if_needed(req.approved_payload_json)
    except Exception:
        payload = None
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        payload = {}

    approved_topic = _clean_text(payload.get("approved_topic") or req.approved_topic)
    approved_script = _clean_text(payload.get("approved_script") or req.approved_script)
    approved_visual_ideas = _clean_text(payload.get("approved_visual_ideas") or req.approved_visual_ideas)
    approved_storyboard = _clean_text(payload.get("approved_storyboard") or req.approved_storyboard)
    caption = _clean_text(payload.get("caption") or req.caption)
    hashtags_list = _normalize_hashtags(payload.get("hashtags") if payload.get("hashtags") not in (None, "") else req.hashtags)

    payload.setdefault("approved_topic", approved_topic)
    payload.setdefault("approved_script", approved_script)
    payload.setdefault("approved_visual_ideas", approved_visual_ideas)
    payload.setdefault("approved_storyboard", approved_storyboard)
    payload.setdefault("caption", caption)
    payload.setdefault("hashtags", hashtags_list)
    payload.setdefault("duration_seconds", req.duration_seconds)

    input_mode = "payload_json" if _clean_text(req.approved_payload_json) else "manual_fields"
    return payload, approved_topic, hashtags_list, caption, [approved_script, approved_visual_ideas, approved_storyboard, input_mode]


def _create_video_core(req: VideoRequest) -> dict:
    diagnostics: List[str] = ["Started Visla wrapper create-video"]

    if not VISLA_CREDENTIAL:
        return _default_fail({}, "manual_fields", "", [], diagnostics, summary_markdown="缺少 VISLA_CREDENTIAL 环境变量。", error_message="VISLA_CREDENTIAL is not configured on Render.")

    api_key, api_secret = _split_credential(VISLA_CREDENTIAL)
    payload, approved_topic, hashtags_list, caption, other = _normalize_payload(req)
    approved_script, approved_visual_ideas, approved_storyboard, input_mode = other
    diagnostics.append(f"Input mode={input_mode}")

    if not approved_topic:
        return _default_fail(payload, input_mode, caption, hashtags_list, diagnostics, summary_markdown="缺少 approved_topic。", error_message="missing approved_topic")
    if not approved_script:
        return _default_fail(payload, input_mode, caption, hashtags_list, diagnostics, summary_markdown="缺少 approved_script。", error_message="missing approved_script")

    teamspace_uuid = _clean_text(req.teamspace_uuid_override) or VISLA_TEAMSPACE_UUID
    if not teamspace_uuid:
        rsp_team, raw_team, status_team = _visla_request("GET", "/workspace/list-my-teamspace", api_key, api_secret)
        diagnostics.append(f"List teamspaces status={status_team}")
        teamspace_uuid = _extract_teamspace_uuid(rsp_team)
        if not teamspace_uuid:
            return _default_fail(
                payload,
                input_mode,
                caption,
                hashtags_list,
                diagnostics,
                summary_markdown="无法自动识别 Visla teamspace。请在 Render 设置 VISLA_TEAMSPACE_UUID，或在请求中传 teamspace_uuid_override。",
                error_message=f"Unable to auto-detect teamspace UUID. Raw: {raw_team[:1200]}",
            )
    diagnostics.append(f"Using teamspace_uuid={teamspace_uuid}")

    selected_voice_id = _clean_text(req.voice_id)
    if not selected_voice_id:
        rsp_voice, raw_voice, status_voice = _visla_request("GET", "/workspace/list-voice", api_key, api_secret)
        diagnostics.append(f"List voices status={status_voice}")
        selected_voice_id = _extract_voice_id(rsp_voice) or "363"
        diagnostics.append(f"Auto voice_id={selected_voice_id}")
    else:
        diagnostics.append(f"Manual voice_id={selected_voice_id}")

    request_body = _build_request_body(
        approved_topic,
        approved_script,
        approved_visual_ideas,
        approved_storyboard,
        int(req.duration_seconds or 45),
        _clean_text(req.aspect_ratio) or "9:16",
        _clean_text(req.video_pace) or "normal",
        _boolify(req.burn_subtitles),
        _boolify(req.use_avatar),
        _clean_text(req.avatar_look_id),
        selected_voice_id,
        hashtags_list,
    )

    rsp_create, raw_create, status_create = _visla_request(
        "POST",
        f"/teamspace/{teamspace_uuid}/script-to-video",
        api_key,
        api_secret,
        body=request_body,
    )
    diagnostics.append(f"Create project status={status_create}")
    project_uuid = _extract_project_uuid(rsp_create)
    if not project_uuid:
        return _default_fail(
            payload,
            input_mode,
            caption,
            hashtags_list,
            diagnostics,
            used_teamspace_uuid=teamspace_uuid,
            used_voice_id=selected_voice_id,
            visla_request_body=json.dumps(request_body, ensure_ascii=False, indent=2),
            summary_markdown="Visla 项目创建失败。请检查 plan 权限、teamspace 或脚本长度。",
            error_message=f"Unable to extract projectUuid. Raw: {raw_create[:1200]}",
        )

    deadline = time.time() + int(req.max_wait_seconds or 180)
    project_status = ""
    raw_project = ""
    while time.time() < deadline:
        rsp_project, raw_project, status_project = _visla_request("GET", f"/project/{project_uuid}/info", api_key, api_secret)
        project_status = _extract_status(rsp_project)
        diagnostics.append(f"Project status={project_status or 'unknown'} (http={status_project})")
        if project_status.lower() == "editing":
            break
        time.sleep(8)

    if project_status.lower() != "editing":
        return _default_fail(
            payload,
            input_mode,
            caption,
            hashtags_list,
            diagnostics,
            video_status="project_processing",
            project_uuid=project_uuid,
            used_teamspace_uuid=teamspace_uuid,
            used_voice_id=selected_voice_id,
            visla_request_body=json.dumps(request_body, ensure_ascii=False, indent=2),
            summary_markdown=f"项目已创建，但在 {req.max_wait_seconds} 秒内尚未进入可导出状态。可稍后继续查询。",
            error_message=raw_project[:1200],
        )

    rsp_export, raw_export, status_export = _visla_request("POST", f"/project/{project_uuid}/export-video", api_key, api_secret, body={})
    diagnostics.append(f"Export status={status_export}")
    clip_uuid = _extract_clip_uuid(rsp_export)
    if not clip_uuid:
        return _default_fail(
            payload,
            input_mode,
            caption,
            hashtags_list,
            diagnostics,
            video_status="export_failed",
            project_uuid=project_uuid,
            used_teamspace_uuid=teamspace_uuid,
            used_voice_id=selected_voice_id,
            visla_request_body=json.dumps(request_body, ensure_ascii=False, indent=2),
            summary_markdown="项目已创建，但导出视频失败。",
            error_message=raw_export[:1200],
        )

    clip_status = ""
    raw_clip = ""
    deadline = time.time() + int(req.max_wait_seconds or 180)
    while time.time() < deadline:
        rsp_clip, raw_clip, status_clip = _visla_request("GET", f"/clip/{clip_uuid}/info", api_key, api_secret)
        clip_status = _extract_status(rsp_clip)
        diagnostics.append(f"Clip status={clip_status or 'unknown'} (http={status_clip})")
        if clip_status.lower() == "completed":
            break
        time.sleep(8)

    if clip_status.lower() != "completed":
        return _default_fail(
            payload,
            input_mode,
            caption,
            hashtags_list,
            diagnostics,
            video_status="clip_processing",
            project_uuid=project_uuid,
            clip_uuid=clip_uuid,
            used_teamspace_uuid=teamspace_uuid,
            used_voice_id=selected_voice_id,
            visla_request_body=json.dumps(request_body, ensure_ascii=False, indent=2),
            summary_markdown=f"视频已进入导出流程，但在 {req.max_wait_seconds} 秒内尚未完成。",
            error_message=raw_clip[:1200],
        )

    rsp_link, raw_link, status_link = _visla_request("GET", f"/clip/{clip_uuid}/get-download-link", api_key, api_secret)
    diagnostics.append(f"Download-link status={status_link}")
    download_url = _extract_download_link(rsp_link)
    hashtags_text = " ".join(hashtags_list)
    summary = "\n".join([
        "# Video Producer Result",
        f"- 状态：{'completed' if download_url else 'completed_without_link'}",
        f"- 项目 ID：{project_uuid}",
        f"- Clip ID：{clip_uuid}",
        f"- Teamspace：{teamspace_uuid}",
        f"- Voice ID：{selected_voice_id}",
        f"- 下载链接：{download_url or '暂未返回，可稍后重试'}",
        "",
        "## Caption",
        caption or "(empty)",
        "",
        "## Hashtags",
        hashtags_text or "(empty)",
    ])
    return {
        "video_status": "completed" if download_url else "completed_without_link",
        "project_uuid": project_uuid,
        "clip_uuid": clip_uuid,
        "download_url": download_url,
        "preview_url": download_url,
        "used_teamspace_uuid": teamspace_uuid,
        "used_voice_id": str(selected_voice_id),
        "visla_request_body": json.dumps(request_body, ensure_ascii=False, indent=2),
        "final_caption": caption,
        "hashtags_text": hashtags_text,
        "summary_markdown": summary,
        "error_message": "" if download_url else raw_link[:1200],
        "diagnostics_text": "\n".join(diagnostics),
        "normalized_payload_json": json.dumps(payload, ensure_ascii=False, indent=2),
        "input_mode": input_mode,
    }


@app.get("/visla/health")
def visla_health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "visla_base_url": VISLA_BASE_URL,
        "credential_configured": bool(VISLA_CREDENTIAL),
        "teamspace_uuid_configured": bool(VISLA_TEAMSPACE_UUID),
        "ssl_verify": VISLA_VERIFY_SSL,
    }


@app.post("/visla/create-video")
def visla_create_video(req: VideoRequest) -> Dict[str, Any]:
    try:
        return _create_video_core(req)
    except Exception as e:
        return {
            "video_status": "failed",
            "project_uuid": "",
            "clip_uuid": "",
            "download_url": "",
            "preview_url": "",
            "used_teamspace_uuid": "",
            "used_voice_id": "",
            "visla_request_body": "",
            "final_caption": _clean_text(req.caption),
            "hashtags_text": " ".join(_normalize_hashtags(req.hashtags)),
            "summary_markdown": "Render Visla wrapper 执行失败。",
            "error_message": str(e),
            "diagnostics_text": f"Unhandled exception: {e}",
            "normalized_payload_json": _clean_text(req.approved_payload_json),
            "input_mode": "payload_json" if _clean_text(req.approved_payload_json) else "manual_fields",
        }


@app.post("/visla/status")
def visla_status(req: StatusRequest) -> Dict[str, Any]:
    diagnostics: List[str] = ["Started status lookup"]
    if not VISLA_CREDENTIAL:
        return {"status": "failed", "error_message": "VISLA_CREDENTIAL is not configured.", "diagnostics_text": "\n".join(diagnostics)}
    api_key, api_secret = _split_credential(VISLA_CREDENTIAL)
    if _clean_text(req.clip_uuid):
        rsp_clip, raw_clip, status_code = _visla_request("GET", f"/clip/{req.clip_uuid}/info", api_key, api_secret)
        clip_status = _extract_status(rsp_clip)
        download_url = ""
        if clip_status.lower() == "completed":
            rsp_link, raw_link, link_status = _visla_request("GET", f"/clip/{req.clip_uuid}/get-download-link", api_key, api_secret)
            diagnostics.append(f"Download-link status={link_status}")
            download_url = _extract_download_link(rsp_link)
            raw_clip = raw_clip + "\n" + raw_link
        diagnostics.append(f"Clip status={clip_status or 'unknown'} (http={status_code})")
        return {
            "status": clip_status or "unknown",
            "project_uuid": _clean_text(req.project_uuid),
            "clip_uuid": _clean_text(req.clip_uuid),
            "download_url": download_url,
            "raw_response": raw_clip[:3000],
            "diagnostics_text": "\n".join(diagnostics),
        }
    if _clean_text(req.project_uuid):
        rsp_project, raw_project, status_code = _visla_request("GET", f"/project/{req.project_uuid}/info", api_key, api_secret)
        project_status = _extract_status(rsp_project)
        diagnostics.append(f"Project status={project_status or 'unknown'} (http={status_code})")
        return {
            "status": project_status or "unknown",
            "project_uuid": _clean_text(req.project_uuid),
            "clip_uuid": "",
            "download_url": "",
            "raw_response": raw_project[:3000],
            "diagnostics_text": "\n".join(diagnostics),
        }
    return {"status": "invalid_input", "error_message": "Provide clip_uuid or project_uuid.", "diagnostics_text": "\n".join(diagnostics)}
