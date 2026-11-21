"""Rebuild the games list in index.html from the family Google Calendar.

Reads the calendar's private iCal address (from the CALENDAR_ICS_URL
environment variable), picks out the kids' upcoming games (today onward,
any sport), and rewrites the block
between the EVENTS:START / EVENTS:END markers in index.html.

Run locally:  CALENDAR_ICS_URL="https://..." python scripts/update_schedule.py
Or pass a downloaded file:  python scripts/update_schedule.py calendar.ics
"""

import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
KIDS = ["Jack", "Wes"]
DAYS_AHEAD = 180          # show games from today through this many days out

# A calendar event counts as a game when its title STARTS with a kid's name
# and mentions a sport (or "game"). Both kids can share one event.
# Matches:      "Jack: Hoops", "Jack Soccer", "Jack + Wes Soccer", "Wes Baseball"
# Doesn't match: "Kellen: Zers game", "Visit Jack's Class!", "Boys: Skate Camp"
SPORTS = {                # word in the title -> sport shown on the page
    "basketball": "basketball", "hoops": "basketball",
    "soccer": "soccer",
    "baseball": "baseball", "t-ball": "baseball", "tball": "baseball",
    "softball": "softball",
    "football": "football",
    "volleyball": "volleyball",
    "hockey": "hockey",
    "lacrosse": "lacrosse",
    "swim": "swimming",
    "tennis": "tennis",
}
GENERIC_GAME_WORDS = ["game", "match", "tournament", "meet"]
# Weekday events (Mon–Fri) are usually practices, so on weekdays the title must
# also say one of GENERIC_GAME_WORDS (e.g. "Wes: Soccer game") to count.
WEEKDAY_NEEDS_GAME_WORD = True
# Titles containing any of these are never games.
SKIP_WORDS = ["practice", "warmup", "warm-up", "warm up", "atkinson",
              "camp", "party", "equipment", "tryout", "clinic", "pictures"]

TIMEZONE = ZoneInfo("America/Los_Angeles")
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "index.html"
BLOCK = re.compile(r"(// EVENTS:START[^\n]*\n).*?(\n// EVENTS:END)", re.S)


def load_calendar() -> icalendar.Calendar:
    if len(sys.argv) > 1:
        data = Path(sys.argv[1]).read_bytes()
    else:
        url = os.environ.get("CALENDAR_ICS_URL", "").strip()
        if not url:
            sys.exit("CALENDAR_ICS_URL is not set. Add it as a repository secret.")
        url = url.replace("webcal://", "https://", 1)
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
    return icalendar.Calendar.from_ical(data)


def match_kids(title: str):
    """Kids named in the title, but only if the title starts with one of them."""
    if not re.match(rf"^\s*({'|'.join(map(re.escape, KIDS))})\b", title, re.I):
        return []
    return [k for k in KIDS if re.search(rf"\b{re.escape(k)}\b(?!['’]s)", title, re.I)]


def game_sport(title: str):
    """The sport if this looks like a game, else None."""
    t = title.lower()
    if any(w in t for w in SKIP_WORDS):
        return None
    for word, sport in SPORTS.items():
        if re.search(rf"\b{re.escape(word)}\b", t):
            return sport
    if any(re.search(rf"\b{w}\b", t) for w in GENERIC_GAME_WORDS):
        return "sports"
    return None


def to_local(value):
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=TIMEZONE)
        return value.astimezone(TIMEZONE)
    return value  # all-day event: a plain date


def collect_games(cal):
    # SCHEDULE_TODAY=YYYY-MM-DD lets you preview the page as of another day.
    today = (dt.date.fromisoformat(os.environ["SCHEDULE_TODAY"]) if os.environ.get("SCHEDULE_TODAY")
             else dt.datetime.now(TIMEZONE).date())
    start = dt.datetime.combine(today, dt.time.min, TIMEZONE)
    end = dt.datetime.combine(today + dt.timedelta(days=DAYS_AHEAD + 1), dt.time.min, TIMEZONE)
    games = {}
    for ev in recurring_ical_events.of(cal).between(start, end):
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        title = str(ev.get("SUMMARY", "")).strip()
        kids = match_kids(title)
        sport = game_sport(title) if kids else None
        if not sport:
            continue
        s = to_local(ev.decoded("DTSTART"))
        e = to_local(ev.decoded("DTEND")) if ev.get("DTEND") else s
        if not isinstance(s, dt.datetime):
            continue  # skip all-day entries
        if WEEKDAY_NEEDS_GAME_WORD and s.weekday() < 5 and not any(
                re.search(rf"\b{w}\b", title, re.I) for w in GENERIC_GAME_WORDS):
            continue
        item = {
            "d": s.strftime("%Y-%m-%d"),
            "t": s.strftime("%H:%M"),
            "end": e.strftime("%H:%M"),
            "sport": sport,
            "who": " + ".join(kids),
            "kind": "Game",
            "title": sport.capitalize() if sport != "sports" else "Game",
            "loc": str(ev.get("LOCATION", "")).split(",")[0].strip(),  # place name only
        }
        games[(item["d"], item["t"], item["who"], sport)] = item  # de-duplicate
    return sorted(games.values(), key=lambda g: (g["d"], g["t"], g["who"]))


def render(games) -> str:
    keys = ["d", "t", "end", "sport", "who", "kind", "title", "loc"]
    rows = []
    for g in games:
        fields = ",".join(f"{k}:{json.dumps(g[k], ensure_ascii=False)}" for k in keys)
        rows.append("  {" + fields + "}")
    return "const events = [\n" + ",\n".join(rows) + "\n];"


def main():
    games = collect_games(load_calendar())
    html = PAGE.read_text(encoding="utf-8")
    if not BLOCK.search(html):
        sys.exit("Couldn't find the EVENTS:START / EVENTS:END markers in index.html.")
    new_html = BLOCK.sub(lambda m: m.group(1) + render(games) + m.group(2), html, count=1)
    if new_html == html:
        print(f"No changes — {len(games)} games already on the page.")
    else:
        PAGE.write_text(new_html, encoding="utf-8")
        print(f"Updated index.html with {len(games)} games:")
    for g in games:
        print(f"  {g['d']} {g['t']}  {g['who']:<12} {g['loc']}")


if __name__ == "__main__":
    main()
