"""Schemas pydantic para autenticación."""
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=8, max_length=12, examples=["11200007"])
    password: str = Field(..., min_length=8, max_length=72, examples=["MiClaveSegura123"])


class RegisterRequest(BaseModel):
    dni: str = Field(..., min_length=8, max_length=8, pattern=r"^\d{8}$", examples=["11209999"])
    celular: str = Field(..., min_length=9, max_length=15, pattern=r"^\d{9,15}$", examples=["987654321"])
    nombres: str = Field(..., min_length=2, max_length=100, examples=["Juan Carlos"])
    apellidos: str = Field(..., min_length=2, max_length=100, examples=["Quispe Ramos"])
    correo: str = Field(
        ...,
        min_length=5,
        max_length=100,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
        examples=["juan.quispe@email.com"],
    )
    password: str = Field(..., min_length=8, max_length=72, examples=["MiClaveSegura123"])


class ClienteInfo(BaseModel):
    codcliente: str
    nombre: str
    pkcliente: int
    username: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_min: int
    cliente: ClienteInfo


class RegisterResponse(BaseModel):
    mensaje: str
    cliente: ClienteInfo
