"""Generate an interactive HTML network visualization of dimension similarity.

Nodes = dimensions; edges = Jaccard similarity >= threshold.
A slider controls the threshold live; hovering a node shows its name and variables.

Reads:
    scripts/dim_similarity.csv
    scripts/dimension_sets.json
    morpc_census/acs_dim_names.json

Writes:
    scripts/dim_network.html

Usage:
    python scripts/dim_network.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
SIM = HERE / "dim_similarity.csv"
DIM_SETS = HERE / "dimension_sets.json"
DIM_NAMES = HERE.parent / "morpc_census" / "acs_dim_names.json"
OUT = HERE / "dim_network.html"

EXCLUDE = {"dim_000", "dim_002"}


def _pad(key: str) -> str:
    prefix, num = key.rsplit("_", 1)
    return f"{prefix}_{int(num):03d}"


def main() -> None:
    df = pd.read_csv(SIM, index_col=0)
    dims = json.loads(DIM_SETS.read_text())
    name_map = json.loads(DIM_NAMES.read_text())

    node_meta: dict[str, dict] = {}
    for k, v in dims.items():
        padded = _pad(k)
        if padded in EXCLUDE:
            continue
        name = name_map.get(padded, padded)
        node_meta[name] = {
            "variables": v["variables"],
            "n_groups": len(v["groups"][0]),
        }

    nodes = []
    for name, meta in node_meta.items():
        vars_list = "".join(f"<li>{v}</li>" for v in meta["variables"])
        title = (
            f"<b>{name}</b>"
            f"<div style='color:#888;margin:2px 0'>Groups: {meta['n_groups']}</div>"
            f"<ul style='margin:4px 0;padding-left:16px'>{vars_list}</ul>"
        )
        nodes.append({
            "id": name,
            "label": name,
            "title": title,
            "value": meta["n_groups"],
        })

    edges = []
    names = list(df.index)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            sim = float(df.loc[a, b])
            if sim > 0:
                edges.append({
                    "from": a,
                    "to": b,
                    "value": round(sim, 1),
                    "title": f"{sim:.1f}%",
                })

    nodes_json = json.dumps(nodes, ensure_ascii=False)
    edges_json = json.dumps(edges, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Dimension Co-occurrence Network</title>
  <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: system-ui, sans-serif; background: #1a1a2e; color: #eee; height: 100vh; display: flex; flex-direction: column; }}
    #controls {{
      display: flex; align-items: center; gap: 20px;
      padding: 10px 20px; background: #16213e; border-bottom: 1px solid #0f3460;
      flex-shrink: 0;
    }}
    #controls h2 {{ font-size: 14px; font-weight: 600; color: #e94560; white-space: nowrap; }}
    .ctrl {{ display: flex; align-items: center; gap: 8px; font-size: 13px; }}
    #threshold {{ width: 200px; accent-color: #e94560; }}
    #stats {{ font-size: 12px; color: #aaa; margin-left: auto; }}
    #network {{ flex: 1; }}
  </style>
</head>
<body>
  <div id="controls">
    <h2>Dimension Co-occurrence Network</h2>
    <div class="ctrl">
      <label for="threshold">Similarity threshold:</label>
      <input type="range" id="threshold" min="0" max="100" value="50" step="1">
      <span id="threshold-val" style="min-width:40px">50%</span>
    </div>
    <div id="stats"></div>
  </div>
  <div id="network"></div>

  <script>
    const ALL_NODES = {nodes_json};
    const ALL_EDGES = {edges_json};

    const nodeSet = new vis.DataSet(ALL_NODES);
    const edgeSet = new vis.DataSet([]);

    const options = {{
      nodes: {{
        shape: "dot",
        scaling: {{ min: 6, max: 40, label: {{ enabled: false }} }},
        font: {{ size: 11, color: "#eee", face: "system-ui" }},
        color: {{ background: "#4a90d9", border: "#2c5f8a", highlight: {{ background: "#e94560", border: "#c73652" }} }},
        borderWidth: 1,
      }},
      edges: {{
        scaling: {{ min: 1, max: 6 }},
        color: {{ inherit: false, color: "#4a90d9", highlight: "#e94560", opacity: 0.6 }},
        smooth: {{ type: "continuous" }},
      }},
      physics: {{
        solver: "forceAtlas2Based",
        forceAtlas2Based: {{ gravitationalConstant: -60, springLength: 120, springConstant: 0.05 }},
        stabilization: {{ iterations: 200 }},
      }},
      interaction: {{
        hover: true,
        tooltipDelay: 100,
      }},
    }};

    const container = document.getElementById("network");
    const network = new vis.Network(container, {{ nodes: nodeSet, edges: edgeSet }}, options);

    function applyThreshold(thresh) {{
      const filtered = ALL_EDGES.filter(e => e.value >= thresh);
      edgeSet.clear();
      edgeSet.add(filtered);

      const connected = new Set(filtered.flatMap(e => [e.from, e.to]));
      nodeSet.forEach(n => {{
        nodeSet.update({{ id: n.id, hidden: !connected.has(n.id) }});
      }});

      document.getElementById("stats").textContent =
        `${{connected.size}} nodes · ${{filtered.length}} edges`;
    }}

    const slider = document.getElementById("threshold");
    const label  = document.getElementById("threshold-val");
    slider.addEventListener("input", () => {{
      const v = +slider.value;
      label.textContent = v + "%";
      applyThreshold(v);
    }});

    applyThreshold(50);
  </script>
</body>
</html>
"""

    OUT.write_text(html, encoding="utf-8")
    n_edges = sum(1 for e in edges if e["value"] >= 50)
    print(f"Wrote {OUT}")
    print(f"  {len(nodes)} nodes · {len(edges)} total edges · {n_edges} at default threshold (50%)")


if __name__ == "__main__":
    main()
