"""
models.py – SQLAlchemy ORM models for Users and uploaded Documents.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Integer, Text, ForeignKey, Float
from sqlalchemy.orm import relationship
from database import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=generate_uuid)
    username = Column(String(80), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(150), nullable=True)
    avatar_color = Column(String(7), default="#10b981")  # Emerald accent
    role = Column(String(20), default="user", nullable=False)  # "admin" or "user"
    is_active = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    documents = relationship("Document", back_populates="owner", cascade="all, delete-orphan")
    chat_messages = relationship("ChatMessage", back_populates="user", cascade="all, delete-orphan")
    kb_messages = relationship("KBMessage", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    filename = Column(String(500), nullable=False)
    original_name = Column(String(500), nullable=False)
    page_count = Column(Integer, default=0)
    chunk_count = Column(Integer, default=0)
    file_size_kb = Column(Integer, default=0)
    collection_name = Column(String(200), nullable=False, unique=True)
    uploaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    summary = Column(Text, nullable=True)
    key_concepts = Column(Text, nullable=True)  # JSON string list
    complexity_level = Column(String(50), nullable=True)
    # Archivo Vivo: base de conocimiento con PDF, audio y video
    media_type = Column(String(10), default="pdf")        # pdf | audio | video
    source_path = Column(String(1000), nullable=True)     # ruta relativa dentro de base_conocimiento/
    duration_sec = Column(Float, nullable=True)           # duración (audio / video)
    file_sig = Column(String(100), nullable=True)         # tamaño-fecha para detectar cambios en la carpeta

    owner = relationship("User", back_populates="documents")
    chat_messages = relationship("ChatMessage", back_populates="document", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)
    sources = Column(Text, nullable=True)  # JSON string with page references
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="chat_messages")
    document = relationship("Document", back_populates="chat_messages")


class KBMessage(Base):
    """Conversación con TODA la base de conocimiento (no con un solo archivo)."""
    __tablename__ = "kb_messages"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    sources = Column(Text, nullable=True)  # JSON: [{doc_id, name, media_type, page, start_sec}]
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
