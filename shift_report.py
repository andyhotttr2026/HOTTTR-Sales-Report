# -*- coding: utf-8 -*-
"""
HOTTTR end-of-shift report.

Fires 3x/day via GitHub Actions, once at each Manila (UTC+8) shift boundary:
    Shift 1  08:00-16:00 MNL  -> report at 16:00 MNL (08:00 UTC)
    Shift 2  16:00-24:00 MNL  -> report at 00:00 MNL (16:00 UTC)
    Shift 3  00:00-08:00 MNL  -> report at 08:00 MNL (00:00 UTC)

Two modes:
    python shift_report.py build   # pull API, write ./site/*.html + slack_payload.json (NO Slack post)
    python shift_report.py send    # POST slack_payload.json to SLACK_WEBHOOK_URL

Shift is auto-detected from current Manila time (drift-tolerant: picks the most
recently ended shift). Override with env SHIFT=1|2|3.
"""
import urllib.request, urllib.error, json, os, sys
from datetime import datetime, timezone, timedelta

API_KEY       = os.environ.get('INFLOWW_API_KEY')
OID           = os.environ.get('INFLOWW_OID')
SLACK_WEBHOOK = os.environ.get('SLACK_WEBHOOK_URL')
PAGES_URL     = os.environ.get('PAGES_URL', 'https://andyhotttr2026.github.io/HOTTTR-Sales-Report/shift.html')
BASE          = "https://openapi.infloww.com"
UA            = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
HEADERS       = {"Authorization": API_KEY, "x-oid": OID, "User-Agent": UA, "Accept": "application/json"}
MNL           = timezone(timedelta(hours=8))

FREE_PAGES = {"Maddy", "Leah Jewel", "Alice Free", "Ella Free", "Alessa Free",
              "Miya Free", "Shy Trans Free", "Miya Rai VIP"}
# refunds/reversals the Infloww dashboard excludes from net — we exclude them too
REFUND_STATUSES = {"undo", "refunded", "chargeback", "cancelled", "canceled"}

# ── UK time (for labels; shifts stay defined in Manila) ─────────────────────────

def _last_sunday(year, month):
    for d in range(31, 24, -1):
        try:
            if datetime(year, month, d).weekday() == 6: return d
        except ValueError:
            continue
    return 25

def to_uk(dt):
    """Convert an aware datetime to UK local time, honouring BST/GMT.
    BST (+1) runs from last Sunday of March 01:00 UTC to last Sunday of October 01:00 UTC."""
    u = dt.astimezone(timezone.utc)
    y = u.year
    bst_start = datetime(y, 3,  _last_sunday(y, 3),  1, tzinfo=timezone.utc)
    bst_end   = datetime(y, 10, _last_sunday(y, 10), 1, tzinfo=timezone.utc)
    off = 1 if bst_start <= u < bst_end else 0
    return u.astimezone(timezone(timedelta(hours=off))), ("BST" if off else "GMT")

# ── Shift window ────────────────────────────────────────────────────────────────

def _windows(midnight):
    """Shift-number -> (start_dt, end_dt) in MNL, relative to a day's midnight."""
    return {
        1: (midnight + timedelta(hours=8),  midnight + timedelta(hours=16)),  # 08:00-16:00 today
        3: (midnight,                       midnight + timedelta(hours=8)),   # 00:00-08:00 today
        2: (midnight - timedelta(hours=8),  midnight),                        # yest 16:00 -> today 00:00
    }

def resolve_shift():
    """Return (shift_no, start_dt_mnl, end_dt_mnl) for the shift that just ended.

    Auto-detects from current Manila time (drift-tolerant). Env SHIFT=1|2|3 forces a
    shift; if that shift hasn't finished yet today, it rolls back to the last completed one.
    """
    now_mnl  = datetime.now(MNL)
    d        = now_mnl.date()
    midnight = datetime(d.year, d.month, d.day, tzinfo=MNL)
    override = os.environ.get('SHIFT')

    if override in ('1', '2', '3'):
        sh = int(override)
        start, end = _windows(midnight)[sh]
        if end > now_mnl:  # window not finished today -> use yesterday's completed instance
            start, end = _windows(midnight - timedelta(days=1))[sh]
        return sh, start, end

    h = now_mnl.hour
    if h >= 16:   return (1,) + _windows(midnight)[1]   # Shift 1 just ended
    elif h >= 8:  return (3,) + _windows(midnight)[3]   # Shift 3 just ended
    else:         return (2,) + _windows(midnight)[2]   # Shift 2 just ended

# ── API ─────────────────────────────────────────────────────────────────────────

def get(path):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def fetch_txns(creator_id, start_ms, end_ms):
    out, cursor = [], None
    while True:
        url = f"/v1/transactions?creatorId={creator_id}&limit=100&startTime={start_ms}&endTime={end_ms}"
        if cursor: url += f"&cursor={cursor}"
        data = get(url)
        out.extend(data["data"]["list"])
        cursor = data.get("cursor")
        if not data.get("hasMore") or not cursor: break
    return out

# ── Build ─────────────────────────────────────────────────────────────────────

def classify(txns):
    d = {"net":0,"gross":0,"new":0,"ren":0,"new_amt":0,"ren_amt":0,
         "ppv":0,"ppv_n":0,"tip":0,"tip_n":0,"post":0,"post_n":0}
    for tx in txns:
        t = tx.get("type","").lower()
        net = int(tx.get("net",0)); gross = int(tx.get("amount",0))
        d["net"] += net; d["gross"] += gross
        if "subscription" in t and "recurring" not in t: d["new"] += 1; d["new_amt"] += net
        elif "recurring" in t: d["ren"] += 1; d["ren_amt"] += net
        elif "message" in t: d["ppv"] += net; d["ppv_n"] += 1
        elif "tip" in t: d["tip"] += net; d["tip_n"] += 1
        elif "post" in t: d["post"] += net; d["post_n"] += 1
    return d

def build():
    if not API_KEY or not OID:
        print("ERROR: INFLOWW_API_KEY / INFLOWW_OID not set"); sys.exit(1)

    shift_no, start_dt, end_dt = resolve_shift()
    # clamp end to now-60s so the API never sees a future endTime (clock skew guard)
    now_utc = datetime.now(timezone.utc)
    if end_dt.astimezone(timezone.utc) > now_utc:
        end_dt = (now_utc - timedelta(seconds=60)).astimezone(MNL)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms   = int(end_dt.timestamp() * 1000)

    creators = get("/v1/creators?limit=100")["data"]["list"]
    rows = []
    hourly = [{"new":0,"ren":0,"ppv":0,"ppv_n":0,"tip":0,"post":0,"net":0} for _ in range(8)]
    tot = {"net":0,"gross":0,"new":0,"ren":0,"new_amt":0,"ren_amt":0,
           "ppv":0,"ppv_n":0,"tip":0,"tip_n":0,"post":0,"post_n":0}
    for c in creators:
        txns = fetch_txns(c["id"], start_ms, end_ms)
        # drop refunds/reversals so net matches the Infloww dashboard
        txns = [tx for tx in txns if tx.get("status","").lower() not in REFUND_STATUSES]
        d = classify(txns)
        if d["net"] == 0 and d["new"] == 0 and d["ren"] == 0 and d["ppv_n"] == 0 and d["tip_n"] == 0 and d["post_n"] == 0:
            continue
        d["name"] = c["name"]; d["free"] = c["name"] in FREE_PAGES
        d["subs"] = d["new"] + d["ren"]
        rows.append(d)
        for k in tot: tot[k] += d[k]
        # per-hour buckets within the shift (by createdTime, Manila hour offset)
        for tx in txns:
            ct = int(tx.get("createdTime", 0))
            if not ct: continue
            hr = int((datetime.fromtimestamp(ct/1000, MNL) - start_dt).total_seconds() // 3600)
            if not (0 <= hr < 8): continue
            t = tx.get("type", "").lower(); net = int(tx.get("net", 0))
            hb = hourly[hr]; hb["net"] += net
            if "subscription" in t and "recurring" not in t: hb["new"] += 1
            elif "recurring" in t: hb["ren"] += 1
            elif "message" in t: hb["ppv"] += net; hb["ppv_n"] += 1
            elif "tip" in t: hb["tip"] += net
            elif "post" in t: hb["post"] += net

    rows.sort(key=lambda r: -r["net"])
    tot["subs"] = tot["new"] + tot["ren"]
    of_fee = tot["gross"] - tot["net"]
    ren_rate = (tot["ren"]/tot["subs"]*100) if tot["subs"] else 0

    uk_start, tz_label = to_uk(start_dt)
    uk_end, _          = to_uk(end_dt)
    ctx = {"shift_no":shift_no, "start":start_dt.isoformat(), "end":end_dt.isoformat(),
           "uk_start":uk_start.isoformat(), "uk_end":uk_end.isoformat(),
           "uk_start_hour":uk_start.hour, "tz_label":tz_label,
           "rows":rows, "tot":tot, "of_fee":of_fee, "ren_rate":ren_rate, "hourly":hourly,
           "start_hour":start_dt.hour}
    write_slack_payload(ctx)
    write_widget(ctx)
    print(f"Built Shift {shift_no} ({start_dt:%b %d %H:%M}-{end_dt:%H:%M} MNL): "
          f"net=${tot['net']/100:,.2f} subs={tot['subs']} ppv=${tot['ppv']/100:,.2f}")
    return ctx

# ── Slack payload ────────────────────────────────────────────────────────────

def _bar(v, mx, w=14):
    if mx <= 0: return " " * w
    frac = v / mx * w; full = int(frac)
    eighths = " ▏▎▍▌▋▊▉█"  # index 0..8
    part = eighths[round((frac - full) * 8)]
    return ("█"*full + (part if part != " " else "")).ljust(w)

def write_slack_payload(ctx):
    tot = ctx["tot"]; rows = ctx["rows"]; hourly = ctx["hourly"]; sh = ctx["uk_start_hour"]
    def m(c): return f"${c/100:,.2f}"

    # creator net-sales bar chart
    active = [r for r in rows if r["net"] > 0]
    nw = min(max((len(r["name"]) for r in active), default=6), 15)
    mx = max((r["net"] for r in active), default=1)
    chart = "\n".join(f"{r['name'][:nw]:<{nw}} {_bar(r['net'],mx)} {m(r['net']):>10}" for r in active)
    chart += f"\n{'TOTAL':<{nw}} {' '*14} {m(tot['net']):>10}"

    # per-hour breakdown: new vs renewals + net, with a mini sales bar
    hmax = max((h["net"] for h in hourly), default=1)
    peak = max(range(8), key=lambda i: hourly[i]["net"])
    hlines = [f"{'Hour':<6}{'New':>4}{'Ren':>4}  {'Net':>8}  Sales"]
    for i, h in enumerate(hourly):
        hr = f"{(sh+i)%24:02d}:00"
        hlines.append(f"{hr:<6}{h['new']:>4}{h['ren']:>4}  {m(h['net']):>8}  {_bar(h['net'],hmax,10)}")
    hlines.append(f"{'TOT':<6}{tot['new']:>4}{tot['ren']:>4}  {m(tot['net']):>8}")
    hourly_table = "\n".join(hlines)

    us, ue = datetime.fromisoformat(ctx["uk_start"]), datetime.fromisoformat(ctx["uk_end"])
    ms, me = datetime.fromisoformat(ctx["start"]),    datetime.fromisoformat(ctx["end"])
    tz = ctx["tz_label"]; rr = ctx["ren_rate"]
    hdr = f"🌙  Shift {ctx['shift_no']} Report — {us:%b %d}, {us:%H:%M}–{ue:%H:%M} {tz}"
    blocks = [
        {"type":"header","text":{"type":"plain_text","text":hdr,"emoji":True}},
        {"type":"context","elements":[{"type":"mrkdwn",
            "text":f"🇬🇧  {tz} · worked {ms:%H:%M}–{me:%H:%M} Manila · <{PAGES_URL}|open dashboard →>"}]},
        {"type":"section","text":{"type":"mrkdwn",
            "text":f"*💰  {m(tot['net'])} net*   ·   ⏱ {us:%H:%M}–{ue:%H:%M} {tz}\n"
                   f"_gross {m(tot['gross'])}  ·  peak hour {(sh+peak)%24:02d}:00 at {m(hourly[peak]['net'])}_"}},
        {"type":"section","fields":[
            {"type":"mrkdwn","text":f"*👥 Paid Subs*\n{tot['subs']}  ·  {tot['new']} new / {tot['ren']} ren"},
            {"type":"mrkdwn","text":f"*🔁 Renewal Rate*\n{rr:.0f}%"},
            {"type":"mrkdwn","text":f"*💬 PPV*\n{m(tot['ppv'])}  ·  {tot['ppv_n']} sold"},
            {"type":"mrkdwn","text":f"*💡 Tips*\n{m(tot['tip'])}"},
            {"type":"mrkdwn","text":f"*⭐ Subs $*\n{m(tot['new_amt']+tot['ren_amt'])}"},
            {"type":"mrkdwn","text":f"*📝 Posts*\n{m(tot['post'])}"},
        ]},
        {"type":"divider"},
        {"type":"section","text":{"type":"mrkdwn","text":f"*⏱  Hourly — new / ren / sales* ({tz})\n```{hourly_table}```"}},
        {"type":"section","text":{"type":"mrkdwn","text":f"*💵  Net Sales — by creator*\n```{chart}```"}},
        {"type":"context","elements":[{"type":"mrkdwn",
            "text":"Pulled live from Infloww · refunds excluded · net = after 20% OnlyFans fee"}]},
    ]
    with open("slack_payload.json","w",encoding="utf-8") as f:
        json.dump({"blocks":blocks}, f)

# ── Widget (self-contained dark dashboard) ─────────────────────────────────────

def write_widget(ctx):
    tot = ctx["tot"]; rows = ctx["rows"]; hourly = ctx["hourly"]; tz = ctx["tz_label"]
    s = datetime.fromisoformat(ctx["uk_start"]); e = datetime.fromisoformat(ctx["uk_end"])
    ms = datetime.fromisoformat(ctx["start"]);   me = datetime.fromisoformat(ctx["end"])
    maxnet = max((r["net"] for r in rows), default=1) or 1
    bars = "".join(
        f'<div class="bar-row"><div class="bar-name">{r["name"]}</div>'
        f'<div class="bar-track"><div class="bar-fill{" top" if i==0 else ""}" style="width:{r["net"]/maxnet*100:.1f}%"></div>'
        f'<div class="bar-val">${r["net"]/100:,.2f}</div></div></div>'
        for i, r in enumerate(rows))
    sh = ctx["uk_start_hour"]
    maxsubs = max((h["new"]+h["ren"] for h in hourly), default=1) or 1
    hbars = "".join(
        f'<div class="hcol"><div class="hstack">'
        f'<div class="seg ren" style="height:{h["ren"]/maxsubs*100:.1f}%"></div>'
        f'<div class="seg new" style="height:{h["new"]/maxsubs*100:.1f}%"></div></div>'
        f'<div class="hnum">{(h["new"]+h["ren"]) or ""}</div>'
        f'<div class="hlab">{(sh+i)%24:02d}</div></div>'
        for i, h in enumerate(hourly))
    htrows = "".join(
        f'<tr><td class="c-name">{(sh+i)%24:02d}:00</td><td class="new-c">{h["new"]}</td>'
        f'<td class="ren-c">{h["ren"]}</td><td>{h["new"]+h["ren"]}</td>'
        f'<td class="num">${h["ppv"]/100:,.2f}</td><td class="num net">${h["net"]/100:,.2f}</td></tr>'
        for i, h in enumerate(hourly))
    trows = "".join(
        f'<tr><td class="c-name">{r["name"]}{" ·free" if r["free"] else ""}</td><td>{r["subs"]}</td>'
        f'<td class="dim">{r["new"]}/{r["ren"]}</td><td class="num">${r["ppv"]/100:,.2f}</td>'
        f'<td class="num">${r["tip"]/100:,.2f}</td><td class="num net">${r["net"]/100:,.2f}</td></tr>'
        for r in rows)
    zeros = [c for c in [] ]  # placeholder
    html = f'''<title>HOTTTR Shift {ctx["shift_no"]} — {s:%b %d %H:%M} {tz}</title>
<style>
  :root{{--bg:#12141f;--card:#1e2130;--panel:#1a1d2b;--line:#2a2e40;--text:#e8eaf0;
    --dim:#8890a6;--dimmer:#5a6178;--green:#3ddc84;--cyan:#4db8ff;--red:#ff5c6c;
    --sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}}
  *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);
    font-family:var(--sans);font-size:14px;padding:20px}}
  .wrap{{max-width:920px;margin:0 auto;display:flex;flex-direction:column;gap:14px}}
  .head{{display:flex;justify-content:space-between;align-items:baseline;padding:2px 4px}}
  .head h1{{font-size:20px;font-weight:600;margin:0;letter-spacing:-.01em}}
  .head .sub{{color:var(--dim);font-size:13px}}
  .kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}}
  .kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}}
  .kpi .lbl{{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.06em}}
  .kpi .big{{font-size:24px;font-weight:700;margin:6px 0 2px;font-variant-numeric:tabular-nums}}
  .kpi .foot{{color:var(--dimmer);font-size:11px}} .kpi.hero .big{{color:var(--green)}}
  .panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px}}
  .panel h2{{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);margin:0 0 16px;font-weight:600}}
  .bar-row{{display:flex;align-items:center;gap:12px;margin-bottom:9px}}
  .bar-name{{width:120px;text-align:right;color:var(--dim);font-size:13px;flex-shrink:0}}
  .bar-track{{flex:1;position:relative;background:#0d0f18;border-radius:4px;height:22px}}
  .bar-fill{{height:100%;background:var(--cyan);border-radius:4px;min-width:2px}} .bar-fill.top{{background:var(--green)}}
  .bar-val{{position:absolute;right:8px;top:50%;transform:translateY(-50%);font-size:11px;color:var(--dim);font-variant-numeric:tabular-nums}}
  .hourly{{display:flex;align-items:flex-end;gap:8px;height:130px;padding-top:8px}}
  .hcol{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%}}
  .hstack{{width:64%;display:flex;flex-direction:column;justify-content:flex-end;height:100%;overflow:hidden;border-radius:3px 3px 0 0}}
  .seg{{width:100%}} .seg.new{{background:var(--cyan)}} .seg.ren{{background:var(--green)}}
  .hnum{{color:var(--dim);font-size:10px;margin-top:4px;height:12px;font-variant-numeric:tabular-nums}}
  .hlab{{color:var(--dimmer);font-size:10px;margin-top:2px;font-variant-numeric:tabular-nums}}
  .legend{{display:flex;gap:16px;align-items:center;color:var(--dim);font-size:11px;margin:-6px 0 14px}}
  .legend .dot{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:middle}}
  .dot.new{{background:var(--cyan)}} .dot.ren{{background:var(--green)}}
  .htbl{{margin-top:18px}} td.new-c{{color:var(--cyan);font-weight:600}} td.ren-c{{color:var(--green);font-weight:600}}
  table{{width:100%;border-collapse:collapse}}
  th{{text-align:left;color:var(--dimmer);font-size:10px;text-transform:uppercase;letter-spacing:.06em;font-weight:600;padding:0 10px 10px;border-bottom:1px solid var(--line)}}
  th.num,td.num{{text-align:right}}
  td{{padding:9px 10px;border-bottom:1px solid #20233340;font-variant-numeric:tabular-nums}}
  td.c-name{{font-weight:500}} td.dim{{color:var(--dim)}} td.net{{color:var(--green);font-weight:600}}
  tr:last-child td{{border-bottom:none}}
  @media(max-width:720px){{.kpis{{grid-template-columns:repeat(2,1fr)}}.bar-name{{width:84px}}}}
</style>
<div class="wrap">
  <div class="head"><h1>Shift {ctx["shift_no"]} Report — {s:%B %d, %Y}</h1>
    <div class="sub">{s:%H:%M}–{e:%H:%M} UK ({tz}) · worked {ms:%H:%M}–{me:%H:%M} Manila</div></div>
  <div class="kpis">
    <div class="kpi hero"><div class="lbl">Net</div><div class="big">${tot["net"]/100:,.0f}</div><div class="foot">after 20% fee</div></div>
    <div class="kpi"><div class="lbl">Gross</div><div class="big">${tot["gross"]/100:,.0f}</div><div class="foot">before fees</div></div>
    <div class="kpi"><div class="lbl">Paid subs</div><div class="big">{tot["subs"]}</div><div class="foot">{tot["new"]} new · {tot["ren"]} ren</div></div>
    <div class="kpi"><div class="lbl">PPV</div><div class="big">${tot["ppv"]/100:,.0f}</div><div class="foot">{tot["ppv_n"]} unlocks</div></div>
    <div class="kpi"><div class="lbl">Tips</div><div class="big">${tot["tip"]/100:,.0f}</div><div class="foot">{tot["tip_n"]} tips</div></div>
  </div>
  <div class="panel"><h2>Subs per hour — new vs renewals (UK time)</h2>
    <div class="legend"><span><span class="dot new"></span>New sub</span><span><span class="dot ren"></span>Renewal</span></div>
    <div class="hourly">{hbars}</div>
    <table class="htbl"><thead><tr><th>Hour</th><th>New</th><th>Ren</th><th>Total</th><th class="num">PPV</th><th class="num">Net</th></tr></thead>
    <tbody>{htrows}</tbody></table></div>
  <div class="panel"><h2>Creator net revenue</h2>{bars}</div>
  <div class="panel"><h2>Creator breakdown</h2>
    <table><thead><tr><th>Creator</th><th>Subs</th><th>New/Ren</th><th class="num">PPV</th><th class="num">Tips</th><th class="num">Net</th></tr></thead>
    <tbody>{trows}</tbody></table></div>
</div>'''
    os.makedirs("docs/archive", exist_ok=True)
    with open("docs/shift.html","w",encoding="utf-8") as f: f.write(html)
    with open(f"docs/archive/shift-{s:%Y-%m-%d}-s{ctx['shift_no']}.html","w",encoding="utf-8") as f: f.write(html)
    from report_common import write_index
    write_index()

# ── Send ──────────────────────────────────────────────────────────────────────

def send():
    if not SLACK_WEBHOOK:
        print("ERROR: SLACK_WEBHOOK_URL not set"); sys.exit(1)
    with open("slack_payload.json","rb") as f:
        payload = f.read()
    req = urllib.request.Request(SLACK_WEBHOOK, data=payload,
                                 headers={"Content-Type":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print("Sent to Slack.")
    except urllib.error.HTTPError as ex:
        print(f"Slack error {ex.code}: {ex.read().decode()}"); sys.exit(1)

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    if mode == "send": send()
    else: build()
