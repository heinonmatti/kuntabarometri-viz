"""Populate output/by-sort/<view-id>/ with rank-prefixed copies of the PNGs.

For each sort view defined in ``config.yaml``, compute the metric per
indicator, rank descending by absolute value (largest gap/change first),
then copy the matching PNG into ``output/by-sort/<view-id>/`` prefixed
with the rank (``01_…``, ``02_…``).

We use file copies rather than symlinks because Windows symlinks are
fiddly and the PNGs are small.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd

from .charts_interactive import compute_metrics
from .charts_static import _safe_filename


def _png_path(out_png: Path, indicator_id: str, source_type: str) -> Path:
    sub = "timeseries" if source_type == "timeseries" else "cross_section"
    return out_png / sub / f"{_safe_filename(indicator_id)}.png"


def build(csv_path: Path, cfg: dict, out_png: Path, by_sort_dir: Path) -> None:
    df = pd.read_csv(csv_path)
    metrics = compute_metrics(df, cfg)
    by_sort_dir.mkdir(parents=True, exist_ok=True)

    for view in cfg["sort_views"]:
        view_dir = by_sort_dir / view["id"]
        if view_dir.exists():
            shutil.rmtree(view_dir)
        view_dir.mkdir(parents=True)

        metric_col = view["metric"]
        # Some metrics (e.g. trend_divergence) only apply to timeseries
        scope = metrics.copy()
        if metric_col.startswith("trend_divergence") or metric_col.startswith("hamina_abs_change"):
            scope = scope[scope["source_type"] == "timeseries"]
        if metric_col not in scope.columns:
            print(f"  [skip] {view['id']}: metric {metric_col!r} not present")
            continue
        ranked = scope.dropna(subset=[metric_col]).copy()
        ranked["sort_key"] = ranked[metric_col].abs()
        ranked = ranked.sort_values("sort_key", ascending=False).reset_index(drop=True)
        if ranked.empty:
            print(f"  [empty] {view['id']}")
            continue
        width = max(2, len(str(len(ranked))))
        copied = 0
        for i, row in ranked.iterrows():
            src = _png_path(out_png, row["indicator_id"], row["source_type"])
            if not src.exists():
                continue
            val = row[metric_col]
            val_str = f"{val:+.2f}".replace(".", ",") if isinstance(val, (int, float)) else "—"
            new_name = f"{str(i + 1).zfill(width)}_{val_str}_{src.name}"
            shutil.copy2(src, view_dir / new_name)
            copied += 1
        print(f"  {view['id']}: copied {copied}/{len(ranked)} → {view_dir}")

    # Index page summarising each view
    index_path = by_sort_dir / "README.md"
    lines = ["# Sort views\n",
             f"Generated from `{csv_path.name}` for focal kunta **{cfg['focal_kunta']['label']}**.\n"]
    for v in cfg["sort_views"]:
        lines.append(f"- `{v['id']}/` – {v['label_fi']} *(metric: `{v['metric']}`)*")
    index_path.write_text("\n".join(lines), encoding="utf-8")
