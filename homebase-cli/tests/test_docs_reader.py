from homebase_cli.docs_reader import get_doc, list_docs


def test_list_docs_contains_current_state() -> None:
    keys = [entry.key for entry in list_docs()]
    assert "current-state" in keys


def test_get_doc_by_stem() -> None:
    entry = get_doc("system-overview")
    assert entry is not None
    assert entry.filename == "system-overview.md"
