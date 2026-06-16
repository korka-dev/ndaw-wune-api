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
from app.models.supervisor_presence import SupervisorPresenceCheck  # noqa: F401
from app.models.audit_log import AuditLog                           # noqa: F401
