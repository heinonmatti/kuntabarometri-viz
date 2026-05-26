"""Render one PNG per indicator.

* Theme-level rows -> line chart with one line per kunta over years.
* Sub-question rows (2026 cross-section) -> horizontal bar chart with one
  bar per kunta.

All Finnish labels, Hamina highlighted. Output goes to ``output/png/`` with
a stable filename derived from indicator_id so sort_views can copy them
into rank-ordered folders.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

LIKERT_FMT = "{:.2f}".format

FIG_W = 9.5
FIG_H = 5.5
DPI = 150


SCALE_BOUNDS = {
    "likert_1_5": (1.0, 5.0),
    "nps": (-100.0, 100.0),
}
SCALE_AXIS_LABEL_FI = {
    "likert_1_5": "Keskiarvo (asteikko 1–5)",
    "nps": "NPS-pisteet (asteikko -100 – +100)",
}


def _scale_of(df: pd.DataFrame) -> str:
    if "scale" not in df.columns:
        return "likert_1_5"
    s = df["scale"].dropna().astype(str)
    return s.iloc[0] if not s.empty else "likert_1_5"


def _zoom_ylim(values: list[float], cis: list[float], scale: str, min_span: float = 0.8) -> tuple[float, float]:
    """Compute a tight y-axis range that comfortably shows the data and CIs.

    Floor of the span is ~2 SD of the visible data (or ``min_span``, whichever
    larger). Clamped to the scale bounds.
    """
    lo_b, hi_b = SCALE_BOUNDS.get(scale, (None, None))
    if not values:
        return lo_b, hi_b
    means = [v for v in values if v is not None]
    if not means:
        return lo_b, hi_b
    cis = [c or 0 for c in cis]
    data_lo = min(m - c for m, c in zip(means, cis))
    data_hi = max(m + c for m, c in zip(means, cis))
    # 2 SD heuristic — for Likert 1-5 SD~1, so 2 SD ≈ 2; for NPS roughly ±50 wide
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


def _safe_filename(s: str, max_len: int = 80) -> str:
    s = re.sub(r"[^a-zA-Z0-9äöåÄÖÅ_-]+", "_", s).strip("_")
    return s[:max_len]


def _european_format(x, _pos=None):
    return f"{x:.2f}".replace(".", ",")


def _wrap_title(title: str, width: int = 80) -> str:
    return "\n".join(textwrap.wrap(title, width=width))


def _style_axes(ax, attribution: str, ylim: tuple[float, float] | None = None, *, fmt: str = "european") -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_alpha(0.4)
    ax.spines["bottom"].set_alpha(0.4)
    ax.tick_params(axis="both", length=0)
    ax.grid(True, axis="y", linestyle=":", linewidth=0.6, alpha=0.55)
    ax.set_axisbelow(True)
    if fmt == "european":
        ax.yaxis.set_major_formatter(plt.FuncFormatter(_european_format))
    else:
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x):d}"))
    if ylim is not None:
        ax.set_ylim(*ylim)
    ax.figure.text(0.01, 0.01, attribution, fontsize=7.5, color="#52606D", ha="left")


def _series_style(spec: dict, is_focal: bool) -> dict:
    if is_focal:
        return {
            "color": spec.get("highlight_color", "#C8102E"),
            "linewidth": 2.6,
            "marker": "o",
            "markersize": 7,
            "zorder": 5,
            "linestyle": "solid",
        }
    return {
        "color": spec.get("color", "#52606D"),
        "linewidth": 1.6,
        "marker": "o",
        "markersize": 5,
        "alpha": 0.9,
        "zorder": 3,
        "linestyle": "dashed" if spec.get("style") == "dashed" else "solid",
    }


def render_timeseries_indicator(df_ind: pd.DataFrame, cfg: dict, out_dir: Path) -> Path | None:
    indicator_label = df_ind["indicator_label_fi"].iloc[0]
    indicator_id = df_ind["indicator_id"].iloc[0]

    focal = cfg["focal_kunta"]
    comparators = cfg["comparators"]
    kunta_specs = {focal["slug"]: {**focal, "is_focal": True}}
    for c in comparators:
        kunta_specs[c["slug"]] = {**c, "is_focal": False}

    df_valid = df_ind[df_ind["mean"].notna()].sort_values("year")
    if df_valid.empty:
        return None

    scale = _scale_of(df_ind)
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), dpi=DPI)
    title = f"{indicator_label}"
    ax.set_title(_wrap_title(title), fontsize=13.5, fontweight="bold", color="#1F2933", loc="left", pad=18)
    ax.set_xlabel("Vuosi", fontsize=10.5, color="#3E4C59")
    ax.set_ylabel(SCALE_AXIS_LABEL_FI.get(scale, "Keskiarvo"), fontsize=10.5, color="#3E4C59")

    # Plot focal last so it sits on top
    series_order = list(kunta_specs.values())
    series_order.sort(key=lambda s: s.get("is_focal", False))
    right_label_points: list[dict] = []

    # x-axis nudge: same logic as charts_interactive — aggregates left, focal next, peers
    nudge_order = sorted(
        [focal, *comparators],
        key=lambda s: (not s.get("is_aggregate"), s["slug"] != focal["slug"]),
    )
    spacing = 0.04
    n_series = len(nudge_order)
    nudges = {s["slug"]: (i - (n_series - 1) / 2) * spacing for i, s in enumerate(nudge_order)}

    for spec in series_order:
        sub = df_valid[df_valid["kunta_slug"] == spec["slug"]]
        if sub.empty:
            continue
        style = _series_style(spec, spec.get("is_focal", False))
        color = style["color"]
        ci = sub["ci95"].fillna(0).to_numpy()
        offset = nudges.get(spec["slug"], 0.0)
        years_nudged = sub["year"] + offset
        ax.errorbar(
            years_nudged, sub["mean"], yerr=ci,
            fmt="none", ecolor=color, elinewidth=1.2, capsize=4, capthick=1.0,
            alpha=0.9 if spec.get("is_focal") else 0.55,
            zorder=style["zorder"] - 1,
        )
        ax.plot(years_nudged, sub["mean"], **style)
        last = sub.iloc[-1]
        right_label_points.append({
            "x": float(last["year"]) + offset,
            "y": float(last["mean"]),
            "label": spec["label"],
            "color": color,
            "is_focal": spec.get("is_focal", False),
        })

    years_present = sorted(df_valid["year"].unique())
    ax.set_xticks(years_present)
    ax.set_xticklabels([str(y) for y in years_present], fontsize=10)

    ylim = _zoom_ylim(
        df_valid["mean"].dropna().tolist(),
        df_valid["ci95"].fillna(0).tolist(),
        scale,
    )
    _style_axes(ax, cfg["attribution_fi"], ylim=ylim, fmt="european" if scale == "likert_1_5" else "integer")

    # Right-anchored coloured labels placed in the right MARGIN (axes-fraction
    # x > 1.0) so the chart's x-axis stays flush to the last data year.
    if right_label_points:
        xmax = max(p["x"] for p in right_label_points)
        ax.set_xlim(min(years_present) - 0.2, xmax + 0.2)
        ymin, ymax = (ylim or (0, 1))
        span = (ymax - ymin) if (ymax and ymin and ymax > ymin) else 1
        min_gap = span * 0.06
        ranked = sorted(right_label_points, key=lambda p: -p["y"])
        last_y = ranked[0]["y"]
        ranked[0]["display_y"] = last_y
        for i in range(1, len(ranked)):
            if last_y - ranked[i]["y"] < min_gap:
                last_y -= min_gap
            else:
                last_y = ranked[i]["y"]
            ranked[i]["display_y"] = last_y
        for p in ranked:
            ax.text(
                1.015, p["display_y"], p["label"],
                transform=ax.get_yaxis_transform(),
                color=p["color"],
                fontweight="bold" if p["is_focal"] else "normal",
                fontsize=11 if p["is_focal"] else 10,
                va="center", ha="left",
                clip_on=False,
            )

    fig.tight_layout(rect=[0, 0.03, 0.86, 1])

    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{_safe_filename(indicator_id)}.png"
    fig.savefig(dest, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return dest


def render_crosssection_indicator(df_ind: pd.DataFrame, cfg: dict, out_dir: Path) -> Path | None:
    indicator_label = df_ind["indicator_label_fi"].iloc[0]
    indicator_id = df_ind["indicator_id"].iloc[0]

    focal = cfg["focal_kunta"]
    comparators = cfg["comparators"]
    # Focal first, regional peers next, aggregates (Koko maa) at the bottom
    peer_comparators = [c for c in comparators if not c.get("is_aggregate")]
    aggregate_comparators = [c for c in comparators if c.get("is_aggregate")]
    ordered_specs = [focal] + peer_comparators + aggregate_comparators
    kunta_specs_map = {focal["slug"]: {**focal, "is_focal": True}}
    for c in comparators:
        kunta_specs_map[c["slug"]] = {**c, "is_focal": False}

    df_valid = df_ind[df_ind["mean"].notna()]
    if df_valid.empty:
        return None
    ordered = [s["slug"] for s in ordered_specs]
    df_valid = df_valid.set_index("kunta_slug").reindex([s for s in ordered if s in df_valid["kunta_slug"].values])
    df_valid = df_valid.reset_index()

    if df_valid.empty:
        return None

    scale = _scale_of(df_ind)
    year_values = df_valid["year"].dropna().unique() if "year" in df_valid.columns else []
    year_label = f"{int(year_values[0])}" if len(year_values) == 1 else "2026"
    fig, ax = plt.subplots(figsize=(FIG_W, max(2.5, 1.0 + 0.55 * len(df_valid))), dpi=DPI)
    title = f"{indicator_label}  ({year_label})"
    ax.set_title(_wrap_title(title), fontsize=13, fontweight="bold", color="#1F2933", loc="left", pad=18)

    colors = []
    for slug in df_valid["kunta_slug"]:
        spec = kunta_specs_map.get(slug, {})
        colors.append(spec.get("highlight_color") if spec.get("is_focal") else spec.get("color", "#52606D"))

    cis = df_valid["ci95"].fillna(0).to_numpy()
    bars = ax.barh(
        df_valid["kunta_label_fi"],
        df_valid["mean"],
        color=colors,
        edgecolor="white",
        linewidth=0.6,
        xerr=cis,
        error_kw=dict(ecolor="#3E4C59", elinewidth=1.0, capsize=3, capthick=0.9, alpha=0.9),
    )
    # Annotate bar ends, accounting for error bar extent and scale
    label_pad = 6 if scale == "nps" else 0.07
    label_fmt = (lambda v: f"{int(round(v)):d}") if scale == "nps" else _european_format
    for bar, val, ci in zip(bars, df_valid["mean"], cis):
        x_text = val + ci + label_pad
        ax.text(x_text, bar.get_y() + bar.get_height() / 2, label_fmt(val),
                va="center", fontsize=10.5, color="#1F2933")

    if scale == "nps":
        ax.set_xlim(-100, 100)
        ax.axvline(0, color="#3E4C59", linewidth=0.8, alpha=0.5)
    else:
        ax.set_xlim(1, 5)
    ax.set_xlabel(SCALE_AXIS_LABEL_FI.get(scale, "Keskiarvo"), fontsize=10.5, color="#3E4C59")
    ax.invert_yaxis()  # Hamina at top
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_alpha(0.4)
    ax.spines["bottom"].set_alpha(0.4)
    ax.tick_params(axis="x", length=0)
    ax.tick_params(axis="y", length=0, pad=12)
    ax.grid(True, axis="x", linestyle=":", linewidth=0.6, alpha=0.55)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(_european_format))

    # Highlight focal label
    for tick in ax.get_yticklabels():
        if tick.get_text() == focal["label"]:
            tick.set_fontweight("bold")
            tick.set_color(focal.get("highlight_color", "#C8102E"))

    fig.text(0.01, 0.01, cfg["attribution_fi"], fontsize=7.5, color="#52606D", ha="left")
    fig.tight_layout(rect=[0, 0.03, 1, 1])

    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{_safe_filename(indicator_id)}.png"
    fig.savefig(dest, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return dest


def render_all(csv_path: Path, cfg: dict, out_dir: Path) -> dict[str, Path]:
    """Render every indicator. Returns indicator_id -> file path.

    Time-series indicators with fewer than 2 valid years are routed to the
    cross-section renderer (a real time series needs ≥2 points).
    """
    df = pd.read_csv(csv_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    rendered: dict[str, Path] = {}
    ts = df[df["source_type"] == "timeseries"]
    for indicator_id in ts["indicator_id"].unique():
        sub = ts[ts["indicator_id"] == indicator_id]
        valid_years = sub[sub["mean"].notna()]["year"].unique()
        if len(valid_years) >= 2:
            p = render_timeseries_indicator(sub, cfg, out_dir / "timeseries")
        else:
            single_year_sub = sub[sub["mean"].notna()].copy()
            p = render_crosssection_indicator(single_year_sub, cfg, out_dir / "cross_section")
        if p:
            rendered[indicator_id] = p

    cs = df[df["source_type"] == "cross_section"]
    for indicator_id in cs["indicator_id"].unique():
        sub = cs[cs["indicator_id"] == indicator_id]
        p = render_crosssection_indicator(sub, cfg, out_dir / "cross_section")
        if p:
            rendered[indicator_id] = p

    print(f"Rendered {len(rendered)} PNGs into {out_dir}")
    return rendered
