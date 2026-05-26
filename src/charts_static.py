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


def _safe_filename(s: str, max_len: int = 80) -> str:
    s = re.sub(r"[^a-zA-Z0-9äöåÄÖÅ_-]+", "_", s).strip("_")
    return s[:max_len]


def _european_format(x, _pos=None):
    return f"{x:.2f}".replace(".", ",")


def _wrap_title(title: str, width: int = 80) -> str:
    return "\n".join(textwrap.wrap(title, width=width))


def _style_axes(ax, attribution: str) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_alpha(0.4)
    ax.spines["bottom"].set_alpha(0.4)
    ax.tick_params(axis="both", length=0)
    ax.grid(True, axis="y", linestyle=":", linewidth=0.6, alpha=0.55)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_european_format))
    ax.set_ylim(1, 5)
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

    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), dpi=DPI)
    title = f"{indicator_label}"
    ax.set_title(_wrap_title(title), fontsize=13.5, fontweight="bold", color="#1F2933", loc="left", pad=18)
    ax.set_xlabel("Vuosi", fontsize=10.5, color="#3E4C59")
    ax.set_ylabel("Keskiarvo (asteikko 1–5)", fontsize=10.5, color="#3E4C59")

    # Plot focal last so it sits on top
    series_order = list(kunta_specs.values())
    series_order.sort(key=lambda s: s.get("is_focal", False))
    handles, labels = [], []
    for spec in series_order:
        sub = df_valid[df_valid["kunta_slug"] == spec["slug"]]
        if sub.empty:
            continue
        style = _series_style(spec, spec.get("is_focal", False))
        color = style["color"]
        # Error bars: 95% CI as vertical whiskers
        ci = sub["ci95"].fillna(0).to_numpy()
        ax.errorbar(
            sub["year"], sub["mean"], yerr=ci,
            fmt="none", ecolor=color, elinewidth=1.2, capsize=4, capthick=1.0,
            alpha=0.9 if spec.get("is_focal") else 0.55,
            zorder=style["zorder"] - 1,
        )
        line, = ax.plot(sub["year"], sub["mean"], label=spec["label"], **style)
        handles.append(line)
        labels.append(spec["label"])
        if spec.get("is_focal"):
            last = sub.iloc[-1]
            ax.annotate(
                _european_format(last["mean"]),
                (last["year"], last["mean"]),
                textcoords="offset points",
                xytext=(10, 4),
                color=color,
                fontweight="bold",
                fontsize=10.5,
            )

    years_present = sorted(df_valid["year"].unique())
    ax.set_xticks(years_present)
    ax.set_xticklabels([str(y) for y in years_present], fontsize=10)

    leg = ax.legend(handles, labels, loc="lower right", frameon=False, fontsize=10)
    for text in leg.get_texts():
        if text.get_text() == focal["label"]:
            text.set_fontweight("bold")
            text.set_color(focal.get("highlight_color"))

    _style_axes(ax, cfg["attribution_fi"])
    fig.tight_layout(rect=[0, 0.03, 1, 1])

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
    kunta_specs_list = [focal] + comparators
    kunta_specs_map = {focal["slug"]: {**focal, "is_focal": True}}
    for c in comparators:
        kunta_specs_map[c["slug"]] = {**c, "is_focal": False}

    df_valid = df_ind[df_ind["mean"].notna()]
    if df_valid.empty:
        return None
    # Ordered: focal first
    ordered = [focal["slug"]] + [c["slug"] for c in comparators]
    df_valid = df_valid.set_index("kunta_slug").reindex([s for s in ordered if s in df_valid["kunta_slug"].values])
    df_valid = df_valid.reset_index()

    if df_valid.empty:
        return None

    fig, ax = plt.subplots(figsize=(FIG_W, max(2.5, 1.0 + 0.55 * len(df_valid))), dpi=DPI)
    title = f"{indicator_label}  (2026)"
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
    # Annotate bar ends, accounting for error bar extent
    for bar, val, ci in zip(bars, df_valid["mean"], cis):
        ax.text(val + ci + 0.07, bar.get_y() + bar.get_height() / 2, _european_format(val),
                va="center", fontsize=10.5, color="#1F2933")

    ax.set_xlim(1, 5)
    ax.set_xlabel("Keskiarvo (asteikko 1–5)", fontsize=10.5, color="#3E4C59")
    ax.invert_yaxis()  # Hamina at top
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_alpha(0.4)
    ax.spines["bottom"].set_alpha(0.4)
    ax.tick_params(axis="both", length=0)
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


def render_all(csv_path: Path, cfg: dict, out_dir: Path) -> list[Path]:
    df = pd.read_csv(csv_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    rendered: list[Path] = []
    # Theme-level time series
    ts = df[df["source_type"] == "timeseries"]
    for indicator_id in ts["indicator_id"].unique():
        sub = ts[ts["indicator_id"] == indicator_id]
        p = render_timeseries_indicator(sub, cfg, out_dir / "timeseries")
        if p:
            rendered.append(p)

    # Cross-section sub-questions
    cs = df[df["source_type"] == "cross_section"]
    for indicator_id in cs["indicator_id"].unique():
        sub = cs[cs["indicator_id"] == indicator_id]
        p = render_crosssection_indicator(sub, cfg, out_dir / "cross_section")
        if p:
            rendered.append(p)

    print(f"Rendered {len(rendered)} PNGs into {out_dir}")
    return rendered
