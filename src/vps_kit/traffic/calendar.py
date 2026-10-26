"""同一月日历供计划、unit 和执行器使用；不读取或修改系统时间。"""
import calendar as month_calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import FIXED_SECONDS, schedule_validate
from .contracts import ContractError, require, utc

UTC = timezone.utc
WINDOW_SECONDS = 240
MAINTENANCE_SECONDS = 900


def anchor_from_local(local, zone):
    """精确整秒本地日期转 UTC；DST 不存在或重叠不猜测。"""
    import re
    from zoneinfo import ZoneInfoNotFoundError
    require(type(local) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}',local), 'ANCHOR_LOCAL_FORMAT')
    require(type(zone) is str and (zone == 'UTC' or ('/' in zone and not zone.startswith('/') and '..' not in zone
            and re.fullmatch(r'[A-Za-z0-9_+/-]+',zone))), 'ANCHOR_TIMEZONE')
    try:
        wall, tz = datetime.fromisoformat(local), ZoneInfo(zone)
        choices = {wall.replace(tzinfo=tz,fold=f).astimezone(UTC) for f in (0,1)
                   if wall.replace(tzinfo=tz,fold=f).astimezone(UTC).astimezone(tz).replace(tzinfo=None) == wall}
    except (ValueError,OverflowError,ZoneInfoNotFoundError):
        raise ContractError('ANCHOR_LOCAL_OR_TIMEZONE') from None
    require(len(choices)==1,'ANCHOR_DST_AMBIGUOUS_OR_MISSING')
    return stamp(choices.pop())


def stamp(value):
    require(value.tzinfo is not None, "CLOCK_NAIVE")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def boundary(schedule, year, month):
    """返回该月唯一 UTC 边界；skip 短月返回 None，DST 歧义直接失败。"""
    last = month_calendar.monthrange(year, month)[1]
    day = schedule["day"]
    if day == "last":
        day = last
    elif day > last:
        if schedule["short_month"] == "skip":
            return None
        day = last
    hour, minute, second = map(int, schedule["time"].split(":"))
    zone = ZoneInfo(schedule["timezone"])
    wall = datetime(year, month, day, hour, minute, second)
    choices = set()
    try:
        for fold in (0, 1):
            value = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            if value.astimezone(zone).replace(tzinfo=None) == wall:
                choices.add(value)
    except (OverflowError, ValueError):
        raise ContractError("CALENDAR_RANGE") from None
    require(len(choices) == 1, "CALENDAR_DST_AMBIGUOUS_OR_MISSING")
    return choices.pop()


def walk(schedule, start, count=24, direction=1, inclusive=False):
    require(start.tzinfo is not None and direction in (-1, 1) and 1 <= count <= 1200, "CALENDAR_ARGUMENT")
    if schedule.get('kind') == 'fixed-interval':
        anchor = utc(schedule['anchor_utc'], 'CALENDAR_ANCHOR')
        delta = start - anchor
        step = timedelta(seconds=FIXED_SECONDS)
        index = delta // step
        exact = delta == index * step
        if direction == 1 and not (inclusive and exact):
            index += 1
        elif direction == -1 and exact and not inclusive:
            index -= 1
        if direction == 1:
            index = max(0, index)
        result = []
        try:
            for n in range(index, index + direction * count, direction):
                if n < 0:
                    break
                result.append(anchor + n * step)
        except (OverflowError, ValueError):
            raise ContractError('CALENDAR_RANGE') from None
        return result
    local = start.astimezone(ZoneInfo(schedule["timezone"]))
    index = local.year * 12 + local.month - 1
    found = []
    for _ in range(count * 3 + 24):
        year, m = divmod(index, 12)
        require(1 <= year <= 9999, "CALENDAR_RANGE")
        value = boundary(schedule, year, m + 1)
        if value is not None:
            delta = (value - start).total_seconds() * direction
            if delta > 0 or (inclusive and delta == 0):
                found.append(value)
                if len(found) == count:
                    return found
        index += direction
    raise ContractError("CALENDAR_SEARCH_LIMIT")


def next_boundary(schedule, after, inclusive=False):
    return walk(schedule, after, 1, inclusive=inclusive)[0]


def expressions(schedule):
    schedule_validate(schedule)
    if schedule.get('kind') == 'fixed-interval':
        return ['*-*-* ' + utc(schedule['anchor_utc'], 'CALENDAR_ANCHOR').strftime('%H:%M:%S') + ' UTC']
    day, policy = schedule["day"], schedule["short_month"]
    if day == "last" or (day == 31 and policy == "clamp-last-day"):
        dates = ["*-*~01"]
    elif day in (29, 30) and policy == "clamp-last-day":
        dates = ["*-02~01", f"*-01,03..12-{day:02d}"]
    else:
        dates = [f"*-*-{day:02d}"]
    return [f"{date} {schedule['time']} {schedule['timezone']}" for date in dates]


def preview(config, now, display_zone="UTC"):
    schedule = config.data["schedule"]
    upcoming = walk(schedule, now, 24)
    first = utc(schedule["first_boundary_utc"], "CALENDAR_FIRST")
    # 首次边界推迟多月也单独检查其后的 24 次，不能只检查普通 timer 唤醒。
    from_first = walk(schedule, first, 24, inclusive=True) if first > now else walk(schedule, now, 24)
    display = ZoneInfo(display_zone)
    return {"schedule_kind": schedule.get('kind', 'monthly-calendar'),
            "on_calendar": expressions(schedule), "next_wakeup_utc": stamp(next_wakeup(schedule, now)),
            "next_24_wakeups_utc": [stamp(v) for v in wakeups(schedule, now, 24)],
            "next_business_boundary_utc": stamp(upcoming[0]),
            "first_allowed_utc": stamp(first), 'first_allowed_next_24_utc': [stamp(v) for v in from_first],
            "next_24": [
                {"utc": stamp(value), "provider": value.astimezone(ZoneInfo(schedule["timezone"])).isoformat(),
                 "display": value.astimezone(display).isoformat()} for value in upcoming]}


def maintenance_window(schedules, now):
    for schedule in schedules:
        before = walk(schedule, now, 1, direction=-1, inclusive=True)
        after = next_boundary(schedule, now)
        distances = [(after-now).total_seconds()] + [(now-b).total_seconds() for b in before]
        require(min(distances) >= MAINTENANCE_SECONDS,
                "MAINTENANCE_NEAR_BOUNDARY")


def wakeups(schedule, after, count=24):
    if schedule.get('kind') != 'fixed-interval':
        return walk(schedule, after, count)
    anchor = utc(schedule['anchor_utc'], 'CALENDAR_ANCHOR')
    value = after.astimezone(UTC).replace(hour=anchor.hour, minute=anchor.minute, second=anchor.second, microsecond=0)
    try:
        if value <= after:
            value += timedelta(days=1)
        return [value + timedelta(days=n) for n in range(count)]
    except (OverflowError, ValueError):
        raise ContractError('CALENDAR_RANGE') from None


def next_wakeup(schedule, after):
    return wakeups(schedule, after, 1)[0]


def rule(schedule):
    """固定周期的展示时区不改变授权；v1/v2 月历语义相同。"""
    ignored = {'first_boundary_utc', 'kind'}
    if schedule.get('kind') == 'fixed-interval':
        ignored.add('timezone')
    return {k:v for k,v in schedule.items() if k not in ignored}


def switching_boundary(old, new, now, old_next):
    """调用者先验证旧历史；B_new 不得提前创造第二次周期机会。"""
    a, b = old.data["schedule"], new.data["schedule"]
    same_rule = rule(a) == rule(b)
    if same_rule:
        require(a["first_boundary_utc"] == b["first_boundary_utc"], "SWITCH_FIRST_BOUNDARY_CHANGED")
        effective = old_next
    else:
        effective = utc(b["first_boundary_utc"], "SWITCH_BOUNDARY")
        require(effective >= old_next, "SWITCH_SHORTENS_PERIOD")
    require(effective > now + timedelta(seconds=MAINTENANCE_SECONDS), "SWITCH_NOT_FUTURE")
    return effective
