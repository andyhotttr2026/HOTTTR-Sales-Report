# -*- coding: utf-8 -*-
"""HOTTTR PPV tracker — what each chatter actually sent, grouped by team.

    py ppv_tracker.py            # yesterday, print only
    py ppv_tracker.py dry        # same
    py ppv_tracker.py send       # post to Slack
    py ppv_tracker.py dry 2026-09-08

Driven by what the data shows, not by the rota. Anyone with activity appears;
nobody is assumed to have been working. Team membership is used only to group
the table and to know each person's target.

Quotas are PPVs SENT per chatter per week, Monday to Sunday.
The Infloww employee report is daily-only — there is no sub-day granularity.
"""
import urllib.request, urllib.error, json, os, sys
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict

API_KEY = os.environ.get("INFLOWW_API_KEY")
OID     = os.environ.get("INFLOWW_OID")
WEBHOOK = os.environ.get("SLACK_WEBHOOK_PPV") or os.environ.get("SLACK_WEBHOOK_DAILY") \
          or os.environ.get("SLACK_WEBHOOK_URL")
BASE    = "https://openapi.infloww.com"
UA      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
HEADERS = {"Authorization": API_KEY, "x-oid": OID, "User-Agent": UA, "Accept": "application/json"}

MODE     = (sys.argv[1] if len(sys.argv) > 1 else "dry").lower()
ARG_DATE = sys.argv[2] if len(sys.argv) > 2 else None

# Team membership and the weekly PPV-sent quota that comes with it.
TEAMS = [
    ("Team 1", 100, ["David", "Aian", "Thomas"]),
    ("Team 2", 100, ["Cherubim", "Kennth", "Jayk"]),
    ("Team 3",  60, ["Jericho", "Drew", "Bea"]),
    ("Team 4",  60, ["Audrey", "Mark"]),
]
QUOTA  = {n: q for _, q, mem in TEAMS for n in mem}
TEAMOF = {n: t for t, _, mem in TEAMS for n in mem}

# Kept out of the ranking: the Sales Director and Team Lead are not chatters,
# and Jafferson is excluded by standing instruction.
EXCLUDE = {"Jafferson", "Andre", "Team Lead - Angie", "Angie"}


def london(d):
    def last_sun(y, mo):
        for x in range(31, 24, -1):
            try:
                if date(y, mo, x).weekday() == 6: return x
            except ValueError: continue
        return 25
    a = date(d.year, 3, last_sun(d.year, 3))
    b = date(d.year, 10, last_sun(d.year, 10))
    return timezone(timedelta(hours=1)) if a <= d < b else timezone.utc


def get(path):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def paged(path):
    out, cursor = [], None
    while True:
        d = get(path + (("&cursor=" + str(cursor)) if cursor else ""))
        out.extend(d["data"]["list"])
        cursor = d.get("cursor")
        if not d.get("hasMore") or not cursor: break
    return out


def employee_report(kind, d1, d2):
    """The API returns employeeId but not employeeName, so join it here."""
    names = {e["employeeId"]: e["employeeName"] for e in paged("/v1/employees?limit=100")}
    ids = list(names)
    ep = "employee-sales-summary" if kind == "sales" else "employee-chat-summary"
    rows = []
    for i in range(0, len(ids), 10):
        q = "employeeIds=" + ",".join(ids[i:i + 10])
        try:
            rows += paged(f"/v1/employee-report/{ep}?{q}"
                          f"&startTime={d1}&endTime={d2}&platformCode=OnlyFans")
        except urllib.error.HTTPError as e:
            print(f"  ! {kind} {d1}..{d2} chunk {i//10}: HTTP {e.code}")
    for r in rows:
        r["employeeName"] = names.get(r["employeeId"], r["employeeId"])
    return rows


def collect(d1, d2):
    agg = defaultdict(lambda: defaultdict(int))
    for r in employee_report("sales", d1, d2):
        agg[r["employeeName"]]["sales"] += int(r.get("ppvSalesAmount") or 0)
    for r in employee_report("chat", d1, d2):
        a = agg[r["employeeName"]]
        a["sent"] += int(r.get("directPpvsSent") or 0)
        a["unl"]  += int(r.get("ppvsUnlocked") or 0)
        a["fans"] += int(r.get("fansChatted") or 0)
        a["dm"]   += int(r.get("directMessagesSent") or 0)
    return agg


# ── window ────────────────────────────────────────────────────────────────────
if ARG_DATE:
    DAY = date.fromisoformat(ARG_DATE)
else:
    u = datetime.now(timezone.utc)
    DAY = u.astimezone(london(u.date())).date() - timedelta(days=1)

WK_MON = DAY - timedelta(days=DAY.weekday())
DAY_N  = (DAY - WK_MON).days + 1

if not API_KEY or not OID:
    print("ERROR: INFLOWW_API_KEY / INFLOWW_OID not set"); sys.exit(1)

print(f"Pulling {DAY} (day {DAY_N} of 7, week of {WK_MON}) ...")
day_agg  = collect(DAY.isoformat(), DAY.isoformat())
week_agg = collect(WK_MON.isoformat(), DAY.isoformat())


def rate(u, s): return f"{u/s*100:.0f}%" if s else "—"
def M(c):       return f"${c/100:,.2f}"


# ── rows ──────────────────────────────────────────────────────────────────────
people = {n for n in set(day_agg) | set(week_agg) if n not in EXCLUDE}
rows = []
for n in people:
    d, w = day_agg.get(n, {}), week_agg.get(n, {})
    q = QUOTA.get(n)
    wsent = w.get("sent", 0)
    rows.append(dict(
        name=n, quota=q,
        sent=d.get("sent", 0), unl=d.get("unl", 0), dm=d.get("dm", 0),
        fans=d.get("fans", 0), sales=d.get("sales", 0),
        wsent=wsent, wunl=w.get("unl", 0),
        left=max(q - wsent, 0) if q else None,
        pace=(q * DAY_N / 7) if q else None))

tot = defaultdict(int)
for r in rows:
    for k in ("sent", "unl", "dm", "fans", "sales", "wsent", "wunl"):
        tot[k] += r[k]
    if r["quota"]: tot["quota"] += r["quota"]
tot["pace"] = tot["quota"] * DAY_N / 7


def sub(g):
    d = {k: sum(r[k] for r in g) for k in
         ("sent", "unl", "dm", "fans", "sales", "wsent", "wunl")}
    d["quota"] = sum(r["quota"] or 0 for r in g)
    d["pace"]  = sum(r["pace"] or 0 for r in g)
    d["left"]  = max(d["quota"] - d["wsent"], 0)
    return d


by_team = []
for team, q, mem in TEAMS:
    g = sorted([r for r in rows if TEAMOF.get(r["name"]) == team],
               key=lambda r: (-r["sent"], -r["wsent"], r["name"]))
    if g: by_team.append((team, g, sub(g)))
other = sorted([r for r in rows if r["name"] not in TEAMOF],
               key=lambda r: (-r["sent"], r["name"]))
if other: by_team.append(("Not on a team", other, sub(other)))


def status(r):
    if not r["quota"]:           return "no quota set"
    if r["wsent"] >= r["quota"]: return "MET"
    if r["wsent"] >= r["pace"]:  return "on pace"
    return f"{r['pace'] - r['wsent']:.0f} behind"


def tstatus(t):
    if not t["quota"]:           return ""
    if t["wsent"] >= t["quota"]: return "MET"
    if t["wsent"] >= t["pace"]:  return "on pace"
    return "SHORT"


# ── print ─────────────────────────────────────────────────────────────────────
HDR = (f"{'chatter':<11}{'sent':>6}{'unlk':>6}{'rate':>7}{'DMs':>7}{'fans':>6}"
       f"{'PPV sales':>12}{'wk sent':>9}{'quota':>7}{'left':>6}  status")

print("\n" + "=" * 88)
print(f"PPV TRACKER — {DAY:%A %d %B %Y}   (day {DAY_N} of 7, week of {WK_MON:%d %b})")
print("=" * 88)

for team, g, t in by_team:
    active = f"{len(g)} {'person' if len(g) == 1 else 'people'} active"
    print(f"\n{team}   ·   weekly target {t['quota'] or '—'}   ·   {active}")
    print(HDR)
    for r in g:
        print(f"{r['name'][:11]:<11}{r['sent']:>6}{r['unl']:>6}{rate(r['unl'],r['sent']):>7}"
              f"{r['dm']:>7}{r['fans']:>6}{M(r['sales']):>12}{r['wsent']:>9}"
              f"{(r['quota'] or '—'):>7}{(r['left'] if r['left'] is not None else '—'):>6}"
              f"  {status(r)}")
    print(f"{'  subtotal':<11}{t['sent']:>6}{t['unl']:>6}{rate(t['unl'],t['sent']):>7}"
          f"{t['dm']:>7}{t['fans']:>6}{M(t['sales']):>12}{t['wsent']:>9}"
          f"{(t['quota'] or '—'):>7}{t['left']:>6}  {tstatus(t)}")

print("\n" + "-" * 88)
print(f"{'AGENCY':<11}{tot['sent']:>6}{tot['unl']:>6}{rate(tot['unl'],tot['sent']):>7}"
      f"{tot['dm']:>7}{tot['fans']:>6}{M(tot['sales']):>12}{tot['wsent']:>9}"
      f"{tot['quota']:>7}{max(tot['quota']-tot['wsent'],0):>6}  pace {tot['pace']:.0f}")

try:
    from ppv_widget import write_widget
    png = write_widget(DAY, DAY_N, WK_MON, by_team, tot)
    print("\nWrote docs/ppv.html" + (f" and {png}" if png else " (no PNG - Chrome missing)"))
except Exception as e:
    png = None
    print(f"\nWidget render failed: {e}")

zero = [r["name"] for r in rows if r["sent"] == 0 and r["dm"] < 50]
idle = [r["name"] for r in rows if r["sent"] == 0 and r["dm"] >= 50]
if zero: print(f"\nSent nothing, barely messaged: {', '.join(zero)} — likely did not work.")
if idle: print(f"Messaging but not offering: {', '.join(idle)} — worked, sent no PPVs.")
print("\nFigures are chatter-attributed and GROSS. Unlock counts settle over ~3 days,")
print("so the most recent days read low; PPVs sent settle faster.")


# ── Slack ─────────────────────────────────────────────────────────────────────
def slack_blocks():
    L = []
    for team, g, t in by_team:
        L.append(f"{team.upper():<10}{'SENT':>5}{'UNL':>5}{'RATE':>6}{'DMS':>6}{'WK':>5}{'LEFT':>6}")
        for r in g:
            L.append(f"  {r['name'][:8]:<8}{r['sent']:>5}{r['unl']:>5}"
                     f"{rate(r['unl'],r['sent']):>6}{r['dm']:>6}{r['wsent']:>5}"
                     f"{(r['left'] if r['left'] is not None else '—'):>6}")
        L.append(f"  {'total':<8}{t['sent']:>5}{t['unl']:>5}{rate(t['unl'],t['sent']):>6}"
                 f"{t['dm']:>6}{t['wsent']:>5}{t['left']:>6}")
        L.append("")
    L.append(f"{'AGENCY':<10}{tot['sent']:>5}{tot['unl']:>5}{rate(tot['unl'],tot['sent']):>6}"
             f"{tot['dm']:>6}{tot['wsent']:>5}{max(tot['quota']-tot['wsent'],0):>6}")
    gs = "   ".join(
        f"*{team}* {t['wsent']}/{t['quota']}"
        f" {'✅' if t['wsent'] >= t['quota'] else ('🟢' if t['wsent'] >= t['pace'] else '🔴')}"
        for team, g, t in by_team if t["quota"])
    top = max(rows, key=lambda r: r["sent"]) if rows else None
    head = f"📨 PPV Tracker — {DAY:%A}, {DAY:%B} {DAY.day}, {DAY.year}"
    return [
        {"type": "header", "text": {"type": "plain_text", "text": head}},
        {"type": "context", "elements": [{"type": "mrkdwn",
         "text": f"Day {DAY_N} of 7 · week of {WK_MON:%d %b} · quota is PPVs *sent*"}]},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Sent today*\n{tot['sent']}"},
            {"type": "mrkdwn", "text": f"*Unlocked*\n{tot['unl']}  ·  {rate(tot['unl'],tot['sent'])}"},
            {"type": "mrkdwn", "text": f"*PPV sales (gross)*\n{M(tot['sales'])}"},
            {"type": "mrkdwn", "text": f"*Week to date*\n{tot['wsent']} / {tot['quota']} · pace {tot['pace']:.0f}"}]},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Weekly quota*\n{gs}" +
                 (f"\n*Top today*  {top['name']} — {top['sent']} sent" if top and top["sent"] else "")}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"```{chr(10).join(L).rstrip()}```"}},
        {"type": "context", "elements": [{"type": "mrkdwn",
         "text": "Chatter-attributed, gross · unlock counts settle over ~3 days · "
                 "a zero with few DMs usually means the shift did not run"}]},
    ]


if MODE == "send":
    if not WEBHOOK:
        print("\nERROR: no SLACK_WEBHOOK_PPV / _DAILY / _URL set"); sys.exit(1)
    req = urllib.request.Request(WEBHOOK, data=json.dumps({"blocks": slack_blocks()}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15)
        print(f"\nSent to Slack: {DAY} · {tot['sent']} sent · {tot['unl']} unlocked")
    except urllib.error.HTTPError as e:
        print(f"\nSlack error {e.code}: {e.read().decode()}"); sys.exit(1)
else:
    print(f"\n[{MODE}] not posting to Slack.")
