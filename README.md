# kuntabarometri-viz

Time-series visualisations of the Suomen Yrittäjät **Kuntabarometri** (Municipal Barometer) survey, focused on **Hamina** against the rest of Finland and selected regional peers (Kotka, Kouvola).

Source: <https://survey.taloustutkimus.fi/dashboard/kuntabarometri_2026/>
Data points: **2022, 2024, 2026** (biannual survey).

## What it does

1. Downloads per-municipality PPTX files exported by the dashboard (one per kunta plus a national aggregate).
2. Extracts the structured chart data tables embedded in the slides.
3. Builds a tidy long-format CSV (`data/processed/kuntabarometri.csv`).
4. Renders one static PNG per indicator (Finnish labels, Hamina highlighted), and a single interactive HTML.
5. Generates **sorted folder views** so the same charts can be browsed by:
   - Largest absolute change in Hamina (2022 → 2026)
   - Largest gap Hamina vs Koko maa (latest year)
   - Largest gap Hamina vs Kotka (latest year)
   - Largest gap Hamina vs Kouvola (latest year)
   - Largest trend divergence Hamina vs Koko maa

## Configuration

`config.yaml` controls which municipalities to fetch and which acts as the focal kunta. By default:

- Focal: **Hamina**
- Comparators: **Koko maa** (national average), **Kotka**, **Kouvola**

Change `config.yaml` to point at any other town and the whole pipeline re-runs.

## Run

```powershell
python -m pip install -r requirements.txt
python -m src.run_all
```

Outputs land under `output/`.

## Repository layout

```
config.yaml            Focal kunta + comparators + sort metrics
src/
  fetch.py             Download PPTX files (cached)
  extract.py           PPTX → tidy CSV
  charts_static.py     CSV → PNG per indicator
  charts_interactive.py CSV → single HTML dashboard
  sort_views.py        Compute sort metrics + populate by-sort/ folders
  run_all.py           Pipeline entry-point
data/
  raw/                 Downloaded PPTX files (gitignored)
  processed/           Tidy CSV (gitignored)
output/
  png/                 One PNG per indicator
  html/                Interactive dashboard
  by-sort/             Symlinked or rank-prefixed copies of PNGs per sort view
```
