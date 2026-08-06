# HOTTTR Creator Tracker

## For Claude — read this first

You are managing the HOTTTR Creator Tracker system. This is a live production automation that runs twice a day. Here is everything you need to know to manage it confidently without asking the user to explain anything.

When the user asks you to make a change:
1. Edit the relevant file (`creator_tracker.py` for creator/config changes, `.github/workflows/daily.yml` for schedule changes)
2. Commit and push to GitHub — the automation picks it up automatically
3. Confirm what you changed in plain English

Do not ask the user for technical details — everything you need is in this file.

---

## What this system does

Runs at **8am and 8pm BST every day** via GitHub Actions. For each of the 9 HOTTTR creators it:
1. Scrapes Instagram posts, reels, and stories via Apify
2. Scrapes TikTok videos via Apify
3. Calculates stats across today / last 7 days / last 30 days
4. Writes results to Supabase (`creator_stats` table)
5. Sends a formatted digest to the HOTTTR Marketing Telegram group

---

## Architecture

- **Scraping**: Apify with residential proxies — 3 actors: `apify~instagram-scraper`, `apify~instagram-story-scraper`, `clockworks~tiktok-scraper`
- **Retry logic**: Instagram retries up to 3 times with 60s delay if blocked
- **Database**: Supabase — `creator_stats` table, one row per creator per platform per run slot
- **Notifications**: Telegram bot `@HOTTTR_Socials_Trackerbot` → HOTTTR Marketing group
- **Scheduler**: GitHub Actions cron — `0 7 * * *` (8am BST) and `0 19 * * *` (8pm BST)

---

## Files

| File | Purpose |
|------|---------|
| `creator_tracker.py` | Main script — all scraping, calculation, Supabase write, Telegram send |
| `.github/workflows/daily.yml` | GitHub Actions schedule and secrets wiring |
| `supabase_schema.sql` | Database schema (already applied — reference only) |
| `HANDOVER.md` | All credentials, access links, and account details |
| `CLAUDE.md` | This file |

---

## Credentials (all accounts are shared under HOTTTR agency)

Stored as GitHub Actions secrets — used automatically when the workflow runs. Also hardcoded as fallbacks in `creator_tracker.py` for local runs.

| Secret name | What it's for |
|-------------|--------------|
| `APIFY_TOKEN` | Apify scraping API |
| `TELEGRAM_BOT_TOKEN` | Telegram bot that posts digests |
| `SUPABASE_URL` | `https://ibhbcdynevvkysbxritv.supabase.co` |
| `SUPABASE_KEY` | Supabase secret key for database writes |

To update a secret: GitHub repo → Settings → Secrets and variables → Actions → edit the relevant secret.

---

## Creators list

Defined in `CREATORS` at the top of `creator_tracker.py`. Edit this to add, remove, or update creators.

```python
CREATORS = [
    {"name": "Alice",    "ig": "alicebaby.x1",       "tt": "aliceisherenow0"},
    {"name": "Anne",     "ig": "loveannetel",         "tt": None},              # no TikTok
    {"name": "Ella",     "ig": "pretty.little.ella",  "tt": "pretty.little.ella"},
    {"name": "Emma",     "ig": "scottishemmastorm",   "tt": "scottishemmastorm"},
    {"name": "FoxHeart", "ig": "foxheart_t",          "tt": "foxheartcosplay"},
    {"name": "Leah",     "ig": "aleah.keith",         "tt": "aleah.keith1"},
    {"name": "Maddy",    "ig": "mads111711",          "tt": "maddy17111"},
    {"name": "Miya",     "ig": "miya_rai_real",       "tt": "miya.rai.real"},
    {"name": "Yasmine",  "ig": "minavangirl",         "tt": "aussievangirlmina"},
]
```

Set `"tt": None` for creators with no TikTok account. After any change, commit and push — the next scheduled run picks it up automatically.

---

## Key config (top of creator_tracker.py)

```python
IG_RESULTS_LIMIT = 60   # posts pulled per IG profile — lower to 40 if Apify credits are low
TT_RESULTS_LIMIT = 60   # videos pulled per TT profile
```

---

## Schedule (daily.yml)

```yaml
- cron: '0 7 * * *'    # 8am BST
- cron: '0 19 * * *'   # 8pm BST
```

GitHub Actions cron is always UTC. BST = UTC+1, so 8am BST = 7am UTC.

---

## Supabase schema

Table: `creator_stats`

| Column | Type | Notes |
|--------|------|-------|
| `run_date` | date | Date of the run |
| `run_slot` | text | `"morning"` or `"evening"` |
| `creator_name` | text | e.g. `"Alice"` |
| `platform` | text | `"instagram"` or `"tiktok"` |
| `today_count` | int | Posts/reels/videos in last 24h |
| `today_stories` | int | IG stories (null for TikTok) |
| `week_count` | int | Content count last 7 days |
| `week_views` | bigint | |
| `week_likes` | bigint | |
| `week_comments` | bigint | |
| `week_shares` | bigint | TikTok only |
| `month_count` | int | Content count last 30 days |
| `month_views` | bigint | |
| `month_likes` | bigint | |

Unique constraint on `(run_date, run_slot, creator_name, platform)`.

---

## How to trigger a manual run

GitHub repo → Actions → HOTTTR Creator Tracker → Run workflow → Run workflow.

---

## Troubleshooting guide

**"scrape failed" for all Instagram creators**
Instagram blocked Apify temporarily. The script retries 3 times automatically. Wait for the next scheduled run — usually fixes itself. If it fails for 2+ consecutive days, swap `IG_ACTOR` in `creator_tracker.py` to `apify~instagram-post-scraper` and push.

**"scrape failed" for one creator only**
That creator's account likely went private, changed handle, or got banned. Check their profile manually and update the handle in `CREATORS` if needed.

**Nothing arriving in Telegram**
The bot was removed from the HOTTTR Marketing group, or the token expired. Re-add `@HOTTTR_Socials_Trackerbot` to the group. If the token expired, go to @BotFather → `/mybots` → select the bot → Revoke token → update the `TELEGRAM_BOT_TOKEN` GitHub secret.

**GitHub Actions stopped running automatically**
GitHub disables scheduled workflows after 60 days of repo inactivity. Fix: make any small change to any file and push — this re-enables the schedule.

**Supabase write errors in the logs**
Check the `run_slot` column exists in the `creator_stats` table. If missing, run in Supabase SQL Editor:
```sql
alter table creator_stats add column run_slot text not null default 'morning';
drop index creator_stats_unique;
create unique index creator_stats_unique on creator_stats (run_date, run_slot, creator_name, platform);
```

**Apify credits running low**
Lower `IG_RESULTS_LIMIT` and `TT_RESULTS_LIMIT` from 60 to 40 in `creator_tracker.py` and push.

---

## Common requests and how to handle them

| User says | What to do |
|-----------|-----------|
| "Add creator X with IG @x and TT @y" | Add line to `CREATORS` in `creator_tracker.py`, commit, push |
| "Remove creator X" | Delete their line from `CREATORS`, commit, push |
| "X changed their handle to @y" | Update the `ig` or `tt` value in `CREATORS`, commit, push |
| "Run it now" | Guide user to Actions → Run workflow, or explain you can't trigger GitHub Actions directly |
| "Change the schedule" | Update cron lines in `.github/workflows/daily.yml`, commit, push |
| "Why did it fail?" | Check GitHub Actions logs for the failed run and explain the error in plain English |
| "Lower the Apify usage" | Reduce `IG_RESULTS_LIMIT` and `TT_RESULTS_LIMIT` to 40 in `creator_tracker.py`, commit, push |
