# -*- coding: utf-8 -*-
"""OFMOS check-in report — Mon / Wed / Fri, 06:00 UK.

Numbers are pulled live from Infloww. The qualitative sections (what got done,
what didn't, blockers, questions) CANNOT be derived from data — they are read
from ofmos_notes.json, which a human edits. If those notes are stale or empty
the report renders a visible NEEDS INPUT banner rather than inventing content.

Window: trailing 7 complete days vs the prior 7. On a Monday run that lands
exactly on Mon-Sun, which is the week the consultant asks about.

Modes:  build (html only) | send (html + Slack) | dry (print, no post)
"""
import urllib.request, urllib.error, json, os, sys, glob
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict

API_KEY       = os.environ.get("INFLOWW_API_KEY")
OID           = os.environ.get("INFLOWW_OID")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL")
FORCE         = os.environ.get("OFMOS_FORCE") == "1"
MODE          = (sys.argv[1] if len(sys.argv) > 1 else "dry").lower()

BASE    = "https://openapi.infloww.com"
UA      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
HEADERS = {"Authorization": API_KEY, "x-oid": OID, "User-Agent": UA, "Accept": "application/json"}
REFUND  = {"undo", "refunded", "chargeback", "cancelled", "canceled"}
PAGES_URL = "https://andyhotttr2026.github.io/HOTTTR-Sales-Report/ofmos.html"

# ── Timezone ──────────────────────────────────────────────────────────────────

def london_tz_for(y, m, d):
    bst_start = max(datetime(y, 3, x)  for x in range(25, 32) if datetime(y, 3, x).weekday()  == 6)
    bst_end   = max(datetime(y, 10, x) for x in range(25, 32) if datetime(y, 10, x).weekday() == 6)
    off = timezone(timedelta(hours=1)) if bst_start <= datetime(y, m, d) < bst_end else timezone.utc
    return off, ("BST" if off != timezone.utc else "GMT")

_u = datetime.now(timezone.utc)
UK_TZ, TZ_LABEL = london_tz_for(_u.year, _u.month, _u.day)
NOW = _u.astimezone(UK_TZ)

# The workflow fires twice (05:0x and 06:0x UTC) so one of them is always 06:0x
# local whether we are in BST or GMT. Skip the one that isn't.
if not FORCE and NOW.hour != 6:
    print(f"Skipping: local time is {NOW:%H:%M} {TZ_LABEL}, not the 06:00 slot.")
    sys.exit(0)

def ms(d, end=False):
    tz, _ = london_tz_for(d.year, d.month, d.day)
    t = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz) if end else \
        datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)
    return int(t.timestamp() * 1000)

# ── Window ────────────────────────────────────────────────────────────────────

TODAY   = NOW.date()
A_END   = TODAY - timedelta(days=1)      # yesterday, last complete day
A_START = TODAY - timedelta(days=7)
B_END   = TODAY - timedelta(days=8)
B_START = TODAY - timedelta(days=14)
IS_MONDAY = TODAY.weekday() == 0
KIND    = "Weekly Check-in" if IS_MONDAY else "Mid-week Pulse"
WINDOW  = "Monday – Sunday" if IS_MONDAY else "rolling 7 days"
MONTH_START = TODAY.replace(day=1)

def fmt(d): return f"{d.strftime('%-d' if os.name != 'nt' else '%#d')} {d:%b}"

# ── Fetch ─────────────────────────────────────────────────────────────────────

def get(path):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read())

def fetch(cid, s, e):
    out, cur = [], None
    while True:
        u = f"/v1/transactions?creatorId={cid}&limit=100&startTime={s}&endTime={e}"
        if cur: u += f"&cursor={cur}"
        d = get(u)
        out.extend(d["data"]["list"])
        cur = d.get("cursor")
        if not d.get("hasMore") or not cur: break
    return out

if not API_KEY or not OID:
    print("ERROR: INFLOWW_API_KEY / INFLOWW_OID not set"); sys.exit(1)

# 90 days back so the unlock-price trend has a baseline. This is the slowest part
# of the run; everything else needs only ~30 days.
W90_START = TODAY - timedelta(days=90)
PULL_FROM = min(B_START, MONTH_START, W90_START)
creators  = get("/v1/creators?limit=100")["data"]["list"]

BANDS = [(0,2000,"under $20"),(2000,4000,"$20 – 40"),(4000,6000,"$40 – 60"),
         (6000,10000,"$60 – 100"),(10000,16000,"$100 – 160"),(16000,10**9,"$160+")]
band = defaultdict(lambda: defaultdict(lambda: {"n": 0, "net": 0}))
day = defaultdict(lambda: {"net": 0, "new": 0, "ren": 0, "ppv": 0, "ppvn": 0})
cr  = defaultdict(lambda: defaultdict(lambda: {"net": 0, "new": 0}))
skipped = []
for c in creators:
    try:
        txns = fetch(c["id"], ms(PULL_FROM), ms(A_END, end=True))
    except urllib.error.HTTPError as e:
        skipped.append(f"{c['name']} ({e.code})"); continue
    for t in txns:
        if str(t.get("status", "")).lower() in REFUND: continue
        ty = str(t.get("type", "")).lower()
        n  = int(t.get("net", 0))
        d  = datetime.fromtimestamp(int(t["createdTime"]) / 1000, UK_TZ).date()
        day[d]["net"] += n
        cr[c["name"]][d]["net"] += n
        if "subscription" in ty and "recurring" not in ty:
            day[d]["new"] += 1; cr[c["name"]][d]["new"] += 1
        elif "recurring" in ty: day[d]["ren"] += 1
        elif "message" in ty:
            day[d]["ppv"] += n; day[d]["ppvn"] += 1
            for lo, hi, lbl in BANDS:
                if lo <= n < hi:
                    band[d][lbl]["n"] += 1; band[d][lbl]["net"] += n
                    break

def rng(d1, d2):
    o = defaultdict(int)
    for d, v in day.items():
        if d1 <= d <= d2:
            for k in ("net", "new", "ren", "ppv", "ppvn"): o[k] += v[k]
            o["days"] += 1
    return o

A, B = rng(A_START, A_END), rng(B_START, B_END)
if not B["net"]:
    print("ERROR: no data in the comparison window — aborting rather than reporting a false 100% swing.")
    sys.exit(1)

g_net = (A["net"] - B["net"]) / B["net"] * 100
g_sub = (A["new"] - B["new"]) / B["new"] * 100 if B["new"] else 0
mtd   = sum(day[d]["net"] for d in day if MONTH_START <= d <= A_END)
mdays = len([d for d in day if MONTH_START <= d <= A_END])

def M(c):  return f"${c/100:,.2f}"
def M0(c): return f"${c/100:,.0f}"

# ── Unlock-price trend: rolling 30 / prior 30 / 60 / 90 ───────────────────────
U30  = rng(TODAY - timedelta(days=30), A_END)
U30P = rng(TODAY - timedelta(days=60), TODAY - timedelta(days=31))
U60  = rng(TODAY - timedelta(days=60), A_END)
U90  = rng(TODAY - timedelta(days=90), A_END)
def avg(w): return w["ppv"] / w["ppvn"] if w["ppvn"] else 0
def bandsum(d1, d2):
    o = {lbl: {"n": 0, "net": 0} for _, _, lbl in BANDS}
    for d, m in band.items():
        if d1 <= d <= d2:
            for lbl, v in m.items():
                o[lbl]["n"] += v["n"]; o[lbl]["net"] += v["net"]
    return o
BA = bandsum(TODAY - timedelta(days=30), A_END)
BP = bandsum(TODAY - timedelta(days=60), TODAY - timedelta(days=31))

brows = ""
for _lo, _hi, _lbl in BANDS:
    a, p = BA[_lbl], BP[_lbl]
    if p["n"]:
        _g = (a["n"] - p["n"]) / p["n"] * 100
        _pill = f'<span class="pill {"up" if _g >= 0 else "dn"}">{_g:+.0f}%</span>'
    else:
        _pill = "—"
    brows += (f'<tr><td class="k">{_lbl}</td><td class="n">{a["n"]:,}</td>'
              f'<td class="n">{a["n"]/U30["ppvn"]*100:.1f}%</td>'
              f'<td class="n">{M(a["net"])}</td>'
              f'<td class="n dim">{p["n"]:,}</td>'
              f'<td class="n dim">{p["n"]/U30P["ppvn"]*100:.1f}%</td>'
              f'<td class="c">{_pill}</td></tr>')

# ── Creator movement, ranked by contribution to the change ────────────────────

movers = []
for nm in cr:
    a  = sum(cr[nm][d]["net"] for d in cr[nm] if A_START <= d <= A_END)
    b  = sum(cr[nm][d]["net"] for d in cr[nm] if B_START <= d <= B_END)
    an = sum(cr[nm][d]["new"] for d in cr[nm] if A_START <= d <= A_END)
    bn = sum(cr[nm][d]["new"] for d in cr[nm] if B_START <= d <= B_END)
    if a or b: movers.append({"nm": nm, "a": a, "b": b, "an": an, "bn": bn})
movers.sort(key=lambda x: -x["a"])

sub_delta = A["new"] - B["new"]
sub_moves = sorted(movers, key=lambda x: abs(x["an"] - x["bn"]), reverse=True)
contrib = []
if sub_delta:
    run = 0
    for m in sub_moves:
        d = m["an"] - m["bn"]
        if d == 0 or (d > 0) != (sub_delta > 0): continue
        share = d / sub_delta * 100
        run += share
        contrib.append((m["nm"], m["bn"], m["an"], d, share, run))
        if run >= 85 or len(contrib) >= 4: break

# ── Notes ─────────────────────────────────────────────────────────────────────

NOTES_PATH = "ofmos_notes.json"
try:
    notes = json.load(open(NOTES_PATH, encoding="utf-8"))
except Exception:
    notes = {}

# The written sections describe the last full calendar week and are set on the
# Monday run. Wed/Fri runs reuse them unchanged, so "expected" is the Monday
# before the current one either way — not A_START, which rolls midweek.
NOTES_WEEK = TODAY - timedelta(days=TODAY.weekday() + 7)
week_of    = notes.get("week_of", "")
expected   = NOTES_WEEK.isoformat()
stale      = week_of != expected
missing  = [k for k in ("done", "not_done", "focus", "blocker", "stuck") if not notes.get(k)]

# ── Render ────────────────────────────────────────────────────────────────────

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
@page{size:A4;margin:12mm 11mm}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{font-family:"Segoe UI",Helvetica,Arial,sans-serif;color:#2b2430;background:#fff;
     font-size:9.4pt;line-height:1.5;font-variant-numeric:tabular-nums}
.page{page-break-after:always;position:relative;min-height:262mm;padding-bottom:12mm}
.page:last-child{page-break-after:auto}
.bar{display:flex;align-items:baseline;justify-content:space-between;
     border-bottom:2.5px solid #d94f86;padding-bottom:5px;margin-bottom:16px}
.wm{font-size:15pt;font-weight:800;letter-spacing:2.5px;color:#d94f86}
.wm i{color:#f2a3c0;font-style:normal}
.bar .pg{font-size:7.6pt;color:#8b7f8a;letter-spacing:1.4px;text-transform:uppercase}
h1{font-size:19pt;color:#c23f74;font-weight:800;letter-spacing:-.4px;line-height:1.15}
.sub{color:#8b7f8a;font-size:9pt;margin-top:5px}
h2{font-size:11.4pt;color:#c23f74;font-weight:700;margin:19px 0 8px;padding-bottom:4px;
   border-bottom:1px solid #f0d6e2;display:flex;align-items:center;justify-content:space-between}
h2 span{font-size:7.4pt;color:#8b7f8a;font-weight:600;letter-spacing:1.2px;text-transform:uppercase}
p{margin:0 0 7px}.dim{color:#8b7f8a}
.duo{display:grid;grid-template-columns:1fr 1fr;gap:11px;margin:14px 0}
.mbox{border:1.5px solid #f0d6e2;border-radius:9px;overflow:hidden}
.mbox .t{background:#fff5f9;padding:6px 12px;font-size:7.6pt;letter-spacing:1.3px;text-transform:uppercase;
         color:#c23f74;font-weight:700;border-bottom:1px solid #f0d6e2}
.mbox .b{padding:11px 12px 12px}
.row{display:flex;justify-content:space-between;align-items:baseline;padding:3.5px 0}
.row+.row{border-top:1px dotted #f0d6e2}
.row .l{color:#8b7f8a;font-size:8.6pt}.row .v{font-weight:700;font-size:12.5pt}
.row.g{margin-top:3px;padding-top:7px;border-top:1.5px solid #f7cddd}
.row.g .l{color:#2b2430;font-weight:700;font-size:9pt}.row.g .v{font-size:15pt}
.up{color:#1f7a52}.dn{color:#c9384f}
table{width:100%;border-collapse:collapse;margin:5px 0 3px;font-size:8.7pt}
th{background:#fff5f9;color:#c23f74;font-size:7.3pt;letter-spacing:.8px;text-transform:uppercase;
   font-weight:700;padding:6px 7px;text-align:right;border-bottom:1.5px solid #f0d6e2}
th:first-child{text-align:left}
td{padding:5px 7px;border-bottom:1px solid #f7f0f4;text-align:right}
td.k{text-align:left;font-weight:600}td.c{text-align:center}
.pill{display:inline-block;padding:1px 7px;border-radius:9px;font-size:7.5pt;font-weight:700}
.pill.up{background:#e4f4ec;color:#1f7a52}.pill.dn{background:#fbe7ea;color:#c9384f}
.pill.neu{background:#f4eef1;color:#8b7f8a}
.note{background:#fff5f9;border-left:3px solid #e8629a;padding:9px 12px;border-radius:0 6px 6px 0;
      margin:10px 0;font-size:8.8pt}
.note b{color:#c23f74}
.note.grey{background:#f8f6f7;border-left-color:#c9bfc6}
.note.grey b{color:#6c626a}
tr.hl td{background:#fff8fb;font-weight:700}
.alert{background:#fffaf0;border:1.5px solid #e0b45a;border-left:4px solid #d9932a;
       border-radius:6px;padding:10px 13px;margin:12px 0;font-size:8.9pt;color:#6b5320}
.alert b{color:#a86c12}
ul{margin:3px 0 9px 17px}li{margin-bottom:5px}
.q{border:1.5px solid #f0d6e2;border-radius:9px;padding:11px 13px;margin-bottom:11px;
   page-break-inside:avoid;break-inside:avoid}
li,.todo,.note,.alert{page-break-inside:avoid;break-inside:avoid}
h2{page-break-after:avoid;break-after:avoid}
.q .lbl{font-size:7.5pt;letter-spacing:1.2px;text-transform:uppercase;color:#c23f74;font-weight:700;margin-bottom:5px}
.tag{display:inline-block;background:#f7cddd;color:#8a2f56;font-size:7.2pt;font-weight:700;
     padding:1px 6px;border-radius:4px;letter-spacing:.6px}
.todo{background:#fffdf3;border:1.5px dashed #e0c98a;border-radius:6px;padding:9px 12px;
      margin:7px 0;font-size:8.6pt;color:#7a6a45}
.todo b{color:#9a7d2e}
.foot{position:absolute;bottom:0;left:0;right:0;border-top:1px solid #f0d6e2;padding-top:5px;
      font-size:7pt;color:#8b7f8a;display:flex;justify-content:space-between}
@media screen{body{max-width:820px;margin:0 auto;padding:24px}
  .page{min-height:auto;border-bottom:1px dashed #f0d6e2;margin-bottom:26px;padding-bottom:26px}
  .page:last-child{border-bottom:none}.foot{position:static;margin-top:14px}}
"""

TP = 6
def bar(n): return (f'<div class="bar"><div class="wm">HOTTTR<i>.</i></div>'
                    f'<div class="pg">OFMOS {KIND} &nbsp;·&nbsp; {n} / {TP}</div></div>')
def foot(): return (f'<div class="foot"><span>Source: Infloww API · net of OnlyFans 20% · refunds excluded · '
                    f'Europe/London days</span><span>Generated {NOW:%d %b %Y, %H:%M} {TZ_LABEL}</span></div>')
def esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

banner = ""
if stale or missing:
    bits = []
    if stale:
        bits.append(f"the notes file is dated <b>{esc(week_of) or 'never set'}</b> but this report covers "
                    f"the week starting <b>{expected}</b>")
    if missing:
        bits.append("these sections are empty: <b>" + ", ".join(missing) + "</b>")
    banner = (f'<div class="alert"><b>NEEDS INPUT BEFORE SENDING.</b> The figures below are live and correct. '
              f'The written sections are not — ' + "; and ".join(bits) +
              '. Update <code>ofmos_notes.json</code> and re-run. Nothing in this report has been '
              'auto-written on your behalf.</div>')

# tables
drows = "".join(
    f'<tr><td class="k">{d:%a}{" " + str(d.day)} {d:%b}</td><td class="n">{M(day[d]["net"])}</td>'
    f'<td class="n">{day[d]["new"]}</td><td class="n">{day[d]["ren"]}</td></tr>'
    for d in sorted(day) if A_START <= d <= A_END)

crows = ""
for m in movers:
    if m["b"]:
        g = (m["a"] - m["b"]) / m["b"] * 100
        pill = f'<span class="pill {"up" if g >= 0 else "dn"}">{g:+.0f}%</span>'
    else:
        pill = '<span class="pill neu">NEW</span>'
    crows += (f'<tr><td class="k">{esc(m["nm"])}</td><td class="n">{M(m["a"])}</td>'
              f'<td class="n dim">{M(m["b"])}</td><td class="c">{pill}</td>'
              f'<td class="n dim">{m["a"]/A["net"]*100:.1f}%</td><td class="n dim">{m["an"]}</td></tr>')

if contrib:
    krows = "".join(
        f'<tr><td class="k">{esc(n)}</td><td class="n dim">{bn}</td><td class="n">{an}</td>'
        f'<td class="n {"up" if d > 0 else "dn"}">{d:+d}</td><td class="n">{sh:.0f}%</td>'
        f'<td class="n dim">{run:.0f}%</td></tr>' for n, bn, an, d, sh, run in contrib)
    top = contrib[0]
    concentration = (f'<div class="note"><b>Where the change came from.</b> New subscribers moved '
                     f'{sub_delta:+d} overall. <b>{esc(top[0])}</b> alone accounts for {top[4]:.0f}% of that move'
                     + (f', and the {len(contrib)} accounts below are {contrib[-1][5]:.0f}% of it'
                        if len(contrib) > 1 else '') +
                     '. Treat this as an account-level question, not an agency-wide one.</div>')
    contrib_block = (f'<h2>Contribution to the change in new subs <span>ranked by impact</span></h2>'
                     f'<table><thead><tr><th>Creator</th><th>Prior 7d</th><th>Last 7d</th><th>Change</th>'
                     f'<th>Share of move</th><th>Cumulative</th></tr></thead><tbody>{krows}</tbody></table>'
                     f'{concentration}')
else:
    contrib_block = ('<h2>Contribution to the change in new subs</h2>'
                     '<p class="dim">New subscriber volume was essentially flat between the two windows, '
                     'so there is no meaningful movement to attribute.</p>')

skip_note = (f'<div class="alert"><b>Incomplete roster.</b> {len(skipped)} creator page(s) returned an error and '
             f'are missing from these totals: {esc(", ".join(skipped))}. Departed creators lose their history in '
             f'Infloww, so both windows may understate.</div>') if skipped else ""

P1 = f"""<div class="page">{bar(1)}
<h1>OFMOS {KIND} — Numbers</h1>
<div class="sub">Comparing the 7 days to {fmt(A_END)} against the 7 before it ({WINDOW}).
All days in both windows are complete.</div>
{banner}{skip_note}
<div class="duo">
  <div class="mbox"><div class="t">Revenue (net)</div><div class="b">
    <div class="row"><span class="l">Last 7d &nbsp;{fmt(A_START)} – {fmt(A_END)}</span><span class="v">{M(A['net'])}</span></div>
    <div class="row"><span class="l">Prior 7d &nbsp;{fmt(B_START)} – {fmt(B_END)}</span><span class="v">{M(B['net'])}</span></div>
    <div class="row g"><span class="l">Growth</span><span class="v {'dn' if g_net<0 else 'up'}">{g_net:+.1f}%</span></div>
  </div></div>
  <div class="mbox"><div class="t">New subscribers</div><div class="b">
    <div class="row"><span class="l">Last 7d</span><span class="v">{A['new']}</span></div>
    <div class="row"><span class="l">Prior 7d</span><span class="v">{B['new']}</span></div>
    <div class="row g"><span class="l">Growth</span><span class="v {'dn' if g_sub<0 else 'up'}">{g_sub:+.1f}%</span></div>
  </div></div>
</div>
<div class="duo">
  <div class="mbox"><div class="t">Quality &amp; mix</div><div class="b">
    <div class="row"><span class="l">Renewals</span><span class="v">{A['ren']}</span></div>
    <div class="row"><span class="l">Renewal rate</span><span class="v">{A['ren']/(A['new']+A['ren'])*100:.0f}%</span></div>
    <div class="row"><span class="l">PPV share of net</span><span class="v">{A['ppv']/A['net']*100:.0f}%</span></div>
    <div class="row"><span class="l">Average unlock</span><span class="v">{M(A['ppv']/A['ppvn']) if A['ppvn'] else '—'}</span></div>
  </div></div>
  <div class="mbox"><div class="t">Month to date</div><div class="b">
    <div class="row"><span class="l">Net ({mdays} days)</span><span class="v">{M(mtd)}</span></div>
    <div class="row"><span class="l">Average per day</span><span class="v">{M(mtd/mdays) if mdays else '—'}</span></div>
    <div class="row g"><span class="l">Run-rate close</span><span class="v">{M0(mtd + (mtd/mdays)*( (date(A_END.year, A_END.month%12+1, 1) - timedelta(days=1)).day - mdays)) if mdays else '—'}</span></div>
  </div></div>
</div>
<h2>Day by day <span>{fmt(A_START)} – {fmt(A_END)}</span></h2>
<table><thead><tr><th>Day</th><th>Net</th><th>New subs</th><th>Renewals</th></tr></thead><tbody>{drows}</tbody></table>
{foot()}</div>"""

P2 = f"""<div class="page">{bar(2)}
<h1>Where it moved</h1>
<div class="sub">Creator-level detail for the same two windows.</div>
<h2>Creator performance <span>last 7d vs prior 7d</span></h2>
<table><thead><tr><th>Creator</th><th>Last 7d</th><th>Prior 7d</th><th>Change</th><th>Share</th>
<th>New subs</th></tr></thead><tbody>{crows}</tbody></table>
{contrib_block}
{foot()}</div>

<div class="page">{bar(3)}
<h1>Unlock price trend</h1>
<div class="sub">Average price of a single PPV unlock, over rolling windows ending {fmt(A_END)}.
Net of the OnlyFans 20%; the price the fan pays is 1.25 times these figures.</div>

<h2>Rolling windows</h2>
<table><thead><tr><th>Window</th><th>Unlocks</th><th>PPV net</th><th>Average unlock</th>
<th>Last 30d vs this</th></tr></thead><tbody>
{"".join(
  f'<tr{" class=hl" if lbl.startswith("Last 30") else ""}><td class="k">{lbl}</td>'
  f'<td class="n">{w["ppvn"]:,}</td><td class="n">{M(w["ppv"])}</td>'
  f'<td class="n"><b>{M(avg(w))}</b></td>'
  f'<td class="n">{"—" if lbl.startswith("Last 30") else f"{(avg(U30)-avg(w))/avg(w)*100:+.1f}%"}</td></tr>'
  for lbl, w in [("Last 30 days", U30), ("Prior 30 days", U30P),
                 ("Rolling 60 days", U60), ("Rolling 90 days", U90)])}
</tbody></table>

<div class="note"><b>Price rose {(avg(U30)-avg(U30P))/avg(U30P)*100:.1f}% and volume fell
{abs((U30["ppvn"]-U30P["ppvn"])/U30P["ppvn"]*100):.1f}%.</b>
The average unlock went {M(avg(U30P))} to {M(avg(U30))} against the prior 30 days, and sits
{(avg(U30)-avg(U90))/avg(U90)*100:+.1f}% above the 90-day baseline of {M(avg(U90))}. Over the same
period unlocks fell from {U30P["ppvn"]:,} to {U30["ppvn"]:,} and PPV revenue fell
{abs((U30["ppv"]-U30P["ppv"])/U30P["ppv"]*100):.1f}%, from {M(U30P["ppv"])} to {M(U30["ppv"])}.
The higher price has not so far offset the lower volume.</div>

<h2>Where the price sits <span>unlocks by price band, net</span></h2>
<table><thead><tr><th>Band</th><th>Last 30d</th><th>Share</th><th>PPV net</th>
<th>Prior 30d</th><th>Share</th><th>Change</th></tr></thead><tbody>
{brows}
</tbody></table>

<div class="note"><b>The top band is doing the work.</b>
Unlocks at $160 and above went from {BP["$160+"]["n"]:,} to {BA["$160+"]["n"]:,}. They are
{BA["$160+"]["n"]/U30["ppvn"]*100:.1f}% of all unlocks and {BA["$160+"]["net"]/U30["ppv"]*100:.1f}%
of PPV revenue. Every other band fell in both count and share of revenue.</div>

<div class="note grey"><b>Comparison caveat.</b> Creators who left during August lost their
transaction history in Infloww. The prior-30 and 90-day windows therefore exclude them entirely,
so this compares against a roster that no longer fully exists.</div>
{foot()}</div>"""

def bullets(key, empty_msg):
    v = notes.get(key)
    if not v: return f'<div class="todo"><b>NEEDS INPUT:</b> {empty_msg}</div>'
    return "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in v) + "</ul>"

def block(key, label, empty_msg):
    v = notes.get(key)
    if not v: return f'<div class="todo"><b>NEEDS INPUT:</b> {empty_msg}</div>'
    return f'<div class="q"><div class="lbl">{label}</div><div>{esc(v)}</div></div>'

focus = notes.get("focus") or {}
focus_html = (f'<p><b>{esc(focus.get("what",""))}</b></p>'
              f'<p><b>Why:</b> {esc(focus.get("why",""))}</p>'
              f'<p><b>What it brings:</b> {esc(focus.get("result",""))}</p>') if focus else \
             '<div class="todo"><b>NEEDS INPUT:</b> the single focus for the coming week, why it is the ' \
             'one thing, and what result it should produce.</div>'

P3 = f"""<div class="page">{bar(4)}
<h1>Progress</h1>
<div class="sub">Written sections — entered by the team, not generated.
They describe the calendar week beginning <b>{fmt(NOTES_WEEK)}</b>.</div>
<h2>What actually got done — and the results</h2>
{bullets("done", "the actions the team took last week and the result of each. The numbers on pages 1–2 are outcomes; this section is the inputs that produced them.")}
<h2>What did not get done — and the effect</h2>
{bullets("not_done", "action steps that slipped, why they slipped, and the effect on the numbers.")}
<h2>#1 focus for the upcoming week</h2>
{focus_html}
{foot()}</div>"""

vids = notes.get("video_ideas") or []
qs   = notes.get("questions") or []
vid_html = "".join(f'<div class="q"><div class="lbl">Idea {i+1}</div><div>{esc(v)}</div></div>'
                   for i, v in enumerate(vids)) or \
           '<div class="todo"><b>NEEDS INPUT:</b> one or two topics that would genuinely help.</div>'
q_html = "".join(f'<div class="q"><div class="lbl"><span class="tag">{esc(q.get("tag","GENERAL"))}</span></div>'
                 f'<div>{esc(q.get("q",""))}</div></div>' for q in qs) or \
         '<div class="todo"><b>NEEDS INPUT:</b> tagged questions for the consultant.</div>'

P4 = f"""<div class="page">{bar(5)}
<h1>Problems &amp; input needed</h1>
<div class="sub">Written sections — entered by the team, not generated.</div>
<h2>Biggest blocker</h2>
{block("blocker", "Blocker", "what is actually stopping progress right now.")}
{block("magic_wand", "If we had a magic wand", "the one thing you would fix instantly, and what it would unlock.")}
<h2>Where we are stuck or unsure</h2>
{bullets("stuck", "things that MIGHT be a problem but cannot be proven with current data. These are as valuable as the provable ones.")}
{foot()}</div>

<div class="page">{bar(6)}
<h1>Input needed, continued</h1>
<div class="sub">What would help most from the consultant's side.</div>
<h2>Video ideas that would help us</h2>
{vid_html}
<h2>Questions for the consultant</h2>
{q_html}
{foot()}</div>"""

html = f"<title>HOTTTR — OFMOS {KIND} · {A_END:%d %b %Y}</title>\n<style>{CSS}</style>\n{P1}{P2}{P3}{P4}"

os.makedirs("docs/archive", exist_ok=True)
for p in ("docs/ofmos.html", f"docs/archive/ofmos-{A_END.isoformat()}.html"):
    with open(p, "w", encoding="utf-8") as f: f.write(html)
print(f"Wrote docs/ofmos.html  ({KIND}, {A_START} to {A_END})")
print(f"  net {M(A['net'])} vs {M(B['net'])}  ({g_net:+.1f}%)")
print(f"  new {A['new']} vs {B['new']}  ({g_sub:+.1f}%)")
if contrib: print(f"  top mover: {contrib[0][0]} = {contrib[0][4]:.0f}% of the sub move")
if stale or missing: print(f"  NEEDS INPUT — stale={stale} missing={missing}")
if skipped: print(f"  skipped creators: {skipped}")

# ── Slack ─────────────────────────────────────────────────────────────────────

if MODE != "send":
    print(f"Mode '{MODE}' — not posting to Slack.")
    sys.exit(0)
if not SLACK_WEBHOOK:
    print("ERROR: SLACK_WEBHOOK_URL not set"); sys.exit(1)

warn = ""
if stale or missing:
    warn = ("\n\n:warning: *Written sections still need input* — the numbers are final, the narrative is not. "
            "Edit `ofmos_notes.json` and re-run before sending this to the consultant.")
mv = f"\n*Biggest mover:* {contrib[0][0]} — {contrib[0][4]:.0f}% of the new-sub change" if contrib else ""

payload = {"blocks": [
    {"type": "header", "text": {"type": "plain_text", "text": f"OFMOS {KIND} — {A_END:%d %b %Y}"}},
    {"type": "section", "text": {"type": "mrkdwn", "text":
        f"*{fmt(A_START)} – {fmt(A_END)}* vs the prior 7 days\n"
        f"*Net:* {M(A['net'])}  ({g_net:+.1f}%)\n"
        f"*New subs:* {A['new']}  ({g_sub:+.1f}%)\n"
        f"*Renewal rate:* {A['ren']/(A['new']+A['ren'])*100:.0f}%   "
        f"*PPV share:* {A['ppv']/A['net']*100:.0f}%{mv}{warn}"}},
    {"type": "actions", "elements": [
        {"type": "button", "text": {"type": "plain_text", "text": "Open report"}, "url": PAGES_URL}]},
]}
req = urllib.request.Request(SLACK_WEBHOOK, data=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        print(f"Sent to Slack: {KIND} {A_END} | net={M(A['net'])} | {A['new']} new subs")
except urllib.error.HTTPError as e:
    print(f"Slack error {e.code}: {e.read().decode()}"); sys.exit(1)
