"""
Tests unitaires — app.core.pagination

Couvre :
  - PageParams : valeurs par défaut, validation ge/le
  - Page[T] : construction et champs
"""
from __future__ import annotations

import pytest

from app.core.pagination import Page, PageParams


# ── PageParams ────────────────────────────────────────────────────────────────

class TestPageParams:
    def test_defaults(self):
        p = PageParams()
        assert p.skip == 0
        assert p.limit == 50   # DEFAULT_PAGE_SIZE dans les settings test

    def test_custom_values(self):
        p = PageParams(skip=20, limit=10)
        assert p.skip == 20
        assert p.limit == 10

    def test_skip_zero_ok(self):
        p = PageParams(skip=0, limit=5)
        assert p.skip == 0

    def test_limit_min_one(self):
        """limit=1 est la valeur minimale autorisée (ge=1)."""
        p = PageParams(skip=0, limit=1)
        assert p.limit == 1

    def test_limit_max(self):
        """limit=200 est la valeur maximale autorisée (le=MAX_PAGE_SIZE)."""
        p = PageParams(skip=0, limit=200)
        assert p.limit == 200

    def test_large_skip(self):
        p = PageParams(skip=10_000, limit=50)
        assert p.skip == 10_000


# ── Page[T] ───────────────────────────────────────────────────────────────────

class TestPage:
    def test_page_with_items(self):
        items = ["a", "b", "c"]
        page: Page[str] = Page(total=10, skip=0, limit=3, items=items)
        assert page.total == 10
        assert page.items == items
        assert len(page.items) == 3

    def test_empty_page(self):
        page: Page[str] = Page(total=0, skip=0, limit=50, items=[])
        assert page.total == 0
        assert page.items == []

    def test_page_preserves_skip_and_limit(self):
        page: Page[str] = Page(total=100, skip=25, limit=25, items=["x"] * 25)
        assert page.skip == 25
        assert page.limit == 25

    def test_page_typed_items(self):
        from app.schemas.eleve import EleveResponse
        import uuid
        item = EleveResponse(id=uuid.uuid4(), nom="Diallo", prenom=None, classe="CI",
                             school_id=None, session_id=None)
        page: Page[EleveResponse] = Page(total=1, skip=0, limit=50, items=[item])
        assert page.items[0].nom == "Diallo"
