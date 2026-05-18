#!/usr/bin/env python3
"""
Diagnostic complet de la synchronisation mobile.

Ce script teste chaque étape de la chaîne :
  1. Santé du backend
  2. Session active
  3. Planning dimanche dans la DB (via API admin)
  4. Sync réelle de chaque enseignant (ce que l'app mobile reçoit)
  5. Présence du créneau dimanche dans le sync

Usage :
    python diagnostic_sync.py
Ou dans le conteneur backend :
    docker exec backend-backend-1 python /app/../diagnostic_sync.py
"""

import json
import sys
import urllib.request
import urllib.error

BASE     = "http://localhost:8000/api/v1"
OK       = "\033[92m✅\033[0m"
ERR      = "\033[91m❌\033[0m"
INF      = "\033[94mℹ️ \033[0m"
WARN     = "\033[93m⚠️ \033[0m"
JOURS    = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"]

def req(method, path, body=None, token=None):
    url     = BASE + path
    data    = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    rq = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(rq, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"error": str(e)}

# ── 1. Health check ───────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"{INF} 1. Health check backend…")
try:
    rq = urllib.request.Request("http://localhost:8000/health")
    with urllib.request.urlopen(rq, timeout=5) as r:
        health = json.loads(r.read())
    print(f"{OK}  Backend OK : {health}")
except Exception as e:
    print(f"{ERR}  Backend inaccessible : {e}")
    sys.exit(1)

# ── 2. Login admin ────────────────────────────────────────────────────────────
print(f"\n{INF} 2. Login admin…")
status, resp = req("POST", "/auth/login", {"identifier": "admin@ared.sn", "password": "P@sser123"})
if status != 200:
    print(f"{ERR}  Login admin échoué ({status}) : {resp}")
    sys.exit(1)
admin_token = resp["access_token"]
print(f"{OK}  Admin connecté.")

# ── 3. Session active ─────────────────────────────────────────────────────────
print(f"\n{INF} 3. Sessions…")
status, resp = req("GET", "/admin/sessions", token=admin_token)
sessions     = resp.get("items", [])
active_sessions = [s for s in sessions if s["status"] == "active"]
if not active_sessions:
    print(f"{ERR}  Aucune session active ! Les mobiles ne reçoivent rien.")
    sys.exit(1)
active = active_sessions[0]
session_id = active["id"]
print(f"{OK}  Session active : « {active['name']} » (id={session_id[:8]}…)")
if len(active_sessions) > 1:
    print(f"{WARN}  {len(active_sessions)} sessions actives — seule la plus récente est utilisée par le sync.")

# ── 4. Planning dans la DB ────────────────────────────────────────────────────
print(f"\n{INF} 4. Planning en DB pour la session active…")
status, resp = req("GET", f"/admin/planning?session_id={session_id}", token=admin_token)
all_segs     = resp.get("items", [])
print(f"{OK}  {len(all_segs)} créneau(x) total en DB.")

by_jour = {}
for s in all_segs:
    j = s["jour"]
    by_jour.setdefault(j, []).append(s)

for j in range(7):
    segs = by_jour.get(j, [])
    if segs:
        label = JOURS[j] if j < 7 else str(j)
        print(f"     {label} ({j}) : {len(segs)} créneau(x)")
        for s in segs:
            teacher = s.get("teacher_name") or "tous les enseignants (NULL)"
            print(f"       • {s['heure_debut']}–{s['heure_fin']}  {s.get('matiere','—')}  → enseignant: {teacher}")

dimanche_db = by_jour.get(6, [])
if dimanche_db:
    print(f"{OK}  Dimanche (jour=6) : {len(dimanche_db)} créneau(x) en DB ✓")
else:
    print(f"{ERR}  Dimanche (jour=6) : AUCUN créneau en DB !")
    print(f"     → Ajoutez un créneau pour Dimanche dans le dashboard admin.")

# ── 5. Sync par enseignant ────────────────────────────────────────────────────
print(f"\n{INF} 5. Test sync mobile pour chaque enseignant…")
status, resp = req("GET", "/admin/teachers", token=admin_token)
teachers     = resp.get("items", [])

if not teachers:
    print(f"{WARN}  Aucun enseignant en base.")
else:
    for teacher in teachers:
        identifier = teacher.get("phone") or teacher.get("email")
        name       = teacher.get("name", "?")
        if not identifier:
            print(f"  {WARN} {name} — pas d'identifiant (email/phone manquant)")
            continue

        # Login enseignant
        s_login, r_login = req("POST", "/auth/login", {"identifier": identifier, "password": "P@sser123"})
        if s_login != 200:
            print(f"  {WARN} {name} — login échoué ({s_login}) : mot de passe différent ?")
            continue

        t_token = r_login["access_token"]

        # Appel sync
        s_sync, r_sync = req("GET", "/app/sync", token=t_token)
        if s_sync != 200:
            print(f"  {ERR} {name} — /app/sync retourne {s_sync} : {r_sync.get('detail','?')}")
            print(f"       → Vérifiez que le compte est bien de rôle 'enseignant'.")
            continue

        planning = r_sync.get("planning", [])
        sess     = r_sync.get("active_session")
        dim_segs = [p for p in planning if p["jour"] == 6]

        sess_label = f"« {sess['name']} »" if sess else "AUCUNE SESSION ACTIVE"
        print(f"\n  {OK} {name}")
        print(f"     Session reçue      : {sess_label}")
        print(f"     Planning total     : {len(planning)} créneau(x)")
        print(f"     Créneaux Dimanche  : {len(dim_segs)}")

        if dim_segs:
            for seg in dim_segs:
                print(f"       • {seg['heure_debut']}–{seg['heure_fin']}  {seg.get('matiere') or seg.get('titre') or '—'}")
        elif dimanche_db:
            print(f"     {ERR} Dimanche est en DB mais PAS dans le sync de cet enseignant !")
            if sess:
                db_session_ids = [s["session_id"] for s in dimanche_db]
                if sess["id"] not in db_session_ids:
                    print(f"       → Les créneaux Dimanche appartiennent à une autre session (DB: {db_session_ids[0][:8]}… / sync: {sess['id'][:8]}…)")
                else:
                    teacher_ids = [s.get("teacher_id") for s in dimanche_db]
                    if any(tid is not None for tid in teacher_ids):
                        print(f"       → Les créneaux ont teacher_id spécifique : {teacher_ids}")
                        print(f"         Assignez-les à NULL (tous enseignants) ou à cet enseignant.")

# ── Résumé ────────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"{OK}  Diagnostic terminé.")
print(f"{'─'*60}\n")
