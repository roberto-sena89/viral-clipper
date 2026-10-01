"""Build the standalone static SEO site without exposing the local app API."""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent


def site_origin(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    local_hosts = ("localhost", "127.", "10.", "192.168.", "172.16.", "172.17.",
                   "172.18.", "172.19.", "172.2", "172.30.", "172.31.")
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")
            or host == "localhost" or host.endswith((".localhost", ".local"))
            or host.startswith(local_hosts)):
        raise ValueError("SITE_URL must be a public HTTPS origin, e.g. https://my-site.com")
    return f"https://{parsed.netloc}"


def configured_origin() -> str:
    value = os.environ.get("VIRAL_CLIPPER_SITE_URL", "").strip()
    if not value:
        vercel_domain = (
            os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "").strip()
            or os.environ.get("VERCEL_URL", "").strip()
        )
        value = f"https://{vercel_domain}" if vercel_domain else ""
    return site_origin(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    origin = configured_origin()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "assets", output / "assets", dirs_exist_ok=True)

    for name in ("index.html", "docs.html"):
        template = (ROOT / f"{name}.template").read_text(encoding="utf-8")
        canonical = origin + ("/" if name == "index.html" else "/docs.html")
        (output / name).write_text(
            template.replace("{{SITE_URL}}", origin).replace("{{CANONICAL_URL}}", canonical),
            encoding="utf-8",
        )

    (output / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {origin}/sitemap.xml\n", encoding="utf-8"
    )
    pages = (origin + "/", origin + "/docs.html")
    urls = "\n".join(f"  <url><loc>{escape(url)}</loc></url>" for url in pages)
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n</urlset>\n"
    )
    (output / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    print(f"Static site and local design assets generated in {output}")


if __name__ == "__main__":
    main()
