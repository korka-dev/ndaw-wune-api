import csv
import io
import json
import logging
import re
import uuid
from datetime import datetime as _dt
from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, delete, func, select, true

from app.core.export_utils import sanitize_cell
from app.core.journal import pile, resume_erreur
from app.core.upload_utils import read_upload_capped, iter_rows_capped, load_workbook_safe
from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.core.redis import invalidate_sync_caches as _invalidate_sync_caches
from app.models.planning import PlanningSegment
from app.models.school import School
from app.models.session import ProgramSession
from app.services.audit_service import journaliser
from app.services.fls_service import (
    ecoles_sans_type, est_creneau_fls, filtre_type_ecole, lire_repartition, rapprocher, resume_public,
)
from app.models.user import User
from app.schemas.planning import (
    PlanningSegmentCreate, PlanningSegmentUpdate,
    PlanningSegmentResponse,
)

logger = logging.getLogger(__name__)

# ── Correspondance nom de jour → indice (0 = Jour 1) ─────────────────────────
# Accepte les noms français classiques ET le format séquentiel "Jour 1/2/..."
_JOURS_MAP = {
    "lundi": 0, "mardi": 1, "mercredi": 2,
    "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6,
    # Format séquentiel (Jour 1 → 0, Jour 2 → 1, ...)
    **{f"jour {i}": i - 1 for i in range(1, 8)},
    **{f"j{i}": i - 1 for i in range(1, 8)},
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

    wb = load_workbook_safe(content)
    ws = wb.active

    all_rows = list(iter_rows_capped(ws))
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
        for page_no, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            logger.info("[PDF] Page %d — %d caractères extraits", page_no + 1, len(text))
            cur_jour: int | None = None

            for line in text.split("\n"):
                line_global += 1
                clean = re.sub(r"[^\x00-\x7F]", "", line).strip()
                logger.debug("[PDF] ligne %d : %r  →  clean=%r", line_global, line, clean)

                # Détecter le nom du jour (insensible à la casse)
                if clean.lower() in _JOURS_MAP:
                    cur_jour = _JOURS_MAP[clean.lower()]
                    logger.info("[PDF] Jour détecté : %r → index %d", clean, cur_jour)
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
                    logger.info("[PDF] Créneau : jour=%d %s-%s %s", cur_jour, debut, fin, matiere)
                except Exception as exc:
                    errors.append({"row": line_global, "error": str(exc)})

    logger.info("[PDF] Résultat : %d créneau(x), %d erreur(s)", len(segments), len(errors))

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
        reserve_fls=bool(seg.reserve_fls),
        teacher_name=teacher_name,
    )


@router.get("", response_model=Page[PlanningSegmentResponse])
async def list_planning(
    db: DB,
    _: AdminUser,
    page: Pagination,
    session_id: uuid.UUID | None = None,
    semaine: int | None = None,
    type_ecole: str | None = Query(None, description="Aperçu : tous | fls | sans_fls (lot L9)"),
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
    visibilite = filtre_type_ecole(type_ecole)
    if visibilite is not None:
        base    = base.where(visibilite)
        count_q = count_q.where(visibilite)

    total = (await db.execute(
        select(func.count()).select_from(count_q.subquery())
    )).scalar_one()

    result = await db.execute(base.offset(page.skip).limit(page.limit))
    items  = [_enrich(r.PlanningSegment, r.name) for r in result.all()]
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.get("/export/csv")
async def export_planning_csv(
    db: DB,
    _: AdminUser,
    session_id: uuid.UUID | None = None,
    semaine: int | None = None,
    jour: int | None = Query(None, ge=0, le=6),
    matiere: str | None = None,
) -> StreamingResponse:
    """Export CSV du planning (mêmes filtres que l'écran — campagne, semaine,
    jour, matière — SANS pagination ; rapport V2, APP-34).
    Colonnes compatibles avec l'import : semaine, jour, heure_debut, heure_fin, classe, matiere."""
    q = select(PlanningSegment).order_by(
        PlanningSegment.semaine.nulls_first(), PlanningSegment.jour, PlanningSegment.heure_debut
    )
    if session_id:
        q = q.where(PlanningSegment.session_id == session_id)
    if semaine is not None:
        q = q.where(PlanningSegment.semaine == semaine)
    if jour is not None:
        q = q.where(PlanningSegment.jour == jour)
    if matiere:
        q = q.where(PlanningSegment.matiere == matiere)
    segments = (await db.execute(q)).scalars().all()

    buf = io.StringIO()
    buf.write("\ufeff")  # BOM : accents corrects à l'ouverture dans Excel
    writer = csv.writer(buf)
    writer.writerow(["semaine", "jour", "heure_debut", "heure_fin", "classe", "matiere"])
    for seg in segments:
        writer.writerow([
            "" if seg.semaine is None else seg.semaine,
            f"Jour {seg.jour + 1}",   # relisible tel quel par l'import
            seg.heure_debut.strftime("%H:%M"),
            seg.heure_fin.strftime("%H:%M"),
            sanitize_cell(seg.classe or ""),
            sanitize_cell(seg.matiere or ""),
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="planning.csv"'},
    )


@router.post("", response_model=PlanningSegmentResponse, status_code=status.HTTP_201_CREATED)
async def create_segment(body: PlanningSegmentCreate, db: DB, _: AdminUser):
    data = body.model_dump()
    if data.get("reserve_fls") is None:
        data["reserve_fls"] = est_creneau_fls(data.get("matiere"))
    seg = PlanningSegment(**data)
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
    champs = body.model_dump(exclude_unset=True)
    if champs.get("reserve_fls") is None:
        champs.pop("reserve_fls", None)
    for field, value in champs.items():
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
    semaine: int | None = Query(None, ge=1, le=53),
    dry_run: bool = Query(False, description="Aperçu : rien n'est modifié."),
    accepter_erreurs: bool = Query(
        False, description="Confirme le remplacement même si des lignes du fichier sont invalides.",
    ),
    file: UploadFile = File(...),
):
    """
    Importe un planning depuis un fichier PDF, Excel ou CSV (rapport V2, APP-26).

    Tout le fichier est validé avant la moindre modification. Le planning
    existant n'est jamais touché si : le fichier ne contient aucun créneau
    valide ; `dry_run=true` (aperçu du périmètre et des créneaux) ; des lignes
    sont invalides et `accepter_erreurs` n'est pas confirmé (409
    `PLANNING_IMPORT_PARTIEL`). Le remplacement se fait dans une transaction
    unique, limité à la campagne et à la semaine annoncées.
    Si `semaine` est fournie, le planning importé est rattaché à cette semaine
    et seul le planning existant de cette semaine est remplacé.

    • PDF : emploi du temps Ndaw Wune (jours en majuscules, créneaux 16h00-16h05)
    • Excel / CSV : colonnes jour, heure_debut, heure_fin, matiere

    Retourne { imported: N, errors: [{row, error}] }
    """
    content  = await read_upload_capped(file)
    filename = (file.filename or "").lower()
    ctype    = (file.content_type or "").lower()
    if _est_repartition_fls(content, filename):
        raise HTTPException(status_code=422, detail={
            "code": "FICHIER_REPARTITION_FLS",
            "message": "Ce fichier est la répartition des écoles avec / sans FLS : utilisez « Importer la répartition FLS ».",
        })

    is_pdf = filename.endswith(".pdf") or "pdf" in ctype
    is_excel = filename.endswith(".xlsx") or filename.endswith(".xls") or "excel" in ctype or "officedocument.spreadsheetml" in ctype

    if is_pdf:
        segments, errors = _parse_pdf(content)
    elif is_excel:
        segments, errors = _parse_excel(content)
    else:
        segments, errors = _parse_csv(content)

    # ── Validation COMPLÈTE avant toute suppression ──────────────────────────
    # Un fichier vide ou illisible ne doit jamais effacer le planning existant.
    valides: list[dict] = []
    for seg_data in segments:
        try:
            # Les parseurs retournent des strings "HH:MM" — asyncpg exige datetime.time
            seg_data["heure_debut"] = _dt.strptime(seg_data["heure_debut"], "%H:%M").time()
            seg_data["heure_fin"]   = _dt.strptime(seg_data["heure_fin"],   "%H:%M").time()
            if seg_data["heure_fin"] <= seg_data["heure_debut"]:
                raise ValueError(
                    f"l'heure de fin ({seg_data['heure_fin']:%H:%M}) doit suivre "
                    f"l'heure de début ({seg_data['heure_debut']:%H:%M})."
                )
            valides.append(seg_data)
        except Exception as exc:
            errors.append({"row": "-", "error": str(exc)})

    if not valides:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Aucun créneau valide dans le fichier : le planning existant n'a PAS été modifié.",
                "errors": errors,
            },
        )

    session = await db.get(ProgramSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Campagne introuvable : planning non modifié.")

    perimetre = and_(PlanningSegment.session_id == session_id,
                     PlanningSegment.semaine == semaine if semaine is not None else true())
    remplaces = (await db.execute(select(func.count()).select_from(PlanningSegment).where(perimetre))).scalar_one()
    apercu = {
        "dry_run": dry_run,
        "session_id": str(session_id),
        "session_nom": session.name,
        "semaine": semaine,
        "perimetre": f"{session.name} — " + (f"semaine {semaine}" if semaine is not None else "toutes les semaines"),
        "a_remplacer": remplaces,
        "valides": len(valides),
        "segments": [
            {**seg, "heure_debut": seg["heure_debut"].strftime("%H:%M"), "heure_fin": seg["heure_fin"].strftime("%H:%M")}
            for seg in valides[:200]
        ],
        "errors": errors,
    }
    if dry_run:
        return apercu
    if errors and not accepter_erreurs:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PLANNING_IMPORT_PARTIEL",
                "message": f"{len(errors)} ligne(s) invalide(s) : confirmez pour remplacer le planning par les "
                           f"{len(valides)} créneau(x) valide(s), ou corrigez le fichier. Planning non modifié.",
                **apercu,
            },
        )

    # Remplacement limité au périmètre annoncé, dans la même transaction que l'insertion
    await db.execute(delete(PlanningSegment).where(perimetre))

    imported = 0
    for seg_data in valides:
        db.add(PlanningSegment(session_id=session_id, semaine=semaine,
                               reserve_fls=est_creneau_fls(seg_data.get("matiere")), **seg_data))
        imported += 1

    try:
        await db.commit()
        await _invalidate_sync_caches()
    except Exception as exc:
        await db.rollback()
        logger.error("[Planning Import] Erreur au commit : %s | %s", resume_erreur(exc), pile(exc))
        raise HTTPException(
            status_code=500,
            detail="Erreur lors de la sauvegarde du planning. Veuillez réessayer.",
        )

    return {"imported": imported, "replaced": remplaces, "errors": errors, "perimetre": apercu["perimetre"]}


# ── Lot L9 : écoles avec / sans FLS ───────────────────────────────────────────

def _est_repartition_fls(content: bytes, filename: str) -> bool:
    """Vrai si le fichier est la répartition FLS (et non un emploi du temps)."""
    if not filename.endswith(".xlsx"):
        return False
    try:
        lire_repartition(content)
        return True
    except HTTPException:
        return False
    except Exception:
        return False


@router.get("/fls/resume")
async def resume_fls(db: DB, _: AdminUser, session_id: uuid.UUID | None = None) -> dict:
    """Combien d'écoles avec, sans FLS ou non renseignées ; créneaux réservés FLS."""
    lignes = (await db.execute(
        select(School.fls, func.count()).where(School.archived_at.is_(None)).group_by(School.fls)
    )).all()
    compte = {fls: int(n) for fls, n in lignes}
    q = select(func.count()).select_from(PlanningSegment).where(PlanningSegment.reserve_fls.is_(True))
    if session_id:
        q = q.where(PlanningSegment.session_id == session_id)
    return {
        "avec_fls": compte.get(True, 0), "sans_fls": compte.get(False, 0), "non_renseignees": compte.get(None, 0),
        "creneaux_reserves_fls": (await db.execute(q)).scalar_one(),
    }


@router.post("/import-fls")
async def import_repartition_fls(
    db: DB,
    current_user: AdminUser,
    dry_run: bool = Query(True, description="Aperçu : rien n'est modifié (défaut)."),
    completer_ief: bool = Query(False, description="Renseigner l'IEF du fichier pour les écoles qui n'en ont pas."),
    file: UploadFile = File(...),
    associations: str | None = Form(None, description='Choix de l\'admin, JSON {"nom dans le fichier": "id école"}'),
) -> dict:
    """Importe la répartition des écoles avec / sans FLS (fichier Excel de l'équipe programme).

    Chaque école du fichier est rapprochée par son nom (accents, casse,
    ponctuation et préfixes EE / EFA ignorés ; IEF pour départager). Seules les
    écoles trouvées sans ambiguïté sont mises à jour ; les autres sont listées,
    avec une suggestion de nom proche. Les créneaux réservés FLS sont alors
    masqués aux tuteurs des écoles sans FLS dès leur prochaine synchronisation.
    """
    content = await read_upload_capped(file)
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=415, detail="Fichier Excel (.xlsx) attendu.")
    choix: dict[str, str] = {}
    if associations:
        try:
            choix = json.loads(associations)
            assert isinstance(choix, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in choix.items())
            for v in choix.values():
                uuid.UUID(v)
        except Exception:
            raise HTTPException(status_code=422, detail="associations : objet JSON {nom dans le fichier: id d'école} attendu.")
    lignes, erreurs = lire_repartition(content)
    r = await rapprocher(db, lignes, choix)
    a_changer = [t for t in r["trouvees"] if t["change"]]
    a_completer = [t for t in r["trouvees"] if completer_ief and t["ief_manquante"] and t["ief_fichier"]]
    rapport = {
        "dry_run": dry_run,
        "lignes_fichier": len(lignes),
        "avec_fls": sum(1 for x in lignes if x.fls),
        "sans_fls": sum(1 for x in lignes if not x.fls),
        "a_modifier": len(a_changer),
        "associations_appliquees": sum(1 for t in r["trouvees"] if t.get("associee")),
        "ief_completees": len(a_completer),
        "erreurs": erreurs,
        **resume_public(r),
    }
    if dry_run:
        return rapport

    for t in a_changer:
        t["ecole"].fls = t["fls"]
    for t in a_completer:
        t["ecole"].region = t["ief_fichier"]
    journaliser(
        db, acteur=current_user, action="import", entity="Écoles", method="POST",
        path="/api/v1/admin/planning/import-fls",
        description=(f"Répartition FLS importée : {len(a_changer)} école(s) mise(s) à jour, "
                     f"{len(a_completer)} IEF complétée(s), {len(r['introuvables'])} introuvable(s), "
                     f"{len(r['ambigues'])} ambiguë(s)"),
        cible_type="schools",
    )
    await db.commit()
    await _invalidate_sync_caches()
    rapport["ecoles_sans_type_restantes"] = await ecoles_sans_type(db)
    return rapport
