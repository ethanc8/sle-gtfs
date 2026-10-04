"""Render every source PDF (including sources/older_examples/) to PNG, for
checking extracted/*.csv cells against the actual timetable by eye."""

import argparse
from pathlib import Path

import pdfplumber

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = REPO_ROOT / "sources"


def render_all(out_dir: Path, resolution=150):
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_paths = sorted(SOURCES_DIR.glob("*.pdf")) + sorted(SOURCES_DIR.glob("older_examples/*.pdf"))
    for pdf_path in pdf_paths:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                out_path = out_dir / f"{pdf_path.stem}-{i + 1}.png"
                page.to_image(resolution=resolution).save(out_path)
                print(f"  {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "build" / "pdf-preview"))
    parser.add_argument("--resolution", type=int, default=150)
    args = parser.parse_args()
    render_all(Path(args.out_dir), args.resolution)


if __name__ == "__main__":
    main()
