# Importer tous les modèles ici garantit qu'Alembic les découvre via Base.metadata
from app.models.base import UUIDMixin, TimestampMixin          # noqa: F401
from app.models.school import School                           # noqa: F401
from app.models.user import User, UserRole, UserStatus         # noqa: F401
from app.models.session import (                               # noqa: F401
    ProgramSession, TeacherSession, SessionStatus,
)
from app.models.planning import PlanningSegment                # noqa: F401
from app.models.seance import Seance, RapportProf, SeanceStatus  # noqa: F401
from app.models.eleve import Eleve                               # noqa: F401
from app.models.rapport_journalier import RapportJournalier      # noqa: F401
from app.models.document import Document                          # noqa: F401
from app.models.evaluation_eleve import EvaluationEleve           # noqa: F401
from app.models.evaluation_sujet import EvaluationSujet            # noqa: F401
from app.models.evaluation_tirage import EvaluationTirage          # noqa: F401
from app.models.evaluation_resultat import EvaluationResultat      # noqa: F401
from app.models.supervisor_presence import SupervisorPresenceCheck  # noqa: F401
from app.models.audit_log import AuditLog                           # noqa: F401
from app.models.rapport_question import RapportQuestion, RapportQuestionVersion  # noqa: F401
from app.models.rapport_difficulte import RapportDifficulte          # noqa: F401
from app.models.school_classe import SchoolClasse                   # noqa: F401
from app.models.usage_log import UsageLog                            # noqa: F401
from app.models.remarque import Remarque                             # noqa: F401
# Modèles auparavant absents du registre : sans ces imports, `alembic check` /
# autogenerate proposait la SUPPRESSION de leurs tables.
from app.models.evaluation_doc import EvaluationDoc                  # noqa: F401
from app.models.evaluation_competence import EvaluationCompetence    # noqa: F401
from app.models.rapport_libelle import RapportLibelle                # noqa: F401
from app.models.progression_config import ProgressionConfig          # noqa: F401
from app.models.eleve_remplacement import EleveRemplacement          # noqa: F401
from app.models.rapport_difficulte_resolution import RapportDifficulteResolution  # noqa: F401
from app.models.rapport_journalier import RapportJournalierVersion    # noqa: F401
from app.models.evaluation_resultat import EvaluationResultatHistorique  # noqa: F401
