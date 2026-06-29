"""Escrituras SQL para solicitar un crédito (registro en dsolicitud).

Alcance: solo Microempresa (ME) y Consumo (CO).
"""
from decimal import Decimal
from math import isfinite

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.repositories.repo_cuentas import PERIODO_CARTERA

# codtipocredito del portal -> codtipocredito en dproducto
MAPA_TIPO_CREDITO = {"ME": "01", "CO": "03"}  # ME=Microempresa, CO=Consumo
ESTADO_EN_EVALUACION = "01"  # dsolicitudestado.codsolicitudestado
ESTADO_RECHAZADO = "03"
TEA_USADA = {"ME": 40.99, "CO": 97.99}


def _tea_a_tem(tea: float) -> float:
    tea_val = float(tea or 0)
    if not isfinite(tea_val) or tea_val < 0:
        return 0.0
    tea_decimal = tea_val / 100 if tea_val > 1 else tea_val
    return (1 + tea_decimal) ** (1 / 12) - 1


def _cuota_francesa(monto: Decimal, plazo: int, tea: float) -> float:
    principal = float(monto or 0)
    plazo = int(plazo or 0)
    if not isfinite(principal) or principal <= 0 or plazo <= 0:
        return 0.0
    tem = _tea_a_tem(tea)
    if not isfinite(tem) or tem <= 0:
        return principal / plazo
    factor = (1 + tem) ** plazo
    denominador = factor - 1
    if denominador <= 0 or not isfinite(denominador):
        return principal / plazo
    cuota = principal * tem * factor / denominador
    return cuota if isfinite(cuota) and cuota >= 0 else 0.0


def evaluar_capacidad_pago(
    montosolicitud: Decimal,
    plazo: int,
    codtipocredito: str,
    montoingresoneto: Decimal,
) -> dict:
    ingreso = float(montoingresoneto or 0)
    tea = TEA_USADA.get(codtipocredito, TEA_USADA["ME"])
    tem = _tea_a_tem(tea)
    cuota = _cuota_francesa(montosolicitud, plazo, tea)
    observaciones = []

    if ingreso <= 0 or not isfinite(ingreso):
        semaforo = "ROJO"
        resultado = "NO APTO"
        rds = None
        observaciones.append("Ingreso neto mensual inválido.")
        observaciones.append("Cliente no apto para aprobación automática.")
    else:
        rds_val = cuota / ingreso
        rds = round(rds_val * 100, 2)
        if rds_val > 0.50:
            semaforo = "ROJO"
            resultado = "NO APTO"
            observaciones.append("Cuota supera el 50% del ingreso neto mensual — riesgo crítico.")
            observaciones.append("Cliente no apto para aprobación automática.")
        elif rds_val > 0.35:
            semaforo = "AMARILLO"
            resultado = "OBSERVADO"
            observaciones.append("Requiere aprobación de jefe de agencia.")
        else:
            semaforo = "VERDE"
            resultado = "APROBABLE"
            observaciones.append("Capacidad de pago adecuada.")

    return {
        "semaforo": semaforo,
        "resultado": resultado,
        "tea_sugerida": tea,
        "tem_sugerida": round(tem * 100, 4),
        "cuota_estimada": round(cuota, 2),
        "rds": rds,
        "observaciones": observaciones,
    }


def _pk_producto_por_tipo(conn: Connection, cod_tipo_producto: str) -> int | None:
    return conn.execute(
        text(
            """
            SELECT MIN(pkproducto) FROM dproducto
            WHERE TRIM(codtipocredito) = :cod AND flagactivo = '1'
            """
        ),
        {"cod": cod_tipo_producto},
    ).scalar()


def _pk_estado_solicitud(conn: Connection, cod: str) -> int | None:
    return conn.execute(
        text("SELECT pksolicitudestado FROM dsolicitudestado WHERE TRIM(codsolicitudestado) = :c"),
        {"c": cod},
    ).scalar()


def _pk_actividad(conn: Connection, cod: str) -> int | None:
    return conn.execute(
        text("SELECT pkactividadeconomica FROM dactividadeconomica WHERE TRIM(codactividadeconomica) = :c"),
        {"c": cod},
    ).scalar()


def _agencia_asesor_del_cliente(conn: Connection, pkcliente: int) -> tuple[int, int]:
    """Toma agencia/asesor del crédito vigente del cliente; si no tiene, usa valores por defecto."""
    row = conn.execute(
        text(
            """
            SELECT pkagencia, pkasesor FROM fagcuentacredito
            WHERE pkcliente = :pk AND periodomes = :periodo
            ORDER BY pkcuentacredito DESC LIMIT 1
            """
        ),
        {"pk": pkcliente, "periodo": PERIODO_CARTERA},
    ).first()
    if row:
        return row[0], row[1]
    # Fallback: primera agencia activa y primer asesor existentes
    pkag = conn.execute(text("SELECT MIN(pkagencia) FROM dagencia")).scalar()
    pkas = conn.execute(text("SELECT MIN(pkasesor) FROM dasesor")).scalar()
    return pkag, pkas


def upsert_fuente_ingreso(
    conn: Connection, pkcliente: int, montoingresoneto: Decimal, pkactividad: int | None
) -> None:
    """Registra el ingreso del cliente (PK compuesta pkcliente+periodomes) de forma idempotente."""
    conn.execute(
        text(
            """
            INSERT INTO fclientefuenteingreso (pkcliente, periodomes, montofuenteingreso,
                                               pkactividadeconomicacliente, fecultactualizacion)
            VALUES (:pk, :periodo, :monto, :act, now())
            ON CONFLICT (pkcliente, periodomes)
            DO UPDATE SET montofuenteingreso = EXCLUDED.montofuenteingreso,
                          pkactividadeconomicacliente = EXCLUDED.pkactividadeconomicacliente,
                          fecultactualizacion = now()
            """
        ),
        {"pk": pkcliente, "periodo": PERIODO_CARTERA, "monto": montoingresoneto, "act": pkactividad},
    )


def crear_solicitud(
    conn: Connection,
    pkcliente: int,
    montosolicitud: Decimal,
    plazo: int,
    codtipocredito: str,
    codactividadeconomica: str,
    montoingresoneto: Decimal,
) -> dict:
    """Registra una solicitud en dsolicitud (estado inicial 'En Evaluación').

    pksolicitud proviene de dsolicitud_pksolicitud_seq y codsolicitud se deriva
    con 'SOL' || LPAD(currval(...)::text, 7, '0').
    """
    cod_tipo_producto = MAPA_TIPO_CREDITO[codtipocredito]
    evaluacion = evaluar_capacidad_pago(montosolicitud, plazo, codtipocredito, montoingresoneto)
    estado_codigo = ESTADO_RECHAZADO if evaluacion["resultado"] == "NO APTO" else ESTADO_EN_EVALUACION
    estado_texto = "Rechazado" if evaluacion["resultado"] == "NO APTO" else "En Evaluación"
    motivo = (
        "NO APTO: capacidad de pago crítica"
        if evaluacion["resultado"] == "NO APTO"
        else "Solicitud HB"
    )

    pkproducto = _pk_producto_por_tipo(conn, cod_tipo_producto)
    if pkproducto is None:
        raise ValueError(f"No hay producto activo para el tipo de crédito '{codtipocredito}'")

    pkestado = _pk_estado_solicitud(conn, estado_codigo)
    if pkestado is None:
        raise ValueError(f"No existe el estado '{estado_texto}' en dsolicitudestado")

    pkactividad = _pk_actividad(conn, codactividadeconomica)
    if pkactividad is None:
        raise ValueError(f"Actividad económica '{codactividadeconomica}' no encontrada")

    pkmoneda = conn.execute(
        text("SELECT pkmoneda FROM dmoneda WHERE TRIM(codmoneda) = 'SO'")
    ).scalar()
    pkagencia, pkasesor = _agencia_asesor_del_cliente(conn, pkcliente)

    # Registra/actualiza la fuente de ingreso (idempotente) antes de la solicitud.
    upsert_fuente_ingreso(conn, pkcliente, montoingresoneto, pkactividad)

    row = conn.execute(
        text(
            """
            INSERT INTO dsolicitud (
                pksolicitud, codsolicitud, pkcliente, codlineacredito,
                pksolicitudestado, pkmoneda, pkproducto,
                codtiposolicitud, destiposolicitud,
                montosolicitudcredito, nrocuotasolicitud, plazosolicitudcredito,
                fechasolicitudcredito, codususol, desmotivosolicitud,
                flaglibreamortizacioncredito, nrodiasgracia,
                pkactividadeconomicasolicitud, pkagencia, pkasesor,
                fechahoracreacion, fechahoraultmodificacion, fecultactualizacion
            ) VALUES (
                nextval('dsolicitud_pksolicitud_seq'),
                'SOL' || LPAD(currval('dsolicitud_pksolicitud_seq')::text, 7, '0'),
                :pkcliente, 'CR',
                :pkestado, :pkmoneda, :pkproducto,
                '01', 'Credito Nuevo',
                :monto, :plazo, :plazo,
                CURRENT_DATE, 'HB', :motivo,
                'N', 0,
                :pkactividad, :pkagencia, :pkasesor,
                now(), now(), now()
            )
            RETURNING pksolicitud, codsolicitud
            """
        ),
        {
            "pkcliente": pkcliente,
            "pkestado": pkestado,
            "pkmoneda": pkmoneda,
            "pkproducto": pkproducto,
            "monto": montosolicitud,
            "plazo": plazo,
            "motivo": motivo,
            "pkactividad": pkactividad,
            "pkagencia": pkagencia,
            "pkasesor": pkasesor,
        },
    ).mappings().first()
    conn.commit()
    return {
        "pksolicitud": row["pksolicitud"],
        "codsolicitud": row["codsolicitud"].strip(),
        "estado": estado_texto,
        **evaluacion,
    }
