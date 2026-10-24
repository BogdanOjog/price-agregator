"""Validity dates in Italian store text, often without a year ("fino al 04/11", "fino al 4 novembre").

The missing year is the one that puts the date on or after the observation day; a result more
than six months away is suspicious and flagged for review."""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7,
    "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}
MONTH = "(" + "|".join(MONTHS) + ")"
SUSPICIOUS_DAYS = 183

# "dal 22/10 al 04/11/2026", "dal 22.10 al 4.11"
RANGE_NUMERIC = re.compile(r"\bdal\s+(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\s+al\s+(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?")
# "dal 22 ottobre al 4 novembre 2026", "dal 16 al 31 ottobre"
RANGE_WORDS = re.compile(rf"\bdal\s+(\d{{1,2}})°?(?:\s+{MONTH})?\s+al\s+(\d{{1,2}})°?\s+{MONTH}(?:\s+(\d{{4}}))?")
# "fino al 04/11", "fino al 01/11/2026"
UNTIL_NUMERIC = re.compile(r"\bfino\s+al\s+(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?")
# "fino al 4 novembre", "fino al 4 novembre 2026"
UNTIL_WORDS = re.compile(rf"\bfino\s+al\s+(\d{{1,2}})°?\s+{MONTH}(?:\s+(\d{{4}}))?")


def _last_sunday(year: int, month: int) -> date:
    day = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return day - timedelta(days=(day.weekday() + 1) % 7)


def rome_date(moment: datetime) -> date:
    """Calendar date in Italy of an aware UTC moment. EU rule: summer time (UTC+2) from the last
    Sunday of March to the last Sunday of October, switching at 01:00 UTC; UTC+1 otherwise.
    (Avoids needing the tz database, which Windows Python does not ship.)"""
    utc = moment.astimezone(UTC)
    start = datetime.combine(_last_sunday(utc.year, 3), time(1), UTC)
    end = datetime.combine(_last_sunday(utc.year, 10), time(1), UTC)
    offset = 2 if start <= utc < end else 1
    return (utc + timedelta(hours=offset)).date()


def rome_date_from_iso(text: str) -> date:
    return rome_date(datetime.fromisoformat(text.replace("Z", "+00:00")))


@dataclass(frozen=True)
class Validity:
    valid_from: date | None = None
    valid_until: date | None = None
    needs_review: bool = False


def _year(text: str | None) -> int | None:
    if not text:
        return None
    year = int(text)
    return year + 2000 if year < 100 else year


def _resolve(day: int, month: int, year: int | None, observed: date) -> tuple[date, bool]:
    if year is not None:
        result = date(year, month, day)
    else:
        result = date(observed.year, month, day)
        if result < observed:
            result = date(observed.year + 1, month, day)
    return result, abs((result - observed).days) > SUSPICIOUS_DAYS


def parse_validity(text: str, observed: date) -> Validity:
    """First validity expression in the text. Unrecognised text -> empty Validity (never guessed)."""
    lowered = re.sub(r"\s+", " ", text.lower())
    try:
        if match := RANGE_NUMERIC.search(lowered):
            d1, m1, y1, d2, m2, y2 = match.groups()
            year_until = _year(y2)
            until, odd_until = _resolve(int(d2), int(m2), year_until, observed)
            year_from = _year(y1) or (until.year if (int(m1), int(d1)) <= (int(m2), int(d2)) else until.year - 1)
            start = date(year_from, int(m1), int(d1))
            return Validity(start, until, odd_until or start > until)
        if match := RANGE_WORDS.search(lowered):
            d1, m1, d2, m2, y2 = match.groups()
            until, odd = _resolve(int(d2), MONTHS[m2], _year(y2), observed)
            month_from = MONTHS[m1] if m1 else MONTHS[m2]
            start = date(until.year if month_from <= until.month else until.year - 1, month_from, int(d1))
            return Validity(start, until, odd or start > until)
        if match := UNTIL_NUMERIC.search(lowered):
            day, month, year = match.groups()
            until, odd = _resolve(int(day), int(month), _year(year), observed)
            return Validity(None, until, odd)
        if match := UNTIL_WORDS.search(lowered):
            day, month, year = match.groups()
            until, odd = _resolve(int(day), MONTHS[month], _year(year), observed)
            return Validity(None, until, odd)
    except ValueError:  # impossible date such as 31/02
        return Validity(needs_review=True)
    return Validity()
