"""
Auth router: register, login, logout, profile, admin panel.
Install: pip install python-jose[cryptography] passlib[bcrypt] python-multipart
"""
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import jwt, JWTError

from database import get_db, UserModel

router = APIRouter(prefix="/auth", tags=["auth"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])

# ─── Config ───────────────────────────────────────────────────────────────────

SECRET_KEY = os.environ.get("JWT_SECRET", secrets.token_hex(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 168  # 7 days

pwd_ctx = CryptContext(schemes=["argon2"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

# Admin email (set via env var or first registered user)
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")


# ─── Schemas ──────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str = ""

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    email: str
    full_name: str
    role: str

class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    created_at: datetime
    approved_at: Optional[datetime] = None
    notes: str = ""
    class Config:
        from_attributes = True

class ApproveRequest(BaseModel):
    user_id: int
    notes: str = ""

class RejectRequest(BaseModel):
    user_id: int
    reason: str = ""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return pwd_ctx.hash(password[:72])

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain[:72], hashed)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)

def create_token(user_id: int, email: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": str(user_id), "email": email, "role": role, "exp": expire},
        SECRET_KEY, algorithm=ALGORITHM
    )

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> UserModel:
    if not credentials:
        raise HTTPException(status_code=401, detail="Не авторизован")
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Недействительный токен")

    result = await db.execute(select(UserModel).where(UserModel.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return user

async def require_approved(user: UserModel = Depends(get_current_user)) -> UserModel:
    if user.role == "pending":
        raise HTTPException(status_code=403, detail="Аккаунт ожидает подтверждения администратором")
    if user.role not in ("approved", "admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    return user

async def require_admin(user: UserModel = Depends(get_current_user)) -> UserModel:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Только для администратора")
    return user


# ─── Auth Routes ──────────────────────────────────────────────────────────────

@router.post("/register", response_model=dict)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # Check duplicate
    existing = await db.execute(select(UserModel).where(UserModel.email == data.email.lower()))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email уже зарегистрирован")

    # Count users — first user becomes admin automatically
    count_result = await db.execute(select(UserModel))
    is_first = len(count_result.scalars().all()) == 0

    # Check if email matches configured admin email
    is_admin = is_first or (ADMIN_EMAIL and data.email.lower() == ADMIN_EMAIL.lower())

    user = UserModel(
        email=data.email.lower().strip(),
        password_hash=hash_password(data.password),
        full_name=data.full_name.strip(),
        role="admin" if is_admin else "pending",
        approved_at=datetime.utcnow() if is_admin else None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    if is_admin:
        return {
            "message": "Аккаунт администратора создан",
            "role": "admin",
            "auto_approved": True,
        }
    return {
        "message": "Регистрация отправлена. Ожидайте подтверждения администратора.",
        "role": "pending",
        "auto_approved": False,
    }


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(UserModel).where(UserModel.email == data.email.lower())
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    if user.role == "pending":
        raise HTTPException(
            status_code=403,
            detail="Аккаунт ожидает подтверждения. Администратор проверит заявку."
        )

    user.last_login_at = datetime.utcnow()
    await db.commit()

    token = create_token(user.id, user.email, user.role)
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
    )


@router.get("/me", response_model=UserOut)
async def get_me(user: UserModel = Depends(get_current_user)):
    return user


# ─── Admin Routes ─────────────────────────────────────────────────────────────

@admin_router.get("/users", response_model=List[UserOut])
async def list_users(
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    status_filter: Optional[str] = None,  # pending | approved | admin
):
    query = select(UserModel).order_by(UserModel.created_at.desc())
    if status_filter:
        query = query.where(UserModel.role == status_filter)
    result = await db.execute(query)
    return result.scalars().all()


@admin_router.get("/stats")
async def admin_stats(
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    all_users = (await db.execute(select(UserModel))).scalars().all()
    return {
        "total": len(all_users),
        "pending": sum(1 for u in all_users if u.role == "pending"),
        "approved": sum(1 for u in all_users if u.role == "approved"),
        "admins": sum(1 for u in all_users if u.role == "admin"),
    }


@admin_router.post("/approve")
async def approve_user(
    data: ApproveRequest,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UserModel).where(UserModel.id == data.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    user.role = "approved"
    user.approved_at = datetime.utcnow()
    user.approved_by = admin.email
    user.notes = data.notes
    await db.commit()

    return {"ok": True, "message": f"Пользователь {user.email} подтверждён"}


@admin_router.post("/reject")
async def reject_user(
    data: RejectRequest,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UserModel).where(UserModel.id == data.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    user.role = "rejected"
    user.notes = data.reason
    await db.commit()
    return {"ok": True, "message": f"Пользователь {user.email} отклонён"}


@admin_router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UserModel).where(UserModel.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.role == "admin":
        raise HTTPException(status_code=400, detail="Нельзя удалить администратора")
    await db.delete(user)
    await db.commit()
    return {"deleted": user_id}


@admin_router.post("/make-admin/{user_id}")
async def make_admin(
    user_id: int,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UserModel).where(UserModel.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.role = "admin"
    user.approved_at = datetime.utcnow()
    user.approved_by = admin.email
    await db.commit()
    return {"ok": True}


# ─── Migration helper (run once) ──────────────────────────────────────────────

async def migrate_add_user_columns():
    """
    Run this once to add user-related columns to existing DB.
    Called automatically at startup in main.py.
    """
    import aiosqlite
    from pathlib import Path
    db_path = Path.home() / ".newsmonitoer" / "news.db"
    async with aiosqlite.connect(db_path) as db:
        # Add user_id to feeds if missing
        cols = [row[1] for row in await (await db.execute("PRAGMA table_info(feeds)")).fetchall()]
        if "user_id" not in cols:
            await db.execute("ALTER TABLE feeds ADD COLUMN user_id INTEGER REFERENCES users(id)")
            await db.commit()
