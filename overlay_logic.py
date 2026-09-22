"""Pure logic for the Codex usage top bar.

The Windows UI lives in ``overlay.py``. Keeping parsing and formatting here
makes the data contract testable without starting a desktop window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class UsageWindow:
    used_percent: float
    remaining_percent: int
    window_minutes: int
    resets_at: float

    @property
    def label(self) -> str:
        if self.window_minutes == 300:
            return "5小时窗口"
        if self.window_minutes == 10080:
            return "周窗口"
        return f"{self.window_minutes}分钟窗口"


@dataclass(frozen=True)
class UsageSnapshot:
    primary: UsageWindow | None
    secondary: UsageWindow | None
    observed_at: float


def _find_rate_limits(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        candidate = value.get("rate_limits")
        if isinstance(candidate, dict):
            return candidate
        for child in value.values():
            found = _find_rate_limits(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_rate_limits(child)
            if found is not None:
                return found
    return None


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _parse_window(value: Any) -> UsageWindow | None:
    if not isinstance(value, dict):
        return None
    used = _as_number(value.get("used_percent"))
    minutes = _as_number(value.get("window_minutes"))
    resets_at = _as_number(value.get("resets_at"))
    if used is None or minutes is None or resets_at is None:
        return None
    used = max(0.0, min(100.0, used))
    return UsageWindow(
        used_percent=used,
        remaining_percent=int(round(100.0 - used)),
        window_minutes=int(minutes),
        resets_at=resets_at,
    )


def _timestamp(value: Any) -> float:
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def parse_usage_record(record: Any) -> UsageSnapshot | None:
    """Extract a usage snapshot from one JSONL event or nested event payload."""

    if not isinstance(record, (dict, list)):
        return None
    rate_limits = _find_rate_limits(record)
    if rate_limits is None:
        return None
    primary = _parse_window(rate_limits.get("primary"))
    secondary = _parse_window(rate_limits.get("secondary"))
    if primary is None and secondary is None:
        return None
    observed_at = _timestamp(record.get("timestamp")) if isinstance(record, dict) else 0.0
    if observed_at == 0.0 and isinstance(record, dict):
        payload = record.get("payload")
        if isinstance(payload, dict):
            observed_at = _timestamp(payload.get("timestamp"))
    return UsageSnapshot(primary=primary, secondary=secondary, observed_at=observed_at)


def _iter_jsonl_records(root: Path) -> Iterable[tuple[Path, Any]]:
    if not root.exists():
        return
    paths = [root] if root.is_file() else root.rglob("*.jsonl")
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        yield path, json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue


def find_latest_usage_snapshot(root: str | Path) -> UsageSnapshot | None:
    """Read the newest usage event from the local Codex session JSONL files."""

    latest: tuple[float, float, UsageSnapshot] | None = None
    for path, record in _iter_jsonl_records(Path(root)):
        snapshot = parse_usage_record(record)
        if snapshot is None:
            continue
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = 0.0
        candidate = (snapshot.observed_at, modified, snapshot)
        if latest is None or candidate[:2] > latest[:2]:
            latest = candidate
    return latest[2] if latest is not None else None


def format_countdown(seconds: float) -> str:
    remaining = max(0, int(seconds))
    days, remainder = divmod(remaining, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days}天{hours}小时" if hours else f"{days}天"
    if hours:
        return f"{hours}小时{minutes}分钟" if minutes else f"{hours}小时"
    if minutes:
        return f"{minutes}分钟"
    return "现在"


def format_usage_summary(snapshot: UsageSnapshot | None, now: float | None = None) -> str:
    if snapshot is None:
        return "暂无用量数据"
    current = datetime.now(tz=timezone.utc).timestamp() if now is None else now
    lines: list[str] = []
    for window in (snapshot.primary, snapshot.secondary):
        if window is None:
            continue
        lines.append(f"{window.label} 还剩 {window.remaining_percent}%")
        lines.append(f"重置倒计时 {format_countdown(window.resets_at - current)}")
    return "\n".join(lines) if lines else "暂无用量数据"


def format_usage_rows(
    snapshot: UsageSnapshot | None, now: float | None = None
) -> list[tuple[str, str, str]]:
    """Return structured, text-only rows for the native tooltip card."""
    if snapshot is None:
        return []
    current = datetime.now(tz=timezone.utc).timestamp() if now is None else now
    rows: list[tuple[str, str, str]] = []
    for window in (snapshot.primary, snapshot.secondary):
        if window is None:
            continue
        rows.append(
            (
                window.label,
                f"还剩 {window.remaining_percent}%",
                f"重置倒计时 {format_countdown(window.resets_at - current)}",
            )
        )
    return rows


def format_usage_banner(snapshot: UsageSnapshot | None, now: float | None = None) -> str:
    """Format the compact English top-of-screen usage text."""
    rows = format_usage_rows(snapshot, now=now)
    if not rows:
        return "Codex usage unavailable"
    labels = {"5小时窗口": "5h", "周窗口": "周"}
    parts: list[str] = []
    for label, value, reset in rows:
        short_label = "7d" if label == "周窗口" else labels.get(label, label)
        remaining = value.replace("还剩 ", "")
        countdown = (
            reset.replace("重置倒计时 ", "")
            .replace("天", "d")
            .replace("小时", "h")
            .replace("分钟", "m")
            .replace("现在", "now")
        )
        parts.append(
            f"{short_label} {remaining} reset {countdown}"
        )
    return "    ".join(parts)


def format_usage_segments(
    snapshot: UsageSnapshot | None, now: float | None = None
) -> list[tuple[str, str]]:
    """Return centered-bar balance and reset strings as separate segments."""
    rows = format_usage_rows(snapshot, now=now)
    segments: list[tuple[str, str]] = []
    for label, value, reset in rows:
        short_label = "7d" if label == "周窗口" else "5h"
        remaining = value.replace("还剩 ", "")
        countdown = (
            reset.replace("重置倒计时 ", "")
            .replace("天", "d")
            .replace("小时", "h")
            .replace("分钟", "m")
            .replace("现在", "now")
        )
        segments.append((f"{short_label} {remaining}", f"reset {countdown}"))
    return segments


def cursor_in_trigger_zone(
    cursor: tuple[int, int] | None,
    screen_width: int,
    bar_width: int,
    trigger_height: int = 32,
) -> bool:
    """Return whether the pointer is over the narrow top-bar trigger area."""
    if cursor is None:
        return False
    left = max(0, (screen_width - bar_width) // 2)
    return left <= cursor[0] <= left + bar_width and 0 <= cursor[1] <= trigger_height


def should_expand_usage_bar(
    codex_running: bool,
    cursor: tuple[int, int] | None,
    screen_width: int,
    bar_width: int,
    trigger_height: int = 32,
    startup_grace: bool = False,
) -> bool:
    """Decide whether the full bar should be visible instead of its handle."""
    return codex_running and (
        startup_grace
        or cursor_in_trigger_zone(cursor, screen_width, bar_width, trigger_height)
    )


def top_bar_position(
    screen_width: int,
    screen_height: int,
    bar_width: int,
    bar_height: int,
    margin: int = 12,
) -> tuple[int, int]:
    """Return a centered, in-bounds top-bar origin."""
    x = max(margin, (screen_width - bar_width) // 2)
    y = max(margin, margin)
    if screen_width > bar_width:
        x = min(x, screen_width - bar_width - margin)
    if screen_height > bar_height:
        y = min(y, screen_height - bar_height - margin)
    return x, y
