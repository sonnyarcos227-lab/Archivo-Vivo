"""
auth.py – JWT-based authentication: password hashing, token creation/validation,
           register & login endpoints, and the current-user dependency.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from database import get_db
    from models import User
except ImportError:
    from backend.database import get_db
    from backend.models import User

# ── Configuration ──────────────────────────────────────────────────────────────
JWT_SECRET = os.getenv("JWT_SECRET_KEY", "super-secret-change-me")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MIN = int(os.getenv("JWT_EXPIRATION_MINUTES", "1440"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


# ── Pydantic Schemas ───────────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=80)
    email: EmailStr
    password: str = Field(..., min_length=6)
    full_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserOut(BaseModel):
    id: str
    username: str
    email: str
    full_name: Optional[str]
    avatar_color: str
    role: str = "user"
    is_active: int = 1


# ── Helpers ────────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    plain_bytes = plain.encode("utf-8")[:72]
    hashed_bytes = hashed.encode("utf-8")
    return bcrypt.checkpw(plain_bytes, hashed_bytes)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MIN)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido o expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    if getattr(user, "is_active", 1) == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta se encuentra suspendida o inactiva",
        )
    return user


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if getattr(current_user, "role", "user") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido: requiere permisos de administrador",
        )
    return current_user


async def seed_default_admin(db: AsyncSession):
    """Ensure at least one admin account exists."""
    result = await db.execute(select(User).where(User.role == "admin"))
    admin = result.scalar_one_or_none()
    if not admin:
        # Check if username or email admin is already used
        check = await db.execute(select(User).where((User.username == "admin") | (User.email == "admin@chatpdf.com")))
        existing = check.scalar_one_or_none()
        if existing:
            existing.role = "admin"
            existing.is_active = 1
            await db.flush()
            print("[INFO] Usuario 'admin' existente promovido a rol admin")
        else:
            default_admin = User(
                username="admin",
                email="admin@chatpdf.com",
                hashed_password=hash_password("admin123"),
                full_name="Administrador Maestro",
                avatar_color="#6366f1",
                role="admin",
                is_active=1,
            )
            db.add(default_admin)
            await db.flush()
            print("[OK] Cuenta de administrador por defecto creada: admin@chatpdf.com / admin123")


# ── Endpoints ──────────────────────────────────────────────────────────────────
@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # Check duplicate email
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="El correo electrónico ya está registrado")

    # Check duplicate username
    existing_u = await db.execute(select(User).where(User.username == body.username))
    if existing_u.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="El nombre de usuario ya existe")

    import random
    colors = ["#10b981", "#6366f1", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4", "#ec4899"]

    # First user or username 'admin' gets admin role automatically
    all_users = await db.execute(select(User))
    user_count = len(all_users.scalars().all())
    assigned_role = "admin" if (user_count == 0 or body.username.lower() == "admin" or body.email.lower() == "admin@chatpdf.com") else "user"

    user = User(
        username=body.username,
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        avatar_color=random.choice(colors),
        role=assigned_role,
        is_active=1,
    )
    db.add(user)
    await db.flush()

    token = create_access_token({"sub": user.id, "username": user.username, "role": user.role})
    return TokenResponse(
        access_token=token,
        user={
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "avatar_color": user.avatar_color,
            "role": user.role,
            "is_active": user.is_active,
        },
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    if getattr(user, "is_active", 1) == 0:
        raise HTTPException(status_code=403, detail="Tu cuenta está desactivada por un administrador")

    role = getattr(user, "role", "user") or "user"
    token = create_access_token({"sub": user.id, "username": user.username, "role": role})
    return TokenResponse(
        access_token=token,
        user={
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "avatar_color": user.avatar_color,
            "role": role,
            "is_active": getattr(user, "is_active", 1),
        },
    )


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return UserOut(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        full_name=current_user.full_name,
        avatar_color=current_user.avatar_color,
        role=getattr(current_user, "role", "user") or "user",
        is_active=getattr(current_user, "is_active", 1),
    )
