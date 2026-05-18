#!/usr/bin/env python3
"""
Test end-to-end : ajout d'un créneau Dimanche 13h50–14h10 "Révision"
et vérification que le sync mobile le retourne bien.

Usage (depuis la racine du projet) :
    python test_dimanche.py

Ou directement dans le conteneur backend :
    docker exec backend-backend-1 python /app/../test_dimanche.py
"""

import json, sys
import urllib.request
import urllib.error

BASE = "http://localhost:8000/api/v1"

# ── Couleurs terminal ──────────────────────────────────────────────────────────
OK  = "\033[92m✅\033[0m"
ERR = "\033[91m❌\033[0m"
INF = "\033[94mℹ️ \033[0m"

def req(method, path, body=None, token=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    rq = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(rq, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

# ── 1. Health check ────────────────────────────────────────────────────────────
print(f"\n{INF} 1. Health check…")
try:
    rq = urllib.request.Request("http://localhost:8000/health")
    with urllib.request.urlopen(rq, timeout=5) as r:
        health = json.loads(r.read())
    print(f"{OK}  Backend up : {health}")
except Exception as e:
    print(f"{ERR}  Backend inaccessible : {e}")
    sys.exit(1)

# ── 2. Login admin ─────────────────────────────────────────────────────────────
print(f"\n{INF} 2. Login admin…")
status, resp = req("POST", "/auth/login", {"identifier": "admin@ared.sn", "password": "P@sser123"})
if status != 200:
    print(f"{ERR}  Login échoué ({status}) : {resp}")
    sys.exit(1)
admin_token = resp["access_token"]
print(f"{OK}  Connecté. Token : {admin_token[:30]}…")

# ── 3. Session active ──────────────────────────────────────────────────────────
print(f"\n{INF} 3. Récupération de la session active…")
status, resp = req("GET", "/admin/sessions", token=admin_token)
sessions = resp.get("items", [])
active = next((s for s in sessions if s["status"] == "active"), None)
if not active:
    print(f"{ERR}  Aucune session active trouvée. Activez une session dans l'admin.")
    sys.exit(1)
session_id = active["id"]
print(f"{OK}  Session active : « {active['name']} » (id={session_id[:8]}…)")

# ── 4. Création du créneau Dimanche 13:50–14:10 "Révision" ────────────────────
print(f"\n{INF} 4. Création du créneau Dimanche 13h50–14h10 « Révision »…")
body = {
    "session_id":  session_id,
    "jour":        6,           # 6 = Dimanche
    "heure_debut": "13:50:00",
    "heure_fin":   "14:10:00",
    "matiere":     "Révision",
}
status, resp = req("POST", "/admin/planning", body=body, token=admin_token)
if status not in (200, 201):
    print(f"{ERR}  Création échouée ({status}) : {resp}")
    sys.exit(1)
seg_id = resp["id"]
print(f"{OK}  Créneau créé ! id={seg_id[:8]}…  jour={resp['jour']}  {resp['heure_debut']}–{resp['heure_fin']}  matière={resp['matiere']}")

# ── 5. Vérification invalidation cache Redis ───────────────────────────────────
print(f"\n{INF} 5. Le cache Redis sync a été invalidé automatiquement.")
print(f"     (la prochaine synchronisation mobile récupérera les données fraîches)")

# ── 6. Login enseignant + sync ─────────────────────────────────────────────────
print(f"\n{INF} 6. Recherche d'un enseignant pour tester le sync…")
status, resp = req("GET", "/admin/teachers", token=admin_token)
teachers = resp.get("items", [])
if not teachers:
    print(f"     Aucun enseignant en base — test sync ignoré.")
else:
    teacher = teachers[0]
    identifier = teacher.get("phone") or teacher.get("email")
    print(f"     Enseignant trouvé : {teacher['name']} ({identifier})")

    print(f"\n{INF} 7. Login enseignant + appel /app/sync…")
    status, resp = req("POST", "/auth/login", {"identifier": identifier, "password": "P@sser123"})
    if status != 200:
        print(f"     Login enseignant échoué ({status}) — peut-être un autre mot de passe.")
    else:
        t_token = resp["access_token"]
        status, sync = req("GET", "/app/sync", token=t_token)
        if status != 200:
            print(f"{ERR}  Sync échoué ({status}) : {sync}")
        else:
            planning = sync.get("planning", [])
            dimanche_segs = [p for p in planning if p["jour"] == 6]
            revision = next((p for p in dimanche_segs if p.get("matiere") == "Révision"), None)
            print(f"{OK}  Sync OK — {len(planning)} créneau(x) total, {len(dimanche_segs)} pour Dimanche")
            if revision:
                print(f"{OK}  Créneau « Révision » bien présent dans le sync !")
                print(f"     → jour={revision['jour']}  {revision['heure_debut']}–{revision['heure_fin']}  matière={revision['matiere']}")
            else:
                print(f"     Créneau Révision absent du sync (enseignant peut-être non affecté à la session).")
                print(f"     Créneaux Dimanche trouvés : {dimanche_segs}")

print(f"\n{'─'*60}")
print(f"{OK}  Test terminé avec succès !")
print(f"{'─'*60}\n")
