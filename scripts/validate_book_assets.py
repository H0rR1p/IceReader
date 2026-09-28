"""Verify that every locally stored chapter image and original preview still exists."""

import argparse
import html
import json
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--repair-previews", action="store_true")
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    database = data_dir / "library.sqlite3"
    with sqlite3.connect(database) as connection:
        chapters = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload FROM records WHERE table_name = 'chapters'"
            )
        ]

    references: list[tuple[str, dict]] = []
    for chapter in chapters:
        references.extend(
            (str(block["asset_url"]), chapter)
            for block in chapter.get("blocks", [])
            if block.get("type") == "image" and block.get("asset_url")
        )
        original_url = chapter.get("originalHtmlUrl") or chapter.get("original_html_url")
        if original_url:
            references.append((str(original_url), chapter))

    prefix = "/api/assets/"
    missing: list[str] = []
    repaired_previews = 0
    for url, chapter in references:
        if not url.startswith(prefix):
            continue
        relative = url[len(prefix):].split("?", 1)[0]
        target = data_dir / "books" / Path(relative)
        if target.is_file():
            continue
        if args.repair_previews and "/documents/" in url and target.suffix.lower() == ".html":
            body: list[str] = []
            for block in chapter.get("blocks", []):
                if block.get("type") == "image" and block.get("asset_url"):
                    body.append(f'<figure><img src="{html.escape(str(block["asset_url"]))}" alt=""></figure>')
                elif block.get("text"):
                    tag = "h2" if block.get("type") == "heading" else "blockquote" if block.get("type") == "quote" else "p"
                    body.append(f"<{tag}>{html.escape(str(block['text']))}</{tag}>")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "<!doctype html><html lang=\"ja\"><meta charset=\"utf-8\">"
                "<style>body{max-width:46rem;margin:2rem auto;padding:0 1.5rem;line-height:2;"
                "font-family:serif}img{max-width:100%;height:auto}blockquote{margin:1em 2em}</style>"
                f"<title>{html.escape(str(chapter.get('title') or '原书预览'))}</title><body>"
                + "".join(body) + "</body></html>",
                encoding="utf-8",
            )
            repaired_previews += 1
        else:
            missing.append(url)

    books_dir = data_dir / "books"
    result = {
        "chapters": len(chapters),
        "managed_book_directories": sum(1 for path in books_dir.iterdir() if path.is_dir()) if books_dir.is_dir() else 0,
        "archived_epubs": len(list(books_dir.glob("*/source/book.epub"))) if books_dir.is_dir() else 0,
        "referenced_resources": len(references),
        "repaired_previews": repaired_previews,
        "missing_resources": len(missing),
        "first_missing": missing[:10],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
