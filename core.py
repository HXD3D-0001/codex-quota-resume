"""Deterministic quota and continuation rules. No I/O or credentials."""
import math
import re


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def normalize_usage(raw, now):
    buckets = raw.get('rateLimitsByLimitId')
    bucket = buckets.get('codex', {}) if isinstance(buckets, dict) else raw.get('rateLimits', {})
    if not isinstance(bucket, dict):
        bucket = {}
    windows = []
    for key in ('primary', 'secondary'):
        item = bucket.get(key)
        if not isinstance(item, dict):
            continue
        used, minutes, reset = (number(item.get(k)) for k in ('usedPercent', 'windowDurationMins', 'resetsAt'))
        if used is None or minutes is None or reset is None or not 0 <= used <= 100 or minutes <= 0 or reset <= 0:
            continue
        windows.append({'used': used, 'minutes': minutes, 'reset': reset})
    return {'observed_at': now, 'windows': windows,
            'denied': raw.get('ordinaryUsageAllowed') is False or bool(bucket.get('spendControlReached'))
                      or bool(bucket.get('rateLimitReachedType'))}


def quota_ready(data, now):
    windows = data.get('windows', [])
    # A partial response is displayable but never enough to authorize a turn.
    return (0 <= now - data.get('observed_at', 0) <= 60
            and len(windows) == 2 and not data.get('denied', True)
            and all(w['used'] < 100 for w in windows))


def next_poll_delay(data, now):
    windows = data.get('windows', [])
    upcoming = [w['reset'] - now for w in windows if w['reset'] > now]
    if any(w['reset'] <= now for w in windows):
        return 5
    if upcoming and min(upcoming) <= 30:
        return max(0.25, min(5, min(upcoming)))
    return 30


QUOTA_ERROR = re.compile(r"hit your usage limit|usage_limit_reached|usage limit (?:has been )?(?:reached|exceeded)|quota exceeded|额度.{0,8}(?:耗尽|用尽)|达到.{0,8}(?:用量|使用).{0,4}限额", re.I)


def candidate_key(detail, marked_turn, activated_at):
    thread = detail.get('thread', {})
    status = thread.get('status', {})
    status = status.get('type') if isinstance(status, dict) else status
    if status != 'idle' or thread.get('archived') or thread.get('kind', 'codex') != 'codex':
        return None
    turns = detail.get('turns') or []  # Desktop read_thread contract: newest first.
    if not turns or not turns[0].get('id') or not thread.get('id'):
        return None
    turn = turns[0]
    if turn.get('status') not in ('completed', 'failed'):
        return None
    key = f"{thread['id']}:{turn['id']}"
    if marked_turn is not None and marked_turn == turn['id']:
        return key
    ended = number(turn.get('completedAt'))
    if turn.get('status') != 'failed' or ended is None or ended < activated_at:
        return None
    error = turn.get('error') or {}
    message = error.get('message', '') if isinstance(error, dict) else str(error)
    return key if QUOTA_ERROR.search(message) else None
