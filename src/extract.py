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
    sd                 standard deviation of the Likert distribution
    se                 standard error of the mean (sd / sqrt(n))
    ci95               half-width of the 95% confidence interval (1.96 * se)
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
    sd: float | None = None
    se: float | None = None
    ci95: float | None = None


def _mean_sd_se_from_counts(counts: dict[int, float]) -> tuple[float | None, float | None, float | None, float | None, int]:
    """Return (mean, sd, se, ci95, n) for a {score: count} dict."""
    n_total = sum(counts.values())
    if n_total <= 0:
        return None, None, None, None, 0
    mean = sum(score * c for score, c in counts.items()) / n_total
    variance = sum(((score - mean) ** 2) * c for score, c in counts.items()) / n_total
    sd = variance ** 0.5
    # SE of the mean uses n - we round to int respondents
    n_int = int(round(n_total))
    se = sd / (n_int ** 0.5) if n_int > 0 else None
    ci95 = 1.96 * se if se is not None else None
    return mean, sd, se, ci95, n_int


# ---------------------------------------------------------------------------
# AskiaVista JSON -> theme-level time series rows
# ---------------------------------------------------------------------------


def _category_stats(chart: dict) -> list[tuple[str, dict[int, float]]]:
    """Return [(category_name, {score: count}), ...] for one askia chart payload.

    Lets the caller compute mean/SD/CI from the per-Likert distribution and
    also aggregate across categories (e.g. national Koko maa pool).
    """
    cats = [c.get("name") if isinstance(c, dict) else c for c in chart.get("categories", [])]
    series = chart.get("series", [])
    likert_series = [s for s in series if isinstance(s, dict) and _safe_score(s.get("name")) is not None]
    out: list[tuple[str, dict[int, float]]] = []
    for i, name in enumerate(cats):
        counts: dict[int, float] = {}
        for s in likert_series:
            score = _safe_score(s.get("name"))
            cnt = s["data"][i] if i < len(s.get("data", [])) else 0
            counts[score] = counts.get(score, 0) + cnt
        out.append((name or "", counts))
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


def _kokomaa_stats(category_stats: list[tuple[str, dict[int, float]]]) -> tuple[float | None, float | None, float | None, float | None, int]:
    """Pool counts across all individual kunta rows (excludes 'Keskiarvo (...)' rows)."""
    pooled: dict[int, float] = {}
    for name, counts in category_stats:
        if not name or name.startswith("Keskiarvo"):
            continue
        for k, v in counts.items():
            pooled[k] = pooled.get(k, 0) + v
    return _mean_sd_se_from_counts(pooled)


def _find_kunta_stats(category_stats: list[tuple[str, dict[int, float]]], kunta_label: str) -> tuple[float | None, float | None, float | None, float | None, int]:
    for name, counts in category_stats:
        if name and (name == kunta_label or name.startswith(kunta_label + " ")):
            return _mean_sd_se_from_counts(counts)
    return None, None, None, None, 0


def askia_rows(json_path: Path, theme: dict, year: int, focal: dict, comparators: list[dict]) -> Iterable[Row]:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    chart_item = next((c for c in payload if isinstance(c, dict) and c.get("type") == "chart"), None)
    if chart_item is None or not chart_item.get("output"):
        return
    chart = chart_item["output"][0]
    cat_stats = _category_stats(chart)

    def _row(kunta_slug: str, kunta_label: str, stats: tuple) -> Row:
        mean, sd, se, ci95, n = stats
        return Row(
            source_type="timeseries",
            kind="theme",
            indicator_id=theme["askia_rows"],
            indicator_label_fi=theme["label_fi"],
            theme_label_fi=theme["label_fi"],
            year=year,
            kunta_slug=kunta_slug,
            kunta_label_fi=kunta_label,
            mean=mean,
            n=n,
            sd=sd,
            se=se,
            ci95=ci95,
        )

    yield _row(focal["slug"], focal["label"], _find_kunta_stats(cat_stats, focal["label"]))
    for comp in comparators:
        stats = _kokomaa_stats(cat_stats) if comp.get("is_aggregate") else _find_kunta_stats(cat_stats, comp["label"])
        yield _row(comp["slug"], comp["label"], stats)


# ---------------------------------------------------------------------------
# PPTX -> 2026 sub-question cross-section rows
# ---------------------------------------------------------------------------


def _strip_year_suffix(s: str) -> str:
    return TRAILING_YEAR_PATTERN.sub("", s).strip()


def _pptx_chart_focal_stats(chart) -> tuple[float | None, float | None, float | None, float | None, int, str | None]:
    """Return (mean, sd, se, ci95, n, focal_cat_label) for the first category.

    PPTX bar-stacked charts have one series per Likert score (named
    "5 Kehittynyt ...", "4 Kehittynyt ...", ..., "1 Kehittynyt ...") plus
    optionally an "En osaa sanoa" series. Each series.data[i] is a
    percentage value summing to 100 across all series for category i.

    We recover counts by multiplying percentages with the total n parsed
    from the category label '(n = X)'. We then compute the mean, SD, SE
    and 95 % CI ourselves rather than trusting the displayed '(ka=...)'
    rounding.
    """
    try:
        plot = chart.plots[0]
        cats = [str(c) for c in plot.categories]
    except Exception:
        return None, None, None, None, 0, None
    if not cats:
        return None, None, None, None, 0, None

    focal_idx = 0
    cat = cats[focal_idx]
    n_match = re.search(r"n\s*=\s*([\d\s]+)", cat)
    n_total = int(n_match.group(1).replace(" ", "")) if n_match else 0
    if n_total == 0:
        return None, None, None, None, 0, cat

    counts: dict[int, float] = {}
    for s in chart.series:
        score = _safe_score(s.name)
        if score is None:
            continue
        try:
            vals = list(s.values)
        except Exception:
            continue
        if focal_idx >= len(vals) or vals[focal_idx] is None:
            continue
        pct = float(vals[focal_idx])
        counts[score] = counts.get(score, 0) + (pct / 100.0) * n_total
    mean, sd, se, ci95, n = _mean_sd_se_from_counts(counts)
    # Fallback: if no Likert series were named "N ..." (e.g. counts chart),
    # parse the printed (ka=...) so we at least keep the mean.
    if mean is None:
        m = re.search(r"ka=([-\d,\.]+)", cat)
        if m:
            mean = float(m.group(1).replace(",", "."))
            n = n_total
    return mean, sd, se, ci95, n, cat


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
            mean, sd, se, ci95, n, _ = _pptx_chart_focal_stats(ch)
            if mean is None:
                continue
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
                sd=sd,
                se=se,
                ci95=ci95,
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
        "sd",
        "se",
        "ci95",
    ]
    count = 0

    def _fmt(v):
        return "" if v is None else f"{v:.6f}"

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
                    "mean": _fmt(r.mean),
                    "n": r.n,
                    "sd": _fmt(r.sd),
                    "se": _fmt(r.se),
                    "ci95": _fmt(r.ci95),
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
