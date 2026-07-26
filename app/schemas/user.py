"""User / operator authentication request-response schemas."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.schemas._shared import EmailStr


class UserBase(BaseModel):
    username: str
    email: EmailStr
    full_name: str
    company: Optional[str] = None


class UserCreate(UserBase):
    password: str


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    company: Optional[str] = None


class UserResponse(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class OperatorLoginRequest(BaseModel):
    username: str
    password: str


class OperatorPasswordResetRequest(BaseModel):
    username: Optional[str] = None
    reset_token: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    operator_id: Optional[int] = None
    username: Optional[str] = None
