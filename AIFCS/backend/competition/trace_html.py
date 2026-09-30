"""A recorded round, as a page you can look at.

`summarise` answers "one pass or six" in a line of prose. This answers the
question after it: what was happening on the way in, and what made it leave.
That one is shaped like a picture and no table of means will ever hold it.

Three things on the page, sharing one time axis:

* **the aim**, in degrees off the nose, on a log scale — because the round
  spans 180 degrees down to a tenth of one and the interesting part is the
  bottom decade. A linear axis to 180 puts every pass in the bottom 3% of the
  plot, which is how this was invisible for a week.
* **the range**, with the firing envelope drawn as a band.
* **a ribbon** marking the frames actually inside the cone, which is the
  scoring's own answer rather than anything reconstructed from the lines.

Self-contained HTML with no network calls, so it opens from a file:// path on a
laptop with no server running.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from competition.trace import passes, read_trace

#: Points drawn per chart. Eighteen thousand frames is more SVG than a browser
#: enjoys and more detail than a screen can show.
BUCKETS = 900


def _downsample(frames: list[dict[str, Any]], buckets: int = BUCKETS) -> list[dict[str, Any]]:
    """Bucketed, and the aggregation is chosen per field rather than sampled.

    Taking every Nth frame would have been one line and wrong: a pass through
    the cone lasts about 0.6 seconds, which is 36 frames, and at one point per
    20 frames a decimated series drops most of them and flattens the rest. The
    question this page exists to answer would be sampled away.

    So the angle is the **minimum** over the bucket — the closest it came, which
    is what the scoring cares about — and `in_envelope` is true if *any* frame
    in the bucket was, so a pass can be narrower than a bucket and still show.
    """
    if not frames:
        return []
    size = max(1, len(frames) // buckets)
    out: list[dict[str, Any]] = []
    for start in range(0, len(frames), size):
        window = frames[start : start + size]
        out.append(
            {
                "t": window[0]["t"],
                "angle": min(f["track_angle_deg"] for f in window),
                "distance": min(f["distance_m"] for f in window),
                "inside": any(f["in_envelope"] for f in window),
                "g": max(abs(f["g_load"]) for f in window),
            }
        )
    return out


def render(path: Path) -> str:
    """One trace file as a standalone HTML page."""
    header, frames, end = read_trace(path)
    envelope = header.get("envelope", {})
    needed = float(envelope.get("kill_seconds", 3.0))
    half_angle = float(envelope.get("half_angle_deg", 1.0))
    min_m = float(envelope.get("min_range_ft", 500.0)) * 0.3048
    max_m = float(envelope.get("max_range_ft", 3000.0)) * 0.3048

    runs = passes(frames)
    total = sum(run["seconds"] for run in runs)
    longest = max((run["seconds"] for run in runs), default=0.0)
    closest = min((f["track_angle_deg"] for f in frames), default=180.0)

    data = {
        "points": _downsample(frames),
        "runs": runs,
        "halfAngle": half_angle,
        "needed": needed,
        "minM": min_m,
        "maxM": max_m,
        "duration": frames[-1]["t"] if frames else 0.0,
    }

    title = f"{header.get('label', 'round')} vs {header.get('opponent', '?')}"
    if not runs:
        # "3.00 s short on its best pass" for a round with no passes at all is
        # true and reads as though there was nearly one.
        verdict = (
            f"Never inside the cone. The nose came within {closest:.1f}\u00b0, "
            f"and {half_angle:.0f}\u00b0 is the edge."
        )
    elif longest >= needed:
        verdict = f"One pass of {longest:.2f} s — already long enough on its own."
    else:
        verdict = (
            f"Best pass {longest:.2f} s of the {needed:.2f} s a kill needs: "
            f"{needed - longest:.2f} s short. "
            f"{len(runs)} passes came to {total:.2f} s in all, and they accumulate."
        )

    return _PAGE.format(
        title=html.escape(title),
        seed=header.get("seed", "?"),
        reason=html.escape(str(end.get("end_reason", "?"))),
        frames=len(frames),
        seconds=f"{len(frames) / 60.0:.0f}",
        passes=len(runs),
        total=f"{total:.2f}",
        needed=f"{needed:.2f}",
        longest=f"{longest:.2f}",
        closest=f"{closest:.2f}",
        verdict=html.escape(verdict),
        data=json.dumps(data, separators=(",", ":")),
    )


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    color-scheme: light;
    --surface-0: #f4f4f2;
    --surface-1: #fcfcfb;
    --border:    #dcdcd6;
    --grid:      #e8e8e2;
    --text-primary:   #0b0b0b;
    --text-secondary: #52514e;
    --text-muted:     #75746e;
    --series-1: #2a78d6;
    --good:     #0ca30c;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      color-scheme: dark;
      --surface-0: #111110;
      --surface-1: #1a1a19;
      --border:    #35352f;
      --grid:      #2a2a26;
      --text-primary:   #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted:     #8f8e85;
      --series-1: #3987e5;
      --good:     #0ca30c;
    }}
  }}
  :root[data-theme="dark"] {{
    color-scheme: dark;
    --surface-0: #111110;
    --surface-1: #1a1a19;
    --border:    #35352f;
    --grid:      #2a2a26;
    --text-primary:   #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted:     #8f8e85;
    --series-1: #3987e5;
    --good:     #0ca30c;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--surface-0); color: var(--text-primary);
    font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  .wrap {{ max-width: 1000px; margin: 0 auto; padding: 32px 16px 64px; }}
  h1 {{ font-size: 1.35rem; margin: 0 0 4px; letter-spacing: -0.01em; }}
  .sub {{ color: var(--text-secondary); margin: 0 0 24px; font-size: 0.92rem; }}
  .tiles {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }}
  .tile {{
    background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 10px; padding: 14px 16px;
  }}
  .tile .k {{ font-size: 0.78rem; color: var(--text-muted); text-transform: uppercase;
             letter-spacing: 0.04em; }}
  .tile .v {{ font-size: 1.7rem; font-weight: 600; letter-spacing: -0.02em;
             font-variant-numeric: tabular-nums; margin-top: 2px; }}
  .tile .n {{ font-size: 0.8rem; color: var(--text-secondary); }}
  .verdict {{
    margin: 20px 0 28px; padding: 12px 16px; border-radius: 10px;
    background: var(--surface-1); border: 1px solid var(--border);
    color: var(--text-secondary); font-size: 0.95rem;
  }}
  figure {{ margin: 0 0 26px; background: var(--surface-1);
            border: 1px solid var(--border); border-radius: 10px; padding: 16px; }}
  figcaption {{ font-size: 0.95rem; font-weight: 600; margin-bottom: 2px; }}
  .note {{ font-size: 0.83rem; color: var(--text-muted); margin-bottom: 10px; }}
  svg {{ display: block; width: 100%; height: auto; overflow: visible; }}
  .axis {{ fill: var(--text-muted); font-size: 11px; }}
  .gridline {{ stroke: var(--grid); stroke-width: 1; }}
  .refline {{ stroke: var(--text-muted); stroke-width: 1; stroke-dasharray: 4 3; opacity: 0.8; }}
  .reftext {{ fill: var(--text-secondary); font-size: 10.5px; }}
  .series {{ fill: none; stroke: var(--series-1); stroke-width: 2;
             stroke-linejoin: round; stroke-linecap: round; }}
  .band {{ fill: var(--series-1); opacity: 0.08; }}
  .inside {{ fill: var(--good); }}
  .ribbonbg {{ fill: var(--grid); }}
  .cross {{ stroke: var(--text-muted); stroke-width: 1; opacity: 0; }}
  .hit {{ fill: transparent; }}
  .tip {{
    position: fixed; pointer-events: none; opacity: 0; transition: opacity .08s;
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px;
    padding: 8px 10px; font-size: 12.5px; font-variant-numeric: tabular-nums;
    box-shadow: 0 6px 18px rgba(0,0,0,.14); z-index: 10; white-space: nowrap;
  }}
  .legend {{ display: flex; gap: 16px; flex-wrap: wrap; font-size: 0.83rem;
             color: var(--text-secondary); margin-top: 8px; }}
  .swatch {{ display: inline-block; width: 11px; height: 11px; border-radius: 3px;
             vertical-align: -1px; margin-right: 6px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.88rem;
           font-variant-numeric: tabular-nums; }}
  th, td {{ text-align: right; padding: 6px 10px; border-bottom: 1px solid var(--border); }}
  th:first-child, td:first-child {{ text-align: left; }}
  th {{ color: var(--text-muted); font-weight: 600; font-size: 0.78rem;
        text-transform: uppercase; letter-spacing: 0.04em; }}
  details {{ margin-top: 8px; }}
  summary {{ cursor: pointer; color: var(--text-secondary); font-size: 0.88rem; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>{title}</h1>
  <p class="sub">seed {seed} · {frames} frames ({seconds} s) · ended {reason}</p>

  <div class="tiles">
    <div class="tile"><div class="k">passes in the cone</div><div class="v">{passes}</div>
      <div class="n">separate times inside</div></div>
    <div class="tile"><div class="k">total in the cone</div><div class="v">{total} s</div>
      <div class="n">{needed} s needed for a kill</div></div>
    <div class="tile"><div class="k">longest single pass</div><div class="v">{longest} s</div>
      <div class="n">held without leaving</div></div>
    <div class="tile"><div class="k">closest the nose came</div><div class="v">{closest}&deg;</div>
      <div class="n">1.00&deg; is inside</div></div>
  </div>

  <p class="verdict">{verdict}</p>

  <figure>
    <figcaption>Aim &mdash; degrees off the nose</figcaption>
    <p class="note">Log scale, fixed 0.2&ndash;180&deg;, so two rounds are comparable at a
      glance and the cone is on the chart even when nothing goes near it. Each drawn point is
      the <em>closest</em> the nose came during its slice, so a pass narrower than a pixel
      still shows.</p>
    <div id="aim"></div>
  </figure>

  <figure>
    <figcaption>Range</figcaption>
    <p class="note">Log scale too: the envelope is 152&ndash;914 m and a round can reach
      200 km, so on a linear axis the band that decides everything is a hairline. Being in it
      is necessary and nowhere near sufficient.</p>
    <div id="range"></div>
  </figure>

  <figure>
    <figcaption>Inside the cone</figcaption>
    <p class="note">The scoring&rsquo;s own verdict per frame, not reconstructed from the lines
      above.</p>
    <div id="ribbon"></div>
    <div class="legend">
      <span><span class="swatch" style="background:var(--good)"></span>inside the firing envelope</span>
      <span><span class="swatch" style="background:var(--grid)"></span>outside</span>
    </div>
    <details>
      <summary>Every pass, as a table</summary>
      <table id="passes"><thead><tr><th>#</th><th>from</th><th>to</th><th>held</th>
        <th>closest</th><th>range</th></tr></thead><tbody></tbody></table>
    </details>
  </figure>
</div>
<div class="tip" id="tip"></div>
<script>
const DATA = {data};
const tip = document.getElementById("tip");
const W = 920, H = 260, M = {{t: 14, r: 16, b: 28, l: 52}};

function svg(tag, attrs) {{
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {{}})) node.setAttribute(k, v);
  return node;
}}
const x = t => M.l + (t / (DATA.duration || 1)) * (W - M.l - M.r);

function lineChart(host, key, {{lo, hi, ticks, refs, band, fmt}}) {{
  // Fixed domains, not fitted to the data. Two reasons, both learned by
  // looking at the first render: a round that never gets closer than 91
  // degrees fitted its axis to 91-180 and dropped the one-degree line off the
  // bottom, so the chart hid the only thing it is for. And a fitted axis makes
  // two rounds incomparable at a glance, which is exactly what we want to do
  // with them.
  const pts = DATA.points;
  const span = (Math.log10(hi) - Math.log10(lo)) || 1;
  const y = v => {{
    const clamped = Math.min(Math.max(v, lo), hi);
    const c = Math.log10(clamped) - Math.log10(lo);
    return H - M.b - (c / span) * (H - M.t - M.b);
  }};

  const root = svg("svg", {{viewBox: `0 0 ${{W}} ${{H}}`, role: "img"}});

  if (band) {{
    const top = y(band[1]), bottom = y(band[0]);
    root.appendChild(svg("rect", {{class: "band", x: M.l, y: Math.min(top, bottom),
      width: W - M.l - M.r, height: Math.abs(bottom - top)}}));
  }}

  for (const v of ticks) {{
    if (v < lo || v > hi) continue;
    root.appendChild(svg("line", {{class: "gridline", x1: M.l, x2: W - M.r, y1: y(v), y2: y(v)}}));
    const label = svg("text", {{class: "axis", x: M.l - 8, y: y(v) + 4, "text-anchor": "end"}});
    label.textContent = fmt(v);
    root.appendChild(label);
  }}
  for (let s = 0; s <= DATA.duration; s += 60) {{
    const label = svg("text", {{class: "axis", x: x(s), y: H - 8, "text-anchor": "middle"}});
    label.textContent = s + "s";
    root.appendChild(label);
  }}

  for (const ref of refs || []) {{
    // No skipping on the data's range: a reference line the data never
    // reaches is the most informative one on the chart.
    if (ref.v < lo || ref.v > hi) continue;
    root.appendChild(svg("line", {{class: "refline", x1: M.l, x2: W - M.r,
      y1: y(ref.v), y2: y(ref.v)}}));
    const label = svg("text", {{class: "reftext", x: W - M.r, y: y(ref.v) - 5,
      "text-anchor": "end"}});
    label.textContent = ref.label;
    root.appendChild(label);
  }}

  const d = pts.map((p, i) => `${{i ? "L" : "M"}}${{x(p.t).toFixed(1)}},${{y(p[key]).toFixed(1)}}`).join("");
  root.appendChild(svg("path", {{class: "series", d}}));

  const cross = svg("line", {{class: "cross", y1: M.t, y2: H - M.b}});
  root.appendChild(cross);
  const hit = svg("rect", {{class: "hit", x: M.l, y: M.t,
    width: W - M.l - M.r, height: H - M.t - M.b}});
  root.appendChild(hit);

  hit.addEventListener("pointermove", event => {{
    const box = root.getBoundingClientRect();
    const px = (event.clientX - box.left) / box.width * W;
    const t = (px - M.l) / (W - M.l - M.r) * DATA.duration;
    let best = pts[0];
    for (const p of pts) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
    cross.setAttribute("x1", x(best.t)); cross.setAttribute("x2", x(best.t));
    cross.setAttribute("opacity", 0.55);
    tip.innerHTML = `<strong>${{best.t.toFixed(1)}} s</strong><br>` +
      `aim ${{best.angle.toFixed(2)}}&deg;<br>range ${{best.distance.toFixed(0)}} m<br>` +
      `${{best.inside ? "inside the cone" : "outside"}}`;
    tip.style.left = Math.min(event.clientX + 14, innerWidth - 150) + "px";
    tip.style.top = (event.clientY - 10) + "px";
    tip.style.opacity = 1;
  }});
  hit.addEventListener("pointerleave", () => {{
    tip.style.opacity = 0; cross.setAttribute("opacity", 0);
  }});
  host.appendChild(root);
}}

lineChart(document.getElementById("aim"), "angle", {{
  // Every round on the same axis, and the cone always on it.
  lo: 0.2, hi: 180,
  ticks: [0.2, 1, 10, 100],
  refs: [{{v: DATA.halfAngle, label: `${{DATA.halfAngle}}\\u00b0 \\u2014 inside the cone`}},
         {{v: 5, label: "5\\u00b0"}}],
  fmt: v => (v >= 1 ? String(v) : v.toFixed(1)) + "\\u00b0",
}});
lineChart(document.getElementById("range"), "distance", {{
  // Log here too: the envelope is 152-914 m and a round reaches 200 km, so on
  // a linear axis the band that decides everything is a hairline at the
  // bottom. It was, in the first render.
  lo: 50, hi: 200000,
  ticks: [100, 1000, 10000, 100000],
  band: [DATA.minM, DATA.maxM],
  refs: [{{v: DATA.maxM, label: "firing envelope"}}],
  fmt: v => v >= 1000 ? (v / 1000) + " km" : v + " m",
}});

(function ribbon() {{
  const h = 34;
  const root = svg("svg", {{viewBox: `0 0 ${{W}} ${{h + 24}}`, role: "img"}});
  root.appendChild(svg("rect", {{class: "ribbonbg", x: M.l, y: 0,
    width: W - M.l - M.r, height: h, rx: 4}}));
  for (const run of DATA.runs) {{
    const x0 = x(run.start_s), x1 = x(run.end_s);
    root.appendChild(svg("rect", {{class: "inside", x: x0, y: 0,
      // A 0.6-second pass in a 300-second round is under a pixel wide; floored
      // at 2px so the thing the page is about cannot vanish into the axis.
      width: Math.max(2, x1 - x0), height: h, rx: 2}}));
  }}
  for (let s = 0; s <= DATA.duration; s += 60) {{
    const label = svg("text", {{class: "axis", x: x(s), y: h + 16, "text-anchor": "middle"}});
    label.textContent = s + "s";
    root.appendChild(label);
  }}
  document.getElementById("ribbon").appendChild(root);
}})();

(function table() {{
  const body = document.querySelector("#passes tbody");
  if (!DATA.runs.length) {{
    const row = body.insertRow();
    const cell = row.insertCell();
    cell.colSpan = 6; cell.style.textAlign = "left";
    cell.textContent = "Never inside the cone.";
    return;
  }}
  DATA.runs.forEach((run, i) => {{
    const row = body.insertRow();
    [i + 1, run.start_s.toFixed(1) + " s", run.end_s.toFixed(1) + " s",
     run.seconds.toFixed(2) + " s", run.closest_deg.toFixed(2) + "\\u00b0",
     Math.round(run.mean_distance_m) + " m"].forEach(v => {{
      row.insertCell().textContent = v;
    }});
  }});
}})();
</script>
</body>
</html>
"""
