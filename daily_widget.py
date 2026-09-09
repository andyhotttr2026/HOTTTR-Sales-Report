# -*- coding: utf-8 -*-
"""Dark dashboard for the daily report.

Writes docs/daily.html (what the Slack message links to) plus a dated copy in
docs/archive/. Mirrors the shift dashboard so the two feel like one system.
"""
import os
from datetime import datetime


def _m(c):
    return f"${c/100:,.2f}"


def write_widget(day_label, tz_label, day_iso, rows, tot):
    """rows: [(name, {net, gross, new, ren, free}), ...] sorted by net desc."""
    paying = [(n, s) for n, s in rows if not s["free"]]
    maxnet = max((s["net"] for _, s in rows), default=1) or 1

    bars = "".join(
        f'<div class="brow"><div class="bname">{n}</div>'
        f'<div class="btrack"><div class="bfill{" top" if i == 0 else ""}" '
        f'style="width:{max(s["net"]/maxnet*100, 0.6):.1f}%"></div></div>'
        f'<div class="bval">{_m(s["net"])}</div></div>'
        for i, (n, s) in enumerate(rows))

    trows = "".join(
        f'<tr><td class="cn">{n}{" <span class=free>free</span>" if s["free"] else ""}</td>'
        f'<td class="num">{s["new"] + s["ren"]}</td>'
        f'<td class="num dim">{s["new"]} / {s["ren"]}</td>'
        f'<td class="num">{_m(s["gross"])}</td>'
        f'<td class="num net">{_m(s["net"])}</td>'
        f'<td class="num dim">{s["net"]/tot["net"]*100 if tot["net"] else 0:.1f}%</td></tr>'
        for n, s in rows)

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>HOTTTR Daily — {day_label}</title>
<style>
:root{{--bg:#12141f;--panel:#1a1d2b;--card:#1e2130;--line:#2a2e40;
 --text:#e8eaf0;--dim:#8890a6;--dimmer:#5a6178;
 --green:#3ddc84;--cyan:#4db8ff;--gold:#f5b544;--purple:#a98bff;--red:#ff5c6c;
 --sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,sans-serif;}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:var(--sans);
 font-size:15px;line-height:1.55;font-variant-numeric:tabular-nums;
 padding:22px 18px 56px;-webkit-text-size-adjust:100%}}
.wrap{{max-width:1080px;margin:0 auto}}
.head{{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;
 flex-wrap:wrap;padding-bottom:14px;border-bottom:1px solid var(--line);margin-bottom:20px}}
h1{{font-size:22px;font-weight:600;letter-spacing:-.3px}}
.sub{{color:var(--dim);font-size:13px;margin-top:3px}}
.stamp{{color:var(--dimmer);font-size:12px;font-variant-numeric:tabular-nums}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:11px;margin-bottom:22px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:13px 15px}}
.kpi .l{{font-size:11px;letter-spacing:.9px;text-transform:uppercase;color:var(--dimmer)}}
.kpi .v{{font-size:24px;font-weight:600;margin-top:3px;line-height:1.15}}
.kpi .s{{font-size:12px;color:var(--dim);margin-top:2px}}
.kpi.net .v{{color:var(--green)}} .kpi.subs .v{{color:var(--cyan)}}
.kpi.fee .v{{color:var(--red)}} .kpi.ren .v{{color:var(--purple)}}
h2{{font-size:14px;font-weight:600;margin:0 0 12px;padding-bottom:7px;
 border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:baseline}}
h2 span{{font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--dimmer);font-weight:400}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;
 padding:15px 17px;margin-bottom:16px}}
.brow{{display:grid;grid-template-columns:130px 1fr 96px;gap:11px;align-items:center;
 padding:5px 0;font-size:13.5px}}
.btrack{{background:#171a26;border-radius:5px;height:19px;overflow:hidden}}
.bfill{{height:100%;background:linear-gradient(90deg,#2f7d5a,var(--green));border-radius:5px}}
.bfill.top{{background:linear-gradient(90deg,#8a6a1e,var(--gold))}}
.bname{{color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.bval{{text-align:right;color:var(--green);font-weight:500}}
.tw{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;min-width:520px}}
th{{text-align:right;font-size:10.5px;letter-spacing:.9px;text-transform:uppercase;
 color:var(--dimmer);font-weight:600;padding:0 9px 8px;border-bottom:1px solid var(--line)}}
th:first-child{{text-align:left}}
td{{padding:8px 9px;border-bottom:1px solid #1d2130}}
td.cn{{font-weight:500}} .num{{text-align:right}} .dim{{color:var(--dim)}}
.net{{color:var(--green);font-weight:500}}
.free{{font-size:10px;color:var(--dimmer);border:1px solid var(--line);
 border-radius:4px;padding:1px 5px;margin-left:5px}}
tr.tot td{{border-top:1px solid var(--line);border-bottom:none;font-weight:600;padding-top:11px}}
.foot{{margin-top:22px;padding-top:13px;border-top:1px solid var(--line);
 font-size:12px;color:var(--dimmer)}}
@media(max-width:560px){{.brow{{grid-template-columns:96px 1fr 82px;font-size:12.5px}}}}
</style></head><body><div class="wrap">

<div class="head">
  <div><h1>HOTTTR Daily Report</h1>
    <div class="sub">{day_label} &middot; London time ({tz_label}) &middot; yesterday's confirmed numbers</div></div>
  <div class="stamp">generated {datetime.utcnow():%d %b %Y %H:%M} UTC</div>
</div>

<div class="kpis">
  <div class="kpi net"><div class="l">Net revenue</div><div class="v">{_m(tot['net'])}</div>
    <div class="s">after the 20% OnlyFans fee</div></div>
  <div class="kpi"><div class="l">Gross</div><div class="v">{_m(tot['gross'])}</div>
    <div class="s">before fees</div></div>
  <div class="kpi subs"><div class="l">Paid subs</div><div class="v">{tot['new'] + tot['ren']}</div>
    <div class="s">{tot['new']} new &middot; {tot['ren']} renewals</div></div>
  <div class="kpi ren"><div class="l">Renewal rate</div>
    <div class="v">{tot['ren']/(tot['new']+tot['ren'])*100 if (tot['new']+tot['ren']) else 0:.1f}%</div>
    <div class="s">of all paid subs</div></div>
  <div class="kpi fee"><div class="l">OF fee</div><div class="v">{_m(tot['gross'] - tot['net'])}</div>
    <div class="s">20% of gross</div></div>
</div>

<div class="panel"><h2>Net sales by creator<span>{len(paying)} earning</span></h2>{bars}</div>

<div class="panel"><h2>Creator breakdown<span>net, gross and share</span></h2><div class="tw">
<table><thead><tr><th>Creator</th><th>Subs</th><th>New / Ren</th><th>Gross</th>
<th>Net</th><th>Share</th></tr></thead><tbody>{trows}
<tr class="tot"><td class="cn">TOTAL</td><td class="num">{tot['new'] + tot['ren']}</td>
<td class="num dim">{tot['new']} / {tot['ren']}</td><td class="num">{_m(tot['gross'])}</td>
<td class="num net">{_m(tot['net'])}</td><td class="num dim">100%</td></tr>
</tbody></table></div></div>

<div class="foot">Source: Infloww API &middot; net of the OnlyFans 20% &middot; refunds excluded
&middot; Europe/London calendar day. Free pages carry traffic but no paid subs.</div>
</div></body></html>"""

    os.makedirs("docs/archive", exist_ok=True)
    with open("docs/daily.html", "w", encoding="utf-8") as f:
        f.write(html)
    with open(f"docs/archive/daily-{day_iso}.html", "w", encoding="utf-8") as f:
        f.write(html)
    return "docs/daily.html"
