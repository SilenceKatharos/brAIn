#!/usr/bin/env python3
"""Download the Wikipedia + GitHub README corpus.

Idempotent: skips files that already exist on disk unless --force is given.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests
import wikipediaapi

ROOT = Path(__file__).resolve().parent.parent
WIKI_DIR = ROOT / "corpus" / "wikipedia"
GITHUB_DIR = ROOT / "corpus" / "github"
USER_AGENT = "brAIn-experiment/0.1 (https://github.com/local; contact: local)"

# Allow running this file directly without packaging.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus import GITHUB_READMES, WIKI_ARTICLES, first_n_wiki  # noqa: E402


def _download_wiki(article, wiki, out_dir: Path, force: bool) -> dict:
    out = out_dir / f"{article.slug}.md"
    if out.exists() and not force:
        return {"slug": article.slug, "status": "skipped", "bytes": out.stat().st_size}
    page = wiki.page(article.title)
    if not page.exists():
        return {"slug": article.slug, "status": "missing", "title": article.title}
    body = f"# {page.title}\n\n{page.text}\n"
    out.write_text(body, encoding="utf-8")
    return {"slug": article.slug, "status": "ok", "bytes": len(body)}


def _download_github(readme, out_dir: Path, force: bool) -> dict:
    out = out_dir / f"{readme.slug}.md"
    if out.exists() and not force:
        return {"slug": readme.slug, "status": "skipped", "bytes": out.stat().st_size}
    candidates = [
        f"https://raw.githubusercontent.com/{readme.repo}/{readme.branch}/README.md",
        f"https://raw.githubusercontent.com/{readme.repo}/main/README.md",
        f"https://raw.githubusercontent.com/{readme.repo}/master/README.md",
    ]
    last_err = None
    for url in candidates:
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            if r.status_code == 200 and r.text.strip():
                out.write_text(r.text, encoding="utf-8")
                return {"slug": readme.slug, "status": "ok", "bytes": len(r.text), "url": url}
            last_err = f"{url} -> HTTP {r.status_code}"
        except requests.RequestException as exc:  # pragma: no cover
            last_err = f"{url} -> {exc}"
    return {"slug": readme.slug, "status": "failed", "error": last_err}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--mini", type=int, default=0,
        help="If > 0, only download the first N Wikipedia articles (skip GitHub).",
    )
    p.add_argument("--force", action="store_true", help="Re-download even if already on disk.")
    p.add_argument("--skip-github", action="store_true", help="Wikipedia only.")
    args = p.parse_args()

    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    GITHUB_DIR.mkdir(parents=True, exist_ok=True)

    wiki = wikipediaapi.Wikipedia(USER_AGENT, language="en")
    wiki_targets = first_n_wiki(args.mini) if args.mini > 0 else WIKI_ARTICLES

    print(f"Wikipedia: {len(wiki_targets)} article(s) -> {WIKI_DIR}")
    for i, article in enumerate(wiki_targets, 1):
        result = _download_wiki(article, wiki, WIKI_DIR, args.force)
        line = f"  [{i:>2}/{len(wiki_targets)}] {article.slug}  {result['status']}"
        if "bytes" in result:
            line += f"  ({result['bytes']:,} bytes)"
        if result.get("status") == "missing":
            line += f"  (title not found: {result['title']})"
        print(line)
        time.sleep(0.05)  # be polite

    if not args.skip_github and args.mini == 0:
        print(f"\nGitHub READMEs: {len(GITHUB_READMES)} -> {GITHUB_DIR}")
        for readme in GITHUB_READMES:
            result = _download_github(readme, GITHUB_DIR, args.force)
            line = f"  {readme.slug:<28} {result['status']}"
            if "bytes" in result:
                line += f"  ({result['bytes']:,} bytes)"
            if result.get("error"):
                line += f"  ERROR {result['error']}"
            print(line)


if __name__ == "__main__":
    main()
