"""Politique commune de normalisation des identifiants (rapport V2, APP-35).

Une adresse e-mail est comparée et stockée sans espaces et en minuscules, sur
TOUS les chemins : création, modification, imports, scripts et connexion.
L'unicité est garantie en base par un index unique sur lower(email).
"""
from __future__ import annotations

from typing import Optional


def normalize_email(v: Optional[str]) -> Optional[str]:
    """« Awa.Ndiaye@ARED.sn  » → « awa.ndiaye@ared.sn » ; vide → None."""
    if v is None:
        return None
    v = str(v).strip().lower()
    return v or None
