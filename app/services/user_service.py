"""
Couche service pour la gestion des utilisateurs.
Toute la logique métier est ici ; les routes délèguent sans connaître les détails.
"""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.models.user import User, UserRole, UserStatus
from app.schemas.user import UserCreate, UserUpdate


# ── Constante mot de passe par défaut ─────────────────────────────────────────
_DEFAULT_PASSWORD = settings.DEFAULT_USER_PASSWORD


# ── Helpers privés ─────────────────────────────────────────────────────────────

async def _assert_unique_credentials(
    db: AsyncSession,
    email: Optional[str],
    phone: Optional[str],
    exclude_id: Optional[uuid.UUID] = None,
) -> None:
    """
    Vérifie en UNE seule requête qu'aucun autre utilisateur
    n'utilise déjà cet e-mail ou ce téléphone.
    """
    if not email and not phone:
        return

    conditions = []
    if email:
        conditions.append(User.email == email)
    if phone:
        conditions.append(User.phone == phone)

    q = select(User.id, User.email, User.phone).where(or_(*conditions))
    if exclude_id:
        q = q.where(User.id != exclude_id)

    row = (await db.execute(q)).first()
    if row is None:
        return

    if email and row.email == email:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cet e-mail est déjà utilisé.")
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ce numéro de téléphone est déjà utilisé.")


# ── API publique du service ────────────────────────────────────────────────────

async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> User:
    result = await db.execute(
        select(User).options(selectinload(User.school)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur introuvable.")
    return user


async def list_by_role(
    db: AsyncSession,
    role: UserRole,
    skip: int = 0,
    limit: int = 50,
) -> tuple[int, Sequence[User]]:
    base = select(User).where(User.role == role).order_by(User.name)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(
        base.options(selectinload(User.school)).offset(skip).limit(limit)
    )).scalars().all()
    return total, items


async def list_admin_accounts(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 50,
) -> tuple[int, Sequence[User]]:
    base = (
        select(User)
        .where(User.role.in_([UserRole.admin, UserRole.coordonnateur]))
        .order_by(User.name)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(
        base.options(selectinload(User.school)).offset(skip).limit(limit)
    )).scalars().all()
    return total, items


async def create_user(
    db: AsyncSession,
    body: UserCreate,
    force_role: Optional[UserRole] = None,
) -> User:
    """
    Crée un utilisateur.
    - Si aucun mot de passe n'est fourni, on applique le mot de passe par défaut.
    - must_change_password est toujours True à la création (premier login obligatoire).
    """
    await _assert_unique_credentials(db, body.email, body.phone)

    password = body.password if body.password else _DEFAULT_PASSWORD

    data = body.model_dump(exclude={"password"})
    data["password_hash"]      = hash_password(password)
    data["must_change_password"] = True     # toujours forcé à la création
    if force_role:
        data["role"] = force_role

    user = User(**data)
    db.add(user)
    await db.flush()
    await db.refresh(user, attribute_names=["school"])
    return user


async def update_user(db: AsyncSession, user_id: uuid.UUID, body: UserUpdate) -> User:
    user = await get_by_id(db, user_id)

    data = body.model_dump(exclude_none=True)
    email = data.get("email")
    phone = data.get("phone")
    if email or phone:
        await _assert_unique_credentials(db, email, phone, exclude_id=user_id)

    if "password" in data:
        user.password_hash = hash_password(data.pop("password"))

    for field, value in data.items():
        setattr(user, field, value)

    await db.flush()
    await db.refresh(user, attribute_names=["school"])
    return user


async def change_password(
    db: AsyncSession,
    user: User,
    new_password: str,
) -> None:
    """
    Change le mot de passe et lève le flag must_change_password.
    Appelé uniquement depuis la route /auth/change-password.
    """
    # Empêcher de réutiliser le mot de passe par défaut
    # Note : comparaison directe en clair (le mot de passe par défaut est connu du système)
    if new_password == _DEFAULT_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous ne pouvez pas conserver le mot de passe temporaire attribué à la création du compte. Choisissez un nouveau mot de passe personnel.",
        )

    # Vérifier que le nouveau mot de passe est suffisamment fort (min 8 caractères)
    if len(new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le mot de passe doit contenir au moins 8 caractères.",
        )

    user.password_hash        = hash_password(new_password)
    user.must_change_password = False
    await db.flush()


async def toggle_status(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await get_by_id(db, user_id)
    user.status = UserStatus.inactif if user.status == UserStatus.actif else UserStatus.actif
    await db.flush()
    await db.refresh(user, attribute_names=["school"])
    return user


async def delete_user(db: AsyncSession, user_id: uuid.UUID, requestor_id: uuid.UUID) -> None:
    if user_id == requestor_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous ne pouvez pas supprimer votre propre compte.",
        )
    user = await get_by_id(db, user_id)
    await db.delete(user)
