"""一次性补丁：把 budget 参数串进 analyzer.py 的各层调用。"""

import io

PATH = "analyzer.py"

REPLACEMENTS = [
    (
        "def screen(articles: list[Article], usage: Usage, progress=print\n"
        "           ) -> list[tuple[Article, dict]]:",
        "def screen(articles: list[Article], usage: Usage, progress=print,\n"
        "           budget=None) -> list[tuple[Article, dict]]:",
    ),
    (
        "data = _chat_json(SCREEN_SYSTEM, user, config.MODEL_SCREEN, usage,\n"
        "                              max_tokens=4000)",
        "data = _chat_json(SCREEN_SYSTEM, user, config.MODEL_SCREEN, usage,\n"
        "                              max_tokens=4000, budget=budget)",
    ),
    (
        "def joint_rank(candidates: list[tuple[Article, dict]], usage: Usage,\n"
        "               progress=print) -> list[tuple[Article, dict]]:",
        "def joint_rank(candidates: list[tuple[Article, dict]], usage: Usage,\n"
        "               progress=print, budget=None) -> list[tuple[Article, dict]]:",
    ),
    (
        "data = _chat_json(RERANK_SYSTEM, user, config.MODEL_RERANK, usage, max_tokens=12000)",
        "data = _chat_json(RERANK_SYSTEM, user, config.MODEL_RERANK, usage,\n"
        "                      max_tokens=12000, budget=budget)",
    ),
    (
        "def write(selected: list[tuple[Article, dict]], usage: Usage, progress=print\n"
        "          ) -> dict:",
        "def write(selected: list[tuple[Article, dict]], usage: Usage, progress=print,\n"
        "          budget=None) -> dict:",
    ),
    (
        "data = _chat_json(WRITE_SYSTEM, user, config.MODEL_WRITE, usage, max_tokens=16000)",
        "data = _chat_json(WRITE_SYSTEM, user, config.MODEL_WRITE, usage,\n"
        "                      max_tokens=16000, budget=budget)",
    ),
    (
        "def analyze(articles: list[Article], progress=print) -> tuple[dict, Usage]:",
        "def analyze(articles: list[Article], progress=print, budget=None) -> tuple[dict, Usage]:",
    ),
    (
        "got = screen(pool, usage, progress)",
        "got = screen(pool, usage, progress, budget=budget)",
    ),
    (
        "ranked = joint_rank(candidates, usage, progress)",
        "ranked = joint_rank(candidates, usage, progress, budget=budget)",
    ),
    (
        "result = write(selected, usage, progress)",
        "result = write(selected, usage, progress, budget=budget)",
    ),
]


def main() -> None:
    source = io.open(PATH, encoding="utf-8").read()
    for old, new in REPLACEMENTS:
        if old not in source:
            print(f"MISS: {old.splitlines()[0]}")
            continue
        source = source.replace(old, new, 1)
    io.open(PATH, "w", encoding="utf-8").write(source)
    print("patched")


if __name__ == "__main__":
    main()
