"""Schemas pydantic para solicitud de crédito."""
from decimal import Decimal
from typing import Literal
from typing import Optional

from pydantic import BaseModel, Field


class SolicitudCreditoRequest(BaseModel):
    montosolicitud: Decimal = Field(..., gt=0)
    plazo: int = Field(..., gt=0, description="Número de cuotas / meses")
    codtipocredito: Literal["ME", "CO"] = Field(..., description="ME=Microempresa, CO=Consumo")
    codactividadeconomica: str
    montoingresoneto: Decimal = Field(..., ge=0)


class SolicitudCreditoResponse(BaseModel):
    mensaje: str
    pksolicitud: int
    codsolicitud: str
    estado: str
    montosolicitud: Decimal
    plazo: int
    semaforo: Optional[str] = None
    resultado: Optional[str] = None
    tea_sugerida: Optional[float] = None
    tem_sugerida: Optional[float] = None
    cuota_estimada: Optional[float] = None
    rds: Optional[float] = None
    observaciones: list[str] = Field(default_factory=list)
