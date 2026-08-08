from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ragx.config import Config
from ragx.factory import build
from ragx.types import Document, Principal, Visibility

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
EVALSET = Path(__file__).resolve().parents[1] / "evalset" / "golden.jsonl"


@pytest.fixture
def corpus_dir() -> Path:
    return CORPUS


@pytest.fixture
def evalset_path() -> Path:
    return EVALSET


@pytest.fixture
def system():
    """Offline system with the sample corpus indexed."""
    sys_ = build(Config(), offline=True)
    sys_.ingest_directory(CORPUS, tenant_id="acme")
    return sys_


@pytest.fixture
def employee() -> Principal:
    return Principal(tenant_id="acme", subject_id="u1", groups=frozenset({"engineering"}))


@pytest.fixture
def finance() -> Principal:
    return Principal(
        tenant_id="acme",
        subject_id="u2",
        groups=frozenset({"finance"}),
        max_visibility=Visibility.CONFIDENTIAL,
    )


def make_document(text: str, doc_id: str = "d1", **kwargs) -> Document:
    defaults = dict(
        doc_id=doc_id,
        tenant_id="acme",
        title="Test Doc",
        text=text,
        uri=f"https://example.test/{doc_id}",
        source="test",
        visibility=Visibility.INTERNAL,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    defaults.update(kwargs)
    return Document(**defaults)
