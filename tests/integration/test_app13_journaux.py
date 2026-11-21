"""APP-13 — journaux sans contenu personnel, historique précis et protégé.

Scénario du rapport : provoquer des erreurs contenant des valeurs sentinelles
fictives, puis vérifier réponse, journaux et historique métier.
"""
from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from starlette.requests import Request

from app.core.database import AsyncSessionLocal
from app.core.journal import resume_erreur
from app.main import unhandled_exception_handler
from app.models.audit_log import AuditLog
from app.models.school import School
from app.models.user import UserRole

from .conftest import bearer, create_user, login
from .test_l3_integrite import _rj

API = "/api/v1"
SENTINELLE = "SENTINELLE-Fatou-771234567"


async def _audits(**filtres) -> list[AuditLog]:
    async with AsyncSessionLocal() as s:
        q = select(AuditLog)
        for k, v in filtres.items():
            q = q.where(getattr(AuditLog, k) == v)
        return list((await s.execute(q.order_by(AuditLog.created_at))).scalars().all())


# ── Journaux et réponses ───────────────────────────────────────────────────

async def test_422_sans_valeur_saisie_ni_dans_la_reponse_ni_dans_les_journaux(client, tag, caplog):
    h = bearer(await login(client, await create_user(UserRole.enseignant, tag)))
    caplog.set_level(logging.DEBUG)
    corps = _rj(tag, nb_absences=SENTINELLE, absents=SENTINELLE, nom_tuteur=SENTINELLE)
    r = await client.post(f"{API}/app/rapports/journalier", headers=h, json=corps)
    assert r.status_code == 422
    assert SENTINELLE not in r.text
    detail = r.json()["detail"]
    assert any(e["loc"][-1] == "nb_absences" for e in detail) and all("input" not in e for e in detail)
    journal = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "nb_absences" in journal and r.headers["x-request-id"] in journal
    assert SENTINELLE not in journal


async def test_500_journal_sans_message_ni_valeur(caplog):
    class Orig(Exception):
        sqlstate = "23505"
        constraint_name = "uq_users_email_lower"

    exc = IntegrityError(f"INSERT INTO users VALUES ('{SENTINELLE}')", {"email": SENTINELLE}, Orig(f"Key (email)=({SENTINELLE})"))
    resume = resume_erreur(exc)
    assert resume == "IntegrityError/Orig sqlstate=23505 contrainte=uq_users_email_lower"

    try:
        raise DBAPIError(f"UPDATE eleves SET nom='{SENTINELLE}'", {"nom": SENTINELLE}, Orig(SENTINELLE))
    except DBAPIError as e:
        boom = e
    req = Request({"type": "http", "method": "POST", "path": "/api/v1/x", "headers": [], "query_string": b"",
                   "state": {"request_id": "req-sentinelle-1"}})
    caplog.set_level(logging.DEBUG)
    r = await unhandled_exception_handler(req, boom)
    assert r.status_code == 500 and SENTINELLE.encode() not in r.body
    journal = "\n".join(rec.getMessage() + (rec.exc_text or "") for rec in caplog.records)
    assert "req-sentinelle-1" in journal and "DBAPIError" in journal and "test_app13_journaux.py" in journal
    assert SENTINELLE not in journal


# ── Historique métier ──────────────────────────────────────────────────────

async def test_correction_et_annulation_tracees_avec_cible_motif_et_reference(client, tag):
    h = bearer(await login(client, u := await create_user(UserRole.enseignant, tag)))
    url = f"{API}/app/rapports/journalier"
    rid = (await client.post(url, headers=h, json=_rj(tag, client_op_id=f"op13-{tag}"))).json()["id"]

    r1 = await client.put(f"{url}/{rid}", headers=h, json={"motif": "Erreur de saisie", "nb_absences": 1, "absents": "A"})
    r2 = await client.post(f"{url}/{rid}/annuler", headers=h, json={"motif": "Doublon du rapport de lundi"})
    assert r1.status_code == r2.status_code == 200

    lignes = await _audits(cible_id=rid)
    assert [(a.action, a.motif, a.user_id) for a in lignes] == [
        ("correction", "Erreur de saisie", u.id),
        ("annulation", "Doublon du rapport de lundi", u.id),
    ]
    assert [a.request_id for a in lignes] == [r1.headers["x-request-id"], r2.headers["x-request-id"]]
    assert all(a.cible_type == "rapport_journalier" for a in lignes)
    assert lignes[0].description.endswith("champs : absents, nb_absences")  # noms de champs, pas les valeurs


async def test_route_admin_une_seule_entree_precise(client, tag):
    ha = bearer(await login(client, await create_user(UserRole.admin, tag)))
    async with AsyncSessionLocal() as s:
        ecole = School(name=f"Ecole APP13 {tag}", code_ecole=uuid.uuid4().int % 10**8)
        s.add(ecole)
        await s.commit()
    r = await client.delete(f"{API}/admin/schools/{ecole.id}", headers=ha)
    assert r.status_code == 200, r.text
    lignes = await _audits(request_id=r.headers["x-request-id"])
    assert len(lignes) == 1 and lignes[0].action == "archive"
    assert (lignes[0].cible_type, lignes[0].cible_id) == ("schools", str(ecole.id))

    # Route admin sans entrée précise : entrée générique, cible déduite du chemin.
    r = await client.post(f"{API}/admin/schools/{ecole.id}/restaurer", headers=ha)
    assert r.status_code == 200
    (ligne,) = await _audits(request_id=r.headers["x-request-id"])
    assert ligne.cible_id == str(ecole.id)


async def test_historique_protege_contre_modification_et_suppression():
    async with AsyncSessionLocal() as s:
        a = AuditLog(user_name="t", user_role="admin", action="update", entity="Test", method="POST",
                     path="/t", description="APP-13 protection")
        s.add(a)
        await s.commit()
        aid = a.id

    for sql in ("UPDATE audit_logs SET description = 'falsifié' WHERE id = :id",
                "DELETE FROM audit_logs WHERE id = :id"):
        async with AsyncSessionLocal() as s:
            with pytest.raises(DBAPIError):
                await s.execute(text(sql), {"id": aid})

    # Purge de rétention explicite (décision ARED) : autorisée dans sa transaction seulement.
    async with AsyncSessionLocal() as s:
        await s.execute(text("SET LOCAL ndawwune.audit_purge = 'on'"))
        await s.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": aid})
        await s.commit()
    assert await _audits(id=aid) == []
