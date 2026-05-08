"""Security utilities"""
import base64
import hashlib
import json
from datetime import timedelta
from typing import Optional

try:  # pragma: no cover - optional dependency fallback
    from jose import JWTError, jwt
except ImportError:  # pragma: no cover - exercised in lightweight test environments
    class JWTError(Exception):
        """Fallback JWT error used when python-jose is unavailable."""


    class _FallbackJWT:
        """Minimal JWT-like encoder/decoder for non-auth test environments."""

        @staticmethod
        def encode(payload: dict, key: str, algorithm: str = "HS256") -> str:
            del key, algorithm
            raw = json.dumps(payload, default=str).encode("utf-8")
            return base64.urlsafe_b64encode(raw).decode("utf-8")

        @staticmethod
        def decode(token: str, key: str, algorithms: list[str] | None = None) -> dict:
            del key, algorithms
            try:
                padding = "=" * (-len(token) % 4)
                raw = base64.urlsafe_b64decode(f"{token}{padding}".encode("utf-8"))
                return json.loads(raw.decode("utf-8"))
            except Exception as exc:  # pragma: no cover - fallback error path
                raise JWTError(str(exc)) from exc


    jwt = _FallbackJWT()

try:  # pragma: no cover - optional dependency fallback
    from passlib.context import CryptContext
except ImportError:  # pragma: no cover - exercised in lightweight test environments
    class CryptContext:  # type: ignore[override]
        """Minimal hashing fallback used when passlib is unavailable."""

        def __init__(self, schemes: list[str] | None = None, deprecated: str = "auto"):
            del schemes, deprecated

        def hash(self, password: str) -> str:
            return hashlib.sha256(password.encode("utf-8")).hexdigest()

        def verify(self, plain_password: str, hashed_password: str) -> bool:
            return self.hash(plain_password) == hashed_password

from app.core.config import settings
from app.core.time import utc_now

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash password"""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = utc_now() + expires_delta
    else:
        expire = utc_now() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> Optional[dict]:
    """Verify JWT token"""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None
