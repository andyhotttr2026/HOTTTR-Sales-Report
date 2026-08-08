# -*- coding: utf-8 -*-
"""Shared landing-page (index.html) generator for the hosted dashboards.

Scans docs/archive/ for saved daily + shift reports and builds a dark landing
page with 'latest' links plus date pickers to browse any previous day.
Both slack_daily_report.py and shift_report.py call write_index() so the
picker always reflects every archived report currently in docs/archive/.
"""
import os, glob, re
from datetime import date

MONTHS = ["", "Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

def _pretty(iso):
    try:
        y, m, d = (int(x) for x in iso.split("-"))
        return f"{MONTHS[m]} {d}, {y}"
    except Exception:
        return iso

def write_index():
    os.makedirs("docs", exist_ok=True)
    dailies = sorted(glob.glob("docs/archive/daily-*.html"), reverse=True)
    shifts  = sorted(glob.glob("docs/archive/shift-*.html"), reverse=True)

    daily_opts = []
    for p in dailies:
        mo = re.search(r"daily-(\d{4}-\d{2}-\d{2})", p.replace("\\", "/"))
        if mo:
            iso = mo.group(1)
            daily_opts.append(f'<option value="archive/daily-{iso}.html">{_pretty(iso)}</option>')

    shift_opts = []
    for p in shifts:
        mo = re.search(r"shift-(\d{4}-\d{2}-\d{2})-s(\d)", p.replace("\\", "/"))
        if mo:
            iso, n = mo.group(1), mo.group(2)
            shift_opts.append(f'<option value="archive/shift-{iso}-s{n}.html">{_pretty(iso)} · Shift {n}</option>')

    daily_sel = ("".join(daily_opts)) or '<option value="">No archived days yet</option>'
    shift_sel = ("".join(shift_opts)) or '<option value="">No archived shifts yet</option>'

    html = f'''<title>HOTTTR Reports</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{{--bg:#0e0f17;--card:#1a1d2b;--line:#2a2e40;--text:#e8eaf0;--dim:#8890a6;--cyan:#4db8ff;--green:#3ddc84;}}
  *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    min-height:100vh;padding:48px 20px;display:flex;flex-direction:column;align-items:center;gap:22px}}
  h1{{font-size:26px;font-weight:800;margin:0;letter-spacing:-.02em}}
  .sub{{color:var(--dim);font-size:13px;margin-top:-14px}}
  .row{{display:flex;gap:14px;flex-wrap:wrap;justify-content:center}}
  a.card{{display:flex;flex-direction:column;gap:4px;background:var(--card);border:1px solid var(--line);
    border-radius:14px;padding:20px 26px;color:var(--text);text-decoration:none;min-width:200px;transition:border-color .15s}}
  a.card:hover{{border-color:var(--cyan)}}
  a.card .big{{font-size:17px;font-weight:700}} a.card.daily .big{{color:var(--green)}} a.card.shift .big{{color:var(--cyan)}}
  a.card .lbl{{color:var(--dim);font-size:12px}}
  .browse{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 24px;
    display:flex;flex-direction:column;gap:16px;width:100%;max-width:440px}}
  .browse h2{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);margin:0}}
  .pick{{display:flex;flex-direction:column;gap:6px}}
  .pick label{{font-size:13px;color:var(--dim)}}
  select{{background:#0e0f17;color:var(--text);border:1px solid var(--line);border-radius:9px;
    padding:11px 12px;font-size:14px;font-family:inherit;cursor:pointer;width:100%}}
  select:focus{{outline:2px solid var(--cyan);outline-offset:1px}}
  .foot{{color:#565d76;font-size:12px}}
</style>
<h1>📊 HOTTTR Reports</h1>
<div class="sub">Auto-updated from Infloww · UK time (BST)</div>

<div class="row">
  <a class="card daily" href="daily.html"><span class="big">💵 Latest Daily</span><span class="lbl">most recent full day</span></a>
  <a class="card shift" href="shift.html"><span class="big">🌙 Latest Shift</span><span class="lbl">most recent shift</span></a>
</div>

<div class="browse">
  <h2>📅 Browse previous dates</h2>
  <div class="pick">
    <label for="d">Daily report by date</label>
    <select id="d" onchange="if(this.value)location.href=this.value">
      <option value="">Pick a day…</option>
      {daily_sel}
    </select>
  </div>
  <div class="pick">
    <label for="s">Shift report by date</label>
    <select id="s" onchange="if(this.value)location.href=this.value">
      <option value="">Pick a shift…</option>
      {shift_sel}
    </select>
  </div>
</div>

<div class="foot">Shared with your team · anyone with the link can view</div>'''
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)
