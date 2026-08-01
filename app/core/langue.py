"""Normalisation des langues nationales d'enseignement.

Les langues sont saisies en texte libre (String(50)) à plusieurs endroits —
écoles, sujets d'évaluation, dossiers d'évaluation, documents — et par des
personnes différentes : on retrouve donc « Wolof », « wolof », « Pulaar »,
« Poular », « Sérère », « seereer »… Une comparaison par égalité stricte ne
fait correspondre que les orthographes rigoureusement identiques (en pratique
seul « wolof », écrit pareil partout), ce qui masquait les contenus des autres
langues aux superviseurs concernés.

`canonical_langue` ramène toutes ces variantes à une clé unique ; `langue_matches`
compare deux valeurs indépendamment de la casse, des accents, des séparateurs et
des orthographes connues.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Variante normalisée → clé canonique. Les clés absentes de cette table sont
# leur propre forme canonique (comparaison casse/accents-insensible uniquement),
# ce qui évite d'avoir à connaître toutes les langues à l'avance.
_ALIASES: dict[str, str] = {
    # Wolof
    "wolof": "wolof",
    "ouolof": "wolof",
    "walaf": "wolof",
    # Pulaar
    "pulaar": "pulaar",
    "poular": "pulaar",
    "pular": "pulaar",
    "peul": "pulaar",
    "peulh": "pulaar",
    "fulfulde": "pulaar",
    "halpulaar": "pulaar",
    "haalpulaar": "pulaar",
    # Seereer
    "seereer": "seereer",
    "sereer": "seereer",
    "serere": "seereer",
    "serer": "seereer",
    "sine": "seereer",
    # Joola
    "joola": "joola",
    "jola": "joola",
    "diola": "joola",
    # Mandinka
    "mandinka": "mandinka",
    "mandinke": "mandinka",
    "mandingue": "mandinka",
    "malinke": "mandinka",
    # Soninke
    "soninke": "soninke",
    "sarakhole": "soninke",
    "sarakole": "soninke",
    # Français (ressources communes)
    "francais": "francais",
    "french": "francais",
}


def strip_accents(value: str) -> str:
    """Retire les diacritiques ('Sérère' → 'Serere')."""
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def normalize_langue(value: Optional[str]) -> Optional[str]:
    """Minuscules, sans accents, sans ponctuation ni espaces superflus.
    Retourne None pour une valeur vide/absente."""
    if not value:
        return None
    cleaned = strip_accents(str(value)).lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or None


def canonical_langue(value: Optional[str]) -> Optional[str]:
    """Clé canonique d'une langue ('Sérère' → 'seereer'). None si indéterminable."""
    normalized = normalize_langue(value)
    if normalized is None:
        return None
    # Les valeurs composées ("wolof / pulaar") ne sont pas gérées : on retient
    # l'alias exact s'il existe, sinon la forme normalisée telle quelle.
    return _ALIASES.get(normalized, _ALIASES.get(normalized.replace(" ", ""), normalized))


def langue_matches(a: Optional[str], b: Optional[str]) -> bool:
    """True si les deux valeurs désignent la même langue. Une valeur vide côté
    contenu signifie « toutes langues » et n'est donc PAS traitée ici : les
    appelants gèrent ce cas explicitement."""
    ca, cb = canonical_langue(a), canonical_langue(b)
    return ca is not None and ca == cb
