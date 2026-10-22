#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["fastapi", "uvicorn", "httpx"]
# ///
#
# Port of https://github.com/cwverhey/adventofcode/tree/main/leaderboard to python and fastapi
#

import json
import os
import random
import time
from datetime import datetime
from html import escape
from math import floor
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

app = FastAPI()

TZ = ZoneInfo("Europe/Amsterdam")
COOKIEFILE = os.environ.get(
    "AOC_COOKIEFILE", "/tmp/aoc-leaderboard/cookie.txt"
)  # must look like: "session=f0oBaRbaz" or session:"f0oBaRbaz"
print(f"{COOKIEFILE=}")
MAX_AGE = 10 * 60  # max cache age
DEFAULT_BOARD = 380357

USERPAGES = {
    379312: "https://github.com/EagleErwin/AdventOfCode/tree/master/{year}",
    380357: "https://github.com/HarmtH/aoc/tree/master/{year}",
    380677: "https://github.com/apie/advent-of-code/tree/master/{year}",
    523364: "https://github.com/kevba/aoc{year}",
    607065: "https://github.com/ysmilda/Advent-of-code/tree/master/pkg/{year}",
    1616236: "https://github.com/cwverhey/adventofcode/tree/main/{year}",
    1620462: "https://github.com/ivorbosloper/aoc/tree/master/{year}",
    1838848: "https://github.com/leonschenk/codeofadvent/tree/main/{year}",
    1907446: "https://github.com/JJStoker/aoc/tree/main/a{year}",
}

STYLE = """
        html, body {margin: 0px; padding: 0px; font-family: monospace;}
        a {color: #000; text-decoration-style: dotted; text-decoration-color: #BBB;}

        .mtime {position: fixed; top: 0px; right: 0px; margin: 8pt; padding: 8pt; line-height: 150%; background-color: rgba(255, 255, 255, 0.8);}
        .yearform {display: inline;}
        #year {font-family: monospace;}
        .darkmodetoggle.light {display: none;}

        .row {white-space: pre; padding-left: 50pt; padding-right: 100pt; clear: left;}
        .stars {padding: 10pt; width: 40pt; font-size: 300%; text-align: center; position: absolute; left: 0;}
        .user {float: left; padding: 10pt; }

        .name {font-weight: bold;}
        .lastact {line-height: 200%;}
        .dt {color: #AAA;}

        body.dark {
            color-scheme: dark;
            color: #6f6f6f;
            background-color: #15191d;
            & a {color: #6f6f6f; text-decoration-color: #6f6f6f;}
            .mtime {background-color: #15191d}
            .dt {color: #4e4e4e;}
            .darkmodetoggle.light {display: unset;}
            .darkmodetoggle.dark {display: none;}
        }

        @media (pointer: coarse), (hover: none) {
            [title] {
                position: relative;
                display: inline-flex;
                justify-content: left;
                z-index: auto;
                line-height: 100%;
            }
            [title]:focus::after {
                content: attr(title);
                position: absolute;
                top: 0;
                padding: 0;
                margin: 0;
                background-color: #DDD;
                z-index: 1;
            }
        }
"""

SCRIPT = """
        function toggleDarkMode() {
            document.body.classList.toggle('dark');
            localStorage.setItem('darkmode', document.body.classList.contains('dark'));
        }

        window.addEventListener('load', function () {
            if (localStorage.getItem('darkmode') === undefined) {
                if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
                    localStorage.setItem('darkmode', true);
                }
            }
            if (localStorage.getItem('darkmode') === 'true') {
                toggleDarkMode();
            }
        });
"""


def local(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, TZ)


def rround(x: float) -> int:
    return floor(x + 0.5)  # round half up, like PHP


def leaderboard_time(ts: int, reference_day: int, year: int) -> str:
    """Terse date/timestamp for 'last active' and task completion times."""
    if ts == 0:
        return "-"

    d = local(ts)
    span = f'<span title="{d:%d-%m-%Y %H:%M:%S}" tabindex=0>'

    if (d.day, d.month, d.year) == (reference_day, 12, year):  # on the reference day
        span += f"{d:%H:%M}"
    elif d.year == year:  # in the reference year
        span += f"{d:%d-%m}"
    else:
        span += f"{d:%d-%m-%Y}"

    return span + "</span>"


def last_update_time(ts: float) -> str:
    """Terse date/timestamp for last cache update."""
    if ts == 0:
        return "-"

    d = local(int(ts))
    span = f'<span title="{d:%d-%m-%Y %H:%M:%S}" tabindex=0>'

    if d.date() == datetime.now(TZ).date():
        span += f"{d:%H:%M}"
    else:
        span += f"{d:%d-%m-%Y %H:%M}"

    return span + "</span>"


def dt(time1: int, time2: int) -> str:
    """Delta t between two timestamps."""
    d = time2 - time1

    if d < 99.5:
        short = f"{d}s"
        long = f"{d}s"
    elif d < 99.5 * 60:
        short = f"{rround(d / 60)}m"
        long = f"{d // 60}m {d % 60}s"
    elif d < 99.5 * 60 * 48:
        short = f"{rround(d / 3600)}h"
        long = f"{d // 3600}h {(d % 3600) // 60}m {d % 60}s"
    else:
        short = f"{rround(d / 86400)}D"
        long = f"{d // 86400}D {(d % 86400) // 3600}h {(d % 3600) // 60}m {d % 60}s"

    return f'<span class="dt" title="∆ {long}" tabindex=0>∆ {short:>3}</span>'


def fetch_leaderboard(board: int, year: int) -> tuple[dict, float] | None:
    """Return (leaderboard json, cache mtime), refreshing the cache when stale."""
    cachefile = f"/tmp/aoc-leaderboard/{board}-{year}.json"

    if (
        not os.path.exists(cachefile)
        or os.path.getmtime(cachefile) < time.time() - MAX_AGE
    ):
        try:
            with open(COOKIEFILE) as f:
                cookie = f.readline().strip().replace(":", "=").replace('"', '')
            r = httpx.get(
                f"https://adventofcode.com/{year}/leaderboard/private/view/{board}.json",
                headers={"Cookie": cookie},
                timeout=15,
            )
            if json.loads(r.text):  # only cache non-empty, valid json
                with open(cachefile, "w") as f:
                    f.write(r.text)
        except (OSError, ValueError, httpx.HTTPError) as e:
            print(e)
            print(r.status_code)
            print(r.text)
            pass  # fall back to a stale cache, if any

    try:
        with open(cachefile) as f:
            data = json.load(f)
        return data, os.path.getmtime(cachefile)
    except (OSError, ValueError):
        return None


def group_members(data: dict) -> list[tuple[int, list[dict]]]:
    """Group members by stars (descending), shuffled within each group."""
    groups: dict[int, list[dict]] = {}
    for m in data["members"].values():
        # remove a level of depth in the time-array, and sort it
        m["completion_day_level"] = {
            int(day): {
                int(p): s["get_star_ts"]
                for p, s in sorted(parts.items(), key=lambda kv: int(kv[0]))
            }
            for day, parts in sorted(
                (m.get("completion_day_level") or {}).items(), key=lambda kv: int(kv[0])
            )
        }
        groups.setdefault(m["stars"], []).append(m)

    for users in groups.values():
        random.shuffle(users)

    return sorted(groups.items(), key=lambda kv: kv[0], reverse=True)


def render_user(user: dict, year: int, days: list[int], today: int) -> str:
    out = ['<div class="user">']

    name = escape(str(user.get("name") or f"anonymous user #{user['id']}"))
    out.append(f'<span class="name" data-uid="{user["id"]}" >')
    if user["id"] in USERPAGES:
        out.append(
            f"<a href='{USERPAGES[user['id']].format(year=year)}' target='_blank'>{name}</a>"
        )
    else:
        out.append(name)
    out.append(f"</span> 🏅{user['local_score']}\n")

    out.append(
        f"<span class='lastact'>last activity: {leaderboard_time(user['last_star_ts'], today, year)}</span>"
    )

    levels = user["completion_day_level"]
    for day in days:
        out.append(
            f'\nday <a href="https://adventofcode.com/{year}/day/{day}">{day:02d}</a> '
        )
        out.append(
            f"(<a href='https://vps3.stad.gr/cachingproxy/proxy?url=https://adventofcode.com/{year}/day/{day}'>mob</a>): "
        )

        if day in levels:
            parts = levels[day]
            out.append(leaderboard_time(parts.get(1, 0), day, year))
            out.append(" / ")
            if len(parts) == 2:
                out.append(leaderboard_time(parts[2], day, year))
                out.append("&nbsp;&nbsp;")
                out.append(dt(parts[1], parts[2]))
            else:
                out.append("-")
        else:
            out.append("-")

    out.append("</div>")
    return "".join(out)


@app.get("/", response_class=HTMLResponse)
def index(id: int | None = Query(None), year: int | None = Query(None)):
    now = datetime.now(TZ)
    board = id if id is not None else DEFAULT_BOARD

    if year is None:
        year = now.year - 1 if now.month < 12 else now.year

    years = list(range(now.year - 1, 2014, -1))  # first AoC edition was 2015
    if now.month == 12:
        years.insert(0, now.year)  # add current year if month ≥ 12

    days = list(range(1, 13 if year >= 2025 else 26))

    if year == now.year and now.day <= max(
        days
    ):  # limit day range if from 1-24 december
        days = list(
            range(1, now.day + 1 if now.hour >= 6 else now.day)
        )  # include today after 6 AM

    result = fetch_leaderboard(board, year)
    if result is None:
        return PlainTextResponse(
            "ERROR: could not load leaderboard data. Change your request parameters or retry at a later time.",
            status_code=404,
        )
    data, data_time = result

    options = "".join(
        f"\t<option value={y}{' selected' if y == year else ''}>{y}</option>\n"
        for y in years
    )
    hidden_id = (
        f'<input type="hidden" name="id" value="{board}" />' if id is not None else ""
    )

    rows = "".join(
        f"<div class='row'>\n<div class='stars'>⭐️<br />{stars}</div>"
        + "".join(render_user(u, year, days, now.day) for u in users)
        + "</div>\n\n"
        for stars, users in group_members(data)
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Advent of Code Leaderboard #{board}</title>
    <style>{STYLE}</style>
    <script>{SCRIPT}</script>
</head>
<body>

<div class='mtime'>

    year: <form action='' class='yearform'>
    {hidden_id}
    <select name="year" id="year" onchange="this.form.submit()">
    {options}
    </select>
    <noscript><input type="submit" value="go"></noscript>
    </form><br />

    last update: {last_update_time(data_time)}<br />
    <a href='https://adventofcode.com/{year}/leaderboard/private/view/{board}'>AoC page</a>
    <a href="#" onclick="toggleDarkMode('dark'); return false;" class="darkmodetoggle dark">🌖</a>
    <a href="#" onclick="toggleDarkMode('light'); return false;" class="darkmodetoggle light">🌘️</a>

</div>

{rows}
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", 12345)),
        reload=bool(os.environ.get("DEBUG")),
    )
