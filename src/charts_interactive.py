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


SCALE_BOUNDS = {
    "likert_1_5": (1.0, 5.0),
    "nps": (-100.0, 100.0),
}
SCALE_AXIS_LABEL_FI = {
    "likert_1_5": "Keskiarvo (1–5)",
    "nps": "NPS-pisteet (-100 – +100)",
}


def _scale_of(df: pd.DataFrame) -> str:
    if "scale" not in df.columns:
        return "likert_1_5"
    s = df["scale"].dropna().astype(str)
    return s.iloc[0] if not s.empty else "likert_1_5"


def _zoom_ylim(values: list[float], cis: list[float], scale: str, min_span: float = 0.8) -> tuple[float, float]:
    lo_b, hi_b = SCALE_BOUNDS.get(scale, (None, None))
    means = [v for v in values if v is not None]
    if not means:
        return lo_b, hi_b
    cis = [c or 0 for c in cis]
    data_lo = min(m - c for m, c in zip(means, cis))
    data_hi = max(m + c for m, c in zip(means, cis))
    sd_estimate = (hi_b - lo_b) * 0.20 if lo_b is not None else 1.0
    target_span = max(min_span, 2.0 * sd_estimate, (data_hi - data_lo) + 0.4)
    centre = (data_lo + data_hi) / 2
    lo = centre - target_span / 2
    hi = centre + target_span / 2
    if lo_b is not None:
        if lo < lo_b:
            hi += (lo_b - lo)
            lo = lo_b
        if hi > hi_b:
            lo -= (hi - hi_b)
            hi = hi_b
        lo = max(lo, lo_b)
        hi = min(hi, hi_b)
    return lo, hi


def _euro(x):
    if pd.isna(x):
        return "—"
    return f"{x:.2f}".replace(".", ",")


def _fmt_value(v: float, scale: str) -> str:
    if pd.isna(v):
        return "—"
    if scale == "nps":
        return f"{int(round(v)):d}"
    return f"{v:.2f}".replace(".", ",")


def _kunta_specs(cfg: dict) -> list[dict]:
    return [cfg["focal_kunta"], *cfg["comparators"]]


def _make_timeseries_fig(sub: pd.DataFrame, cfg: dict) -> go.Figure:
    fig = go.Figure()
    focal = cfg["focal_kunta"]
    scale = _scale_of(sub)

    # x-axis nudges so 4 series at the same year don't render on top of each
    # other. Spacing is just a touch wider than the whisker cap (~4 px), so
    # markers are distinguishable but the visual year-reading is preserved.
    # Order left -> right: [Koko maa, Hamina, Kotka, Kouvola].
    all_specs = [focal, *cfg["comparators"]]
    display_order = sorted(all_specs, key=lambda s: (not s.get("is_aggregate"), s["slug"] != focal["slug"]))
    n_series = len(display_order)
    spacing = 0.04
    offsets = {s["slug"]: (i - (n_series - 1) / 2) * spacing for i, s in enumerate(display_order)}

    all_means: list[float] = []
    all_cis: list[float] = []
    right_labels: list[dict] = []
    all_years: list[int] = []
    for spec in (cfg["comparators"] + [focal]):
        s = sub[(sub["kunta_slug"] == spec["slug"]) & sub["mean"].notna()].sort_values("year")
        if s.empty:
            continue
        is_focal = spec["slug"] == focal["slug"]
        color = spec.get("highlight_color") if is_focal else spec.get("color")
        ci = s["ci95"].fillna(0).tolist()
        years_real = s["year"].tolist()
        years_nudged = [y + offsets.get(spec["slug"], 0.0) for y in years_real]
        all_means.extend(s["mean"].tolist())
        all_cis.extend(ci)
        all_years.extend(years_real)
        # customdata carries [ci, real_year] per point for the hover tooltip
        customdata = [[ci_v, y_real] for ci_v, y_real in zip(ci, years_real)]
        fig.add_trace(go.Scatter(
            x=years_nudged,
            y=s["mean"].tolist(),
            error_y=dict(type="data", array=ci, visible=True, thickness=1.1, width=4, color=color),
            mode="lines+markers",
            name=spec["label"],
            line=dict(
                color=color,
                width=3.5 if is_focal else 1.8,
                dash="solid" if (is_focal or spec.get("style") != "dashed") else "dash",
            ),
            marker=dict(size=10 if is_focal else 6),
            hovertemplate="<b>%{customdata[1]}</b>: %{y:.2f} ±%{customdata[0]:.2f}<extra>" + spec["label"] + "</extra>",
            customdata=customdata,
        ))
        last_year_real = int(s["year"].iloc[-1])
        last_year_nudged = last_year_real + offsets.get(spec["slug"], 0.0)
        last_mean = float(s["mean"].iloc[-1])
        right_labels.append({"x": last_year_nudged, "y": last_mean, "label": spec["label"], "color": color, "is_focal": is_focal})

    ylo, yhi = _zoom_ylim(all_means, all_cis, scale)
    tickformat = ".2f" if scale == "likert_1_5" else "d"

    # Right-anchored coloured labels placed in the right MARGIN (xref="paper")
    # so the axis itself stays flush to the last data year.
    annotations = []
    if right_labels:
        xmax = max(p["x"] for p in right_labels)
        span = (yhi - ylo) if (yhi is not None and ylo is not None and yhi > ylo) else 1
        min_gap = span * 0.06
        ranked = sorted(right_labels, key=lambda p: -p["y"])
        last_y = ranked[0]["y"]
        ranked[0]["display_y"] = last_y
        for i in range(1, len(ranked)):
            if last_y - ranked[i]["y"] < min_gap:
                last_y -= min_gap
            else:
                last_y = ranked[i]["y"]
            ranked[i]["display_y"] = last_y
        for p in ranked:
            annotations.append(dict(
                x=1.005, y=p["display_y"], xref="paper", yref="y",
                text=f"<b>{p['label']}</b>" if p["is_focal"] else p["label"],
                showarrow=False,
                xanchor="left", yanchor="middle",
                font=dict(color=p["color"], size=12.5 if p["is_focal"] else 11.5),
            ))
        xmin = min(all_years)
        xrange = [xmin - 0.2, xmax + 0.2]
        tickvals = sorted(set(all_years))
    else:
        xrange = None
        tickvals = None

    fig.update_layout(
        height=400,
        margin=dict(l=40, r=140, t=10, b=40),
        yaxis=dict(range=[ylo, yhi], title=SCALE_AXIS_LABEL_FI.get(scale, "Keskiarvo"), gridcolor="#E4E7EB", tickformat=tickformat, zeroline=(scale == "nps"), zerolinecolor="#3E4C59"),
        xaxis=dict(title="Vuosi", tickvals=tickvals, range=xrange),
        plot_bgcolor="white",
        showlegend=False,
        separators=", ",
        annotations=annotations,
    )
    return fig


def _make_crosssection_fig(sub: pd.DataFrame, cfg: dict) -> go.Figure:
    focal = cfg["focal_kunta"]
    scale = _scale_of(sub)
    # Focal first, then regional peers, then aggregates (Koko maa) at the bottom
    peer_comparators = [c for c in cfg["comparators"] if not c.get("is_aggregate")]
    aggregate_comparators = [c for c in cfg["comparators"] if c.get("is_aggregate")]
    ordered_slugs = [focal["slug"]] + [c["slug"] for c in peer_comparators] + [c["slug"] for c in aggregate_comparators]
    sub = (
        sub[sub["mean"].notna()]
        .set_index("kunta_slug")
        .reindex([s for s in ordered_slugs if s in sub["kunta_slug"].values])
        .dropna(subset=["mean"])
        .reset_index()
    )

    colors = []
    for slug in sub["kunta_slug"]:
        if slug == focal["slug"]:
            colors.append(focal.get("highlight_color"))
        else:
            spec = next(c for c in cfg["comparators"] if c["slug"] == slug)
            colors.append(spec.get("color"))

    labels = sub["kunta_label_fi"].tolist()
    means = sub["mean"].tolist()
    cis = sub["ci95"].fillna(0).tolist()

    if scale == "nps":
        axis_lo, axis_hi_raw = SCALE_BOUNDS["nps"]
        pad = 6.0
        axis_max = axis_hi_raw + 15
        tickformat = "d"
        tickvals = [-100, -75, -50, -25, 0, 25, 50, 75, 100]
        hover_fmt = "%{y}: <b>%{x:.0f}</b> ±%{customdata:.1f}<extra></extra>"
    else:
        axis_lo = 1.0
        pad = 0.08
        axis_max = 5.0 + 0.35
        tickformat = ".2f"
        tickvals = [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5]
        hover_fmt = "%{y}: <b>%{x:.2f}</b> ±%{customdata:.2f}<extra></extra>"

    annotations = []
    for label, mean, ci in zip(labels, means, cis):
        x_label = mean + ci + pad
        if x_label > axis_max - pad * 0.5:
            annotations.append(dict(
                x=axis_lo + pad, y=label, xref="x", yref="y",
                text=f"<b>{_fmt_value(mean, scale)}</b>", showarrow=False, xanchor="left",
                font=dict(color="white", size=12),
            ))
        else:
            annotations.append(dict(
                x=x_label, y=label, xref="x", yref="y",
                text=_fmt_value(mean, scale), showarrow=False, xanchor="left",
                font=dict(color="#1F2933", size=12),
            ))

    fig = go.Figure(go.Bar(
        x=means,
        y=labels,
        orientation="h",
        marker=dict(color=colors),
        error_x=dict(type="data", array=cis, visible=True, thickness=1.1, width=4, color="#3E4C59"),
        hovertemplate=hover_fmt,
        customdata=cis,
        cliponaxis=False,
    ))
    xaxis = dict(
        range=[axis_lo, axis_max],
        title=SCALE_AXIS_LABEL_FI.get(scale, "Keskiarvo"),
        gridcolor="#E4E7EB",
        tickformat=tickformat,
        tickvals=tickvals,
    )
    if scale == "nps":
        xaxis["zeroline"] = True
        xaxis["zerolinecolor"] = "#3E4C59"
    fig.update_layout(
        height=80 + 50 * len(labels),
        margin=dict(l=150, r=60, t=10, b=40),
        xaxis=xaxis,
        yaxis=dict(
            type="category",
            categoryorder="array",
            categoryarray=labels[::-1],
            title=None,
            ticksuffix="   ",  # breathing room between label and bar
            automargin=True,
        ),
        plot_bgcolor="white",
        separators=", ",
        annotations=annotations,
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

    # Theme order: askia themes in config order, then "Muu" group for anything unmapped
    theme_order = [t["label_fi"] for t in cfg["themes"]]

    cards = []
    for ind_id in df["indicator_id"].unique():
        sub = df[df["indicator_id"] == ind_id]
        source = sub["source_type"].iloc[0]
        label = sub["indicator_label_fi"].iloc[0]
        theme = sub["theme_label_fi"].dropna().iloc[0] if sub["theme_label_fi"].notna().any() else "Muu"
        # A "time series" with <2 valid years is really a single-year snapshot.
        valid_years = sub.loc[sub["mean"].notna(), "year"].unique()
        single_year = source == "timeseries" and len(valid_years) < 2
        if single_year:
            source = "cross_section"
            sub = sub[sub["mean"].notna()].copy()
        if source == "timeseries":
            fig = _make_timeseries_fig(sub, cfg)
        else:
            fig = _make_crosssection_fig(sub, cfg)
        # Plotly figure as JSON for client-side render
        import plotly.io as pio
        fig_json = pio.to_json(fig, validate=False, remove_uids=True)
        m = metric_lookup.get(ind_id, {})
        if source == "timeseries":
            badge_text = "aikasarja"
        else:
            yrs = sorted(sub.loc[sub["mean"].notna(), "year"].unique())
            badge_text = str(int(yrs[-1])) if yrs else "2026"
        is_theme_aggregate = label == theme  # askia theme rows have indicator label == theme
        cards.append({
            "id": ind_id,
            "label": label,
            "theme": theme,
            "source": source,
            "badge": badge_text,
            "is_theme_aggregate": is_theme_aggregate,
            "fig_json": fig_json,
            "metrics": {k: (None if (isinstance(v, float) and pd.isna(v)) else v) for k, v in m.items() if k != "indicator_label_fi"},
        })

    cards_json = json.dumps(cards, ensure_ascii=False)
    sort_views = json.dumps(cfg["sort_views"], ensure_ascii=False)
    theme_order_json = json.dumps(theme_order, ensure_ascii=False)
    focal_label = cfg["focal_kunta"]["label"]
    attribution = html.escape(cfg["attribution_fi"])

    html_doc = dedent("""
    <!doctype html>
    <html lang="fi">
    <head>
      <meta charset="utf-8" />
      <title>Kuntabarometri 2022–2026</title>
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
        header p { margin: 4px 0 0; color: var(--muted); font-size: 13px; max-width: 70em; }
        header p.ci-note { margin-top: 10px; padding: 10px 14px; background: #FFF7E6; border-left: 4px solid #D55E00; color: #1F2933; font-size: 13.5px; line-height: 1.45; }
        header p.ci-note strong { color: #1F2933; }
        header p.method-note { margin-top: 8px; padding: 10px 14px; background: #EAF4FB; border-left: 4px solid #0072B2; color: #1F2933; font-size: 13px; line-height: 1.45; }
        header p.method-note strong { color: #1F2933; }
        .controls { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; padding: 14px 32px; background: white; border-bottom: 1px solid #CBD2D9; position: sticky; top: 0; z-index: 10; }
        .controls label { font-size: 13px; color: var(--muted); margin-right: 6px; }
        .controls select, .controls input { font-size: 14px; padding: 6px 10px; border: 1px solid #CBD2D9; border-radius: 6px; }
        .controls input[type="search"] { min-width: 240px; }
        .grid { padding: 24px 32px; display: flex; flex-direction: column; gap: 8px; }
        .theme-group { display: flex; flex-direction: column; gap: 14px; padding: 14px 0 24px; border-top: 2px solid #D9DEE6; }
        .theme-group:first-child { border-top: none; padding-top: 4px; }
        .theme-header { display: flex; align-items: baseline; justify-content: space-between; gap: 14px; padding: 8px 4px 2px; }
        .theme-header h2 { margin: 0; font-size: 20px; font-weight: 700; color: var(--ink); letter-spacing: -0.01em; }
        .theme-header .theme-meta { font-size: 12.5px; color: var(--muted); }
        .theme-cards { display: grid; grid-template-columns: 1fr; gap: 16px; }
        .card { background: var(--card); border-radius: 12px; padding: 18px 20px 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
        .card.theme-aggregate { border-left: 4px solid var(--accent); }
        .card .card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 4px; }
        .card h2 { margin: 0; font-size: 15px; font-weight: 600; flex: 1; }
        .card .meta { color: var(--muted); font-size: 12px; margin-bottom: 8px; }
        .card .metrics { color: var(--muted); font-size: 11.5px; margin-bottom: 4px; }
        .download-btn { background: white; color: var(--muted); border: 1px solid #CBD2D9; border-radius: 6px; padding: 4px 10px; font-size: 12px; cursor: pointer; white-space: nowrap; transition: all 0.15s; }
        .download-btn:hover { background: var(--accent); color: white; border-color: var(--accent); }
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
        <p class="ci-note"><strong>Lue virhepalkit näin:</strong> Pylväät ja viivat näyttävät vastausten keskiarvon (asteikko 1–5). Niitä ympäröivät virhepalkit ovat <em>95 %:n luottamusvälejä</em>. Jos kahden kunnan virhepalkit <strong>eivät leikkaa toisiaan</strong>, ero on tilastollisesti merkitsevä (p&nbsp;&lt;&nbsp;0,05) – sääntö pätee aina yhteen suuntaan. Päinvastoin se ei pidä paikkaansa: luottamusvälien limittyminen ei suoraan tarkoita, ettei ero ole merkitsevä, varsinkin jos limitys on hyvin pieni. Pienissä kunnissa (esim. Hamina ~90 vastaajaa) virhepalkit ovat luonnostaan suuremmat kuin koko maan koonnissa (~12 000 vastaajaa).</p>
        <p class="method-note"><strong>Huom. aikasarjojen keskiarvojen laskenta:</strong> Arvot lasketaan tällä sivustolla vastaustasolla: kaikki teeman alakysymyksien vastaukset yhdistetään ja niistä lasketaan keskiarvo. Tämä on ainoa tapa saada vertailukelpoinen aikasarja vuosille 2022–2026, sillä Taloustutkimus julkaisee <a href="https://survey.taloustutkimus.fi/dashboard/kuntabarometri_2026/" target="_blank" rel="noopener noreferrer">lähdesivustolla</a> tarkemman, vastaajatasolla lasketun teemaindeksin (vastaajan oma keskiarvo teemansa kysymyksistä) vain uusimmasta vuodesta. Siksi täsmälliset keskiarvolukemaat saattavat poiketa kuntaraporteista, mutta trendit ja eri alueiden väliset erot pätevät suhteessa toisiinsa.</p>
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
            __SORT_OPTS__
            <option value="label">Aakkosellinen</option>
          </select>
        </label>
        <label><input type="checkbox" id="hideMissing" checked /> Piilota tyhjät</label>
      </div>
      <div class="grid" id="grid"></div>
      <footer>__ATTRIB__</footer>
      <script>
        const CARDS = __CARDS_JSON__;
        const SORT_VIEWS = __SORT_VIEWS__;
        const THEME_ORDER = __THEME_ORDER__;
        const FOCAL = "__FOCAL__";
        const grid = document.getElementById("grid");
        const search = document.getElementById("search");
        const filterType = document.getElementById("filterType");
        const sortBy = document.getElementById("sortBy");
        const hideMissing = document.getElementById("hideMissing");

        function metricVal(card, mode) {
          if (mode === "label") return null;
          const view = SORT_VIEWS.find(v => v.id === mode);
          if (!view) return null;
          const v = card.metrics ? card.metrics[view.metric] : null;
          return (v == null || isNaN(v)) ? null : v;
        }

        function cardSortKey(card, mode) {
          // Theme aggregate always first within its group
          const aggKey = card.is_theme_aggregate ? 0 : 1;
          if (mode === "label") return [aggKey, card.label.toLowerCase()];
          const v = metricVal(card, mode);
          if (v == null) return [aggKey, Infinity, card.label.toLowerCase()];
          return [aggKey, -Math.abs(v), card.label.toLowerCase()];
        }

        function groupSortKey(theme, cardsInGroup, mode) {
          if (mode === "label") return [theme.toLowerCase()];
          // Use theme aggregate's metric if present; else max across group
          const agg = cardsInGroup.find(c => c.is_theme_aggregate);
          let m = null;
          if (agg) m = metricVal(agg, mode);
          if (m == null) {
            for (const c of cardsInGroup) {
              const v = metricVal(c, mode);
              if (v != null && (m == null || Math.abs(v) > Math.abs(m))) m = v;
            }
          }
          if (m == null) return [Infinity, theme.toLowerCase()];
          return [-Math.abs(m), theme.toLowerCase()];
        }

        function compare(a, b) {
          for (let i = 0; i < Math.min(a.length, b.length); i++) {
            if (a[i] < b[i]) return -1;
            if (a[i] > b[i]) return 1;
          }
          return a.length - b.length;
        }

        function slugify(s) {
          return (s||"").toLowerCase()
            .replace(/[äÄ]/g, "a").replace(/[öÖ]/g, "o").replace(/[åÅ]/g, "a")
            .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 80);
        }

        function downloadChart(divId, card) {
          const fname = slugify(card.label) || card.id;
          Plotly.downloadImage(divId, {format: 'png', filename: fname, width: 1400, height: 700, scale: 2});
        }

        function renderCard(c, idx, container) {
          const card = document.createElement("div");
          card.className = "card" + (c.is_theme_aggregate ? " theme-aggregate" : "");
          const badgeText = c.badge || (c.source === "timeseries" ? "aikasarja" : "2026");
          const badge = c.source === "timeseries"
            ? `<span class="badge ts">${badgeText}</span>`
            : `<span class="badge cs">${badgeText}</span>`;
          card.innerHTML = `
            <div class="card-head">
              <h2>${badge}${c.label}</h2>
              <button class="download-btn" type="button" data-fig="fig-${idx}" title="Lataa PNG-kuva">Lataa kuva</button>
            </div>
            <div id="fig-${idx}" style="height: ${c.source==='timeseries'?'380':'280'}px;"></div>
          `;
          container.appendChild(card);
          const fig = JSON.parse(c.fig_json);
          fig.layout = Object.assign({}, fig.layout, { dragmode: false });
          Plotly.newPlot(`fig-${idx}`, fig.data, fig.layout, {
            displayModeBar: false,
            responsive: true,
            scrollZoom: false,
            doubleClick: false,
            showAxisDragHandles: false,
            showAxisRangeEntryBoxes: false,
          });
          const btn = card.querySelector(".download-btn");
          if (btn) btn.addEventListener("click", () => downloadChart(btn.dataset.fig, c));
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

          // Bucket by theme. Themes not in THEME_ORDER fall to "Muu" group, rendered last.
          const buckets = new Map();
          for (const c of filtered) {
            const theme = c.theme || "Muu";
            if (!buckets.has(theme)) buckets.set(theme, []);
            buckets.get(theme).push(c);
          }
          const themes = Array.from(buckets.keys());
          themes.sort((a, b) => compare(groupSortKey(a, buckets.get(a), s), groupSortKey(b, buckets.get(b), s)));

          grid.innerHTML = "";
          let idx = 0;
          for (const theme of themes) {
            const cardsInTheme = buckets.get(theme).slice();
            cardsInTheme.sort((a, b) => compare(cardSortKey(a, s), cardSortKey(b, s)));
            const group = document.createElement("section");
            group.className = "theme-group";
            const view = SORT_VIEWS.find(v => v.id === s);
            const agg = cardsInTheme.find(c => c.is_theme_aggregate);
            let metaText = "";
            if (view) {
              const m = metricVal(agg, s);
              if (m != null) metaText = `${view.label_fi}: ${m >= 0 ? "+" : ""}${m.toFixed(2).replace(".", ",")}`;
            }
            const header = document.createElement("div");
            header.className = "theme-header";
            header.innerHTML = `<h2>${theme}</h2>${metaText ? `<span class="theme-meta">${metaText}</span>` : ""}`;
            group.appendChild(header);
            const themeCards = document.createElement("div");
            themeCards.className = "theme-cards";
            group.appendChild(themeCards);
            // Attach to DOM before Plotly.newPlot — Plotly needs the target div
            // to be in the document tree, not in a detached fragment.
            grid.appendChild(group);
            for (const c of cardsInTheme) {
              renderCard(c, idx, themeCards);
              idx += 1;
            }
          }
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
        .replace("__THEME_ORDER__", theme_order_json)
    )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html_doc, encoding="utf-8")
    print(f"Wrote {dest} ({dest.stat().st_size:,} bytes)")
    return dest
