"""Lot L2 du rapport V2 rejoué contre l'API réelle : idempotence (APP-16),
réconciliation des séances (APP-18), séance oubliée (APP-21), photos (APP-37)."""
from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.rapport_journalier import RapportJournalier
from app.models.seance import Seance, SeanceStatus
from app.models.session import ProgramSession, SessionStatus
from app.models.user import UserRole
from app.services.seance_service import close_stale_seances

from .conftest import bearer, create_user, login

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


async def _session(tag: str) -> ProgramSession:
    async with AsyncSessionLocal() as s:
        ps = ProgramSession(name=f"Session {tag}", date_debut=date(2026, 1, 1), date_fin=date(2026, 12, 31),
                            status=SessionStatus.active)
        s.add(ps)
        await s.commit()
        await s.refresh(ps)
        return ps


def _rapport(tag: str, **over) -> dict:
    body = {
        "date_rapport": "2026-11-20", "ief": "IEF", "commune": "Commune", "ecole": f"Ecole {tag}",
        "superviseur": "Sup", "nom_tuteur": "Tuteur", "nb_absences": 0, "semaine": 3,
        "jour_cours": 2, "difficultes": "[]", "directeur_venu": True, "besoin_appui": False,
        "photos_classe_url": f'["data:image/jpeg;base64,{base64.b64encode(PNG).decode()}"]',
    }
    body.update(over)
    return body


def _start(session_id, **over) -> dict:
    now = datetime.now(timezone.utc)
    body = {
        "session_id": str(session_id), "classe": "CE1", "date_seance": now.isoformat(),
        "started_at": now.isoformat(),
    }
    body.update(over)
    return body


async def test_rapport_dix_envois_une_ecriture(client, tag):  # APP-16
    u = await create_user(UserRole.enseignant, tag)
    h = bearer(await login(client, u))
    body = _rapport(tag, client_op_id=f"op-{tag}")
    rs = [await client.post("/api/v1/app/rapports/journalier", headers=h, json=body) for _ in range(5)]
    rs += await asyncio.gather(*[
        client.post("/api/v1/app/rapports/journalier", headers=h, json=body) for _ in range(5)
    ])
    assert all(r.status_code == 201 for r in rs), [r.text for r in rs]
    assert len({r.json()["id"] for r in rs}) == 1
    async with AsyncSessionLocal() as s:
        n = (await s.execute(select(RapportJournalier).where(RapportJournalier.teacher_id == u.id))).scalars().all()
    assert len(n) == 1

    # Même clé, contenu différent → refus explicite
    r = await client.post("/api/v1/app/rapports/journalier", headers=h,
                          json=_rapport(tag, client_op_id=f"op-{tag}", nb_absences=4))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_rapport_sans_photos_dans_les_listes(client, tag):  # APP-37
    u = await create_user(UserRole.enseignant, tag)
    h = bearer(await login(client, u))
    created = (await client.post("/api/v1/app/rapports/journalier", headers=h,
                                 json=_rapport(tag, client_op_id=f"p-{tag}"))).json()
    assert "photos_classe_url" not in created and created["nb_photos"] == 1

    page = (await client.get("/api/v1/app/rapports/journalier", headers=h)).json()
    assert page["items"][0]["nb_photos"] == 1 and "photos_classe_url" not in page["items"][0]

    photo = await client.get(f"/api/v1/app/rapports/journalier/{created['id']}/photo/0", headers=h)
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/png"
    assert photo.content == PNG

    autre = bearer(await login(client, await create_user(UserRole.enseignant, tag)))
    r = await client.get(f"/api/v1/app/rapports/journalier/{created['id']}/photo/0", headers=autre)
    assert r.status_code == 404


async def test_seance_hors_ligne_ne_touche_pas_la_seance_active(client, tag):  # APP-16/18
    u = await create_user(UserRole.enseignant, tag)
    h = bearer(await login(client, u))
    ps = await _session(tag)

    # Séance B active sur le serveur
    b = await client.post("/api/v1/app/seances/start", headers=h, json=_start(ps.id, classe="CM2"))
    assert b.status_code == 201
    # Démarrer A en ligne : conflit explicite qui décrit B
    r = await client.post("/api/v1/app/seances/start", headers=h,
                          json=_start(ps.id, client_seance_id=f"offline-A-{tag}"))
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "SEANCE_ACTIVE" and detail["active"]["id"] == b.json()["id"]

    # A envoyée en une fois (faite hors ligne), deux fois : une seule séance
    hier = datetime.now(timezone.utc) - timedelta(days=1)
    a_body = _start(ps.id, classe="CE1", date_seance=hier.isoformat(), started_at=hier.isoformat()) | {
        "client_seance_id": f"offline-A-{tag}",
        "finished_at": (hier + timedelta(minutes=50)).isoformat(), "duree_minutes": 50,
    }
    a1 = await client.post("/api/v1/app/seances/complete", headers=h, json=a_body)
    a2 = await client.post("/api/v1/app/seances/complete", headers=h, json=a_body)
    assert a1.status_code == a2.status_code == 201
    assert a1.json()["id"] == a2.json()["id"] != b.json()["id"]
    assert a1.json()["duree_minutes"] == 50 and a1.json()["cloture_mode"] == "tuteur"

    # B n'a pas été modifiée
    active = (await client.get("/api/v1/app/seances/active", headers=h)).json()
    assert active["id"] == b.json()["id"] and active["status"] == "en_cours"


async def test_cloture_rejouee_et_rapport_rejoue(client, tag):  # APP-16
    u = await create_user(UserRole.enseignant, tag)
    h = bearer(await login(client, u))
    ps = await _session(tag)
    debut = datetime.now(timezone.utc) - timedelta(minutes=50)
    sid = (await client.post("/api/v1/app/seances/start", headers=h,
                             json=_start(ps.id, started_at=debut.isoformat(), date_seance=debut.isoformat()))).json()["id"]
    fin = {"finished_at": datetime.now(timezone.utc).isoformat(), "duree_minutes": 45}
    assert (await client.post(f"/api/v1/app/seances/{sid}/finish", headers=h, json=fin)).status_code == 200
    # Réponse perdue → renvoi identique : succès, pas d'échec affiché
    assert (await client.post(f"/api/v1/app/seances/{sid}/finish", headers=h, json=fin)).status_code == 200
    # Valeurs différentes : conflit explicite
    r = await client.post(f"/api/v1/app/seances/{sid}/finish", headers=h, json=fin | {"duree_minutes": 10})
    assert r.status_code == 409

    rapport = {"seance_id": sid, "contenu": "Lecture syllabique"}
    r1 = await client.post("/api/v1/app/rapports", headers=h, json=rapport)
    r2 = await client.post("/api/v1/app/rapports", headers=h, json=rapport)
    assert r1.status_code == 201 and r2.status_code == 201 and r1.json()["id"] == r2.json()["id"]


async def test_seance_oubliee_recuperation_sans_duree(client, tag):  # APP-21
    u = await create_user(UserRole.enseignant, tag)
    h = bearer(await login(client, u))
    ps = await _session(tag)
    il_y_a_30h = datetime.now(timezone.utc) - timedelta(hours=30)

    # Clôture automatique : jamais de durée inférée
    old = (await client.post("/api/v1/app/seances/start", headers=h, json=_start(
        ps.id, date_seance=il_y_a_30h.isoformat(), started_at=il_y_a_30h.isoformat(),
        client_seance_id=f"vieille-{tag}",
    ))).json()
    async with AsyncSessionLocal() as s:
        await close_stale_seances(s, u.id)
        await s.commit()
        seance = await s.get(Seance, uuid.UUID(old["id"]))
        assert seance.status == SeanceStatus.terminee and seance.cloture_mode == "automatique"
        assert seance.duree_minutes is None and seance.finished_at is None

    # L'arrêt réel arrivé plus tard depuis le téléphone est accepté
    fin = {"finished_at": (il_y_a_30h + timedelta(minutes=55)).isoformat(), "duree_minutes": 55}
    r = await client.post(f"/api/v1/app/seances/{old['id']}/finish", headers=h, json=fin)
    assert r.status_code == 200 and r.json()["cloture_mode"] == "tuteur_tardive"
    assert r.json()["duree_minutes"] == 55

    # Récupération explicite par le tuteur, tracée, puis nouvelle séance possible
    bloquante = (await client.post("/api/v1/app/seances/start", headers=h, json=_start(ps.id))).json()
    r = await client.post(f"/api/v1/app/seances/{bloquante['id']}/cloturer-oubli", headers=h,
                          json={"motif": "Téléphone déchargé"})
    assert r.status_code == 200
    assert r.json()["cloture_mode"] == "recuperation" and r.json()["duree_minutes"] is None
    assert (await client.post("/api/v1/app/seances/start", headers=h, json=_start(ps.id))).status_code == 201
    async with AsyncSessionLocal() as s:
        logs = (await s.execute(select(AuditLog).where(AuditLog.user_id == u.id))).scalars().all()
    assert any("Séance oubliée" in log.description for log in logs)
