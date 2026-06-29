"""Controlador de créditos: solicitar crédito (ME/CO)."""
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.engine import Connection

from app.repositories import repo_creditos


def solicitar(
    conn: Connection,
    pkcliente: int,
    montosolicitud: Decimal,
    plazo: int,
    codtipocredito: str,
    codactividadeconomica: str,
    montoingresoneto: Decimal,
) -> dict:
    if codtipocredito not in repo_creditos.MAPA_TIPO_CREDITO:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tipo de crédito fuera de alcance (solo ME o CO)",
        )
    try:
        res = repo_creditos.crear_solicitud(
            conn,
            pkcliente=pkcliente,
            montosolicitud=montosolicitud,
            plazo=plazo,
            codtipocredito=codtipocredito,
            codactividadeconomica=codactividadeconomica,
            montoingresoneto=montoingresoneto,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    estado = res.get("estado", "En Evaluación")
    mensaje = "Solicitud registrada (En Evaluación)"
    if estado == "Rechazado":
        mensaje = "Solicitud registrada como NO APTO por capacidad de pago"

    return {
        "mensaje": mensaje,
        "estado": estado,
        "montosolicitud": montosolicitud,
        "plazo": plazo,
        **res,
    }
