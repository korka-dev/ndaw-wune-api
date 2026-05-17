"""Paramètres de pagination réutilisables sur tous les endpoints de liste."""
# PAS de 'from __future__ import annotations' ici :
# les annotations de PageParams.__init__ doivent rester des objets réels
# pour que FastAPI/Pydantic 2 puisse les résoudre sans ForwardRef.

from typing import Annotated, Generic, List, TypeVar

from fastapi import Depends, Query
from pydantic import BaseModel

from app.core.config import settings

T = TypeVar("T")


class PageParams:
    """Injecté via Depends() dans les routes qui retournent une liste paginée."""

    def __init__(
        self,
        skip: int = Query(
            default=0,
            ge=0,
            description="Nombre d'éléments à sauter",
        ),
        limit: int = Query(
            default=settings.DEFAULT_PAGE_SIZE,
            ge=1,
            le=settings.MAX_PAGE_SIZE,
            description="Nombre max d'éléments à retourner",
        ),
    ) -> None:
        self.skip = skip
        self.limit = limit


# Alias typé pour injection dans les routes
Pagination = Annotated[PageParams, Depends(PageParams)]


class Page(BaseModel, Generic[T]):
    """Réponse paginée générique."""
    total: int
    skip:  int
    limit: int
    items: List[T]
