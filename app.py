"""A small, read-only LAN bookshelf. Flask handles HTTP file transfers."""

from __future__ import annotations

import argparse
import ipaddress
import os
import secrets
import socket
import unicodedata
from pathlib import Path
from urllib.parse import quote, urlsplit

from flask import Flask, abort, jsonify, render_template, request, send_from_directory
from waitress import serve
from werkzeug.http import dump_options_header

from conversion import ConversionQueue, find_converter

# EPUB is deliberately excluded: stock Kindle cannot directly read an EPUB download.
BOOK_TYPES = {
    ".mobi": "application/x-mobipocket-ebook",
    ".azw3": "application/x-mobipocket-ebook",
    ".azw": "application/vnd.amazon.ebook",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
}


def book_path(root: Path, relative: str, *, allow_epub: bool = False) -> Path:
    """Only allow visible, non-symlink books inside the selected directory."""
    parts = Path(relative).parts
    if not parts or any(part.startswith(".") for part in parts):
        abort(404)
    candidate = root / relative
    try:
        candidate.resolve(strict=True).relative_to(root)
        current = root
        for part in parts:
            current = current / part
            if current.is_symlink():
                abort(404)
        allowed = set(BOOK_TYPES) | ({".epub"} if allow_epub else set())
        if not candidate.is_file() or candidate.suffix.lower() not in allowed:
            abort(404)
    except (OSError, ValueError, RuntimeError):
        abort(404)
    return candidate


def list_books(root: Path, *, include_epub: bool = False) -> list[dict]:
    books = []
    allowed = set(BOOK_TYPES) | ({".epub"} if include_epub else set())
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = sorted(
            name
            for name in folders
            if not name.startswith(".") and not (Path(directory) / name).is_symlink()
        )
        for name in files:
            path = Path(directory) / name
            if name.startswith(".") or path.is_symlink() or path.suffix.lower() not in allowed:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue  # A book may have been moved while refreshing the page.
            relative = path.relative_to(root).as_posix()
            output = path.with_suffix(".mobi")
            converted = output.is_file() and not output.is_symlink() if include_epub else False
            books.append(
                {
                    "name": name,
                    "title": path.stem,
                    "path": relative,
                    "format": path.suffix[1:].upper(),
                    "epub": path.suffix.lower() == ".epub",
                    "size": f"{size / 1024 / 1024:.2f} MB",
                    "converted": converted,
                    "output": output.relative_to(root).as_posix() if converted else None,
                }
            )
    return sorted(books, key=lambda book: book["path"].casefold())


def create_app(books_dir: str | Path, *, ebook_convert: str | None = None) -> Flask:
    root = Path(books_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"书籍文件夹不存在：{root}")
    app = Flask(__name__)
    queue = ConversionQueue(root, find_converter(ebook_convert))
    app.extensions["conversions"] = queue
    csrf_token = secrets.token_urlsafe(32)

    @app.before_request
    def protect_admin():
        if request.path == "/admin" or request.path.startswith("/admin/"):
            try:
                local = ipaddress.ip_address(request.remote_addr or "").is_loopback
            except ValueError:
                local = False
            if not local:
                abort(403, description="请在运行服务的电脑上通过 127.0.0.1 打开管理页面。")
            if request.method == "POST" and not secrets.compare_digest(
                request.headers.get("X-CSRF-Token", ""), csrf_token
            ):
                abort(403)

    @app.after_request
    def admin_headers(response):
        if request.path.startswith("/admin"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/admin")
    def admin():
        books = list_books(root, include_epub=True)
        counts = {
            "all": len(books),
            "epub": sum(book["epub"] for book in books),
            "ready": sum(not book["epub"] for book in books),
        }
        query = request.args.get("q", "").strip()
        view = request.args.get("view", "all")
        if view not in {"all", "epub", "ready"}:
            view = "all"
        filtered = [
            book
            for book in books
            if (view == "all" or (book["epub"] if view == "epub" else not book["epub"]))
            and query.casefold() in book["path"].casefold()
        ]
        port = urlsplit(request.host_url).port or 80
        kindle_url = (
            app.config.get("KINDLE_URL") or f"http://{lan_address() or '<电脑的局域网 IP>'}:{port}"
        )
        return render_template(
            "admin.html",
            books=filtered,
            counts=counts,
            query=query,
            view=view,
            root=str(root),
            converter=queue.executable,
            jobs=queue.jobs(),
            csrf_token=csrf_token,
            kindle_url=kindle_url,
        )

    @app.post("/admin/convert")
    def convert():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("path"), str):
            return jsonify(error="请选择 EPUB 文件。"), 400
        source = book_path(root, data["path"], allow_epub=True)
        if source.suffix.lower() != ".epub":
            return jsonify(error="只能将 EPUB 转为 MOBI。"), 400
        try:
            job = queue.submit(source)
        except ValueError as error:
            return jsonify(error=str(error)), 409
        return jsonify(job=job), 202

    @app.get("/admin/jobs")
    def conversion_jobs():
        return jsonify(jobs=queue.jobs())

    @app.get("/")
    def index():
        response = app.make_response(render_template("index.html", books=list_books(root)))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/download/<path:relative>")
    def download(relative: str):
        path = book_path(root, relative)
        response = send_from_directory(
            root,
            relative,
            as_attachment=True,
            mimetype=BOOK_TYPES[path.suffix.lower()],
            conditional=True,
        )
        # Older Kindle browsers may ignore RFC 5987 Unicode filenames.
        stem = unicodedata.normalize("NFKD", path.stem).encode("ascii", "ignore").decode()
        fallback = (stem.strip() or "book") + path.suffix.lower()
        response.headers["Content-Disposition"] = (
            dump_options_header("attachment", {"filename": fallback})
            + "; filename*=UTF-8''"
            + quote(path.name, safe="")
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    return app


def lan_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            # Routing lookup only: connect() sends no UDP payload.
            connection.connect(("192.0.2.1", 80))
            address = connection.getsockname()[0]
            return address if not address.startswith("127.") else None
    except OSError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="让 Kindle 通过浏览器下载电脑上的书籍")
    parser.add_argument("--books", type=Path, help="书籍文件夹（含子文件夹），默认 ./books")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=8080, help="端口，默认 8080")
    parser.add_argument("--ebook-convert", help="Calibre ebook-convert 程序路径，默认自动查找")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1 到 65535 之间")
    root = (args.books or Path("books")).expanduser().resolve()
    if args.books is None:
        root.mkdir(exist_ok=True)
    try:
        app = create_app(root, ebook_convert=args.ebook_convert)
    except ValueError as error:
        parser.error(str(error))
    print(f"共享书籍文件夹：{root}", flush=True)
    if args.host == "0.0.0.0":
        print(f"电脑管理：http://127.0.0.1:{args.port}/admin", flush=True)
        address = lan_address()
        if address:
            print(f"Kindle 浏览器打开：http://{address}:{args.port}", flush=True)
        else:
            print(f"Kindle 浏览器打开：http://<电脑的局域网 IPv4 地址>:{args.port}", flush=True)
    else:
        print(f"浏览器地址：http://{args.host}:{args.port}", flush=True)
    print("电脑与 Kindle 请连接同一 Wi-Fi；按 Ctrl+C 停止共享。", flush=True)
    try:
        serve(app, host=args.host, port=args.port, threads=4)
    except OSError as error:
        parser.exit(1, f"服务启动失败：{error}\n")
    finally:
        app.extensions["conversions"].close()


if __name__ == "__main__":
    main()
