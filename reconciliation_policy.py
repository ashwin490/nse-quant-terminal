"""Pure decisions for restoring local paper-trade rows from Supabase.

Local CLOSED rows are deliberately immutable. Cloud 'last_checked' is an audit
checkpoint, not a dependable execution timestamp. A remotely closed row can
supersede a local ACTIVE row so that old positions are not silently reopened.
No DB or market network calls happen here.
"""


def is_closed(status) -> bool:
    return str(status).strip().upper() != "ACTIVE"


def preserve_local_row(local_status, remote_status, local_timestamp="", remote_timestamp="") -> bool:
    """Whether to skip cloud overwrite of an existing local row."""
    if is_closed(local_status):
        return True
    if is_closed(remote_status):
        return False
    lt = str(local_timestamp or "").strip()
    rt = str(remote_timestamp or "").strip()
    return bool(lt) and (not rt or lt >= rt)


def closed_record_conflict(local_status, remote_status, local_timestamp="", remote_timestamp="") -> bool:
    """Warn about differing finalised records, rather than quietly rewriting."""
    if not (is_closed(local_status) and is_closed(remote_status)):
        return False
    return (str(local_status).strip().upper() != str(remote_status).strip().upper()
            or str(local_timestamp or "") != str(remote_timestamp or ""))
