"""Filtres SQLAlchemy réutilisables sur `Eleve.statut_selection` (Base NWV 2026 / RCT).

`statut_selection` vaut `Titulaire` / `Remplaçant` pour les élèves couverts par
l'import RCT (voir `backend/scripts/import_groupes_rct_2026.py`), et NULL pour
les élèves hors périmètre de cet import (écoles non couvertes) — ceux-là sont
traités comme titulaires, sinon ils disparaîtraient purement et simplement des
listes du tuteur et du superviseur.

Ne jamais comparer `statut_selection` par égalité stricte sans normaliser la
casse : la donnée vient d'un import ponctuel, pas d'une saisie contrôlée.
"""
from __future__ import annotations

from sqlalchemy import ColumnElement, func, or_

from app.models.eleve import Eleve

EST_TITULAIRE: ColumnElement[bool] = or_(
    Eleve.statut_selection.is_(None),
    func.lower(Eleve.statut_selection) != "remplaçant",
)

EST_REMPLACANT: ColumnElement[bool] = func.lower(Eleve.statut_selection) == "remplaçant"
