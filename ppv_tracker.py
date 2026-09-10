# -*- coding: utf-8 -*-
"""HOTTTR PPV tracker — what each chatter actually sent.

    py ppv_tracker.py            # yesterday, print only
    py ppv_tracker.py dry        # same
    py ppv_tracker.py send       # post to Slack
    py ppv_tracker.py dry 2026-09-08

Driven entirely by what the data shows, not by the rota. Anyone with activity
that day appears; anyone with none does not. No shift labels, no assumptions
about who was meant to be working.

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

# Weekly PPV-sent quota per chatter. The only roster the report needs.
QUOTA = {name: 100 for name in ("Cherubim", "David", "Aian", "Thomas", "Kennth", "Jayk")}
QUOTA.update({name: 60 for name in ("Mark", "Bea", "Jericho", "Drew", "Audrey")})

# Kept out of the ranking: the Sales Director and the Team Lead are not chatters,
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
        a["sent"]     += int(r.get("directPpvsSent") or 0)
        a["unl"]      += int(r.get("ppvsUnlocked") or 0)
        a["fans"]     += int(r.get("fansChatted") or 0)
        a["dm"]       += int(r.get("directMessagesSent") or 0)
        a["creators"] += 1
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


# Everyone who did anything on the day or so far this week.
people = {n for n in set(day_agg) | set(week_agg) if n not in EXCLUDE}
rows = []
for n in people:
    d, w = day_agg.get(n, {}), week_agg.get(n, {})
    q = QUOTA.get(n)
    wsent = w.get("sent", 0)
    rows.append(dict(
        name=n, quota=q,
        sent=d.get("sent", 0), unl=d.get("unl", 0), dm=d.get("dm", 0),
        fans=d.get("fans", 0), sales=d.get("sales", 0), creators=d.get("creators", 0),
        wsent=wsent, wunl=w.get("unl", 0), wsales=w.get("sales", 0),
        left=max(q - wsent, 0) if q else None,
        pace=(q * DAY_N / 7) if q else None))
rows.sort(key=lambda r: (-r["sent"], -r["wsent"], r["name"]))

tot = defaultdict(int)
for r in rows:
    for k in ("sent", "unl", "dm", "fans", "sales", "wsent", "wunl", "wsales"):
        tot[k] += r[k]
    if r["quota"]: tot["quota"] += r["quota"]
tot["pace"] = tot["quota"] * DAY_N / 7

groups = []
for label, q in (("100 / week", 100), ("60 / week", 60)):
    g = [r for r in rows if r["quota"] == q]
    if not g: continue
    groups.append(dict(label=label, n=len(g),
                       sent=sum(r["sent"] for r in g), unl=sum(r["unl"] for r in g),
                       wsent=sum(r["wsent"] for r in g),
                       quota=sum(r["quota"] for r in g),
                       pace=sum(r["pace"] for r in g)))

# ── print ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 86)
print(f"PPV TRACKER — {DAY:%A %d %B %Y}   (day {DAY_N} of 7, week of {WK_MON:%d %b})")
print("=" * 86)
print(f"\n{'chatter':<11}{'sent':>6}{'unlk':>6}{'rate':>7}{'DMs':>7}{'fans':>6}"
      f"{'PPV sales':>12}{'wk sent':>9}{'quota':>7}{'left':>6}  status")
for r in rows:
    if r["quota"] is None:
        st = "no quota set"
    elif r["wsent"] >= r["quota"]:
        st = "MET"
    elif r["wsent"] >= r["pace"]:
        st = "on pace"
    else:
        st = f"{r['pace'] - r['wsent']:.0f} behind"
    print(f"{r['name'][:11]:<11}{r['sent']:>6}{r['unl']:>6}{rate(r['unl'],r['sent']):>7}"
          f"{r['dm']:>7}{r['fans']:>6}{M(r['sales']):>12}{r['wsent']:>9}"
          f"{r['quota'] if r['quota'] else '—':>7}{r['left'] if r['left'] is not None else '—':>6}"
          f"  {st}")
print(f"\n{'TOTAL':<11}{tot['sent']:>6}{tot['unl']:>6}{rate(tot['unl'],tot['sent']):>7}"
      f"{tot['dm']:>7}{tot['fans']:>6}{M(tot['sales']):>12}{tot['wsent']:>9}"
      f"{tot['quota']:>7}{max(tot['quota']-tot['wsent'],0):>6}"
      f"  pace {tot['pace']:.0f}")

print(f"\n{'quota group':<14}{'people':>7}{'sent':>7}{'unlk':>6}{'wk sent':>9}"
      f"{'target':>8}{'pace':>7}  status")
for g in groups:
    st = "MET" if g["wsent"] >= g["quota"] else ("on pace" if g["wsent"] >= g["pace"] else "SHORT")
    print(f"{g['label']:<14}{g['n']:>7}{g['sent']:>7}{g['unl']:>6}{g['wsent']:>9}"
          f"{g['quota']:>8}{g['pace']:>7.0f}  {st}")

zero = [r["name"] for r in rows if r["sent"] == 0 and r["dm"] < 50]
if zero:
    print(f"\nSent nothing and barely messaged: {', '.join(zero)} — check before reading as underperformance.")
print("\nFigures are chatter-attributed and GROSS. Unlock counts settle over ~3 days,")
print("so the most recent days read low; PPVs sent settle faster.")


# ── Slack ─────────────────────────────────────────────────────────────────────
def slack_blocks():
    L = [f"{'CHATTER':<10}{'SENT':>5}{'UNL':>5}{'RATE':>6}{'DMS':>6}{'WK':>5}{'LEFT':>6}", "─" * 43]
    for r in rows:
        L.append(f"{r['name'][:10]:<10}{r['sent']:>5}{r['unl']:>5}{rate(r['unl'],r['sent']):>6}"
                 f"{r['dm']:>6}{r['wsent']:>5}"
                 f"{(r['left'] if r['left'] is not None else '—'):>6}")
    L.append("─" * 43)
    L.append(f"{'TOTAL':<10}{tot['sent']:>5}{tot['unl']:>5}{rate(tot['unl'],tot['sent']):>6}"
             f"{tot['dm']:>6}{tot['wsent']:>5}{max(tot['quota']-tot['wsent'],0):>6}")
    gs = "   ".join(
        f"*{g['label']}* {g['wsent']}/{g['quota']}"
        f" {'✅' if g['wsent']>=g['quota'] else ('🟢' if g['wsent']>=g['pace'] else '🔴')}"
        for g in groups)
    top = rows[0] if rows and rows[0]["sent"] else None
    return [
        {"type": "header", "text": {"type": "plain_text",
         "text": f"📨 PPV Tracker — {DAY:%A}, {DAY:%B} {DAY.day}, {DAY.year}"}},
        {"type": "context", "elements": [{"type": "mrkdwn",
         "text": f"Day {DAY_N} of 7 · week of {WK_MON:%d %b} · quota is PPVs *sent*"}]},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Sent today*\n{tot['sent']}"},
            {"type": "mrkdwn", "text": f"*Unlocked*\n{tot['unl']}  ·  {rate(tot['unl'],tot['sent'])}"},
            {"type": "mrkdwn", "text": f"*PPV sales (gross)*\n{M(tot['sales'])}"},
            {"type": "mrkdwn", "text": f"*Week to date*\n{tot['wsent']} / {tot['quota']} · pace {tot['pace']:.0f}"}]},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Weekly quota*  {gs}" + (f"\n*Top today*  {top['name']} — {top['sent']} sent" if top else "")}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"```{chr(10).join(L)}```"}},
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
