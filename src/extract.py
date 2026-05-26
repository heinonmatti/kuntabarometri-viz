"""Convert raw askia JSON + per-kunta PPTX into one tidy CSV.

Output schema (long format, one row per data point)::

    source_type        timeseries  | cross_section
    kind               theme       | sub_question
    indicator_id       e.g. "Kokonaisranking", "osio1", "subq_03"
    indicator_label_fi human-readable question / theme name
    theme_label_fi     theme this sub-question belongs to (NA for theme rows)
    year               int (2020/2022/2024/2026 for theme rows; 2026 for cross-section)
    kunta_slug         hamina | kotka | kouvola | kokomaa
    kunta_label_fi     "Hamina" | "Kotka" | "Kouvola" | "Koko maa"
    mean               weighted mean of Likert 1-5
    n                  count of respondents (sum of Likert counts excluding "En osaa sanoa")
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pptx import Presentation

LIKERT_SCORES = (1, 2, 3, 4, 5)
EOS_PATTERN = re.compile(r"^en\s*osaa\s*sanoa", re.IGNORECASE)
TRAILING_YEAR_PATTERN = re.compile(r"\s*\(\d{4}\)\s*$")


@dataclass(frozen=True)
class Row:
    source_type: str
    kind: str
    indicator_id: str
    indicator_label_fi: str
    theme_label_fi: str | None
    year: int
    kunta_slug: str
    kunta_label_fi: str
    mean: float | None
    n: int


# ---------------------------------------------------------------------------
# AskiaVista JSON -> theme-level time series rows
# ---------------------------------------------------------------------------


def _category_means(chart: dict) -> list[tuple[str, float | None, int]]:
    """Return [(category_name, mean, n), ...] for one chart payload."""
    cats = [c.get("name") if isinstance(c, dict) else c for c in chart.get("categories", [])]
    series = chart.get("series", [])
    likert_series = [s for s in series if isinstance(s, dict) and _safe_score(s.get("name")) is not None]
    out: list[tuple[str, float | None, int]] = []
    for i, name in enumerate(cats):
        total_weighted = 0.0
        n = 0
        for s in likert_series:
            score = _safe_score(s.get("name"))
            cnt = s["data"][i] if i < len(s.get("data", [])) else 0
            total_weighted += score * cnt
            n += cnt
        mean = (total_weighted / n) if n else None
        out.append((name or "", mean, int(n)))
    return out


def _safe_score(name) -> int | None:
    """Extract the leading Likert score from a series label.

    Names take several forms across themes:
      "5"                                  -> 5
      "5 Kehittynyt merkittävästi ..."     -> 5
      "En osaa sanoa"                      -> None (excluded from mean)
      "Not asked"                          -> None
    """
    if name is None:
        return None
    m = re.match(r"^\s*([1-5])\b", str(name))
    if not m:
        return None
    return int(m.group(1))


def _kokomaa_mean(category_means: list[tuple[str, float | None, int]]) -> tuple[float | None, int]:
    """Weighted mean across all individual kunta rows (excludes 'Keskiarvo (...)' rows)."""
    total = 0.0
    n = 0
    for name, mean, cnt in category_means:
        if not name or name.startswith("Keskiarvo"):
            continue
        if mean is None:
            continue
        total += mean * cnt
        n += cnt
    return (total / n, n) if n else (None, 0)


def _find_kunta_row(category_means: list[tuple[str, float | None, int]], kunta_label: str) -> tuple[float | None, int]:
    for name, mean, cnt in category_means:
        if name and name.startswith(kunta_label + " "):
            return mean, cnt
        if name == kunta_label:
            return mean, cnt
    return None, 0


def askia_rows(json_path: Path, theme: dict, year: int, focal: dict, comparators: list[dict]) -> Iterable[Row]:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    # payload is a list; chart is the dict where type=='chart'
    chart_item = next((c for c in payload if isinstance(c, dict) and c.get("type") == "chart"), None)
    if chart_item is None or not chart_item.get("output"):
        return
    chart = chart_item["output"][0]
    cat_means = _category_means(chart)

    # Focal kunta
    mean, n = _find_kunta_row(cat_means, focal["label"])
    yield Row(
        source_type="timeseries",
        kind="theme",
        indicator_id=theme["askia_rows"],
        indicator_label_fi=theme["label_fi"],
        theme_label_fi=theme["label_fi"],
        year=year,
        kunta_slug=focal["slug"],
        kunta_label_fi=focal["label"],
        mean=mean,
        n=n,
    )
    for comp in comparators:
        if comp.get("is_aggregate"):
            cmean, cn = _kokomaa_mean(cat_means)
        else:
            cmean, cn = _find_kunta_row(cat_means, comp["label"])
        yield Row(
            source_type="timeseries",
            kind="theme",
            indicator_id=theme["askia_rows"],
            indicator_label_fi=theme["label_fi"],
            theme_label_fi=theme["label_fi"],
            year=year,
            kunta_slug=comp["slug"],
            kunta_label_fi=comp["label"],
            mean=cmean,
            n=cn,
        )


# ---------------------------------------------------------------------------
# PPTX -> 2026 sub-question cross-section rows
# ---------------------------------------------------------------------------


def _strip_year_suffix(s: str) -> str:
    return TRAILING_YEAR_PATTERN.sub("", s).strip()


def _pptx_chart_mean(chart) -> tuple[float | None, int, str | None]:
    """Compute the focal (first) category's mean from a PPTX chart.

    PPTX charts have shape: each series corresponds to a Likert score level
    ("5 ...", "4 ...", "3 ...", "2 ...", "1 ...", optionally "En osaa sanoa"),
    each series.data has one value per category, values are percentages.

    The category labels in PPTX charts also include the mean in form '(ka=X.XX)' —
    we parse that directly rather than recomputing.
    """
    cats: list[str] = []
    try:
        plot = chart.plots[0]
        cats = [str(c) for c in plot.categories]
    except Exception:
        cats = []
    means: list[float | None] = []
    ns: list[int] = []
    for cat in cats:
        m = re.search(r"ka=([-\d,\.]+)", cat)
        n_match = re.search(r"n\s*=\s*([\d\s]+)", cat)
        means.append(float(m.group(1).replace(",", ".")) if m else None)
        ns.append(int(n_match.group(1).replace(" ", "")) if n_match else 0)
    if means and means[0] is not None:
        return means[0], ns[0], cats[0] if cats else None
    return None, 0, cats[0] if cats else None


def pptx_rows(pptx_path: Path, kunta_slug: str, kunta_label: str, year: int = 2026) -> Iterable[Row]:
    prs = Presentation(pptx_path)
    seen_titles: dict[str, int] = {}
    for slide_idx, slide in enumerate(prs.slides):
        for shape in slide.shapes:
            if not (hasattr(shape, "has_chart") and shape.has_chart):
                continue
            ch = shape.chart
            if not (hasattr(ch, "has_title") and ch.has_title):
                continue
            try:
                title = ch.chart_title.text_frame.text.strip()
            except Exception:
                title = ""
            if not title:
                continue
            label = _strip_year_suffix(title)
            if not label:
                continue
            mean, n, focal_cat_text = _pptx_chart_mean(ch)
            if mean is None:
                continue
            # Stable id: derived from cleaned title, deduped if needed
            base_id = re.sub(r"[^a-z0-9]+", "_", label.lower())[:60].strip("_")
            count = seen_titles.get(base_id, 0)
            seen_titles[base_id] = count + 1
            indicator_id = f"{base_id}_{count}" if count else base_id
            yield Row(
                source_type="cross_section",
                kind="sub_question",
                indicator_id=f"subq_{slide_idx:02d}_{indicator_id}",
                indicator_label_fi=label,
                theme_label_fi=None,
                year=year,
                kunta_slug=kunta_slug,
                kunta_label_fi=kunta_label,
                mean=mean,
                n=n,
            )


# ---------------------------------------------------------------------------
# Combine everything
# ---------------------------------------------------------------------------


def write_csv(rows: Iterable[Row], dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source_type",
        "kind",
        "indicator_id",
        "indicator_label_fi",
        "theme_label_fi",
        "year",
        "kunta_slug",
        "kunta_label_fi",
        "mean",
        "n",
    ]
    count = 0
    with dest.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "source_type": r.source_type,
                    "kind": r.kind,
                    "indicator_id": r.indicator_id,
                    "indicator_label_fi": r.indicator_label_fi,
                    "theme_label_fi": r.theme_label_fi or "",
                    "year": r.year,
                    "kunta_slug": r.kunta_slug,
                    "kunta_label_fi": r.kunta_label_fi,
                    "mean": "" if r.mean is None else f"{r.mean:.6f}",
                    "n": r.n,
                }
            )
            count += 1
    return count


def build_all(cfg: dict, raw_dir: Path, processed_dir: Path) -> Path:
    focal = cfg["focal_kunta"]
    comparators = cfg["comparators"]
    themes = cfg["themes"]
    years = cfg["years"]

    rows: list[Row] = []

    # 1) askia theme-level time series
    askia_dir = raw_dir / "askia"
    for theme in themes:
        for year in years:
            jp = askia_dir / f"{theme['askia_rows']}_{year}.json"
            if not jp.exists():
                continue
            rows.extend(askia_rows(jp, theme, year, focal, comparators))

    # 2) PPTX cross-section for each kunta
    pptx_dir = raw_dir / "pptx"
    kuntas = [focal] + comparators
    for k in kuntas:
        path = pptx_dir / f"{k['slug']}.pptx"
        if not path.exists():
            continue
        rows.extend(pptx_rows(path, k["slug"], k["label"]))

    dest = processed_dir / "kuntabarometri.csv"
    n = write_csv(rows, dest)
    print(f"Wrote {n:,} rows to {dest}")
    return dest
