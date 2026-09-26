from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class BusinessSignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str = Field(min_length=2, max_length=160)
    business_name: str = Field(min_length=2, max_length=200)
    legal_name: str | None = Field(default=None, max_length=250)
    email: EmailStr
    phone: str = Field(min_length=5, max_length=32)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("full_name", "business_name", mode="before")
    @classmethod
    def clean_required_name(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("This name is required")
        return " ".join(value.split())

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        return str(value).strip().lower()

    @field_validator("legal_name", mode="before")
    @classmethod
    def clean_legal_name(cls, value):
        if value is None:
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Legal name cannot be blank")
        return " ".join(value.split())

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value):
        value = value.strip()
        if not any(character.isdigit() for character in value):
            raise ValueError("Phone must contain a digit")
        return value


class BusinessProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legal_name: str | None = Field(default=None, max_length=250)
    display_name: str | None = Field(default=None, max_length=200)
    business_type: str | None = Field(default=None, max_length=120)
    industry: str | None = Field(default=None, max_length=120)
    entity_type: str | None = Field(default=None, max_length=120)
    registration_number: str | None = Field(default=None, max_length=100)
    pan: str | None = Field(default=None, max_length=20)
    gstin: str | None = Field(default=None, max_length=20)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    registered_address: dict[str, str | None] | None = None
    operating_address: dict[str, str | None] | None = None
    contact_person: dict[str, str | None] | None = None
    employee_count: int | None = Field(default=None, ge=0)
    annual_turnover: float | None = Field(default=None, ge=0)
    production_capacity_kg_per_day: float | None = Field(default=None, ge=0)
    business_activities: list[str] | None = Field(default=None, max_length=100)
    location_details: dict[str, str | None] | None = None

    @field_validator("legal_name", "display_name", "business_type", "industry", "entity_type", mode="before")
    @classmethod
    def trim_text(cls, value):
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("This field cannot be blank")
        return value.strip()

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value):
        if value is None or not value.strip():
            return value
        value = value.strip()
        if not any(character.isdigit() for character in value):
            raise ValueError("Phone must contain a digit")
        return value

    @field_validator("registered_address", "operating_address", mode="before")
    @classmethod
    def validate_address_fields(cls, value):
        if value is None:
            return value
        allowed = {"address_line_1", "address_line_2", "city", "district", "state", "pincode", "country"}
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError("Address contains unsupported fields")
        if any(item is not None and not isinstance(item, str) for item in value.values()):
            raise ValueError("Address values must be text")
        return {key: item.strip() if item is not None else None for key, item in value.items()}


class BusinessApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: str = Field(min_length=24, max_length=24, pattern=r"^[0-9a-fA-F]{24}$")


class BusinessApplicationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_type: str | None = Field(default=None, min_length=1, max_length=120)
    approval_name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("approval_type", "approval_name")
    @classmethod
    def clean_optional_text(cls, value):
        if value is None:
            return value
        value = " ".join(value.split())
        if not value:
            raise ValueError("Application fields cannot be blank")
        return value


class BusinessSignupResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict
    business: dict


class Contact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr | None = None
    phone: str | None = None

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value):
        if value is None or not value.strip():
            return value
        value = value.strip()
        if not any(character.isdigit() for character in value):
            raise ValueError("Phone must contain a digit")
        return value


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address_line_1: str = Field(min_length=1, max_length=250)
    address_line_2: str | None = Field(default=None, max_length=250)
    city: str = Field(min_length=1, max_length=120)
    district: str = Field(min_length=1, max_length=120)
    state: str = Field(min_length=1, max_length=120)
    pincode: str = Field(min_length=4, max_length=12)
    country: str = Field(default="India", min_length=2, max_length=100)

    @field_validator("pincode")
    @classmethod
    def valid_pincode(cls, value: str) -> str:
        value = value.strip()
        if not value.replace(" ", "").replace("-", "").isalnum():
            raise ValueError("Pincode contains invalid characters")
        return value


class BusinessFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_name: str = Field(min_length=2, max_length=200)
    legal_name: str | None = Field(default=None, max_length=250)
    business_type: str = Field(min_length=2, max_length=120)
    industry: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)
    entity_type: str = Field(min_length=2, max_length=120)
    registration_number: str | None = Field(default=None, max_length=100)
    contact: Contact
    address: Address

    @field_validator("business_name", "business_type", "industry", "entity_type", mode="before")
    @classmethod
    def require_nonblank_text(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("This field cannot be blank")
        return value.strip()


class BusinessCreate(BusinessFields):
    pass


class BusinessUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_name: str | None = Field(default=None, min_length=2, max_length=200)
    legal_name: str | None = Field(default=None, max_length=250)
    business_type: str | None = Field(default=None, min_length=2, max_length=120)
    industry: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    entity_type: str | None = Field(default=None, min_length=2, max_length=120)
    registration_number: str | None = Field(default=None, max_length=100)
    contact: Contact | None = None
    address: Address | None = None

    @field_validator("business_name", "business_type", "industry", "entity_type", mode="before")
    @classmethod
    def require_nonblank_optional_text(cls, value):
        if value is None:
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError("This field cannot be blank")
        return value.strip()

    @model_validator(mode="after")
    def validate_non_nullable_fields(self):
        for field in ("business_name", "business_type", "industry", "description", "entity_type", "contact", "address"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field.replace('_', ' ').capitalize()} cannot be null")
        return self


class BusinessResponse(BusinessFields):
    id: str
    status: str
    created_at: datetime
    updated_at: datetime
