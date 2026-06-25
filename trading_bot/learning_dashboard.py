"""Learning dashboard — visualise how the bot's WeightTuner adapts over time.

Reads data/learning_history.jsonl (one snapshot per tuning step, written by
core.weight_tuner.WeightTuner) and renders a self-contained HTML page so you
can SEE the bot learning: agent weights drifting toward the agents that have
been right, win rate, entry thresholds self-adjusting, and the current
per-agent skill multipliers.

Usage:
    python learning_dashboard.py            # build dashboard/learning.html and open it
    python learning_dashboard.py --no-open  # just write the file
"""
from __future__ import annotations

import argparse
import json
import webbrowser
from pathlib import Path

_HISTORY_FILE = Path(__file__).parent / "data" / "learning_history.jsonl"
_OUT_FILE = Path(__file__).parent.parent / "dashboard" / "learning.html"

# Stable colour per agent so a line keeps its colour across charts.
_AGENT_COLORS = {
    "fundamental": "#58a6ff",
    "vision":      "#a371f7",
    "technical":   "#3fb950",
    "liquid":      "#d29922",
    "insider":     "#f85149",
    "squeeze":     "#ff7b72",
    "macro":       "#56d4dd",
}


def load_history(path: Path = _HISTORY_FILE) -> list[dict]:
    """Read the JSONL learning log into a list of snapshots (oldest first)."""
    if not path.exists():
        return []
    snapshots: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            snapshots.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return snapshots


def build_learning_dashboard(history: list[dict]) -> str:
    """Render the learning history into a self-contained HTML string."""
    agents = sorted({a for s in history for a in s.get("weights", {})})
    if not agents:
        agents = list(_AGENT_COLORS)

    labels = [s.get("ts", "")[:19].replace("T", " ") for s in history]
    weight_series = {
        a: [round(s.get("weights", {}).get(a, 0.0) * 100, 2) for s in history]
        for a in agents
    }
    win_rate = [s.get("win_rate") for s in history]
    long_wr = [s.get("long_win_rate") for s in history]
    short_wr = [s.get("short_win_rate") for s in history]
    long_thr = [s.get("long_threshold") for s in history]
    short_thr = [s.get("short_threshold") for s in history]

    latest = history[-1] if history else {}
    latest_mults = latest.get("multipliers", {})
    mult_agents = sorted(latest_mults)
    mult_values = [latest_mults.get(a, 1.0) for a in mult_agents]

    weight_datasets = json.dumps([
        {
            "label": a,
            "data": weight_series[a],
            "borderColor": _AGENT_COLORS.get(a, "#8b949e"),
            "backgroundColor": "transparent",
            "tension": 0.3,
            "pointRadius": 2,
        }
        for a in agents
    ])
    mult_colors = json.dumps([_AGENT_COLORS.get(a, "#8b949e") for a in mult_agents])

    summary = ""
    if latest:
        wr = latest.get("win_rate")
        wr_str = f"{wr:.1f}%" if wr is not None else "n/a"
        wr_cls = "green" if (wr or 0) >= 50 else "red"
        summary = (
            f'<div class="card"><div class="stat-value {wr_cls}">{wr_str}</div>'
            f'<div class="stat-label">Win Rate ({latest.get("sample_size", 0)} trades)</div></div>'
            f'<div class="card"><div class="stat-value blue">{latest.get("bias", "neutral").upper()}</div>'
            f'<div class="stat-label">Learned Bias</div></div>'
            f'<div class="card"><div class="stat-value">{latest.get("long_threshold", "-")}'
            f' / {latest.get("short_threshold", "-")}</div>'
            f'<div class="stat-label">LONG / SHORT Threshold</div></div>'
            f'<div class="card"><div class="stat-value muted">{len(history)}</div>'
            f'<div class="stat-label">Tuning Steps Logged</div></div>'
        )

    empty_note = (
        ""
        if history
        else '<div class="card" style="margin-bottom:24px"><p style="color:var(--muted)">'
        "No learning history yet. The WeightTuner starts adapting after 10 resolved "
        "trades; once it does, each closed position appends a snapshot here. Re-run "
        "this command to refresh.</p></div>"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bot Learning Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {{ --bg:#0d1117; --surface:#161b22; --border:#30363d; --text:#e6edf3;
           --muted:#8b949e; --green:#3fb950; --red:#f85149; --blue:#58a6ff; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--text);
          font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:14px; }}
  .header {{ background:var(--surface); border-bottom:1px solid var(--border);
             padding:16px 24px; display:flex; align-items:center; gap:12px; }}
  .header h1 {{ font-size:18px; font-weight:600; }}
  .badge {{ background:#a371f726; color:#a371f7; padding:2px 8px; border-radius:12px; font-size:12px; }}
  .container {{ max-width:1400px; margin:0 auto; padding:24px; }}
  .grid-4 {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:24px; }}
  .grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:24px; }}
  .card {{ background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:16px; }}
  .card h2 {{ font-size:13px; font-weight:600; color:var(--muted); text-transform:uppercase;
              letter-spacing:.05em; margin-bottom:12px; }}
  .stat-value {{ font-size:28px; font-weight:700; }}
  .stat-label {{ font-size:12px; color:var(--muted); margin-top:4px; }}
  .green {{ color:var(--green); }} .red {{ color:var(--red); }}
  .blue {{ color:var(--blue); }} .muted {{ color:var(--muted); }}
  canvas {{ max-height:320px; }}
  @media (max-width:768px) {{ .grid-2,.grid-4 {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="header">
  <h1>🧠 Bot Learning Dashboard</h1>
  <span class="badge">WeightTuner — online learning</span>
</div>
<div class="container">
  {empty_note}
  <div class="grid-4">{summary}</div>

  <div class="card" style="margin-bottom:24px">
    <h2>Agent Weights Over Time (%) — the bot shifting trust toward agents that are right</h2>
    <canvas id="weights-chart"></canvas>
  </div>

  <div class="grid-2">
    <div class="card">
      <h2>Win Rate Over Time</h2>
      <canvas id="winrate-chart"></canvas>
    </div>
    <div class="card">
      <h2>Entry Thresholds (self-adjusting)</h2>
      <canvas id="threshold-chart"></canvas>
    </div>
  </div>

  <div class="card">
    <h2>Current Agent Skill Multipliers (2× = always right, 1× = random, 0.1× = always wrong)</h2>
    <canvas id="mult-chart"></canvas>
  </div>
</div>

<script>
const LABELS = {json.dumps(labels)};
const axis = (c) => ({{ ticks:{{ color:'#8b949e', maxTicksLimit:10 }}, grid:{{ color:'#30363d' }} }});
const baseOpts = {{ scales:{{ x:axis(), y:axis() }} }};

new Chart(document.getElementById('weights-chart'), {{
  type:'line',
  data:{{ labels:LABELS, datasets:{weight_datasets} }},
  options:{{ ...baseOpts, plugins:{{ legend:{{ labels:{{ color:'#e6edf3' }} }} }} }}
}});

new Chart(document.getElementById('winrate-chart'), {{
  type:'line',
  data:{{ labels:LABELS, datasets:[
    {{ label:'Overall', data:{json.dumps(win_rate)}, borderColor:'#58a6ff', backgroundColor:'transparent', tension:0.3 }},
    {{ label:'Long', data:{json.dumps(long_wr)}, borderColor:'#3fb950', backgroundColor:'transparent', tension:0.3 }},
    {{ label:'Short', data:{json.dumps(short_wr)}, borderColor:'#f85149', backgroundColor:'transparent', tension:0.3 }}
  ] }},
  options:{{ ...baseOpts, plugins:{{ legend:{{ labels:{{ color:'#e6edf3' }} }} }} }}
}});

new Chart(document.getElementById('threshold-chart'), {{
  type:'line',
  data:{{ labels:LABELS, datasets:[
    {{ label:'LONG above', data:{json.dumps(long_thr)}, borderColor:'#3fb950', backgroundColor:'transparent', tension:0.3 }},
    {{ label:'SHORT below', data:{json.dumps(short_thr)}, borderColor:'#f85149', backgroundColor:'transparent', tension:0.3 }}
  ] }},
  options:{{ ...baseOpts, plugins:{{ legend:{{ labels:{{ color:'#e6edf3' }} }} }} }}
}});

new Chart(document.getElementById('mult-chart'), {{
  type:'bar',
  data:{{ labels:{json.dumps(mult_agents)}, datasets:[
    {{ label:'Skill multiplier', data:{json.dumps(mult_values)}, backgroundColor:{mult_colors} }}
  ] }},
  options:{{ plugins:{{ legend:{{ display:false }} }},
            scales:{{ x:axis(), y:{{ ...axis(), suggestedMin:0, suggestedMax:2 }} }} }}
}});
</script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the bot learning dashboard")
    parser.add_argument("--no-open", action="store_true", help="write the file but don't open a browser")
    args = parser.parse_args()

    history = load_history()
    html = build_learning_dashboard(history)
    _OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OUT_FILE.write_text(html, encoding="utf-8")
    print(f"Learning dashboard written to {_OUT_FILE} ({len(history)} snapshots)")
    if not args.no_open:
        webbrowser.open(_OUT_FILE.as_uri())


if __name__ == "__main__":
    main()
