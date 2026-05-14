"""Terminal progress lines (Korean) for batch ingest."""

from __future__ import annotations


PROGRESS_PREFIX = "[진행]"


def progress_print(msg: str, *, enabled: bool = True) -> None:
    if not enabled:
        return
    # Windows cp949 콘솔에서 U+2014/U+2026 인코딩 오류 방지
    safe = msg.replace("\u2014", "-").replace("\u2026", "...")
    print(f"{PROGRESS_PREFIX} {safe}", flush=True)
