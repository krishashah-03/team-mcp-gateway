import pytest
from mcp.server.mcpserver.exceptions import ToolError

from docs_search_mcp import config
from docs_search_mcp.server import list_documents, read_document, search_documents


@pytest.fixture
def docs(tmp_path, monkeypatch):
    """An isolated documents folder, with files that must never be served."""
    (tmp_path / "returns.md").write_text("# Returns\nItems may be returned within 30 days for a refund.", encoding="utf-8")
    (tmp_path / "shipping.txt").write_text("Shipping takes 3 to 5 days. Refund of shipping is not offered.", encoding="utf-8")
    (tmp_path / "notes.pdf").write_text("not a supported type", encoding="utf-8")
    (tmp_path / ".hidden.md").write_text("# Hidden\nrefund", encoding="utf-8")
    (tmp_path / "huge.md").write_text("refund " * 200_000, encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.md").write_text("refund nested", encoding="utf-8")
    outside = tmp_path.parent / "outside-secret.md"
    outside.write_text("TOP SECRET refund", encoding="utf-8")

    monkeypatch.setenv("DOCS_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()
    outside.unlink(missing_ok=True)


def test_only_supported_regular_files_are_listed(docs):
    # Not listed: other file types, hidden files, oversized files, and subfolders.
    assert {d["name"] for d in list_documents()} == {"returns.md", "shipping.txt"}


def test_titles_come_from_the_first_heading(docs):
    assert {d["name"]: d["title"] for d in list_documents()}["returns.md"] == "Returns"


def test_search_ranks_and_snippets(docs):
    results = search_documents("refund")
    assert results and results[0]["name"] in {"returns.md", "shipping.txt"}
    assert all("refund" in r["snippet"].lower() for r in results)
    assert results == sorted(results, key=lambda r: r["score"], reverse=True)


def test_search_never_leaks_files_outside_the_folder_or_nested(docs):
    names = {r["name"] for r in search_documents("refund", limit=20)}
    assert "outside-secret.md" not in names and "nested.md" not in names and "huge.md" not in names


def test_search_limit_and_no_hits(docs):
    assert len(search_documents("refund", limit=1)) == 1
    assert search_documents("zzzqqq") == []


@pytest.mark.parametrize("query", ["!!", "  ", "?"])
def test_queries_without_words_are_rejected(docs, query):
    with pytest.raises(ToolError):
        search_documents(query if len(query) >= 2 else query + query)


@pytest.mark.parametrize("name,expected", [("returns.md", "returns.md"), ("RETURNS", "returns.md"), (" shipping ", "shipping.txt")])
def test_read_by_name_or_stem(docs, name, expected):
    assert read_document(name)["name"] == expected


@pytest.mark.parametrize(
    "evil",
    [
        "../outside-secret.md",
        "..\\outside-secret.md",
        "../../etc/passwd",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "returns.md/../../outside-secret.md",
        "sub/nested.md",
        "notes.pdf",
        "huge.md",
        "returns.md\x00.txt",
        ".hidden.md",
        "",
        "*",
    ],
)
def test_path_traversal_and_unlisted_files_are_refused(docs, evil):
    with pytest.raises(ToolError, match="No document named"):
        read_document(evil)


def test_symlinks_are_not_followed(docs):
    link = docs / "link.md"
    try:
        link.symlink_to(docs.parent / "outside-secret.md")
    except (OSError, NotImplementedError):
        pytest.skip("this system does not allow creating symlinks")
    assert "link.md" not in {d["name"] for d in list_documents()}
    with pytest.raises(ToolError):
        read_document("link.md")


def test_a_missing_folder_means_no_documents(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(tmp_path / "does-not-exist"))
    config.get_settings.cache_clear()
    try:
        assert list_documents() == [] and search_documents("anything") == []
    finally:
        config.get_settings.cache_clear()
