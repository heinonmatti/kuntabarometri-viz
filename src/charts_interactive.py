"""Single self-contained interactive HTML dashboard via Plotly.

Each indicator is rendered as a Plotly figure embedded inside a card. A
top-of-page filter bar lets the user search/select indicators, switch
between time-series and cross-section views, and sort by any of the
metrics defined in config.yaml.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from textwrap import dedent

import pandas as pd
import plotly.graph_objects as go

EUROPEAN_FMT = ",.2f"  # Plotly tickformat — uses ',' as decimal in 'de' locale config


def _euro(x):
    if pd.isna(x):
        return "—"
    return f"{x:.2f}".replace(".", ",")


def _kunta_specs(cfg: dict) -> list[dict]:
    return [cfg["focal_kunta"], *cfg["comparators"]]


def _make_timeseries_fig(sub: pd.DataFrame, cfg: dict) -> go.Figure:
    fig = go.Figure()
    focal = cfg["focal_kunta"]
    specs = {focal["slug"]: {**focal, "is_focal": True}}
    for c in cfg["comparators"]:
        specs[c["slug"]] = {**c, "is_focal": False}

    for spec in (cfg["comparators"] + [focal]):
        s = sub[(sub["kunta_slug"] == spec["slug"]) & sub["mean"].notna()].sort_values("year")
        if s.empty:
            continue
        is_focal = spec["slug"] == focal["slug"]
        fig.add_trace(go.Scatter(
            x=s["year"], y=s["mean"], mode="lines+markers",
            name=spec["label"],
            line=dict(
                color=spec.get("highlight_color") if is_focal else spec.get("color"),
                width=3.5 if is_focal else 1.8,
                dash="solid" if (is_focal or spec.get("style") != "dashed") else "dash",
            ),
            marker=dict(size=10 if is_focal else 6),
            hovertemplate="%{x}: <b>%{y:.2f}</b><extra>" + spec["label"] + "</extra>",
        ))
    fig.update_layout(
        height=380,
        margin=dict(l=40, r=20, t=10, b=40),
        yaxis=dict(range=[1, 5], title="Keskiarvo (1–5)", gridcolor="#E4E7EB", tickformat=".2f"),
        xaxis=dict(title="Vuosi", dtick=2),
        plot_bgcolor="white",
        showlegend=True,
        legend=dict(orientation="h", y=-0.2),
        separators=", ",  # European: comma decimal, space thousands
    )
    return fig


def _make_crosssection_fig(sub: pd.DataFrame, cfg: dict) -> go.Figure:
    focal = cfg["focal_kunta"]
    ordered_slugs = [focal["slug"]] + [c["slug"] for c in cfg["comparators"]]
    sub = sub[sub["mean"].notna()].set_index("kunta_slug").reindex(ordered_slugs).dropna(subset=["mean"]).reset_index()

    colors = []
    for slug in sub["kunta_slug"]:
        if slug == focal["slug"]:
            colors.append(focal.get("highlight_color"))
        else:
            spec = next(c for c in cfg["comparators"] if c["slug"] == slug)
            colors.append(spec.get("color"))

    fig = go.Figure(go.Bar(
        x=sub["mean"], y=sub["kunta_label_fi"], orientation="h",
        marker=dict(color=colors),
        text=[_euro(v) for v in sub["mean"]],
        textposition="outside",
        hovertemplate="%{y}: <b>%{x:.2f}</b><extra></extra>",
    ))
    fig.update_layout(
        height=80 + 45 * len(sub),
        margin=dict(l=120, r=40, t=10, b=40),
        xaxis=dict(range=[1, 5], title="Keskiarvo (1–5)", gridcolor="#E4E7EB", tickformat=".2f"),
        yaxis=dict(autorange="reversed", title=None),
        plot_bgcolor="white",
        separators=", ",
    )
    return fig


# --- sort metric helpers (also used by sort_views, kept lightweight here) ---


def compute_metrics(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    focal = cfg["focal_kunta"]
    out = []
    for ind_id, sub in df.groupby("indicator_id"):
        source = sub["source_type"].iloc[0]
        label = sub["indicator_label_fi"].iloc[0]
        focal_rows = sub[sub["kunta_slug"] == focal["slug"]]
        if focal_rows.empty:
            continue
        focal_valid = focal_rows[focal_rows["mean"].notna()].sort_values("year")
        if focal_valid.empty:
            continue
        latest_year = sub[sub["mean"].notna()]["year"].max()
        latest = sub[(sub["year"] == latest_year) & sub["mean"].notna()]
        focal_latest = latest[latest["kunta_slug"] == focal["slug"]]["mean"].mean()
        first_focal = focal_valid["mean"].iloc[0]
        last_focal = focal_valid["mean"].iloc[-1]
        row = {
            "indicator_id": ind_id,
            "indicator_label_fi": label,
            "source_type": source,
            "latest_year": int(latest_year) if pd.notna(latest_year) else None,
            "hamina_first": first_focal,
            "hamina_latest": last_focal,
            "hamina_abs_change_first_to_last": abs(last_focal - first_focal),
            "hamina_signed_change_first_to_last": last_focal - first_focal,
        }
        for comp in cfg["comparators"]:
            cmp_latest = latest[latest["kunta_slug"] == comp["slug"]]["mean"].mean()
            row[f"gap_vs_{comp['slug']}_latest"] = (focal_latest - cmp_latest) if pd.notna(cmp_latest) else None
            row[f"gap_vs_{comp['slug']}_abs_latest"] = abs(row[f"gap_vs_{comp['slug']}_latest"]) if pd.notna(row[f"gap_vs_{comp['slug']}_latest"]) else None
            if source == "timeseries":
                cmp_first = sub[(sub["kunta_slug"] == comp["slug"]) & (sub["year"] == focal_valid["year"].iloc[0])]["mean"].mean()
                cmp_last = sub[(sub["kunta_slug"] == comp["slug"]) & (sub["year"] == focal_valid["year"].iloc[-1])]["mean"].mean()
                if pd.notna(cmp_first) and pd.notna(cmp_last):
                    row[f"trend_divergence_vs_{comp['slug']}"] = abs((last_focal - first_focal) - (cmp_last - cmp_first))
        out.append(row)
    return pd.DataFrame(out)


def render(csv_path: Path, cfg: dict, dest: Path) -> Path:
    df = pd.read_csv(csv_path)
    metrics = compute_metrics(df, cfg)
    metric_lookup = {m["indicator_id"]: m for m in metrics.to_dict("records")}

    cards = []
    for ind_id in df["indicator_id"].unique():
        sub = df[df["indicator_id"] == ind_id]
        source = sub["source_type"].iloc[0]
        label = sub["indicator_label_fi"].iloc[0]
        theme = sub["theme_label_fi"].dropna().iloc[0] if sub["theme_label_fi"].notna().any() else ""
        if source == "timeseries":
            fig = _make_timeseries_fig(sub, cfg)
        else:
            fig = _make_crosssection_fig(sub, cfg)
        # Plotly figure as JSON for client-side render
        fig_json = json.dumps(fig.to_plotly_json(), default=str)
        m = metric_lookup.get(ind_id, {})
        cards.append({
            "id": ind_id,
            "label": label,
            "theme": theme,
            "source": source,
            "fig_json": fig_json,
            "metrics": {k: (None if (isinstance(v, float) and pd.isna(v)) else v) for k, v in m.items() if k != "indicator_label_fi"},
        })

    cards_json = json.dumps(cards, ensure_ascii=False)
    sort_views = json.dumps(cfg["sort_views"], ensure_ascii=False)
    focal_label = cfg["focal_kunta"]["label"]
    attribution = html.escape(cfg["attribution_fi"])

    html_doc = dedent("""
    <!doctype html>
    <html lang="fi">
    <head>
      <meta charset="utf-8" />
      <title>Kuntabarometri 2022–2026 — __FOCAL__</title>
      <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
      <style>
        :root {
          --ink: #1F2933;
          --muted: #52606D;
          --accent: #C8102E;
          --bg: #F5F7FA;
          --card: #FFFFFF;
        }
        * { box-sizing: border-box; }
        body { margin: 0; padding: 0; font-family: -apple-system, "Segoe UI", Inter, Helvetica, Arial, sans-serif; color: var(--ink); background: var(--bg); }
        header { padding: 24px 32px 12px; border-bottom: 1px solid #CBD2D9; background: white; }
        header h1 { margin: 0; font-size: 22px; }
        header p { margin: 4px 0 0; color: var(--muted); font-size: 13px; }
        .controls { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; padding: 14px 32px; background: white; border-bottom: 1px solid #CBD2D9; position: sticky; top: 0; z-index: 10; }
        .controls label { font-size: 13px; color: var(--muted); margin-right: 6px; }
        .controls select, .controls input { font-size: 14px; padding: 6px 10px; border: 1px solid #CBD2D9; border-radius: 6px; }
        .controls input[type="search"] { min-width: 240px; }
        .grid { padding: 24px 32px; display: grid; grid-template-columns: 1fr; gap: 20px; }
        .card { background: var(--card); border-radius: 12px; padding: 18px 20px 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
        .card h2 { margin: 0 0 4px; font-size: 15px; font-weight: 600; }
        .card .meta { color: var(--muted); font-size: 12px; margin-bottom: 8px; }
        .card .metrics { color: var(--muted); font-size: 11.5px; }
        .badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; background: #E4E7EB; color: var(--ink); margin-right: 6px; }
        .badge.ts { background: #FFE5EA; color: var(--accent); }
        .badge.cs { background: #E4E7EB; color: var(--ink); }
        footer { padding: 16px 32px; color: var(--muted); font-size: 12px; }
      </style>
    </head>
    <body>
      <header>
        <h1>Kuntabarometri – __FOCAL__ vs vertailukunnat</h1>
        <p>Aikasarja 2022–2026 teemoittain (13) ja poikkileikkaus 2026 alakysymyksittäin. __ATTRIB__</p>
      </header>
      <div class="controls">
        <label>Hae: <input type="search" id="search" placeholder="kysymyksen tai teeman osa..."/></label>
        <label>Tyyppi:
          <select id="filterType">
            <option value="all">Kaikki</option>
            <option value="timeseries">Vain aikasarja</option>
            <option value="cross_section">Vain poikkileikkaus 2026</option>
          </select>
        </label>
        <label>Järjestys:
          <select id="sortBy">
            <option value="label">Aakkosellinen</option>
            __SORT_OPTS__
          </select>
        </label>
        <label><input type="checkbox" id="hideMissing" checked /> Piilota tyhjät</label>
      </div>
      <div class="grid" id="grid"></div>
      <footer>__ATTRIB__</footer>
      <script>
        const CARDS = __CARDS_JSON__;
        const SORT_VIEWS = __SORT_VIEWS__;
        const FOCAL = "__FOCAL__";
        const grid = document.getElementById("grid");
        const search = document.getElementById("search");
        const filterType = document.getElementById("filterType");
        const sortBy = document.getElementById("sortBy");
        const hideMissing = document.getElementById("hideMissing");

        function getSortKey(card, mode) {
          if (mode === "label") return [card.label.toLowerCase()];
          const view = SORT_VIEWS.find(v => v.id === mode);
          if (!view) return [card.label.toLowerCase()];
          const v = card.metrics ? card.metrics[view.metric] : null;
          // Sort descending (largest first), nulls last
          if (v == null || isNaN(v)) return [Infinity, card.label.toLowerCase()];
          return [-Math.abs(v), card.label.toLowerCase()];
        }

        function compare(a, b) {
          for (let i = 0; i < a.length; i++) {
            if (a[i] < b[i]) return -1;
            if (a[i] > b[i]) return 1;
          }
          return 0;
        }

        function metricsToBadges(card) {
          const m = card.metrics || {};
          const parts = [];
          if (card.source === "timeseries" && m.hamina_signed_change_first_to_last != null) {
            const v = m.hamina_signed_change_first_to_last;
            parts.push(`Muutos ${FOCAL}: ${(v>=0?"+":"")}${v.toFixed(2).replace(".", ",")}`);
          }
          if (m.gap_vs_kokomaa_latest != null) {
            const v = m.gap_vs_kokomaa_latest;
            parts.push(`Ero koko maahan: ${(v>=0?"+":"")}${v.toFixed(2).replace(".", ",")}`);
          }
          return parts.join(" · ");
        }

        function render() {
          const q = search.value.trim().toLowerCase();
          const t = filterType.value;
          const s = sortBy.value;
          const hm = hideMissing.checked;
          const filtered = CARDS.filter(c => {
            if (t !== "all" && c.source !== t) return false;
            if (q && !(c.label.toLowerCase().includes(q) || (c.theme||"").toLowerCase().includes(q))) return false;
            if (hm && c.source === "timeseries" && (!c.metrics || c.metrics.hamina_latest == null)) return false;
            return true;
          });
          filtered.sort((a, b) => compare(getSortKey(a, s), getSortKey(b, s)));
          grid.innerHTML = "";
          filtered.forEach((c, i) => {
            const card = document.createElement("div");
            card.className = "card";
            const badge = c.source === "timeseries" ? '<span class="badge ts">aikasarja</span>' : '<span class="badge cs">2026</span>';
            const themeBadge = c.theme && c.theme !== c.label ? `<span class="badge">${c.theme}</span>` : "";
            card.innerHTML = `
              <h2>${badge}${themeBadge}${c.label}</h2>
              <div class="metrics">${metricsToBadges(c)}</div>
              <div id="fig-${i}" style="height: ${c.source==='timeseries'?'380':'280'}px;"></div>
            `;
            grid.appendChild(card);
            const fig = JSON.parse(c.fig_json);
            Plotly.newPlot(`fig-${i}`, fig.data, fig.layout, {displayModeBar: false, responsive: true});
          });
        }
        search.addEventListener("input", render);
        filterType.addEventListener("change", render);
        sortBy.addEventListener("change", render);
        hideMissing.addEventListener("change", render);
        render();
      </script>
    </body>
    </html>
    """).strip()

    sort_opts = "\n            ".join(
        f'<option value="{html.escape(v["id"])}">{html.escape(v["label_fi"])}</option>'
        for v in cfg["sort_views"]
    )
    html_doc = (html_doc
        .replace("__FOCAL__", html.escape(focal_label))
        .replace("__ATTRIB__", attribution)
        .replace("__CARDS_JSON__", cards_json)
        .replace("__SORT_VIEWS__", sort_views)
        .replace("__SORT_OPTS__", sort_opts)
    )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html_doc, encoding="utf-8")
    print(f"Wrote {dest} ({dest.stat().st_size:,} bytes)")
    return dest
