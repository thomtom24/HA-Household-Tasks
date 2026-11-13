"""Pure calendar recurrence calculations for fixed monthly/yearly schedules."""
from calendar import monthrange


def fixed_schedule(now, config, created, completed_token=None):
    """Return (due, occurrence token) without shifting after late completion.

    Each occurrence is identified by its scheduled local datetime. Days beyond
    the end of a month are clamped to the last day.
    """
    kind = config["trigger_type"]
    if kind not in ("monthly", "yearly"):
        raise ValueError("Unsupported schedule")
    interval = int(config.get("schedule_interval", 1))
    if not 1 <= interval <= 120:
        raise ValueError("Schedule interval must be between 1 and 120")
    day = int(config.get("month_day", 1))
    hour, minute = int(config.get("hour", 9)), int(config.get("minute", 0))
    if kind == "monthly":
        if not 1 <= day <= 31:
            raise ValueError("Invalid day of month")
        origin = 2000 * 12
        current = now.year * 12 + now.month - 1
        period = origin + ((current - origin) // interval) * interval

        def occurrence(p):
            year, month0 = divmod(p, 12)
            month = month0 + 1
            return now.replace(year=year, month=month,
                               day=min(day, monthrange(year, month)[1]),
                               hour=hour, minute=minute, second=0, microsecond=0)
    else:
        month = int(config.get("year_month", 1))
        if not 1 <= month <= 12 or not 1 <= day <= 31:
            raise ValueError("Invalid annual date")
        origin = 2000
        period = origin + ((now.year - origin) // interval) * interval

        def occurrence(p):
            return now.replace(year=p, month=month,
                               day=min(day, monthrange(p, month)[1]),
                               hour=hour, minute=minute, second=0, microsecond=0)

    slot = occurrence(period)
    if slot > now:
        period -= interval
        slot = occurrence(period)
    token = f"{kind}:{slot.isoformat()}"
    if (completed_token == token or
            (created is not None and created > slot and completed_token is None)):
        slot = occurrence(period + interval)
        token = f"{kind}:{slot.isoformat()}"
    return slot, token
