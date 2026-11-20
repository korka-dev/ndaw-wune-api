"""
Compatibilité application mobile / API (rapport V2, APP-46).

L'application envoie sa version dans l'en-tête X-App-Version. Si
MIN_APP_VERSION est défini et que la version annoncée est plus ancienne, les
routes /api/v1/app/* répondent 426 avec detail="APP_UPDATE_REQUIRED" :
l'application affiche alors un écran de mise à jour SANS effacer sa file
d'attente (les saisies repartent après la mise à jour).

Les clients qui n'envoient pas encore l'en-tête (APK déjà distribués) ne sont
jamais bloqués par ce mécanisme : la transition se fait en publiant d'abord une
version qui envoie l'en-tête, puis en relevant MIN_APP_VERSION.
"""
from __future__ import annotations

import re

_NUM = re.compile(r"\d+")


def parse_version(v: str | None) -> tuple[int, ...] | None:
    if not v:
        return None
    nums = _NUM.findall(v.split("+", 1)[0].split("-", 1)[0])
    if not nums:
        return None
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - min(3, len(nums)))


def is_outdated(client_version: str | None, min_version: str | None) -> bool:
    """True seulement si les deux versions sont lisibles et client < minimum."""
    client, minimum = parse_version(client_version), parse_version(min_version)
    if client is None or minimum is None:
        return False
    return client < minimum
