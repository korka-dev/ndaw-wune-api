import csv
import io
import logging
import re
import uuid
from datetime import datetime as _dt
from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from sqlalchemy import delete, func, select

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.core.redis import get_redis
from app.models.planning import PlanningSegment
from app.models.user import User
from app.schemas.planning import (
    PlanningSegmentCreate, PlanningSegmentUpdate,
    PlanningSegmentResponse,
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


def _parse_excel(content: bytes) -> tuple[list[dict], list[dict]]:
    """Retourne (segments, errors) depuis un fichier Excel (.xlsx, .xls) via openpyxl."""
    import datetime

    try:
        import openpyxl
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="La bibliothèque openpyxl n'est pas installée sur le serveur.",
        )

    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        ws = wb.active
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Impossible de lire le fichier Excel : {e}",
        )

    all_rows = list(ws.iter_rows(values_only=True))
    if not all_rows:
        raise HTTPException(status_code=400, detail="Fichier Excel vide.")

    header = [str(c).strip().lower() if c is not None else "" for c in all_rows[0]]
    required = {"jour", "heure_debut", "heure_fin"}

    if not required.issubset(set(h for h in header if h)):
        raise HTTPException(
            status_code=400,
            detail=f"Colonnes requises : {', '.join(sorted(required))}. "
                   f"Colonnes trouvées : {[h for h in header if h]}",
        )

    def _cell_to_str(val) -> str:
        """Convertit une valeur de cellule openpyxl en chaîne."""
        if val is None:
            return ""
        if isinstance(val, datetime.time):
            return val.strftime("%H:%M")
        if isinstance(val, datetime.datetime):
            return val.strftime("%H:%M")
        return str(val).strip()

    segments: list[dict] = []
    errors:   list[dict] = []

    for line_no, row in enumerate(all_rows[1:], start=2):
        try:
            row_dict = {
                header[i]: _cell_to_str(v)
                for i, v in enumerate(row)
                if i < len(header) and header[i]
            }

            raw_jour  = row_dict.get("jour", "")
            raw_debut = row_dict.get("heure_debut", "")
            raw_fin   = row_dict.get("heure_fin", "")
            raw_mat   = row_dict.get("matiere", "") or None

            # Sauter les lignes vides
            if not raw_jour or not raw_debut or not raw_fin or raw_jour.lower() in ("none", "nan"):
                continue

            # Gérer "1.0" → "1" pour les jours numériques issus d'Excel
            if raw_jour.endswith(".0"):
                raw_jour = raw_jour[:-2]

            jour = _parse_jour(raw_jour)
            segments.append({
                "jour":        jour,
                "heure_debut": _h_to_colon(raw_debut),
                "heure_fin":   _h_to_colon(raw_fin),
                "matiere":     raw_mat if raw_mat and raw_mat.lower() not in ("none", "nan") else None,
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
        semaine=seg.semaine,
        jour=seg.jour,
        heure_debut=seg.heure_debut,
        heure_fin=seg.heure_fin,
        classe=seg.classe,
        matiere=seg.matiere,
        teacher_name=teacher_name,
    )


@router.get("", response_model=Page[PlanningSegmentResponse])
async def list_planning(
    db: DB,
    _: AdminUser,
    page: Pagination,
    session_id: uuid.UUID | None = None,
    semaine: int | None = None,
) -> Page[PlanningSegmentResponse]:
    base = (
        select(PlanningSegment, User.name)
        .outerjoin(User, User.id == PlanningSegment.teacher_id)
        .order_by(PlanningSegment.semaine.nulls_first(), PlanningSegment.jour, PlanningSegment.heure_debut)
    )
    count_q = select(PlanningSegment)
    if session_id:
        base    = base.where(PlanningSegment.session_id == session_id)
        count_q = count_q.where(PlanningSegment.session_id == session_id)
    if semaine is not None:
        base    = base.where(PlanningSegment.semaine == semaine)
        count_q = count_q.where(PlanningSegment.semaine == semaine)

    total = (await db.execute(
        select(func.count()).select_from(count_q.subquery())
    )).scalar_one()

    result = await db.execute(base.offset(page.skip).limit(page.limit))
    items  = [_enrich(r.PlanningSegment, r.name) for r in result.all()]
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


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
    # exclude_unset (et non exclude_none) : permet d'effacer la semaine en envoyant null
    for field, value in body.model_dump(exclude_unset=True).items():
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
    semaine: int | None = None,
    file: UploadFile = File(...),
):
    """
    Importe un planning depuis un fichier PDF, Excel ou CSV.
    Si `semaine` est fournie, le planning importé est rattaché à cette semaine
    et seul le planning existant de cette semaine est remplacé.

    • PDF : emploi du temps Ndaw Wune (jours en majuscules, créneaux 16h00-16h05)
    • Excel / CSV : colonnes jour, heure_debut, heure_fin, matiere

    Retourne { imported: N, errors: [{row, error}] }
    """
    content  = await file.read()
    filename = (file.filename or "").lower()
    ctype    = (file.content_type or "").lower()

    is_pdf = filename.endswith(".pdf") or "pdf" in ctype
    is_excel = filename.endswith(".xlsx") or filename.endswith(".xls") or "excel" in ctype or "officedocument.spreadsheetml" in ctype

    if is_pdf:
        segments, errors = _parse_pdf(content)
    elif is_excel:
        segments, errors = _parse_excel(content)
    else:
        segments, errors = _parse_csv(content)

    # Supprimer l'ancien planning (remplacement complet — limité à la semaine si fournie)
    del_stmt = delete(PlanningSegment).where(PlanningSegment.session_id == session_id)
    if semaine is not None:
        del_stmt = del_stmt.where(PlanningSegment.semaine == semaine)
    await db.execute(del_stmt)

    imported = 0
    for seg_data in segments:
        try:
            # Les parseurs retournent des strings "HH:MM" — asyncpg exige datetime.time
            seg_data["heure_debut"] = _dt.strptime(seg_data["heure_debut"], "%H:%M").time()
            seg_data["heure_fin"]   = _dt.strptime(seg_data["heure_fin"],   "%H:%M").time()
            seg = PlanningSegment(session_id=session_id, semaine=semaine, **seg_data)
            db.add(seg)
            imported += 1
        except Exception as exc:
            errors.append({"row": "-", "error": str(exc)})

    try:
        await db.commit()
        await _invalidate_sync_caches()
    except Exception as exc:
        await db.rollback()
        logger.error("[Planning Import] Erreur au commit : %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Erreur lors de la sauvegarde du planning. Veuillez réessayer.",
        )

    return {"imported": imported, "errors": errors}
