/**
 * Test de charge NDAW WUNE — vérifie combien de connexions/requêtes
 * simultanées le backend encaisse réellement, plutôt que de le deviner.
 *
 * Usage :
 *   k6 run -e BASE_URL=http://localhost:8000 \
 *          -e IDENTIFIER=adiallo@gmail.com \
 *          -e PASSWORD='P@sser123' \
 *          -e TARGET_VUS=200 \
 *          loadtest/k6-script.js
 *
 * IMPORTANT :
 *   - Ne JAMAIS lancer ceci contre l'API de production (api.ndawwune.cloud)
 *     sans fenêtre de maintenance planifiée — un test à pleine charge peut
 *     saturer le pool de connexions et impacter les vrais utilisateurs.
 *   - TARGET_VUS=200 par défaut pour rester raisonnable sur une machine de
 *     dev/staging modeste. Augmenter progressivement (200 → 500 → 1000) en
 *     surveillant `docker stats` et les logs Postgres entre chaque palier.
 */
import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL    = __ENV.BASE_URL    || "http://localhost:8000";
const IDENTIFIER   = __ENV.IDENTIFIER || "adiallo@gmail.com";
const PASSWORD     = __ENV.PASSWORD   || "P@sser123";
const TARGET_VUS    = parseInt(__ENV.TARGET_VUS || "200", 10);

export const options = {
  scenarios: {
    charge: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "30s", target: TARGET_VUS },   // montée en charge progressive
        { duration: "60s", target: TARGET_VUS },   // palier soutenu
        { duration: "15s", target: 0 },            // descente
      ],
      gracefulRampDown: "10s",
    },
  },
  thresholds: {
    // Sous ces seuils, le système est considéré comme tenant la charge.
    http_req_failed:               ["rate<0.01"],   // < 1% d'erreurs
    "http_req_duration{endpoint:health}": ["p(95)<300"],
    "http_req_duration{endpoint:dashboard}": ["p(95)<1500"],
  },
};

// ── setup() : exécuté UNE SEULE FOIS avant la montée en charge ────────────────
// Un seul login (le endpoint est limité à 5/min/IP côté serveur) ; le token
// est ensuite réutilisé par tous les VUs — on ne teste pas la capacité de
// /auth/login ici, mais celle des endpoints réellement sollicités en continu
// par l'app (sync, dashboard, etc.).
export function setup() {
  const res = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ identifier: IDENTIFIER, password: PASSWORD }),
    { headers: { "Content-Type": "application/json" } }
  );
  if (res.status !== 200) {
    throw new Error(
      `Login échoué (${res.status}) — vérifie IDENTIFIER/PASSWORD ou que le backend tourne sur ${BASE_URL}.\n${res.body}`
    );
  }
  const token = res.json("access_token");
  return { token };
}

// ── Scénario exécuté par chaque VU en boucle ──────────────────────────────────
export default function (data) {
  const authHeaders = {
    headers: { Authorization: `Bearer ${data.token}` },
  };

  // 80% du trafic : endpoint DB-lourd (agrégations dashboard admin) —
  // représentatif du vrai goulot d'étranglement (pool de connexions).
  // 20% : /health, sans base de données — mesure la capacité brute HTTP/serveur.
  if (Math.random() < 0.8) {
    const res = http.get(`${BASE_URL}/api/v1/admin/dashboard/stats`, {
      ...authHeaders,
      tags: { endpoint: "dashboard" },
    });
    check(res, { "dashboard 200": (r) => r.status === 200 });
  } else {
    const res = http.get(`${BASE_URL}/health`, { tags: { endpoint: "health" } });
    check(res, { "health 200": (r) => r.status === 200 });
  }

  sleep(1); // pause réaliste entre deux actions d'un même utilisateur
}
