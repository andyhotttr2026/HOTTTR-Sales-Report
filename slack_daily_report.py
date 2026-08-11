import urllib.request, urllib.error, json, os
from datetime import datetime, timezone, timedelta

API_KEY      = os.environ.get('INFLOWW_API_KEY')
OID          = os.environ.get('INFLOWW_OID')
SLACK_WEBHOOK = os.environ.get('SLACK_WEBHOOK_URL')
BASE         = "https://openapi.infloww.com"
UA           = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
HEADERS      = {"Authorization": API_KEY, "x-oid": OID, "User-Agent": UA, "Accept": "application/json"}
BST          = timezone(timedelta(hours=1))

# ── Timezone helpers ──────────────────────────────────────────────────────────

def london_tz_for(year, month, day):
    bst_start = max(datetime(year, 3, d) for d in range(25, 32) if datetime(year, 3, d).weekday() == 6)
    bst_end   = max(datetime(year, 10, d) for d in range(25, 32) if datetime(year, 10, d).weekday() == 6)
    return BST if bst_start <= datetime(year, month, day) < bst_end else timezone.utc

def yesterday_london():
    now_utc     = datetime.now(timezone.utc)
    # Determine current London offset
    y0, m0, d0  = now_utc.year, now_utc.month, now_utc.day
    tz_now      = london_tz_for(y0, m0, d0)
    now_lon     = now_utc.astimezone(tz_now)
    yest        = (now_lon - timedelta(days=1)).date()
    y, m, d     = yest.year, yest.month, yest.day
    tz_yest     = london_tz_for(y, m, d)
    start       = int(datetime(y, m, d,  0,  0,  0, tzinfo=tz_yest).timestamp() * 1000)
    end         = int(datetime(y, m, d, 23, 59, 59, tzinfo=tz_yest).timestamp() * 1000)
    tz_label    = "BST" if tz_yest.utcoffset(None).seconds == 3600 else "GMT"
    label       = f"{yest.strftime('%A, %B')} {yest.day}, {yest.year}"
    return start, end, label, tz_label

# ── API helpers ───────────────────────────────────────────────────────────────

def get(path):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def fetch_day(creator_id, start, end):
    net = gross = new_subs = renewals = 0
    cursor = None
    while True:
        url = f"/v1/transactions?creatorId={creator_id}&limit=100&startTime={start}&endTime={end}"
        if cursor: url += f"&cursor={cursor}"
        data = get(url)
        for tx in data["data"]["list"]:
            t      = tx.get("type", "").lower()
            net   += int(tx.get("net", 0))
            gross += int(tx.get("amount", 0))
            if "subscription" in t and "recurring" not in t: new_subs += 1
            elif "recurring" in t: renewals += 1
        cursor = data.get("cursor")
        if not data.get("hasMore") or not cursor: break
    return net, gross, new_subs, renewals

# ── Fetch ─────────────────────────────────────────────────────────────────────

DAY_START, DAY_END, DAY_LABEL, TZ_LABEL = yesterday_london()

creators    = get("/v1/creators?limit=100")["data"]["list"]
results     = {}
grand_net   = grand_gross = grand_new = grand_ren = 0

FREE_PAGES  = {"Maddy", "Leah Jewel", "Alice Free", "Ella Free", "Alessa Free",
               "Miya Free", "Shy Trans Free", "Miya Rai VIP"}

for c in creators:
    cid, name = c["id"], c["name"]
    net, gross, new_subs, renewals = fetch_day(cid, DAY_START, DAY_END)
    if net == 0 and new_subs == 0 and renewals == 0: continue
    results[name] = {"net": net, "gross": gross, "new": new_subs, "ren": renewals,
                     "free": name in FREE_PAGES}
    grand_net   += net
    grand_gross += gross
    grand_new   += new_subs
    grand_ren   += renewals

# ── Format Slack message ──────────────────────────────────────────────────────

sorted_creators = sorted(results.items(), key=lambda x: -x[1]["net"])

total_subs   = grand_new + grand_ren
of_fee       = grand_gross - grand_net
renewal_rate = (grand_ren / total_subs * 100) if total_subs > 0 else 0

# Creator table rows
table_lines = []
col_w = 22
header = f"{'CREATOR':<{col_w}}  {'NET':>9}    SUBS (new / ren)"
table_lines.append(header)
table_lines.append("─" * 58)

for i, (name, s) in enumerate(sorted_creators):
    subs_str = f"{s['new']+s['ren']} ({s['new']} new / {s['ren']} ren)" if not s["free"] else "— free page"
    tag      = " 🆓" if s["free"] else ""
    medal    = "🥇 " if i == 0 else "   "
    row      = f"{medal}{(name+tag):<{col_w}}  ${s['net']/100:>8.2f}    {subs_str}"
    table_lines.append(row)

table_lines.append("─" * 58)
table_lines.append(f"{'   TOTAL':<{col_w+3}}  ${grand_net/100:>8.2f}    {total_subs} ({grand_new} new / {grand_ren} ren)")

table_str = "\n".join(table_lines)

blocks = [
    {
        "type": "header",
        "text": {"type": "plain_text", "text": f"📊 HOTTTR Daily Report — {DAY_LABEL}"}
    },
    {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"London time ({TZ_LABEL}) · yesterday's confirmed numbers"}]
    },
    {"type": "divider"},
    {
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Net Revenue*\n${grand_net/100:,.2f}"},
            {"type": "mrkdwn", "text": f"*Gross Revenue*\n${grand_gross/100:,.2f}"},
            {"type": "mrkdwn", "text": f"*Paid Subs*\n{total_subs}  ({grand_new} new · {grand_ren} renewals)"},
            {"type": "mrkdwn", "text": f"*Renewal Rate*\n{renewal_rate:.1f}%"},
            {"type": "mrkdwn", "text": f"*OF Fee (20%)*\n${of_fee/100:,.2f}"},
        ]
    },
    {"type": "divider"},
    {
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*Creator Breakdown*\n```{table_str}```"}
    }
]

payload = {"blocks": blocks}

# ── Send ──────────────────────────────────────────────────────────────────────

if not SLACK_WEBHOOK:
    print("ERROR: SLACK_WEBHOOK_URL not set")
    exit(1)

req = urllib.request.Request(
    SLACK_WEBHOOK,
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print(f"Sent to Slack: {DAY_LABEL} | net=${grand_net/100:,.2f} | {total_subs} subs")
except urllib.error.HTTPError as e:
    print(f"Slack error {e.code}: {e.read().decode()}")
    exit(1)
