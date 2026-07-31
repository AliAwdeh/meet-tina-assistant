from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class UserRead(BaseModel):
    id: str
    name: str
    email: EmailStr
    role: str
    status: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


class CreateUserRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)
    role: str = "assistant_user"


class PasskeyOptionsResponse(BaseModel):
    options: dict[str, Any]


class PasskeyRegisterVerifyRequest(BaseModel):
    credential: dict[str, Any]
    device_name: str = Field(default="Passkey", min_length=1, max_length=255)


class PasskeyLoginOptionsRequest(BaseModel):
    email: EmailStr


class PasskeyLoginVerifyRequest(BaseModel):
    email: EmailStr
    credential: dict[str, Any]


class PasskeyRead(BaseModel):
    id: str
    device_name: str
    created_at: datetime
    last_used_at: datetime | None = None
