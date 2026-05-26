"""End-to-end pipeline.

Usage::

    python -m src.run_all                 # full pipeline (cached askia + cached pptx)
    python -m src.run_all --refetch       # re-fetch askia and pptx
    python -m src.run_all --no-fetch      # only re-process from existing raw data
    python -m src.run_all --only fetch    # only the fetch step
    python -m src.run_all --only extract  # only the extract step
    python -m src.run_all --only charts   # only the chart generation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from . import askia_client, fetch_pptx, extract

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUT_DIR = ROOT / "output"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def step_fetch(cfg: dict, *, force: bool) -> None:
    # PPTX
    kunta_specs = [cfg["focal_kunta"], *cfg["comparators"]]
    paths = fetch_pptx.fetch_all_pptx(
        cfg["dashboard_base_url"],
        kunta_specs,
        RAW_DIR,
        force=force,
    )
    print(f"pptx: {len(paths)} files in data/raw/pptx/")

    # AskiaVista
    askia_client.fetch_all(
        cfg["themes"],
        cfg["years"],
        RAW_DIR,
        force=force,
    )


def step_extract(cfg: dict) -> Path:
    return extract.build_all(cfg, RAW_DIR, PROCESSED_DIR)


def step_charts(cfg: dict, csv_path: Path) -> None:
    try:
        from . import charts_static, charts_interactive, sort_views
    except ImportError as e:
        print(f"chart modules not yet implemented: {e}")
        return
    charts_static.render_all(csv_path, cfg, OUTPUT_DIR / "png")
    charts_interactive.render(csv_path, cfg, OUTPUT_DIR / "html" / "kuntabarometri.html")
    sort_views.build(csv_path, cfg, OUTPUT_DIR / "png", OUTPUT_DIR / "by-sort")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refetch", action="store_true", help="Force re-download")
    parser.add_argument("--no-fetch", action="store_true", help="Skip fetch step")
    parser.add_argument("--only", choices=["fetch", "extract", "charts"], help="Run only one step")
    args = parser.parse_args()

    cfg = load_config()

    if args.only is None or args.only == "fetch":
        if not args.no_fetch:
            step_fetch(cfg, force=args.refetch)

    csv_path = PROCESSED_DIR / "kuntabarometri.csv"
    if args.only is None or args.only == "extract":
        csv_path = step_extract(cfg)

    if args.only is None or args.only == "charts":
        if not csv_path.exists():
            print("No processed CSV; run extract first.", file=sys.stderr)
            sys.exit(1)
        step_charts(cfg, csv_path)


if __name__ == "__main__":
    main()
