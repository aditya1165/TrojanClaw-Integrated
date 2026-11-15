"""Account input and public profile contracts. Never return password hashes."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class RegisterRequest(LoginRequest):
    name: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-z0-9_]+$")
    password: SecretStr = Field(min_length=8, max_length=128)
    confirm_password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def matching_passwords(self) -> "RegisterRequest":
        if self.password.get_secret_value() != self.confirm_password.get_secret_value():
            raise ValueError("Passwords do not match.")
        return self


class AccountProfile(BaseModel):
    id: UUID
    name: str
    username: str


class UscLoginStartRequest(BaseModel):
    """Where to open the sign-in popup (screen pixels, top-left corner). Optional."""
    left: int | None = Field(default=None, ge=-20000, le=20000)
    top: int | None = Field(default=None, ge=-20000, le=20000)


class UscLoginStart(BaseModel):
    attempt_id: str


class UscLoginPoll(BaseModel):
    attempt_id: str = Field(min_length=1, max_length=64)


class UscLoginStatus(BaseModel):
    status: Literal["waiting", "failed", "done"]
    detail: str | None = None
    account: AccountProfile | None = None
