import re
import subprocess
import sys
import threading
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest

from app import create_app
from conversion import find_converter


def write_epub(path):
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        )
        archive.writestr(
            "content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="id">kindle-share-test</dc:identifier><dc:title>传书测试</dc:title>
<dc:language>zh-CN</dc:language><dc:creator>Kindle Share</dc:creator></metadata>
<manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
<spine><itemref idref="chapter"/></spine></package>""",
        )
        archive.writestr(
            "chapter.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>传书测试</title></head>
<body><h1>传书测试</h1><p>这是一本用于验证 EPUB 转 MOBI 的测试书籍。</p></body></html>""",
        )


@pytest.fixture
def manager(tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    source = root / "中文书籍.epub"
    write_epub(source)
    app = create_app(root, ebook_convert=sys.executable)
    app.config["TESTING"] = True
    client = app.test_client()
    html = client.get("/admin").get_data(as_text=True)
    token = re.search(r'name="csrf-token" content="([^"]+)"', html)[1]
    yield root, app, client, {"X-CSRF-Token": token}
    app.extensions["conversions"].close()


def success_converter(command, **kwargs):
    Path(command[2]).write_bytes(b"\0" * 60 + b"BOOKMOBI" + b"fake test payload")
    return subprocess.CompletedProcess(command, 0)


def test_admin_shows_epub_but_kindle_does_not(manager):
    _, _, client, _ = manager
    assert "中文书籍.epub" in client.get("/admin").get_data(as_text=True)
    assert "中文书籍.epub" not in client.get("/").get_data(as_text=True)
    assert client.get("/download/中文书籍.epub").status_code == 404


def test_admin_is_local_and_post_requires_token(manager):
    _, _, client, headers = manager
    for path in ("/admin", "/admin/jobs"):
        assert (
            client.get(path, environ_overrides={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
        )
    assert client.post("/admin/convert", json={"path": "中文书籍.epub"}).status_code == 403
    assert (
        client.post(
            "/admin/convert",
            json={"path": "中文书籍.epub"},
            headers=headers,
            environ_overrides={"REMOTE_ADDR": "192.168.1.20"},
        ).status_code
        == 403
    )
    assert client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.20"}).status_code == 200


def test_convert_publishes_download_preserves_source_and_never_overwrites(manager, monkeypatch):
    root, app, client, headers = manager
    source_bytes = (root / "中文书籍.epub").read_bytes()
    monkeypatch.setattr("conversion.subprocess.run", success_converter)
    result = client.post("/admin/convert", json={"path": "中文书籍.epub"}, headers=headers)
    assert result.status_code == 202
    app.extensions["conversions"].close()
    jobs = client.get("/admin/jobs").json["jobs"]
    assert jobs[0]["status"] == "done"
    assert (root / "中文书籍.epub").read_bytes() == source_bytes
    assert client.get("/download/中文书籍.mobi").data == (root / "中文书籍.mobi").read_bytes()
    assert "中文书籍.mobi" in client.get("/").get_data(as_text=True)
    assert "已生成 MOBI" in client.get("/admin").get_data(as_text=True)
    assert not list(root.glob(".kindle-convert-*"))
    before = (root / "中文书籍.mobi").read_bytes()
    assert (
        client.post("/admin/convert", json={"path": "中文书籍.epub"}, headers=headers).status_code
        == 409
    )
    assert (root / "中文书籍.mobi").read_bytes() == before


@pytest.mark.parametrize("failure", ["exit", "timeout", "missing", "race"])
def test_conversion_failures_do_not_publish_partial_files(manager, monkeypatch, failure):
    root, app, client, headers = manager

    def broken_converter(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 300)
        if failure == "race":
            (root / "中文书籍.mobi").write_bytes(b"existing book")
            return success_converter(command, **kwargs)
        if failure == "exit":
            Path(command[2]).write_bytes(b"partial")
            kwargs["stdout"].write(b"invalid EPUB")
            return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("conversion.subprocess.run", broken_converter)
    assert (
        client.post("/admin/convert", json={"path": "中文书籍.epub"}, headers=headers).status_code
        == 202
    )
    app.extensions["conversions"].close()
    assert client.get("/admin/jobs").json["jobs"][0]["status"] == "failed"
    if failure == "race":
        assert (root / "中文书籍.mobi").read_bytes() == b"existing book"
    else:
        assert not (root / "中文书籍.mobi").exists()
    assert not list(root.glob(".kindle-convert-*"))


def test_repeated_click_reuses_active_job(manager, monkeypatch):
    _, app, client, headers = manager
    release = threading.Event()

    def slow_converter(command, **kwargs):
        assert release.wait(5)
        return success_converter(command, **kwargs)

    monkeypatch.setattr("conversion.subprocess.run", slow_converter)
    try:
        first = client.post("/admin/convert", json={"path": "中文书籍.epub"}, headers=headers)
        second = client.post("/admin/convert", json={"path": "中文书籍.epub"}, headers=headers)
        assert first.json["job"]["id"] == second.json["job"]["id"]
        assert len(client.get("/admin/jobs").json["jobs"]) == 1
    finally:
        release.set()
        app.extensions["conversions"].close()


def test_filter_search_and_malformed_requests(manager):
    root, _, client, headers = manager
    (root / "ready.mobi").write_bytes(b"book")
    assert "中文书籍.epub" not in client.get("/admin?view=ready").get_data(as_text=True)
    assert "ready.mobi" not in client.get("/admin?view=epub").get_data(as_text=True)
    assert "ready.mobi" not in client.get("/admin?q=missing").get_data(as_text=True)
    for value in (None, [], {"path": []}, {"path": "ready.mobi"}):
        assert client.post("/admin/convert", json=value, headers=headers).status_code == 400
    for value in ("../outside.epub", ".secret.epub", "missing.epub"):
        assert (
            client.post("/admin/convert", json={"path": value}, headers=headers).status_code == 404
        )


def test_missing_calibre_is_actionable(tmp_path):
    (tmp_path / "book.epub").write_bytes(b"book")
    app = create_app(tmp_path, ebook_convert=str(tmp_path / "not-installed"))
    client = app.test_client()
    html = client.get("/admin").get_data(as_text=True)
    assert "需要安装 Calibre" in html
    token = re.search(r'name="csrf-token" content="([^"]+)"', html)[1]
    response = client.post(
        "/admin/convert", json={"path": "book.epub"}, headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 409 and "Calibre" in response.json["error"]
    app.extensions["conversions"].close()


@pytest.mark.skipif(find_converter() is None, reason="Calibre is not installed")
def test_real_calibre_epub_to_mobi(tmp_path):
    source = tmp_path / "真实转换测试.epub"
    write_epub(source)
    original = source.read_bytes()
    app = create_app(tmp_path)
    try:
        app.extensions["conversions"].submit(source)
        app.extensions["conversions"].close()
        job = app.extensions["conversions"].jobs()[0]
        assert job["status"] == "done", job["message"]
        mobi = source.with_suffix(".mobi").read_bytes()
        assert mobi[60:68] == b"BOOKMOBI"
        assert len(mobi) > 1024
        assert source.read_bytes() == original
        assert app.test_client().get("/download/真实转换测试.mobi").data == mobi
    finally:
        app.extensions["conversions"].close()
