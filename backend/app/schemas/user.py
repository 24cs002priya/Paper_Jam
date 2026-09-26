from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class UserProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    phone: str | None = Field(default=None, max_length=32)

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value):
        if value is not None:
            value = " ".join(value.split())
            if len(value) < 2:
                raise ValueError("Full name must contain at least two characters")
        return value

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value):
        if value is None or not value.strip():
            return value
        value = value.strip()
        if not any(character.isdigit() for character in value):
            raise ValueError("Phone must contain a digit")
        return value

    @model_validator(mode="after")
    def validate_name_if_present(self):
        if "full_name" in self.model_fields_set and self.full_name is None:
            raise ValueError("Full name cannot be null")
        return self


class AdminUserUpdate(UserProfileUpdate):
    status: str | None = None
    role: str | None = None
    department_id: str | None = None

    @model_validator(mode="after")
    def validate_status_and_role_if_present(self):
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("Status cannot be null")
        if "role" in self.model_fields_set and self.role is None:
            raise ValueError("Role cannot be null")
        return self

    @field_validator("status")
    @classmethod
    def valid_status(cls, value):
        if value is not None and value not in {"active", "inactive"}:
            raise ValueError("Status must be active or inactive")
        return value

    @field_validator("role")
    @classmethod
    def valid_role(cls, value):
        if value is not None and value not in {"applicant", "officer", "admin"}:
            raise ValueError("Role must be applicant, officer, or admin")
        return value


class AdminUserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    phone: str = Field(min_length=5, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    role: str = "officer"
    department_id: str | None = None

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("Full name must contain at least two characters")
        return value

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value: str) -> str:
        value = value.strip()
        if not any(character.isdigit() for character in value):
            raise ValueError("Phone must contain a digit")
        return value

    @field_validator("role")
    @classmethod
    def officer_only(cls, value):
        if value != "officer":
            raise ValueError("Admin-created accounts must use the officer role")
        return value
