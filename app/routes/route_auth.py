"""Router de autenticación."""
from fastapi import APIRouter, Depends
from sqlalchemy.engine import Connection

from app.controllers import ctrl_auth
from app.core.cfg_database import get_db
from app.schemas.sch_auth import LoginRequest, LoginResponse, RegisterRequest, RegisterResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, conn: Connection = Depends(get_db)):
    return ctrl_auth.login(conn, body.username, body.password)


@router.post("/register", response_model=RegisterResponse, status_code=201)
def register(body: RegisterRequest, conn: Connection = Depends(get_db)):
    return ctrl_auth.register(
        conn,
        dni=body.dni,
        celular=body.celular,
        nombres=body.nombres,
        apellidos=body.apellidos,
        correo=body.correo,
        password=body.password,
    )
