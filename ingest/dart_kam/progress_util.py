"""Terminal progress lines (Korean) for batch ingest.

배치 인제스트(다운로드/파싱/조회) 단계에서 사용하는 한국어 진행 메시지 유틸.

- :func:`progress_print` 는 줄 단위 출력 + Windows cp949 콘솔 호환(em-dash/ellipsis 치환).
- :class:`BatchProgress` 는 ``총 N건 중 i번째 (남음 …)`` 패턴을 일관되게 포맷한다.
  4개 서비스(``document_service``, ``parse_audit``, ``ae00024``, ``list_service``)에서
  중복되던 진행 표시를 한 곳으로 모은다.
"""

from __future__ import annotations

from dataclasses import dataclass


PROGRESS_PREFIX = "[진행]"

# Windows cp949 콘솔에서 인코딩 오류가 자주 났던 문자 → ASCII 치환표.
# 새 문자를 추가하려면 (원문자, 치환문자) 페어를 늘리면 된다.
_SAFE_CHAR_MAP: tuple[tuple[str, str], ...] = (
    ("\u2014", "-"),   # em-dash → "-"
    ("\u2026", "..."),  # ellipsis → "..."
)


def _to_console_safe(msg: str) -> str:
    """콘솔 인코딩(특히 Windows cp949) 충돌을 피하기 위한 안전 치환."""
    for src, dst in _SAFE_CHAR_MAP:
        if src in msg:
            msg = msg.replace(src, dst)
    return msg


def progress_print(msg: str, *, enabled: bool = True) -> None:
    """``[진행] ...`` 한 줄을 stdout에 즉시 flush 출력.

    :param msg: 메시지 본문 (접두사 ``[진행]``는 자동으로 붙는다).
    :param enabled: ``False`` 면 무시 (``--quiet`` 옵션과 연계).
    """
    if not enabled:
        return
    print(f"{PROGRESS_PREFIX} {_to_console_safe(msg)}", flush=True)


@dataclass
class BatchProgress:
    """배치 작업의 ``총 N건 중 i번째 (남음 K건)`` 진행 메시지 헬퍼.

    동일한 문자열 포맷이 여러 서비스에서 반복되던 것을 한곳으로 모은다.

    사용 예:

    >>> bp = BatchProgress(label="파싱", total=13, enabled=True)
    >>> bp.start(extra="(다운로드 완료·미파싱 또는 실패 재시도)")
    >>> for i, item in enumerate(items, start=1):
    ...     bp.tick(i, detail=f"rcept_no={item}")
    ...     # ... 작업 수행 ...
    ...     bp.done(i, ok=ok, bad=bad, note="의견=...")
    >>> bp.finish(ok=ok, bad=bad)
    """

    label: str
    total: int
    enabled: bool = True

    def start(self, *, extra: str = "") -> None:
        tail = f" {extra}" if extra else ""
        progress_print(f"{self.label} - 대상 총 {self.total}건{tail}", enabled=self.enabled)
        if self.total == 0:
            progress_print(f"{self.label} 대상이 없습니다.", enabled=self.enabled)

    def tick(self, idx: int, *, detail: str) -> None:
        """단건 처리 *시작* 알림. ``idx`` 는 1-based."""
        remain = max(0, self.total - idx)
        progress_print(
            f"총 {self.total}건 중 {idx}번째 처리 중 (남음 {remain}건) - {detail}",
            enabled=self.enabled,
        )

    def done(self, idx: int, *, ok: int, bad: int, note: str = "") -> None:
        """단건 처리 *성공* 알림."""
        tail = f" - {note}" if note else ""
        progress_print(
            f"{self.label} 완료 {idx}/{self.total} - 성공 누적 {ok}건, 실패 {bad}건{tail}",
            enabled=self.enabled,
        )

    def fail(self, idx: int, *, ok: int, bad: int, error: str) -> None:
        """단건 처리 *실패* 알림 (에러 메시지는 200자 컷)."""
        progress_print(
            f"{self.label} 실패 {idx}/{self.total} - 성공 누적 {ok}건, "
            f"실패 누적 {bad}건 - {error[:200]}",
            enabled=self.enabled,
        )

    def finish(self, *, ok: int, bad: int) -> None:
        """배치 종료 요약."""
        progress_print(
            f"{self.label} 단계 종료 - 성공 {ok}건, 실패 {bad}건 (대상 {self.total}건)",
            enabled=self.enabled,
        )
