"""Tests — filtrage du contenu par langue d'enseignement.

Les langues sont saisies en texte libre à plusieurs endroits (écoles,
ressources, dossiers d'évaluation). Une comparaison stricte ne faisait
correspondre que « wolof », écrit identiquement partout : les enseignants
pulaar et seereer ne voyaient aucun contenu. Ces tests figent la règle.
"""
from app.core.langue import canonical_langue, langue_matches, normalize_langue

# Valeurs réellement présentes en base (août 2026)
LANGUES_ECOLES = ["pulaar", "seereer", "wolof"]


class TestCanonicalLangue:
    def test_casse_et_accents_neutralises(self):
        assert canonical_langue("Wolof") == "wolof"
        assert canonical_langue("  WOLOF ") == "wolof"
        assert canonical_langue("Sérère") == "seereer"

    def test_orthographes_connues_regroupees(self):
        assert canonical_langue("poular") == canonical_langue("Pulaar") == "pulaar"
        assert canonical_langue("peul") == "pulaar"
        assert canonical_langue("sereer") == canonical_langue("seereer") == "seereer"
        assert canonical_langue("diola") == canonical_langue("joola") == "joola"

    def test_langue_inconnue_reste_elle_meme(self):
        assert canonical_langue("balant") == "balant"

    def test_valeurs_vides(self):
        assert canonical_langue(None) is None
        assert canonical_langue("") is None
        assert canonical_langue("   ") is None
        assert normalize_langue(None) is None


class TestVisibiliteDuContenu:
    def test_une_ressource_wolof_ne_va_qu_aux_ecoles_wolof(self):
        for ecole in LANGUES_ECOLES:
            attendu = ecole == "wolof"
            assert langue_matches("Wolof", ecole) is attendu

    def test_orthographe_du_dashboard_vs_orthographe_des_ecoles(self):
        # Le sélecteur du dashboard proposait « sereer », les écoles disent
        # « seereer » : sans normalisation, ce contenu n'atteignait personne.
        assert langue_matches("sereer", "seereer") is True
        assert langue_matches("Pulaar", "pulaar") is True

    def test_langues_distinctes_ne_se_melangent_pas(self):
        assert langue_matches("wolof", "pulaar") is False
        assert langue_matches("seereer", "wolof") is False

    def test_valeur_absente_ne_correspond_jamais(self):
        # Un contenu sans langue est « toutes langues » : ce cas est traité par
        # les appelants (`not d.langue or langue_matches(...)`), pas ici.
        assert langue_matches(None, "wolof") is False
        assert langue_matches("wolof", None) is False
