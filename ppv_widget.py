# -*- coding: utf-8 -*-
"""Dark visual for the PPV tracker.

Writes docs/ppv.html plus a dated archive copy, and renders docs/ppv-<date>.png
if Chrome is available. The PNG is what Slack shows inline.
"""
import os, subprocess, shutil
from datetime import datetime

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "google-chrome", "google-chrome-stable", "chromium-browser", "chromium",
]


def _chrome():
    for c in CHROME_CANDIDATES:
        if os.path.sep in c or ":" in c:
            if os.path.exists(c): return c
        else:
            p = shutil.which(c)
            if p: return p
    return None


def _bar(done, target, pace):
    """Weekly progress bar with a pace marker."""
    pct  = min(done / target * 100, 100) if target else 0
    ppct = min(pace / target * 100, 100) if target else 0
    cls  = "ok" if done >= target else ("on" if done >= pace else "off")
    return (f'<div class="bar"><div class="fill {cls}" style="width:{pct:.1f}%"></div>'
            f'<div class="pace" style="left:{ppct:.1f}%"></div></div>')


def write_widget(day, day_n, wk_mon, by_team, tot, out_dir="docs"):
    def rate(u, s): return f"{u/s*100:.0f}%" if s else "—"

    cards = ""
    for team, g, t in by_team:
        rows = ""
        for r in g:
            q, w = r["quota"] or 0, r["wsent"]
            pace = r["pace"] or 0
            flag = ""
            if r["sent"] == 0 and r["dm"] < 50:
                flag = '<span class="flag off">no shift</span>'
            elif r["sent"] == 0:
                flag = '<span class="flag idle">no offers</span>'
            rows += (
                f'<div class="row">'
                f'<div class="nm">{r["name"][:12]}{flag}</div>'
                f'<div class="td">{r["sent"]}</div>'
                f'<div class="ul">{r["unl"]}<i>{rate(r["unl"], r["sent"])}</i></div>'
                f'<div class="wk">{_bar(w, q, pace)}'
                f'<span class="lab">{w}<i>/{q or "—"}</i></span></div></div>')
        tpct = t["wsent"] / t["quota"] * 100 if t["quota"] else 0
        tcls = ("ok" if t["quota"] and t["wsent"] >= t["quota"]
                else "on" if t["quota"] and t["wsent"] >= t["pace"] else "off")
        cards += (
            f'<div class="card"><div class="ch"><h2>{team}</h2>'
            f'<span class="pill {tcls}">{t["wsent"]} / {t["quota"] or "—"}'
            f'<i>{tpct:.0f}%</i></span></div>'
            f'<div class="rh"><span>chatter</span><span>sent</span>'
            f'<span>unlocked</span><span>week vs quota</span></div>'
            f'{rows}'
            f'<div class="row sub"><div class="nm">team</div><div class="td">{t["sent"]}</div>'
            f'<div class="ul">{t["unl"]}<i>{rate(t["unl"], t["sent"])}</i></div>'
            f'<div class="wk">{_bar(t["wsent"], t["quota"], t["pace"])}'
            f'<span class="lab">{t["left"]}<i> to go</i></span></div></div></div>')

    pace_pct = tot["pace"] / tot["quota"] * 100 if tot["quota"] else 0
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>HOTTTR PPV — {day}</title>
<style>
:root{{--bg:#12141f;--panel:#1a1d2b;--card:#1e2130;--line:#2a2e40;--track:#171a26;
 --text:#e8eaf0;--dim:#8890a6;--dimmer:#5a6178;
 --green:#3ddc84;--cyan:#4db8ff;--gold:#f5b544;--red:#ff5c6c;--pink:#e8629a;
 --sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,sans-serif}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:15px;
 font-variant-numeric:tabular-nums;padding:22px 24px 20px;width:1080px}}
.top{{display:flex;justify-content:space-between;align-items:flex-end;
 border-bottom:1px solid var(--line);padding-bottom:13px;margin-bottom:16px}}
h1{{font-size:21px;font-weight:600;letter-spacing:-.3px}}
.sub{{color:var(--dim);font-size:12.5px;margin-top:3px}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:11px;margin-bottom:16px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:11px 14px}}
.kpi .l{{font-size:10.5px;letter-spacing:.9px;text-transform:uppercase;color:var(--dimmer)}}
.kpi .v{{font-size:25px;font-weight:600;margin-top:2px;line-height:1.1}}
.kpi .s{{font-size:11.5px;color:var(--dim);margin-top:2px}}
.kpi.a .v{{color:var(--cyan)}} .kpi.b .v{{color:var(--green)}} .kpi.c .v{{color:var(--gold)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:12px 14px}}
.ch{{display:flex;justify-content:space-between;align-items:center;margin-bottom:9px}}
h2{{font-size:14.5px;font-weight:600}}
.pill{{font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px}}
.pill i{{font-style:normal;font-weight:400;opacity:.75;margin-left:6px}}
.pill.ok{{background:#12351f;color:var(--green)}}
.pill.on{{background:#123049;color:var(--cyan)}}
.pill.off{{background:#3d1a1f;color:var(--red)}}
.rh,.row{{display:grid;grid-template-columns:150px 46px 76px 1fr;gap:9px;align-items:center}}
.rh{{font-size:9.5px;letter-spacing:.8px;text-transform:uppercase;color:var(--dimmer);
 padding-bottom:6px;border-bottom:1px solid var(--line);margin-bottom:3px}}
.rh span:nth-child(2),.rh span:nth-child(3){{text-align:right}}
.row{{padding:6px 0;border-bottom:1px solid #1d2130;font-size:13.5px}}
.row.sub{{border-bottom:none;border-top:1px solid var(--line);margin-top:3px;
 padding-top:8px;font-weight:600;color:var(--dim)}}
.nm{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.td{{text-align:right;font-weight:600}}
.ul{{text-align:right;color:var(--dim)}}
.ul i{{font-style:normal;font-size:11px;color:var(--dimmer);margin-left:5px}}
.wk{{display:flex;align-items:center;gap:9px}}
.bar{{position:relative;flex:1;height:15px;background:var(--track);border-radius:4px;overflow:hidden}}
.fill{{height:100%;border-radius:4px}}
.fill.ok{{background:linear-gradient(90deg,#2f7d5a,var(--green))}}
.fill.on{{background:linear-gradient(90deg,#1d5f8a,var(--cyan))}}
.fill.off{{background:linear-gradient(90deg,#7d2b34,var(--red))}}
.pace{{position:absolute;top:0;bottom:0;width:2px;background:var(--text);opacity:.55}}
.lab{{font-size:12px;color:var(--dim);min-width:58px;text-align:right}}
.lab i{{font-style:normal;color:var(--dimmer)}}
.flag{{font-size:9.5px;padding:1px 6px;border-radius:4px;margin-left:7px;vertical-align:1px}}
.flag.off{{background:#3d1a1f;color:#ff9aa4}}
.flag.idle{{background:#453213;color:var(--gold)}}
.foot{{margin-top:14px;padding-top:11px;border-top:1px solid var(--line);
 font-size:11.5px;color:var(--dimmer);display:flex;justify-content:space-between}}
</style></head><body>

<div class="top">
  <div><h1>PPV Tracker</h1>
    <div class="sub">{day:%A %d %B %Y} &middot; day {day_n} of 7 &middot; week of {wk_mon:%d %b}
      &middot; quota is PPVs <b>sent</b></div></div>
  <div class="sub">generated {datetime.utcnow():%d %b %H:%M} UTC</div>
</div>

<div class="kpis">
  <div class="kpi a"><div class="l">Sent today</div><div class="v">{tot['sent']}</div>
    <div class="s">across {len(by_team)} teams</div></div>
  <div class="kpi b"><div class="l">Unlocked</div><div class="v">{tot['unl']}</div>
    <div class="s">{rate(tot['unl'], tot['sent'])} unlock rate</div></div>
  <div class="kpi c"><div class="l">Week to date</div><div class="v">{tot['wsent']}</div>
    <div class="s">of {tot['quota']} &middot; pace {tot['pace']:.0f} ({pace_pct:.0f}%)</div></div>
  <div class="kpi"><div class="l">Left to quota</div>
    <div class="v">{max(tot['quota'] - tot['wsent'], 0)}</div>
    <div class="s">{7 - day_n} day{'' if 7 - day_n == 1 else 's'} remaining</div></div>
</div>

<div class="grid">{cards}</div>

<div class="foot">
  <span>White marker on each bar is where the chatter should be today &middot;
    chatter-attributed, gross</span>
  <span>Unlock counts settle over ~3 days &middot; sends settle faster</span>
</div>
</body></html>"""

    os.makedirs(f"{out_dir}/archive", exist_ok=True)
    with open(f"{out_dir}/ppv.html", "w", encoding="utf-8") as f: f.write(html)
    with open(f"{out_dir}/archive/ppv-{day}.html", "w", encoding="utf-8") as f: f.write(html)

    png = f"{out_dir}/ppv-{day}.png"
    ch = _chrome()
    if not ch:
        print("no Chrome found — HTML written, PNG skipped")
        return None
    # Size the canvas to the content so Slack does not show a wall of empty space.
    card_rows = -(-len(by_team) // 2)                    # ceil, two cards per row
    tallest   = max((len(g) for _, g, _ in by_team), default=0) + 1   # +1 team line
    height    = 200 + card_rows * (112 + tallest * 31)
    subprocess.run([ch, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                    f"--window-size=1080,{height}", f"--screenshot={os.path.abspath(png)}",
                    "file:///" + os.path.abspath(f"{out_dir}/ppv.html").replace("\\", "/")],
                   capture_output=True)
    return png if os.path.exists(png) else None
