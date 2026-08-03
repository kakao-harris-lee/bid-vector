"""Authentication routes"""

import hmac
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import verify_password, get_password_hash, create_access_token
from app.core.single_user import ensure_operator_account, get_operator_account
from app.models.models import User
from app.schemas.schemas import (
    OperatorLoginRequest,
    OperatorPasswordResetRequest,
    TokenResponse,
    UserCreate,
    UserResponse,
)

router = APIRouter()


def _create_session_response(user: User) -> dict:
    access_token = create_access_token({"sub": str(user.id), "type": "access"})
    refresh_token = create_access_token(
        {"sub": str(user.id), "type": "refresh"},
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "operator_id": user.id,
        "username": user.username,
    }


@router.post("/register", response_model=UserResponse, deprecated=True)
@router.post("/bootstrap", response_model=UserResponse)
def register(user: UserCreate, db: Session = Depends(get_db)):
    """Initialize the singleton operator account used by the service."""
    operator = get_operator_account(db)
    if operator:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Single-user mode already has an operator account. Update /api/v1/operator/profile instead.",
        )

    existing_user = (
        db.query(User)
        .filter((User.username == user.username) | (User.email == user.email))
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already exists",
        )

    db_user = User(
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        company=user.company,
        hashed_password=get_password_hash(user.password),
        is_admin=True,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    return db_user


@router.post("/login", response_model=TokenResponse, deprecated=True)
@router.post("/session", response_model=TokenResponse)
def login(
    credentials: Optional[OperatorLoginRequest] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Login the singleton operator account."""
    resolved_username = credentials.username if credentials else username
    resolved_password = credentials.password if credentials else password

    if not resolved_username or not resolved_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="username and password are required",
        )

    user = db.query(User).filter(User.username == resolved_username).first()

    if not user or not verify_password(resolved_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive"
        )

    return _create_session_response(user)


@router.post("/password-reset", response_model=TokenResponse)
def reset_operator_password(
    request: OperatorPasswordResetRequest, db: Session = Depends(get_db)
):
    """Reset the singleton operator password when the server reset token matches."""
    configured_token = settings.OPERATOR_PASSWORD_RESET_TOKEN.strip()
    if not configured_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password reset is not configured",
        )

    if not hmac.compare_digest(request.reset_token, configured_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password reset token",
        )

    operator = None
    if request.username is not None:
        operator = db.query(User).filter(User.username == request.username).first()
    if operator is None:
        operator = ensure_operator_account(db)

    if request.username is not None and request.username != operator.username:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Operator account not found",
        )

    operator.hashed_password = get_password_hash(request.new_password)
    operator.is_active = True
    db.commit()
    db.refresh(operator)

    return _create_session_response(operator)


@router.get("/me", response_model=UserResponse)
def get_current_user(db: Session = Depends(get_db)):
    """Get the current singleton operator profile."""
    return ensure_operator_account(db)
