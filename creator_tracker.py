#!/usr/bin/env python3
"""
HOTTTR Creator Tracker
Scrapes Instagram (posts + reels) and TikTok for each creator via Apify,
works out today / last 7 days / last 30 days counts and engagement,
saves a daily snapshot to history.json and fires a digest to Telegram.
Also writes to Supabase if credentials are present.

Run once a day on a cron/scheduler.

Setup:
  pip install requests
  Fill in the CONFIG section below, or set environment variables.
"""

import json
import os
import sys
import time
import requests
from datetime import datetime, timedelta, timezone

# ============================================================
# CONFIG - reads from environment variables (GitHub Actions secrets),
# falls back to hardcoded values for local runs
# ============================================================

APIFY_TOKEN          = os.environ.get("APIFY_TOKEN")
TELEGRAM_BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID     = "-1003975833641"
TELEGRAM_TOPIC_ID    = None

SUPABASE_URL         = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY         = os.environ.get("SUPABASE_KEY", "")

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")

CREATORS = [
    {"name": "Alice",    "ig": "alicebaby.x1",       "tt": "aliceisherenow0"},
    {"name": "Anne",     "ig": "loveannetel",        "tt": None},
    {"name": "Ella",     "ig": "pretty.little.ella", "tt": "pretty.little.ella"},
    {"name": "Emma",     "ig": "scottishemmastorm",  "tt": "scottishemmastorm"},
    {"name": "FoxHeart", "ig": "foxheart_t",         "tt": "foxheartcosplay"},
    {"name": "Leah",     "ig": "aleah.keith",        "tt": "aleah.keith1"},
    {"name": "Maddy",    "ig": "mads111711",         "tt": "maddy17111"},
    {"name": "Miya",     "ig": "miya_rai_real",      "tt": "miya.rai.real"},
    {"name": "Yasmine",  "ig": "minavangirl",        "tt": "aussievangirlmina"},
]

IG_RESULTS_LIMIT = 60
TT_RESULTS_LIMIT = 60

IG_ACTOR        = "apify~instagram-scraper"
IG_STORY_ACTOR  = "apify~instagram-story-scraper"
TT_ACTOR        = "clockworks~tiktok-scraper"

# ============================================================
# APIFY HELPERS
# ============================================================

def run_apify_actor(actor_id, run_input, timeout_secs=600):
    url = f"https://api.apify.com/v2/acts/{actor_id}/runs?token={APIFY_TOKEN}"
    resp = requests.post(url, json=run_input, timeout=60)
    resp.raise_for_status()
    run = resp.json()["data"]
    run_id = run["id"]

    status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
    waited = 0
    while waited < timeout_secs:
        time.sleep(10)
        waited += 10
        r = requests.get(status_url, timeout=60)
        r.raise_for_status()
        status = r.json()["data"]["status"]
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {run_id} ended with status {status}")
    else:
        raise RuntimeError(f"Apify run {run_id} timed out after {timeout_secs}s")

    dataset_id = r.json()["data"]["defaultDatasetId"]
    items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={APIFY_TOKEN}&clean=true&format=json"
    items = requests.get(items_url, timeout=120).json()
    return items


def run_apify_actor_with_retry(actor_id, run_input, timeout_secs=600, max_retries=2):
    for attempt in range(max_retries + 1):
        try:
            return run_apify_actor(actor_id, run_input, timeout_secs)
        except Exception as e:
            if attempt < max_retries:
                print(f"Attempt {attempt + 1} failed ({e}). Retrying in 60s...")
                time.sleep(60)
            else:
                raise


def scrape_instagram(handles):
    """Returns ({handle: [post items]}, {restricted handles}).
    Restricted/age-gated profiles return a profile stub with an error instead of
    posts — we flag those so the digest can say 'restricted' rather than a false 0."""
    out = {h: [] for h in handles}
    restricted = set()
    for i in range(0, len(handles), 3):
        batch = handles[i:i+3]
        run_input = {
            "directUrls": [f"https://www.instagram.com/{h}/" for h in batch],
            "resultsType": "posts",
            "resultsLimit": IG_RESULTS_LIMIT,
            "addParentData": False,
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        }
        try:
            items = run_apify_actor_with_retry(IG_ACTOR, run_input)
            for it in items:
                owner = (it.get("ownerUsername") or "").lower()
                if owner in out:
                    out[owner].append(it)
                elif it.get("error"):
                    # profile stub (no posts) — restricted, private, or age-gated
                    uname = (it.get("username") or "").lower()
                    if uname in out:
                        restricted.add(uname)
        except Exception as e:
            print(f"IG batch {batch} failed after retries: {e}")
    return out, restricted


def scrape_tiktok(handles):
    out = {h: [] for h in handles}
    for i in range(0, len(handles), 3):
        batch = handles[i:i+3]
        run_input = {
            "profiles": batch,
            "resultsPerPage": TT_RESULTS_LIMIT,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        }
        try:
            items = run_apify_actor_with_retry(TT_ACTOR, run_input)
            for it in items:
                author = ((it.get("authorMeta") or {}).get("name") or "").lower()
                if author in out:
                    out[author].append(it)
        except Exception as e:
            print(f"TT batch {batch} failed after retries: {e}")
    return out

def scrape_instagram_stories(handles):
    """Scrape active stories for a list of IG handles. Returns {handle: story_count}."""
    out = {h: 0 for h in handles}
    for i in range(0, len(handles), 3):
        batch = handles[i:i+3]
        run_input = {
            "usernames": batch,
            "maxItems": 50,
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        }
        try:
            items = run_apify_actor_with_retry(IG_STORY_ACTOR, run_input)
            for it in items:
                owner = (it.get("username") or it.get("ownerUsername") or "").lower().lstrip("@")
                if owner in out:
                    out[owner] += 1
        except Exception as e:
            print(f"Story batch {batch} failed after retries: {e}")
    return out

# ============================================================
# STATS
# ============================================================

def parse_ig_item(it):
    ts_raw = it.get("timestamp")
    ts = None
    if ts_raw:
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    item_type = it.get("type", "")
    kind = "reel" if item_type == "Video" else "post"
    views = it.get("videoPlayCount") or it.get("videoViewCount") or 0
    likes = it.get("likesCount") or 0
    comments = it.get("commentsCount") or 0
    return ts, kind, views, likes, comments


def parse_tt_item(it):
    ts = None
    create_time = it.get("createTimeISO") or it.get("createTime")
    if isinstance(create_time, str):
        try:
            ts = datetime.fromisoformat(create_time.replace("Z", "+00:00"))
        except ValueError:
            pass
    elif isinstance(create_time, (int, float)):
        ts = datetime.fromtimestamp(create_time, tz=timezone.utc)
    views    = it.get("playCount") or 0
    likes    = it.get("diggCount") or 0
    comments = it.get("commentCount") or 0
    shares   = it.get("shareCount") or 0
    return ts, views, likes, comments, shares


def window_stats_ig(items, since):
    reels = posts = views = likes = comments = 0
    for it in items:
        ts, kind, v, l, c = parse_ig_item(it)
        if ts is None or ts < since:
            continue
        if kind == "reel":
            reels += 1
        else:
            posts += 1
        views    += v
        likes    += l
        comments += c
    return {"reels": reels, "posts": posts, "views": views, "likes": likes, "comments": comments}


def window_stats_tt(items, since):
    vids = views = likes = comments = shares = 0
    for it in items:
        ts, v, l, c, s = parse_tt_item(it)
        if ts is None or ts < since:
            continue
        vids     += 1
        views    += v
        likes    += l
        comments += c
        shares   += s
    return {"videos": vids, "views": views, "likes": likes, "comments": comments, "shares": shares}

# ============================================================
# FORMATTING
# ============================================================

def fmt_num(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def build_digest(results, run_date):
    lines = [f"*HOTTTR Creator Digest - {run_date.strftime('%a %d %b %Y')}*", ""]
    for r in results:
        lines.append(f"*{r['name']}*")

        if r.get("ig_error"):
            lines.append(f"  IG (@{r['ig']}): scrape failed")
        elif r.get("ig_restricted"):
            lines.append(f"  IG (@{r['ig']}): restricted profile (needs login to read)")
        else:
            d, w, m = r["ig_day"], r["ig_week"], r["ig_month"]
            stories = r.get("ig_stories", "?")
            lines.append(f"  IG (@{r['ig']})")
            lines.append(
                f"  Today: {d['reels']} reels, {d['posts']} posts, {stories} stories | "
                f"{fmt_num(d['views'])} views, {fmt_num(d['likes'])} likes, {fmt_num(d['comments'])} comments"
            )
            lines.append(
                f"  7d: {w['reels']} reels, {w['posts']} posts | "
                f"{fmt_num(w['views'])} views, {fmt_num(w['likes'])} likes, {fmt_num(w['comments'])} comments"
            )
            lines.append(
                f"  30d: {m['reels']} reels, {m['posts']} posts | "
                f"{fmt_num(m['views'])} views, {fmt_num(m['likes'])} likes"
            )

        if r.get("tt_none"):
            lines.append(f"  TT: No account")
        elif r.get("tt_error"):
            lines.append(f"  TT (@{r['tt']}): scrape failed")
        else:
            d, w, m = r["tt_day"], r["tt_week"], r["tt_month"]
            lines.append(f"  TT (@{r['tt']})")
            lines.append(
                f"  Today: {d['videos']} {'video' if d['videos'] == 1 else 'videos'} | "
                f"{fmt_num(d['views'])} views, {fmt_num(d['likes'])} likes, {fmt_num(d['comments'])} comments, {fmt_num(d['shares'])} shares"
            )
            lines.append(
                f"  7d: {w['videos']} videos | "
                f"{fmt_num(w['views'])} views, {fmt_num(w['likes'])} likes, "
                f"{fmt_num(w['comments'])} comments, {fmt_num(w['shares'])} shares"
            )
            lines.append(
                f"  30d: {m['videos']} videos | "
                f"{fmt_num(m['views'])} views, {fmt_num(m['likes'])} likes"
            )
        lines.append("")
    return "\n".join(lines)

# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = []
    while text:
        if len(text) <= 4000:
            chunks.append(text)
            break
        cut = text.rfind("\n\n", 0, 4000)
        if cut == -1:
            cut = 4000
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    for chunk in chunks:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown",
        }
        if TELEGRAM_TOPIC_ID:
            payload["message_thread_id"] = TELEGRAM_TOPIC_ID
        resp = requests.post(url, json=payload, timeout=30)
        if not resp.ok:
            payload.pop("parse_mode", None)
            requests.post(url, json=payload, timeout=30)

# ============================================================
# SUPABASE
# ============================================================

def write_to_supabase(results, run_date):
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("No Supabase credentials found, skipping database write.")
        return

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    date_str = run_date.strftime("%Y-%m-%d")
    hour = run_date.hour
    if hour < 6:
        run_slot = "night"
    elif hour < 12:
        run_slot = "morning"
    elif hour < 18:
        run_slot = "afternoon"
    else:
        run_slot = "evening"
    rows = []

    for r in results:
        if not r.get("ig_error") and not r.get("ig_restricted"):
            rows.append({
                "run_date":      date_str,
                "run_slot":      run_slot,
                "creator_name":  r["name"],
                "platform":      "instagram",
                "today_count":   r["ig_day"]["reels"] + r["ig_day"]["posts"],
                "today_stories": r.get("ig_stories") if isinstance(r.get("ig_stories"), int) else None,
                "week_count":    r["ig_week"]["reels"] + r["ig_week"]["posts"],
                "week_views":    r["ig_week"]["views"],
                "week_likes":    r["ig_week"]["likes"],
                "week_comments": r["ig_week"]["comments"],
                "week_shares":   None,
                "month_count":   r["ig_month"]["reels"] + r["ig_month"]["posts"],
                "month_views":   r["ig_month"]["views"],
                "month_likes":   r["ig_month"]["likes"],
            })
        if not r.get("tt_error") and not r.get("tt_none"):
            rows.append({
                "run_date":      date_str,
                "run_slot":      run_slot,
                "creator_name":  r["name"],
                "platform":      "tiktok",
                "today_count":   r["tt_day"]["videos"],
                "week_count":    r["tt_week"]["videos"],
                "week_views":    r["tt_week"]["views"],
                "week_likes":    r["tt_week"]["likes"],
                "week_comments": r["tt_week"]["comments"],
                "week_shares":   r["tt_week"]["shares"],
                "month_count":   r["tt_month"]["videos"],
                "month_views":   r["tt_month"]["views"],
                "month_likes":   r["tt_month"]["likes"],
            })

    if not rows:
        return

    url = f"{SUPABASE_URL}/rest/v1/creator_stats"
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    resp = requests.post(url, headers=headers, json=rows, timeout=30)
    if resp.ok:
        print(f"Supabase: wrote {len(rows)} rows for {date_str} ({run_slot}).")
    else:
        print(f"Supabase write failed: {resp.status_code} {resp.text}")

# ============================================================
# HISTORY (local only, not used in GitHub Actions)
# ============================================================

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return {}


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

# ============================================================
# MAIN
# ============================================================

def main():
    now       = datetime.now(timezone.utc)
    day_ago   = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago  = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    ig_handles = [c["ig"].lower() for c in CREATORS]
    tt_handles = [c["tt"].lower() for c in CREATORS if c["tt"]]

    print("Scraping Instagram...")
    try:
        ig_data, ig_restricted = scrape_instagram(ig_handles)
    except Exception as e:
        print(f"Instagram scrape failed entirely: {e}")
        ig_data = None
        ig_restricted = set()

    print("Scraping Instagram stories...")
    try:
        ig_story_data = scrape_instagram_stories(ig_handles)
    except Exception as e:
        print(f"Instagram story scrape failed: {e}")
        ig_story_data = None

    print("Scraping TikTok...")
    try:
        tt_data = scrape_tiktok(tt_handles)
    except Exception as e:
        print(f"TikTok scrape failed entirely: {e}")
        tt_data = None

    results = []
    for c in CREATORS:
        r = {"name": c["name"], "ig": c["ig"], "tt": c["tt"]}

        if ig_data is None:
            r["ig_error"] = True
        elif c["ig"].lower() in ig_restricted:
            r["ig_restricted"] = True
        else:
            items = ig_data.get(c["ig"].lower(), [])
            r["ig_day"]    = window_stats_ig(items, day_ago)
            r["ig_week"]   = window_stats_ig(items, week_ago)
            r["ig_month"]  = window_stats_ig(items, month_ago)
            r["ig_stories"] = ig_story_data.get(c["ig"].lower(), 0) if ig_story_data is not None else 0

        if c["tt"] is None:
            r["tt_none"] = True
        elif tt_data is None:
            r["tt_error"] = True
        else:
            items = tt_data.get(c["tt"].lower(), [])
            r["tt_day"]   = window_stats_tt(items, day_ago)
            r["tt_week"]  = window_stats_tt(items, week_ago)
            r["tt_month"] = window_stats_tt(items, month_ago)

        results.append(r)

    # Save to Supabase
    write_to_supabase(results, now)

    # Save local history snapshot
    try:
        history = load_history()
        history[now.strftime("%Y-%m-%d")] = results
        if len(history) > 400:
            for k in sorted(history.keys())[:-400]:
                del history[k]
        save_history(history)
    except Exception:
        pass  # history.json not available in cloud environments

    # Send Telegram digest
    digest = build_digest(results, now)
    print(digest)
    send_telegram(digest)
    print("Digest sent.")


if __name__ == "__main__":
    main()
