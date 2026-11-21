"""A completed paper deletion cannot be undone by a late synchronous writer."""
from io import BytesIO
from pathlib import Path

import pytest

from config.settings import settings
from paper import export, store


@pytest.mark.parametrize("late_write", ["requirement", "guideline", "export", "parsed", "analysis", "similar", "version"])
def test_late_write_does_not_recreate_deleted_paper(tmp_path, monkeypatch, late_write):
    monkeypatch.setattr(settings, "PAPER_DATA_DIR", str(tmp_path / "papers"))
    paper_id = store.new_paper_id()
    store.save_meta(paper_id, {"filename": "deleted.pdf", "title": "Deleted paper"})
    paper_path = Path(store.paper_dir(paper_id))
    assert store.delete_paper(paper_id) is True
    assert not paper_path.exists()

    # A request may already have read this paper before the delete committed.
    # Either reject its stale ID or silently skip an optional export copy.
    try:
        if late_write == "requirement":
            store.save_requirement(paper_id, {"confirmed": True})
        elif late_write == "guideline":
            store.save_rewrite_guide(paper_id, {"summary": "A late guide", "version": 0})
        elif late_write == "parsed":
            store.save_parsed(paper_id, {"paragraphs": []})
        elif late_write == "analysis":
            store.save_analysis(paper_id, "level", {"score": 80})
        elif late_write == "similar":
            store.save_similar(paper_id, {"items": []})
        elif late_write == "version":
            store.save_version(paper_id, [])
        else:
            export._save_export(paper_id, "optimized.docx", BytesIO(b"late export"))
    except (FileNotFoundError, ValueError):
        pass

    assert not paper_path.exists()
    assert store.search_papers()["total"] == 0
