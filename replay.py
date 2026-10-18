"""存档与重放：把一次运行的数据存成 JSON，之后改规则可以零成本重跑。

为什么要这个文件
----------------
一次完整的 AI 运行约 5 万 tokens（≈0.3 元）。调"挑选规则"的时候你会想改
十几次 —— 每次重新抓取 + 重新调模型，等于把调试成本乘以次数。

所以把运行拆成两层存档：

    --save-raw       抓取 + 过滤后的**文章清单**（含标题、摘要、来源、时间）
    --from-raw       直接用存档的文章跑 AI，跳过网络抓取（省时间，但还花 token）

    --save-analysis  模型的三步产出（召回理由、分数、分档、板块、简报正文）
    --from-analysis  直接用存档的模型判定重新渲染周报（**零 token**）

调"分配/排序/渲染"用 --from-analysis，调"提示词/召回"用 --from-raw，
两者都不需要重新抓 RSS。

存档是 JSON，放在 .tmp/ 下（已在 .gitignore 里），不进仓库。
"""

import json
from datetime import datetime
from pathlib import Path

import fetch


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def _dt(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 第一层：文章清单
# ---------------------------------------------------------------------------
def dump_articles(path: str | Path, articles: list[fetch.Article]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": _iso(datetime.now()),
        "count": len(articles),
        "articles": [
            {
                "title": a.title,
                "url": a.url,
                "summary": a.summary,
                "published": _iso(a.published),
                "source": a.source,
                "category": a.category,
                "lang": a.lang,
            }
            for a in articles
        ],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return target


def load_articles(path: str | Path) -> list[fetch.Article]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        fetch.Article(
            title=str(item.get("title", "")),
            url=str(item.get("url", "")),
            summary=str(item.get("summary", "")),
            published=_dt(item.get("published")),
            source=str(item.get("source", "")),
            category=str(item.get("category", "ai")),
            lang=str(item.get("lang", "zh")),
        )
        for item in data.get("articles") or []
    ]


# ---------------------------------------------------------------------------
# 第二层：模型判定 + 渲染所需的全部参数
# ---------------------------------------------------------------------------
def dump_analysis(path: str | Path, analysis: dict, usage,
                  start: datetime, end: datetime, window_desc: str,
                  stats: dict) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": _iso(datetime.now()),
        "usage": str(usage),
        "window_desc": window_desc,
        "start": _iso(start),
        "end": _iso(end),
        "stats": stats,
        "analysis": {
            "overview": analysis.get("overview", ""),
            "items": [
                {
                    "title": it["title"],
                    "summary": it["summary"],
                    "why": it["why"],
                    "url": it["url"],
                    "source": it["source"],
                    "category": it["category"],
                    "lang": it["lang"],
                    "published": _iso(it["published"]),
                    "score": it.get("score", 0),
                }
                for it in analysis.get("items", [])
            ],
        },
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return target


def load_analysis(path: str | Path) -> dict:
    """还原成 build_weekly_markdown() 需要的那一套参数。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("analysis", {}).get("items") or []
    for it in items:
        it["published"] = _dt(it.get("published"))
    return {
        "analysis": {"overview": data.get("analysis", {}).get("overview", ""),
                     "items": items},
        "usage": data.get("usage", "（存档用量）"),
        "window_desc": data.get("window_desc", "存档"),
        "start": _dt(data.get("start")) or datetime.now(),
        "end": _dt(data.get("end")) or datetime.now(),
        "stats": data.get("stats") or {
            "sources_total": 0, "sources_ok": 0, "sources_failed": 0,
            "fetched": 0, "in_window": 0, "after_dedupe": 0, "after_filter": 0,
        },
    }
