# HOTTTR Infloww Brain

## Environment
You are running inside Claude Code with full internet access and the ability to execute Python scripts directly via Bash. Always write and run code directly — do not tell the user to run it themselves unless they ask.

---

## Credentials — read from the environment, NEVER hardcode

**This repository is public.** Nothing secret goes in a tracked file. Scripts read
from environment variables; GitHub Actions supplies them from repo Secrets
(`INFLOWW_API_KEY`, `INFLOWW_OID`, `SLACK_WEBHOOK_URL`). Locally they come from
the untracked `.env`.

```python
import os
API_KEY = os.environ["INFLOWW_API_KEY"]
OID     = os.environ["INFLOWW_OID"]
BASE    = "https://openapi.infloww.com"
UA      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
HEADERS = {"Authorization": API_KEY, "x-oid": OID, "User-Agent": UA, "Accept": "application/json"}
```

Slack webhook URL: *(paste current webhook URL here after regenerating from Slack)*

---

## Infloww API rules

- **Amounts are in CENTS** — always divide by 100 for dollars
- **Timestamps are Unix milliseconds** — `int(datetime(..., tzinfo=tz).timestamp() * 1000)`
- **Auth** = `Authorization` header, value is key directly, NO "Bearer" prefix
- **Pagination** = cursor-based, always loop until `hasMore` is false

### Standard fetch function
```python
import urllib.request, json

def get(path):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())
```

### Key endpoints
```
GET /v1/creators?limit=100
GET /v1/transactions?creatorId=X&startTime=MS&endTime=MS&limit=100
```

### Transaction types
| Type | Meaning |
|------|---------|
| `subscription` (no "recurring") | New sub |
| `recurringsubscription` | Renewal |
| `message` | PPV / paid message |
| `tip` | Tip |

**Never sum total net for sub earnings** — filter only `subscription` + `recurringsubscription`.

---

## Active creator pages (Aug 2026)

| Creator | ID |
|---------|-----|
| Emma Storm VIP | 2211853128433668 |
| Yasmine | 2347404913344525 |
| Ella Robinson | 2400360786427934 |
| Fox Heart | 2238449545969676 |
| Alice Baby | 1998117335531524 |
| Leah Jewel | 2286031391096834 |
| Maddy | 2342180572233763 |
| Alessa | 2437974986260522 |
| Miya Rai | 2249045263384591 |

> Note: Shy Trans (2472285299015681) returns 400 errors — skip or investigate separately.

---

## Timezone rules

- **Daily report** = Europe/London (BST/GMT) — yesterday midnight to today midnight
- **Shift analysis** = use the shift's local timezone, convert to ms
- **Message Dashboard Excel exports** = timestamps in local team timezone

### BST date window (for daily report)
```python
import pytz
from datetime import datetime, timedelta

london   = pytz.timezone('Europe/London')
now      = datetime.now(london)
yest     = now.date() - timedelta(days=1)
start_ms = int(london.localize(datetime(yest.year, yest.month, yest.day)).timestamp() * 1000)
end_ms   = int(london.localize(datetime(now.year, now.month, now.day)).timestamp() * 1000)
```

### Custom time window (for shift analysis)
```python
from datetime import timezone, timedelta, datetime
PHT      = timezone(timedelta(hours=8))  # Manila
start_ms = int(datetime(2026, 8, 7, 0, 0, tzinfo=PHT).timestamp() * 1000)
end_ms   = int(datetime(2026, 8, 7, 8, 30, tzinfo=PHT).timestamp() * 1000)
```

---

## Standard scripts

### Fetch all active creators
```python
creators = get("/v1/creators?limit=100")["data"]["list"]
ACTIVE = {"Emma Storm VIP","Yasmine","Ella Robinson","Fox Heart","Alice Baby",
          "Leah Jewel","Maddy","Alessa","Miya Rai"}
creators = [c for c in creators if c["name"] in ACTIVE]
```

### Fetch transactions for one creator (paginated)
```python
def fetch_transactions(creator_id, start_ms, end_ms):
    results, cursor = [], None
    while True:
        url = f"/v1/transactions?creatorId={creator_id}&limit=100&startTime={start_ms}&endTime={end_ms}"
        if cursor: url += f"&cursor={cursor}"
        data = get(url)
        results.extend(data["data"]["list"])
        cursor = data.get("cursor")
        if not data.get("hasMore") or not cursor: break
    return results
```

### Sub earnings only
```python
def calc_subs(txns):
    new_count = new_net = ren_count = ren_net = 0
    for tx in txns:
        t = tx.get("type", "").lower()
        n = int(tx.get("net", 0))
        if "subscription" in t and "recurring" not in t:
            new_count += 1; new_net += n
        elif "recurring" in t:
            ren_count += 1; ren_net += n
    return new_count, new_net, ren_count, ren_net
```

### Message Dashboard Excel analysis
```python
import pandas as pd

def load_dashboard(path):
    df = pd.read_excel(path, header=0)
    df.columns = ['Sender','Creator','FansMessage','CreatorMessage',
                  'SentTime','SentDate','ReplyTime','Price',
                  'Purchased','Source','Status','SentTo']
    df['datetime'] = df.apply(lambda r: pd.to_datetime(
        f"{r['SentDate']} {r['SentTime']}", format="%b %d, %Y %H:%M:%S"), axis=1)
    df['Price']     = pd.to_numeric(df['Price'], errors='coerce').fillna(0)
    df['Purchased'] = df['Purchased'].astype(str).str.strip().str.lower() == 'yes'
    return df
```

---

## GitHub / Automation

- **Repo:** `https://github.com/andyhotttr2026/HOTTTR-Sales-Report`
- **Daily Slack report:** runs via GitHub Actions at 4AM UTC (~5AM BST, ~12PM Manila) — GitHub scheduling can drift up to 1 hour
- **Manual trigger:** GitHub → Actions → HOTTTR Slack Daily Report → Run workflow
- **Secrets in repo:** `INFLOWW_API_KEY`, `INFLOWW_OID`, `SLACK_WEBHOOK_URL`

---

## Common requests

| Ask | What to do |
|-----|-----------|
| Pull yesterday's revenue | BST date window, loop all active creators, sum gross/net/subs |
| Analyse a specific shift | Set start/end in correct timezone, fetch + break down by hour and creator |
| Sub earnings only | Filter `subscription` + `recurringsubscription` only |
| PPV revenue only | Filter `message` type |
| Per-creator hourly grid | Loop creators × hourly windows |
| Build a shift report for AJ | Dark HTML dashboard artifact: KPI cards, hourly bar chart, creator PPV table, sub heatgrid, key factors, action items |
| Send to Slack | POST JSON payload to `SLACK_WEBHOOK_URL` using `urllib.request` |
