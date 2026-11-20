"""Photos des rapports journaliers (rapport V2, APP-37).

Les photos sont stockées en base en data URI base64 (jusqu'à 3 par rapport,
1 à 3 Mo au total). Les listes — admin comme mobile — n'en transportent que le
nombre ; chaque image se charge à la demande, décodée en binaire.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Optional

from fastapi import HTTPException, status
from fastapi.responses import Response
from sqlalchemy import func

from app.models.rapport_journalier import RapportJournalier


# Nombre de photos calculé DANS Postgres, sans rapatrier le base64 : on compte
# les occurrences du préfixe « data: » dans le texte. Volontairement tolérant —
# un JSON malformé donnerait 0 plutôt que de faire échouer toute la requête,
# ce que ferait un json_array_length().
def compte_data_uri(col):
    return func.coalesce(
        (func.length(col) - func.length(func.replace(col, "data:", ""))) / 5, 0
    )


# GREATEST des deux colonnes plutôt qu'un repli : un `photos_classe_url` valant
# « [] » compterait 0 et masquerait la photo de l'ancienne colonne.
NB_PHOTOS = func.greatest(
    compte_data_uri(RapportJournalier.photos_classe_url),
    compte_data_uri(RapportJournalier.photo_classe_url),
)

LIST_COLUMNS = [
    RapportJournalier.id, RapportJournalier.teacher_id, RapportJournalier.date_rapport,
    RapportJournalier.ief, RapportJournalier.commune, RapportJournalier.ecole,
    RapportJournalier.superviseur, RapportJournalier.nom_tuteur,
    RapportJournalier.nb_absences, RapportJournalier.absents,
    RapportJournalier.semaine, RapportJournalier.jour_cours,
    RapportJournalier.difficultes, RapportJournalier.autres_difficultes,
    RapportJournalier.description_difficultes, RapportJournalier.directeur_venu,
    RapportJournalier.besoin_appui, RapportJournalier.domaines_appui,
    RapportJournalier.has_observations, RapportJournalier.commentaires,
    RapportJournalier.soumis_en_offline, RapportJournalier.reponses_questions,
    RapportJournalier.created_at,
    NB_PHOTOS.label("nb_photos"),
]


def photos_de(photos_json: Optional[str], photo_unique: Optional[str]) -> list[str]:
    """Les deux colonnes coexistent : `photos_classe_url` (liste JSON, jusqu'à
    3 photos) a remplacé `photo_classe_url` (une seule), gardée pour les
    rapports anciens."""
    if photos_json:
        try:
            parsed = json.loads(photos_json)
            if isinstance(parsed, list):
                urls = [u for u in parsed if isinstance(u, str) and u]
                if urls:
                    return urls
        except (ValueError, TypeError):
            pass
    return [photo_unique] if photo_unique else []


_DATA_URI = re.compile(r"^data:([\w.+-]+/[\w.+-]+)?;base64,(.+)$", re.DOTALL)


_DATA_URI = re.compile(r"^data:([\w.+-]+/[\w.+-]+)?;base64,(.+)$", re.DOTALL)


def photo_response(photos_json: Optional[str], photo_unique: Optional[str], index: int) -> Response:
    """Sert UNE photo, décodée en binaire.

    Le stockage est un data URI base64 : le renvoyer tel quel coûterait 33 % de
    plus et ne serait pas mis en cache. On décode, on annonce le vrai type MIME,
    et on autorise le cache privé — l'image d'un rapport ne change jamais.
    """
    urls = photos_de(photos_json, photo_unique)
    if index < 0 or index >= len(urls):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo introuvable.")

    m = _DATA_URI.match(urls[index].strip())
    if not m:
        # Ancien format : une vraie URL. On la renvoie au client, à lui d'aller
        # la chercher — le serveur ne relaie pas de ressource externe.
        return Response(content=urls[index], media_type="text/plain")
    try:
        binaire = base64.b64decode(m.group(2), validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Photo illisible.")

    return Response(
        content=binaire,
        media_type=_type_reel(binaire, m.group(1)),
        headers={"Cache-Control": "private, max-age=86400"},
    )


def _type_reel(binaire: bytes, declare: Optional[str]) -> str:
    """Type MIME déduit des octets, pas de l'étiquette du data URI.

    L'app mobile annonce systématiquement `image/jpeg`, y compris pour des PNG
    (constaté en base : un data URI « data:image/jpeg;base64,iVBORw0KGgo… », or
    iVBORw est la signature PNG). Les navigateurs corrigent d'eux-mêmes, mais un
    téléchargement ou un outil tiers se fierait à l'en-tête.
    """
    if binaire.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if binaire.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if binaire.startswith(b"GIF8"):
        return "image/gif"
    if binaire[:4] == b"RIFF" and binaire[8:12] == b"WEBP":
        return "image/webp"
    return declare or "image/jpeg"
