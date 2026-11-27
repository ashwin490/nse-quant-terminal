"""Pure, non-destructive reconciliation decisions for closed NSE paper trades.

Local closed rows are immutable. Cloud last_checked/last_audited timestamps are
*checkpoints*, not verified execution timestamps, so differing timestamps alone
must never be reported as a completed-trade outcome conflict.
"""

import re


def is_closed(status) -> bool:
    return re.search(r"(?<![A-Z])ACTIVE(?![A-Z])", str(status or "").upper()) is None


def preserve_local_row(local_status, remote_status, local_timestamp="", remote_timestamp="") -> bool:
    if is_closed(local_status):
        return True
    if is_closed(remote_status):
        return False
    lt = str(local_timestamp or "").strip()
    rt = str(remote_timestamp or "").strip()
    return bool(lt) and (not rt or lt >= rt)


def _final_outcome(status) -> str:
    value = str(status or '').strip().upper()
    if 'BREAK-EVEN' in value or value == 'BE':
        return 'BREAK_EVEN'
    if 'LOSS' in value or 'STOPPED OUT' in value:
        return 'LOSS'
    if 'WIN' in value or 'TARGET HIT' in value:
        return 'WIN'
    if 'EXPIRED' in value or 'TIME EXIT' in value:
        return 'TIME_EXIT'
    return value


def closed_record_conflict(local_status, remote_status, local_timestamp="", remote_timestamp="") -> bool:
    """Only incompatible *outcomes* are conflicts; timestamps are audit metadata.

    This check cannot establish whether actual fill prices agree, so it never
    claims a complete reconciliation of trade contents or original exit times.
    """
    if not (is_closed(local_status) and is_closed(remote_status)):
        return False
    return _final_outcome(local_status) != _final_outcome(remote_status)
