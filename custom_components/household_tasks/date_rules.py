"""Pure calendar-date calculations for Household Tasks."""
from datetime import timedelta


def completion_due_date(completed_date, interval_days):
    """The date N calendar days after completion, ignoring clock time."""
    return completed_date + timedelta(days=int(interval_days))


def offset_due_date(event_date, days_before):
    """The date N calendar days before an event, ignoring its clock time."""
    return event_date - timedelta(days=int(days_before))


def legacy_calendar_days(config):
    """Convert existing hour-based offsets to whole days without requiring recreation."""
    if "calendar_offset_days" in config:
        return int(config["calendar_offset_days"])
    return int(config.get("calendar_offset_hours", 24)) // 24
