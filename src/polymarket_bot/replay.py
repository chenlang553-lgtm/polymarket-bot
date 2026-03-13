from __future__ import print_function

from .archive import load_jsonl_records


def format_replay_line(record):
    record_type = record.get("recordType")
    if record_type == "state":
        return (
            "STATE window=%s tau=%ss spot=%s yes_price=%s no_price=%s fair_yes=%s fair_no=%s edge_yes=%s edge_no=%s pos=%s"
            % (
                record.get("marketSlug"),
                record.get("timeToExpirySec"),
                _fmt(record.get("spot")),
                _fmt(record.get("yesPrice")),
                _fmt(record.get("noPrice")),
                _fmt(record.get("fairYes")),
                _fmt(record.get("fairNo")),
                _fmt(record.get("edgeYes")),
                _fmt(record.get("edgeNo")),
                record.get("position", "flat"),
            )
        )
    if record_type == "activity":
        return (
            "ACTIVITY window=%s action=%s side=%s size=%s price=%s tau=%ss reason=%s"
            % (
                record.get("marketSlug"),
                record.get("action"),
                record.get("side"),
                _fmt(record.get("size")),
                _fmt(record.get("price")),
                record.get("timeToExpirySec"),
                record.get("reason"),
            )
        )
    if record_type == "window":
        return (
            "WINDOW window=%s phase=%s start=%s end=%s"
            % (
                record.get("marketSlug"),
                record.get("phase"),
                record.get("startTime"),
                record.get("endTime"),
            )
        )
    return "UNKNOWN %s" % record


def run_replay(path, limit=0):
    records = load_jsonl_records(path)
    lines = []
    count = 0
    for record in records:
        lines.append(format_replay_line(record))
        count += 1
        if limit and count >= limit:
            break
    return "\n".join(lines)


def _fmt(value):
    if value is None:
        return "None"
    if isinstance(value, float):
        return "%.4f" % value
    return str(value)
