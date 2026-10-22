"""
admin.py – Complete Administrator Management Router.
Provides CRUD operations for users, role management, and system analytics.
Only accessible to users with role == 'admin'.
"""

import os
import random
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from database import get_db
    from models import User, Document, ChatMessage
    from auth import get_current_admin, hash_password
    from services.pdf_service import delete_document_data
except ImportError:
    from backend.database import get_db
    from backend.models import User, Document, ChatMessage
    from backend.auth import get_current_admin, hash_password
    from backend.services.pdf_service import delete_document_data

router = APIRouter(prefix="/api/admin", tags=["Admin Management"])


# ── Schemas ───────────────────────────────────────────────────────────────────
class AdminStatsResponse(BaseModel):
    total_users: int
    admin_users: int
    regular_users: int
    total_documents: int
    total_messages: int
    total_storage_kb: int


class AdminUserOut(BaseModel):
    id: str
    username: str
    email: str
    full_name: Optional[str]
    avatar_color: str
    role: str
    is_active: int
    created_at: Optional[str]
    documents_count: int = 0
    messages_count: int = 0


class AdminCreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=80)
    email: EmailStr
    password: str = Field(..., min_length=6)
    full_name: Optional[str] = None
    role: str = Field(default="user")


class AdminUpdateUserRequest(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=80)
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[int] = None
    password: Optional[str] = Field(None, min_length=6)


class RoleUpdateRequest(BaseModel):
    role: str = Field(..., pattern="^(admin|user)$")


class StatusUpdateRequest(BaseModel):
    is_active: int = Field(..., ge=0, le=1)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/stats", response_model=AdminStatsResponse)
async def get_admin_stats(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Retrieve overall system and database metrics."""
    users_res = await db.execute(select(User))
    all_users = users_res.scalars().all()
    
    total_users = len(all_users)
    admin_users = sum(1 for u in all_users if getattr(u, "role", "user") == "admin")
    regular_users = total_users - admin_users

    docs_res = await db.execute(select(Document))
    all_docs = docs_res.scalars().all()
    total_documents = len(all_docs)
    total_storage_kb = sum(doc.file_size_kb or 0 for doc in all_docs)

    msgs_res = await db.execute(select(ChatMessage))
    all_msgs = msgs_res.scalars().all()
    total_messages = len(all_msgs)

    return AdminStatsResponse(
        total_users=total_users,
        admin_users=admin_users,
        regular_users=regular_users,
        total_documents=total_documents,
        total_messages=total_messages,
        total_storage_kb=total_storage_kb,
    )


@router.get("/users", response_model=List[AdminUserOut])
async def list_users(
    search: Optional[str] = None,
    role: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """List all registered users with document counts and activity details."""
    query = select(User).order_by(desc(User.created_at))
    result = await db.execute(query)
    users = result.scalars().all()

    output = []
    for u in users:
        # Filter by role if requested
        u_role = getattr(u, "role", "user") or "user"
        if role and role.lower() != "all" and u_role != role.lower():
            continue

        # Filter by search string
        if search:
            s = search.lower()
            u_name = (u.full_name or "").lower()
            u_user = (u.username or "").lower()
            u_email = (u.email or "").lower()
            if s not in u_name and s not in u_user and s not in u_email:
                continue

        # Count documents
        doc_count_res = await db.execute(
            select(func.count(Document.id)).where(Document.user_id == u.id)
        )
        doc_count = doc_count_res.scalar_one_or_none() or 0

        # Count chat messages
        msg_count_res = await db.execute(
            select(func.count(ChatMessage.id)).where(ChatMessage.user_id == u.id)
        )
        msg_count = msg_count_res.scalar_one_or_none() or 0

        created_str = u.created_at.isoformat() if u.created_at else None

        output.append(
            AdminUserOut(
                id=u.id,
                username=u.username,
                email=u.email,
                full_name=u.full_name,
                avatar_color=u.avatar_color or "#10b981",
                role=u_role,
                is_active=getattr(u, "is_active", 1),
                created_at=created_str,
                documents_count=doc_count,
                messages_count=msg_count,
            )
        )

    return output


@router.post("/users", response_model=AdminUserOut, status_code=201)
async def create_user(
    body: AdminCreateUserRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Admin endpoint to create a new user or administrator."""
    # Check duplicates
    existing_e = await db.execute(select(User).where(User.email == body.email))
    if existing_e.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="El correo electrónico ya se encuentra registrado")

    existing_u = await db.execute(select(User).where(User.username == body.username))
    if existing_u.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="El nombre de usuario ya existe")

    colors = ["#10b981", "#6366f1", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4", "#ec4899"]
    valid_role = "admin" if body.role.lower() == "admin" else "user"

    new_user = User(
        username=body.username,
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        avatar_color=random.choice(colors),
        role=valid_role,
        is_active=1,
    )
    db.add(new_user)
    await db.flush()

    return AdminUserOut(
        id=new_user.id,
        username=new_user.username,
        email=new_user.email,
        full_name=new_user.full_name,
        avatar_color=new_user.avatar_color,
        role=new_user.role,
        is_active=new_user.is_active,
        created_at=new_user.created_at.isoformat() if new_user.created_at else None,
        documents_count=0,
        messages_count=0,
    )


@router.put("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: str,
    body: AdminUpdateUserRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Update user information, credentials, or status."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # If updating username, check uniqueness
    if body.username and body.username != user.username:
        chk = await db.execute(select(User).where(User.username == body.username))
        if chk.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="El nombre de usuario ya está en uso")
        user.username = body.username

    # If updating email, check uniqueness
    if body.email and body.email != user.email:
        chk = await db.execute(select(User).where(User.email == body.email))
        if chk.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="El correo electrónico ya está en uso")
        user.email = body.email

    if body.full_name is not None:
        user.full_name = body.full_name

    if body.role is not None:
        if user.id == admin.id and body.role.lower() != "admin":
            raise HTTPException(
                status_code=400,
                detail="No puedes revocar tu propio rol de administrador actual",
            )
        user.role = "admin" if body.role.lower() == "admin" else "user"

    if body.is_active is not None:
        if user.id == admin.id and body.is_active == 0:
            raise HTTPException(
                status_code=400,
                detail="No puedes desactivar tu propia cuenta de administrador",
            )
        user.is_active = body.is_active

    if body.password:
        user.hashed_password = hash_password(body.password)

    await db.flush()

    # Get counts
    doc_count_res = await db.execute(select(func.count(Document.id)).where(Document.user_id == user.id))
    doc_count = doc_count_res.scalar_one_or_none() or 0
    msg_count_res = await db.execute(select(func.count(ChatMessage.id)).where(ChatMessage.user_id == user.id))
    msg_count = msg_count_res.scalar_one_or_none() or 0

    return AdminUserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        avatar_color=user.avatar_color,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at.isoformat() if user.created_at else None,
        documents_count=doc_count,
        messages_count=msg_count,
    )


@router.patch("/users/{user_id}/role", response_model=AdminUserOut)
async def change_user_role(
    user_id: str,
    body: RoleUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Toggle or assign role (admin or user)."""
    if user_id == admin.id and body.role != "admin":
        raise HTTPException(
            status_code=400,
            detail="No puedes degradar tu propia cuenta de administrador",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user.role = body.role
    await db.flush()

    doc_count_res = await db.execute(select(func.count(Document.id)).where(Document.user_id == user.id))
    doc_count = doc_count_res.scalar_one_or_none() or 0
    msg_count_res = await db.execute(select(func.count(ChatMessage.id)).where(ChatMessage.user_id == user.id))
    msg_count = msg_count_res.scalar_one_or_none() or 0

    return AdminUserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        avatar_color=user.avatar_color,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at.isoformat() if user.created_at else None,
        documents_count=doc_count,
        messages_count=msg_count,
    )


@router.patch("/users/{user_id}/status", response_model=AdminUserOut)
async def change_user_status(
    user_id: str,
    body: StatusUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Enable or disable user account access."""
    if user_id == admin.id and body.is_active == 0:
        raise HTTPException(
            status_code=400,
            detail="No puedes desactivar tu propia cuenta de administrador",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user.is_active = body.is_active
    await db.flush()

    doc_count_res = await db.execute(select(func.count(Document.id)).where(Document.user_id == user.id))
    doc_count = doc_count_res.scalar_one_or_none() or 0
    msg_count_res = await db.execute(select(func.count(ChatMessage.id)).where(ChatMessage.user_id == user.id))
    msg_count = msg_count_res.scalar_one_or_none() or 0

    return AdminUserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        avatar_color=user.avatar_color,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at.isoformat() if user.created_at else None,
        documents_count=doc_count,
        messages_count=msg_count,
    )


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Delete a user, cascade cleaning their documents and files."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=400,
            detail="No puedes eliminar tu propia cuenta de administrador activa",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Clean up physical files and vector data for their documents
    docs_result = await db.execute(select(Document).where(Document.user_id == user_id))
    docs = docs_result.scalars().all()
    for doc in docs:
        try:
            from services import kb_service
            delete_document_data(doc.collection_name, file_path=kb_service.doc_file_path(doc))
        except Exception as e:
            print(f"[WARN] No se pudo limpiar datos de archivo para {doc.id}: {e}")

    await db.delete(user)
    return {
        "success": True,
        "detail": f"Usuario @{user.username} y todos sus documentos asociados han sido eliminados de la base de datos.",
    }
