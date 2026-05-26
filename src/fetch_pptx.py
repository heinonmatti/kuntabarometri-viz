"""Download (and cache) the 2026 per-municipality PPTX exports.

These contain the sub-question cross-section (one slide per indicator,
Hamina/Kotka/Kouvola/Kokomaa vs all-respondents distribution + mean).
Only 2026 data — historical years live in the askia API path.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path


def download_pptx(base_url: str, source_path: str, dest: Path, *, force: bool = False) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0 and not force:
        return dest
    url = f"{base_url}/{source_path}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as out:
        out.write(resp.read())
    return dest


def fetch_all_pptx(base_url: str, kunta_specs: list[dict], raw_dir: Path, *, force: bool = False) -> dict[str, Path]:
    """kunta_specs is a list of dicts with slug+pptx_source. Returns slug→Path."""
    out: dict[str, Path] = {}
    for spec in kunta_specs:
        dest = raw_dir / "pptx" / f"{spec['slug']}.pptx"
        out[spec["slug"]] = download_pptx(base_url, spec["pptx_source"], dest, force=force)
    return out
