import csv
import io
import logging
import re
import uuid
from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.core.redis import get_redis
from app.models.planning import PlanningSegment
from app.models.user import User
from app.schemas.planning import (
    PlanningSegmentCreate, PlanningSegmentUpdate,
    PlanningSegmentResponse, PlanningList,
)

logger = logging.getLogger(__name__)


async def _invalidate_sync_caches() -> None:
    """
    Supprime tous les caches de synchronisation des enseignants (clés sync:*).
    À appeler après toute modification du planning afin que les mobiles
    reçoivent les données fraîches au prochain appel /app/sync.
    """
    try:
        redis = await get_redis()
        keys  = await redis.keys("sync:*")
        if keys:
            deleted = await redis.delete(*keys)
            logger.info("[Planning] Cache Redis invalidé : %d clé(s) supprimée(s)", deleted)
        else:
            logger.info("[Planning] Cache Redis : aucune clé sync:* à supprimer")
    except Exception as exc:
        # Logguer l'erreur clairement — le planning est bien sauvé en DB
        # mais les enseignants devront attendre l'expiration naturelle du cache (TTL)
        logger.error(
            "[Planning] ⚠️  Impossible d'invalider le cache Redis : %s. "
            "Les mobiles recevront les nouvelles données dans au plus %d secondes (TTL).",
            exc,
            300,
        )

# ── Correspondance nom de jour → indice (0 = Lundi) ───────────────────────────
_JOURS_MAP = {
    "lundi": 0, "mardi": 1, "mercredi": 2,
    "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6,
}

def _parse_jour(raw: str) -> int:
    raw = raw.strip()
    if raw.isdigit():
        v = int(raw)
        if v not in range(7):
            raise ValueError(f"Jour hors plage (0–6) : {raw}")
        return v
    v = _JOURS_MAP.get(raw.lower())
    if v is None:
        raise ValueError(f"Jour inconnu : « {raw} »")
    return v

def _h_to_colon(t: str) -> str:
    """Convertit '16h05' → '16:05'  (no-op si déjà '16:05')."""
    return re.sub(r"(\d{1,2})h(\d{2})", r"\1:\2", t)


# ── Parseurs de fichier ────────────────────────────────────────────────────────

def _parse_csv(content: bytes) -> tuple[list[dict], list[dict]]:
    """Retourne (segments, errors) depuis un fichier CSV."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    required = {"jour", "heure_debut", "heure_fin"}
    if reader.fieldnames is None or not required.issubset(
        {f.strip().lower() for f in reader.fieldnames}
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Colonnes requises : {', '.join(sorted(required))}. "
                   f"Colonnes trouvées : {reader.fieldnames}",
        )

    segments: list[dict] = []
    errors:   list[dict] = []
    for line_no, row in enumerate(reader, start=2):
        row = {k.strip().lower(): v.strip() for k, v in row.items() if k}
        try:
            jour = _parse_jour(row.get("jour", ""))
            segments.append({
                "jour":        jour,
                "heure_debut": _h_to_colon(row["heure_debut"]),
                "heure_fin":   _h_to_colon(row["heure_fin"]),
                "matiere":     row.get("matiere") or None,
            })
        except Exception as exc:
            errors.append({"row": line_no, "error": str(exc)})

    return segments, errors


def _parse_pdf(content: bytes) -> tuple[list[dict], list[dict]]:
    """
    Extrait les segments depuis un PDF emploi du temps Ndaw Wune.

    Format reconnu :
        LUNDI          ← nom du jour en majuscules (ligne seule)
        Heure Activité ← en-tête ignoré
        16h00-16h05 Promesse (5 mn)
        16h05-16h40 Leçon Maths (35 mn)
        ...
        MERCREDI
        ...
    """
    try:
        import pdfplumber
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="La bibliothèque pdfplumber n'est pas installée sur le serveur.",
        )

    # Regex pour une ligne de créneau : "16h00-16h05  Activité"
    _TIME_RE = re.compile(
        r"(\d{1,2}h\d{2})\s*[-–]\s*(\d{1,2}h\d{2})\s+(.+)", re.IGNORECASE
    )
    # Durée entre parenthèses à supprimer : "(35 mn)" ou "(35mn)"
    _DUR_RE  = re.compile(r"\s*\(\d+\s*mn?\)", re.IGNORECASE)

    segments: list[dict] = []
    errors:   list[dict] = []
    line_global = 0

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            cur_jour: int | None = None

            for line in text.split("\n"):
                line_global += 1
                # Normaliser : retirer les caractères non-ASCII pour la comparaison
                # des noms de jours (ex : "LUNDI" sans BOM ni espace insécable)
                clean = re.sub(r"[^\x00-\x7F]", "", line).strip()

                # Détecter le nom du jour (insensible à la casse)
                if clean.lower() in _JOURS_MAP:
                    cur_jour = _JOURS_MAP[clean.lower()]
                    continue

                if cur_jour is None:
                    continue  # avant le premier jour → ignorer

                m = _TIME_RE.match(line.strip())
                if not m:
                    continue  # en-tête, ligne vide, etc.

                debut   = _h_to_colon(m.group(1))
                fin     = _h_to_colon(m.group(2))
                matiere = _DUR_RE.sub("", m.group(3)).strip() or None

                try:
                    segments.append({
                        "jour":        cur_jour,
                        "heure_debut": debut,
                        "heure_fin":   fin,
                        "matiere":     matiere,
                    })
                except Exception as exc:
                    errors.append({"row": line_global, "error": str(exc)})

    if not segments and not errors:
        raise HTTPException(
            status_code=422,
            detail="Aucun créneau trouvé dans le PDF. "
                   "Vérifiez que le fichier contient un emploi du temps Ndaw Wune.",
        )

    return segments, errors


# ── Router ─────────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/planning", tags=["Admin — Planning"])


def _enrich(seg: PlanningSegment, teacher_name: str | None = None) -> PlanningSegmentResponse:
    return PlanningSegmentResponse(
        id=seg.id,
        session_id=seg.session_id,
        teacher_id=seg.teacher_id,
        jour=seg.jour,
        heure_debut=seg.heure_debut,
        heure_fin=seg.heure_fin,
        classe=seg.classe,
        matiere=seg.matiere,
        teacher_name=teacher_name,
    )


@router.get("", response_model=PlanningList)
async def list_planning(db: DB, _: AdminUser, session_id: uuid.UUID | None = None):
    q = select(PlanningSegment, User.name).outerjoin(User, User.id == PlanningSegment.teacher_id)
    if session_id:
        q = q.where(PlanningSegment.session_id == session_id)
    result = await db.execute(q.order_by(PlanningSegment.jour, PlanningSegment.heure_debut))
    rows = result.all()
    items = [_enrich(r.PlanningSegment, r.name) for r in rows]
    return PlanningList(total=len(items), items=items)


@router.post("", response_model=PlanningSegmentResponse, status_code=status.HTTP_201_CREATED)
async def create_segment(body: PlanningSegmentCreate, db: DB, _: AdminUser):
    seg = PlanningSegment(**body.model_dump())
    db.add(seg)
    await db.commit()
    await db.refresh(seg)
    await _invalidate_sync_caches()
    teacher = (await db.execute(select(User).where(User.id == seg.teacher_id))).scalar_one_or_none() if seg.teacher_id else None
    return _enrich(seg, teacher.name if teacher else None)


@router.patch("/{seg_id}", response_model=PlanningSegmentResponse)
async def update_segment(seg_id: uuid.UUID, body: PlanningSegmentUpdate, db: DB, _: AdminUser):
    result = await db.execute(select(PlanningSegment).where(PlanningSegment.id == seg_id))
    seg = result.scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="Créneau introuvable.")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(seg, field, value)
    await db.commit()
    await db.refresh(seg)
    await _invalidate_sync_caches()
    teacher = (await db.execute(select(User).where(User.id == seg.teacher_id))).scalar_one_or_none() if seg.teacher_id else None
    return _enrich(seg, teacher.name if teacher else None)


@router.delete("/{seg_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_segment(seg_id: uuid.UUID, db: DB, _: AdminUser) -> Response:
    result = await db.execute(select(PlanningSegment).where(PlanningSegment.id == seg_id))
    seg = result.scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="Créneau introuvable.")
    await db.delete(seg)
    await db.commit()
    await _invalidate_sync_caches()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/import")
async def import_planning(
    db: DB,
    _: AdminUser,
    session_id: uuid.UUID,
    file: UploadFile = File(...),
):
    """
    Importe un planning depuis un fichier PDF ou CSV.

    • PDF : emploi du temps Ndaw Wune (jours en majuscules, créneaux 16h00-16h05)
    • CSV : colonnes jour, heure_debut, heure_fin, matiere

    Retourne { imported: N, errors: [{row, error}] }
    """
    content  = await file.read()
    filename = (file.filename or "").lower()
    ctype    = (file.content_type or "").lower()

    is_pdf = filename.endswith(".pdf") or "pdf" in ctype

    if is_pdf:
        segments, errors = _parse_pdf(content)
    else:
        segments, errors = _parse_csv(content)

    imported = 0
    for seg_data in segments:
        try:
            seg = PlanningSegment(session_id=session_id, **seg_data)
            db.add(seg)
            imported += 1
        except Exception as exc:
            errors.append({"row": "-", "error": str(exc)})

    if imported:
        await db.commit()
        await _invalidate_sync_caches()

    return {"imported": imported, "errors": errors}
