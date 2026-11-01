from urllib.parse import quote, unquote

import pytest

from app import create_app


@pytest.fixture
def shelf(tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    (root / "中文书名.azw3").write_bytes(b"test book content")
    (root / "nested").mkdir()
    (root / "nested" / "a book.MOBI").write_bytes(b"0123456789")
    (root / "unsupported.epub").write_bytes(b"epub")
    (root / "secret.env").write_text("secret")
    (root / ".hidden.txt").write_text("hidden")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside secret")
    (root / "linked.txt").symlink_to(outside)
    (root / "linked-directory").symlink_to(tmp_path, target_is_directory=True)
    return root, create_app(root).test_client()


def test_listing_is_plain_html_and_refreshes(shelf):
    root, client = shelf
    response = client.get("/")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "中文书名.azw3" in html and "a book.MOBI" in html
    assert "unsupported.epub" not in html and "secret.env" not in html
    assert "linked.txt" not in html and ".hidden.txt" not in html
    assert "<script" not in html
    assert response.headers["Cache-Control"] == "no-store"
    (root / "new.mobi").write_bytes(b"new")
    assert "new.mobi" in client.get("/").get_data(as_text=True)


def test_unicode_download_and_old_browser_fallback(shelf):
    _, client = shelf
    response = client.get("/download/" + quote("中文书名.azw3"))
    assert response.data == b"test book content"
    assert response.mimetype == "application/x-mobipocket-ebook"
    disposition = response.headers["Content-Disposition"]
    assert "filename=book.azw3" in disposition
    assert "中文书名.azw3" in unquote(disposition)
    assert response.headers["Content-Length"] == str(len(response.data))


def test_nested_download_head_and_range(shelf):
    _, client = shelf
    url = "/download/nested/a%20book.MOBI"
    assert client.get(url).data == b"0123456789"
    response = client.get(url, headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.data == b"2345"
    assert client.head(url).headers["Content-Length"] == "10"


@pytest.mark.parametrize(
    "relative",
    [
        "../outside.txt",
        "%2e%2e/outside.txt",
        "nested/../../outside.txt",
        "linked.txt",
        "linked-directory/outside.txt",
        "secret.env",
        "unsupported.epub",
        ".hidden.txt",
        "missing.mobi",
        "nested",
    ],
)
def test_only_selected_books_can_be_downloaded(shelf, relative):
    _, client = shelf
    assert client.get("/download/" + relative).status_code == 404


def test_html_filename_is_escaped(shelf):
    root, client = shelf
    (root / "<script>alert(1)<script>.txt").write_text("book")
    assert "<script>" not in client.get("/").get_data(as_text=True)


def test_missing_folder_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="书籍文件夹不存在"):
        create_app(tmp_path / "missing")
