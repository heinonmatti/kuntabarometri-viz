"""AskiaVista client driven by Playwright.

Opens the Kuntabarometri dashboard once (which configures the askia
JavaScript library with a valid session and rotating auth token), then
invokes ``askiaVista.getPages`` from inside the page with custom
parameters. Returned data is the raw chart payload (Likert counts per
category) for one (theme, year) pair.

The dashboard's own chart-rendering pipeline is broken in headless mode
(it never populates ``Highcharts.charts``), but the askia data fetch
itself works fine. We bypass rendering entirely.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

DASHBOARD_URL = "https://survey.taloustutkimus.fi/dashboard/kuntabarometri_2026/#/home"

GET_PAGES_TEMPLATE = """
() => new Promise((resolve) => {{
  try {{
    askiaVista.getPages({{
      id: {chart_id_json},
      containerId: {chart_id_json},
      rows: {rows_json},
      profileColumns: "RANKING_KUNNAT",
      profileEdges: {edges_json},
      level: {level},
      chart: {{ name: "Highcharts", options: {{ chartType: "line" }} }},
      calculations: [{{ type: "PercentageX" }}, {{ type: "CountsX" }}, {{ type: "MeanX" }}, {{ type: "NX" }}],
      success: function (strData) {{ resolve({{ ok: true, data: strData }}); }},
      error: function (err) {{ resolve({{ ok: false, err: JSON.stringify(err).slice(0, 800) }}); }}
    }});
    setTimeout(() => resolve({{ ok: false, err: "timeout" }}), 60000);
  }} catch (e) {{ resolve({{ ok: false, err: e.message }}); }}
}})
"""


@contextmanager
def askia_session(headless: bool = True, warmup_ms: int = 15000):
    """Open dashboard, wait for askia to be ready, yield the page."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(warmup_ms)
        try:
            yield page
        finally:
            browser.close()


def fetch_theme_year(page, theme_rows: str, theme_level: int, year: int) -> dict:
    """Fetch one (theme, year) chart payload.

    Returns the parsed JSON from askiaVista.getPages. Raises on failure.
    """
    chart_id = f"viz_{theme_rows}_{year}"
    script = GET_PAGES_TEMPLATE.format(
        chart_id_json=json.dumps(chart_id),
        rows_json=json.dumps([theme_rows]),
        edges_json=json.dumps(f"ROUND_{year}"),
        level=theme_level,
    )
    res = page.evaluate(script)
    if not res.get("ok"):
        raise RuntimeError(f"askia getPages failed for {theme_rows}/{year}: {res.get('err')}")
    return json.loads(res["data"])


def cache_path(raw_dir: Path, theme_rows: str, year: int) -> Path:
    return raw_dir / "askia" / f"{theme_rows}_{year}.json"


def fetch_respondent_counts(
    focal: dict,
    comparators: list[dict],
    years: list[int],
    raw_dir: Path,
    *,
    force: bool = False,
) -> dict:
    """Return {year: {kunta_slug: respondent_n}} for each (year, kunta) pair.

    Queries the 'Sukupuoli' (gender) baseline question cross-tabbed with
    RANKING_KUNNAT and ROUND_YYYY. Summing answer counts across gender
    options for a given kunta row gives the true respondent count
    (everyone answers gender, with negligible attrition).
    """
    cache_file = raw_dir / "askia" / "_respondent_counts.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    if cache_file.exists() and not force:
        return json.loads(cache_file.read_text(encoding="utf-8"))

    script = """
    ([rows, edges, columns]) => new Promise((resolve) => {
      try {
        askiaVista.getPages({
          id: "rn_probe", containerId: "rn_probe",
          rows: [rows],
          profileColumns: columns,
          profileEdges: edges,
          level: 0,
          chart: { name: "Highcharts", options: { chartType: "line" } },
          calculations: [{ type: "PercentageX" }, { type: "CountsX" }, { type: "MeanX" }, { type: "NX" }],
          success: function (strData) { resolve({ ok: true, data: strData }); },
          error: function (err) { resolve({ ok: false, err: JSON.stringify(err).slice(0, 400) }); }
        });
        setTimeout(() => resolve({ ok: false, err: "timeout" }), 30000);
      } catch (e) { resolve({ ok: false, err: e.message }); }
    })
    """
    out: dict[str, dict[str, int]] = {}
    with askia_session() as page:
        for year in years:
            res = page.evaluate(script, ["Sukupuoli", f"ROUND_{year}", "RANKING_KUNNAT"])
            if not res.get("ok"):
                print(f"  respondent-count fetch failed for {year}: {res.get('err')}")
                continue
            data = json.loads(res["data"])
            chart_item = next((c for c in data if c.get("type") == "chart"), None)
            if not chart_item or not chart_item.get("output"):
                continue
            chart = chart_item["output"][0]
            cats = [c.get("name") for c in chart.get("categories", [])]
            per_cat: dict[str, float] = {}
            for s in chart.get("series", []):
                for i, n in enumerate(s.get("data", [])):
                    per_cat[cats[i]] = per_cat.get(cats[i], 0) + n
            year_counts: dict[str, int] = {}
            for k in [focal, *comparators]:
                if k.get("is_aggregate"):
                    n = sum(per_cat[c] for c in cats if c and not c.startswith("Keskiarvo"))
                else:
                    row = next((c for c in cats if c and (c == k["label"] or c.startswith(k["label"] + " "))), None)
                    n = per_cat.get(row, 0) if row else 0
                year_counts[k["slug"]] = int(round(n))
            out[str(year)] = year_counts
            print(f"  respondent counts {year}: " + ", ".join(f"{s}={n}" for s, n in year_counts.items()))
    cache_file.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def fetch_all(themes: list[dict], years: list[int], raw_dir: Path, *, force: bool = False, sleep_between: float = 0.3) -> list[Path]:
    """Fetch every (theme, year) pair, cache to disk, return list of file paths."""
    out_dir = raw_dir / "askia"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    todo = [(t, y) for t in themes for y in years]
    cached = [tp for tp in todo if not force and cache_path(raw_dir, tp[0]["askia_rows"], tp[1]).exists()]
    todo_uncached = [tp for tp in todo if force or not cache_path(raw_dir, tp[0]["askia_rows"], tp[1]).exists()]
    print(f"askia: {len(cached)} cached, {len(todo_uncached)} to fetch")
    paths.extend(cache_path(raw_dir, t["askia_rows"], y) for t, y in cached)
    if not todo_uncached:
        return paths
    with askia_session() as page:
        for t, y in todo_uncached:
            cp = cache_path(raw_dir, t["askia_rows"], y)
            try:
                payload = fetch_theme_year(page, t["askia_rows"], t["level"], y)
                cp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                paths.append(cp)
                print(f"  fetched {t['askia_rows']} / {y} -> {cp.name} ({cp.stat().st_size:,} bytes)")
            except Exception as e:
                print(f"  FAIL  {t['askia_rows']} / {y}: {e}")
            time.sleep(sleep_between)
    return paths
