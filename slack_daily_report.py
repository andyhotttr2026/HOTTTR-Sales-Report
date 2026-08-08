# -*- coding: utf-8 -*-
"""HOTTTR daily Slack report — BST full day (yesterday).

Matches the Infloww "Creator earnings overview" dashboard:
  - excludes refunds/reversals (status 'undo' etc.)
  - tracks Posts as its own revenue category
Sends KPI cards + the dashboard category split + per-creator Paid Subs and
Net Sales breakdowns (the copy-paste format).

Run `python slack_daily_report.py dry` to print the payload without posting.
"""
import urllib.request, urllib.error, json, os, sys
from datetime import datetime, timezone, timedelta

API_KEY       = os.environ.get('INFLOWW_API_KEY')
OID           = os.environ.get('INFLOWW_OID')
SLACK_WEBHOOK = os.environ.get('SLACK_WEBHOOK_URL')
BASE          = "https://openapi.infloww.com"
UA            = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
HEADERS       = {"Authorization": API_KEY, "x-oid": OID, "User-Agent": UA, "Accept": "application/json"}
REFUND_STATUSES = {"undo", "refunded", "chargeback", "cancelled", "canceled"}

# ── London time (BST/GMT, DST-aware) ────────────────────────────────────────────

def _last_sunday(y, mo):
    for d in range(31, 24, -1):
        try:
            if datetime(y, mo, d).weekday() == 6: return d
        except ValueError:
            continue
    return 25

def london_offset(y, mo, d):
    """Hours offset for London on a given date (1=BST, 0=GMT)."""
    noon = datetime(y, mo, d, 12, tzinfo=timezone.utc)
    a = datetime(y, 3,  _last_sunday(y, 3),  1, tzinfo=timezone.utc)
    b = datetime(y, 10, _last_sunday(y, 10), 1, tzinfo=timezone.utc)
    return 1 if a <= noon < b else 0

def yesterday_window():
    now = datetime.now(timezone.utc)
    off = london_offset(now.year, now.month, now.day)
    lon_now = now + timedelta(hours=off)
    yest = (lon_now - timedelta(days=1)).date()
    o = london_offset(yest.year, yest.month, yest.day)
    tz = timezone(timedelta(hours=o))
    start = datetime(yest.year, yest.month, yest.day, 0, 0, tzinfo=tz)
    end = start + timedelta(days=1)
    label = f"{yest.strftime('%A, %B')} {yest.day}, {yest.year}"
    return int(start.timestamp()*1000), int(end.timestamp()*1000), label, ("BST" if o else "GMT"), yest

# ── API ─────────────────────────────────────────────────────────────────────────

def get(path):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def fetch(cid, s, e):
    out, cur = [], None
    while True:
        url = f"/v1/transactions?creatorId={cid}&limit=100&startTime={s}&endTime={e}"
        if cur: url += f"&cursor={cur}"
        d = get(url); out.extend(d["data"]["list"]); cur = d.get("cursor")
        if not d.get("hasMore") or not cur: break
    return out

def kind(t):
    t = t.lower()
    if "recurring" in t: return "ren"
    if "subscription" in t: return "new"
    if "message" in t: return "ppv"
    if "tip" in t: return "tip"
    if "post" in t: return "post"
    return "other"

# ── Gather ──────────────────────────────────────────────────────────────────────

def gather(start_ms, end_ms):
    creators = get("/v1/creators?limit=100")["data"]["list"]
    rows = {}
    tot = {"net":0,"gross":0,"new":0,"ren":0,"new_amt":0,"ren_amt":0,
           "ppv":0,"ppv_n":0,"tip":0,"post":0}
    for c in creators:
        r = {"net":0,"gross":0,"new":0,"ren":0}
        for tx in fetch(c["id"], start_ms, end_ms):
            if tx.get("status","").lower() in REFUND_STATUSES: continue
            net = int(tx.get("net",0)); gross = int(tx.get("amount",0)); k = kind(tx.get("type",""))
            r["net"]+=net; r["gross"]+=gross
            tot["net"]+=net; tot["gross"]+=gross
            if k=="new": r["new"]+=1; tot["new"]+=1; tot["new_amt"]+=net
            elif k=="ren": r["ren"]+=1; tot["ren"]+=1; tot["ren_amt"]+=net
            elif k=="ppv": tot["ppv"]+=net; tot["ppv_n"]+=1
            elif k=="tip": tot["tip"]+=net
            elif k=="post": tot["post"]+=net
        if r["net"] or r["new"] or r["ren"]:
            r["subs"]=r["new"]+r["ren"]; rows[c["name"]]=r
    return rows, tot

# ── Build Slack payload ─────────────────────────────────────────────────────────

def build_payload():
    if not API_KEY or not OID:
        print("ERROR: INFLOWW_API_KEY / INFLOWW_OID not set"); sys.exit(1)
    s, e, label, tz, yest = yesterday_window()
    rows, tot = gather(s, e)
    # prior day (for DoD)
    p_start = datetime(yest.year, yest.month, yest.day, tzinfo=timezone(timedelta(hours=london_offset(yest.year,yest.month,yest.day)))) - timedelta(days=1)
    py, pm, pd = p_start.year, p_start.month, p_start.day
    ptz = timezone(timedelta(hours=london_offset(py,pm,pd)))
    ps = int(datetime(py,pm,pd,tzinfo=ptz).timestamp()*1000); pe = int((datetime(py,pm,pd,tzinfo=ptz)+timedelta(days=1)).timestamp()*1000)
    _, ptot = gather(ps, pe)
    prev_net = ptot["net"]

    subs = tot["new"]+tot["ren"]; fee = tot["gross"]-tot["net"]
    rr = (tot["ren"]/subs*100) if subs else 0
    subs_amt = tot["new_amt"]+tot["ren_amt"]
    dod = ((tot["net"]-prev_net)/prev_net*100) if prev_net else 0

    def m(c): return f"${c/100:,.2f}"

    # copy-paste blocks
    subs_sorted = sorted([(n,r) for n,r in rows.items() if r["subs"]>0], key=lambda x:-x[1]["subs"])
    sales_sorted = sorted([(n,r) for n,r in rows.items() if r["net"]>0], key=lambda x:-x[1]["net"])
    subs_txt = "\n".join(f"{n} — {r['subs']} subs ({r['new']} new / {r['ren']} ren)" for n,r in subs_sorted)
    subs_txt += f"\nTotal: {subs} subs ({tot['new']} new / {tot['ren']} ren)"
    sales_txt = "\n".join(f"{n} — {m(r['net'])}" for n,r in sales_sorted)
    sales_txt += f"\nTOTAL NET: {m(tot['net'])} | GROSS: {m(tot['gross'])}"

    arrow = "🔻" if dod<0 else "🔺"
    blocks = [
        {"type":"header","text":{"type":"plain_text","text":f"📊 HOTTTR Daily Report — {label}"}},
        {"type":"context","elements":[{"type":"mrkdwn",
            "text":f"UK time ({tz}) · full day · matches Infloww dashboard · {arrow} {dod:+.1f}% vs prior day"}]},
        {"type":"divider"},
        {"type":"section","fields":[
            {"type":"mrkdwn","text":f"*Net Revenue*\n{m(tot['net'])}"},
            {"type":"mrkdwn","text":f"*Gross Revenue*\n{m(tot['gross'])}"},
            {"type":"mrkdwn","text":f"*OF Fee (20%)*\n{m(fee)}"},
            {"type":"mrkdwn","text":f"*Paid Subs*\n{subs} ({tot['new']} new · {tot['ren']} ren)"},
            {"type":"mrkdwn","text":f"*Renewal Rate*\n{rr:.1f}%"},
        ]},
        {"type":"section","fields":[
            {"type":"mrkdwn","text":f"*Subscriptions*\n{m(subs_amt)}"},
            {"type":"mrkdwn","text":f"*Messages (PPV)*\n{m(tot['ppv'])}"},
            {"type":"mrkdwn","text":f"*Tips*\n{m(tot['tip'])}"},
            {"type":"mrkdwn","text":f"*Posts*\n{m(tot['post'])}"},
            {"type":"mrkdwn","text":f"*Prior Day Net*\n{m(prev_net)}"},
        ]},
        {"type":"divider"},
        {"type":"section","text":{"type":"mrkdwn","text":f"*Paid Subs*\n```{subs_txt}```"}},
        {"type":"section","text":{"type":"mrkdwn","text":f"*Net Sales*\n```{sales_txt}```"}},
    ]
    return {"blocks": blocks}, tot, label

# ── Send ──────────────────────────────────────────────────────────────────────

def main():
    dry = len(sys.argv) > 1 and sys.argv[1] == "dry"
    payload, tot, label = build_payload()
    if dry:
        # readable preview
        for b in payload["blocks"]:
            if b["type"]=="header": print(b["text"]["text"])
            elif b["type"]=="context": print(b["elements"][0]["text"])
            elif b["type"]=="section" and "fields" in b: print("  " + " | ".join(f["text"].replace("\n"," ") for f in b["fields"]))
            elif b["type"]=="section": print(b["text"]["text"].replace("```",""))
            elif b["type"]=="divider": print("-"*48)
        return
    if not SLACK_WEBHOOK:
        print("ERROR: SLACK_WEBHOOK_URL not set"); sys.exit(1)
    req = urllib.request.Request(SLACK_WEBHOOK, data=json.dumps(payload).encode(),
                                 headers={"Content-Type":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"Sent daily report: {label} | net=${tot['net']/100:,.2f}")
    except urllib.error.HTTPError as ex:
        print(f"Slack error {ex.code}: {ex.read().decode()}"); sys.exit(1)

if __name__ == "__main__":
    main()
