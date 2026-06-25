"""Consultas SQL relacionadas con la autenticación del cliente del portal.

El cliente vive en dcliente y se autentica vía usuarios_homebanking.
NO se cruzan dpersonal ni dasesor (universos distintos).
"""
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

MAX_INTENTOS = 5


class CuentaHomebankingExistente(Exception):
    """El cliente ya tiene usuario de homebanking."""


def buscar_usuario_por_username(conn: Connection, username: str) -> dict | None:
    """Busca el usuario (case-insensitive) y une dcliente para nombre/codcliente."""
    sql = text(
        """
        SELECT u.pkusuario, u.pkcliente, u.username, u.password_hash,
               u.intentos_fallidos, u.bloqueado, u.activo,
               TRIM(c.codcliente) AS codcliente, c.nomcliente
        FROM usuarios_homebanking u
        JOIN dcliente c ON c.pkcliente = u.pkcliente
        WHERE LOWER(u.username) = LOWER(:username)
        """
    )
    row = conn.execute(sql, {"username": username}).mappings().first()
    return dict(row) if row else None


def registrar_homebanking(
    conn: Connection,
    *,
    dni: str,
    celular: str,
    nombres: str,
    apellidos: str,
    correo: str,
    password_hash: str,
) -> dict:
    """Crea acceso homebanking usando dcliente como maestro de clientes."""
    nombre_completo = f"{apellidos.strip()}, {nombres.strip()}"
    email = correo.strip().lower()

    try:
        with conn.begin():
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:dni))"), {"dni": dni})

            cliente = _buscar_cliente_por_dni(conn, dni)
            if cliente is None:
                cliente = _crear_cliente_desde_registro(
                    conn,
                    dni=dni,
                    celular=celular,
                    nombre_completo=nombre_completo,
                    email=email,
                )
            else:
                cliente = _actualizar_cliente_si_faltan_datos(
                    conn,
                    pkcliente=cliente["pkcliente"],
                    celular=celular,
                    email=email,
                    nombre_completo=nombre_completo,
                )

            if _buscar_usuario_por_pkcliente(conn, cliente["pkcliente"]) is not None:
                raise CuentaHomebankingExistente
            if buscar_usuario_por_username(conn, dni) is not None:
                raise CuentaHomebankingExistente

            usuario = conn.execute(
                text(
                    """
                    INSERT INTO usuarios_homebanking (
                        pkcliente, username, password_hash, intentos_fallidos,
                        bloqueado, activo, fecultactualizacion
                    )
                    VALUES (:pkcliente, :username, :password_hash, 0, 'N', 'S', now())
                    RETURNING pkusuario
                    """
                ),
                {"pkcliente": cliente["pkcliente"], "username": dni, "password_hash": password_hash},
            ).mappings().one()

            return {**cliente, "pkusuario": usuario["pkusuario"], "username": dni}
    except IntegrityError as exc:
        if conn.in_transaction():
            conn.rollback()
        raise CuentaHomebankingExistente from exc


def _buscar_cliente_por_dni(conn: Connection, dni: str) -> dict | None:
    row = conn.execute(
        text(
            """
            SELECT pkcliente, TRIM(codcliente) AS codcliente, nomcliente,
                   email, numerotelefonopersonal, telefono
            FROM dcliente
            WHERE TRIM(numerodocumentoidentidad) = :dni
            ORDER BY pkcliente
            LIMIT 1
            """
        ),
        {"dni": dni},
    ).mappings().first()
    return dict(row) if row else None


def _buscar_usuario_por_pkcliente(conn: Connection, pkcliente: int) -> dict | None:
    row = conn.execute(
        text(
            """
            SELECT pkusuario, pkcliente, username
            FROM usuarios_homebanking
            WHERE pkcliente = :pkcliente
            """
        ),
        {"pkcliente": pkcliente},
    ).mappings().first()
    return dict(row) if row else None


def _actualizar_cliente_si_faltan_datos(
    conn: Connection,
    *,
    pkcliente: int,
    celular: str,
    email: str,
    nombre_completo: str,
) -> dict:
    row = conn.execute(
        text(
            """
            UPDATE dcliente
            SET nomcliente = CASE
                    WHEN NULLIF(TRIM(nomcliente), '') IS NULL THEN :nombre
                    ELSE nomcliente
                END,
                email = CASE
                    WHEN NULLIF(TRIM(COALESCE(email, '')), '') IS NULL THEN :email
                    ELSE email
                END,
                numerotelefonopersonal = CASE
                    WHEN NULLIF(TRIM(COALESCE(numerotelefonopersonal, '')), '') IS NULL THEN :celular
                    ELSE numerotelefonopersonal
                END,
                telefono = CASE
                    WHEN NULLIF(TRIM(COALESCE(telefono, '')), '') IS NULL THEN :celular
                    ELSE telefono
                END,
                fecultactualizacion = now()
            WHERE pkcliente = :pkcliente
            RETURNING pkcliente, TRIM(codcliente) AS codcliente, nomcliente
            """
        ),
        {"pkcliente": pkcliente, "celular": celular, "email": email, "nombre": nombre_completo},
    ).mappings().one()
    return dict(row)


def _crear_cliente_desde_registro(
    conn: Connection,
    *,
    dni: str,
    celular: str,
    nombre_completo: str,
    email: str,
) -> dict:
    row = conn.execute(
        text(
            """
            WITH nuevo AS (
                SELECT nextval(pg_get_serial_sequence('dcliente', 'pkcliente')) AS pkcliente
            )
            INSERT INTO dcliente (
                pkcliente, codcliente, nomcliente,
                pkclasepersona, codclasepersona, desclasepersona,
                fechaingresocaja, email,
                pktipodocumentoidentidad, codtipodocumentoidentidad, destipodocumentoidentidad,
                numerodocumentoidentidad, numerotelefonopersonal, telefono, fecultactualizacion
            )
            SELECT
                nuevo.pkcliente,
                'CLI' || LPAD(nuevo.pkcliente::text, 6, '0'),
                :nombre,
                cp.pkclasepersona,
                cp.codclasepersona,
                cp.desclasepersona,
                CURRENT_DATE,
                :email,
                td.pktipodocumentoidentidad,
                td.codtipodocumentoidentidad,
                td.destipodocumentoidentidad,
                :dni,
                :celular,
                :celular,
                now()
            FROM nuevo
            JOIN dclasepersona cp ON TRIM(cp.codclasepersona) = '01'
            JOIN dtipodocumentoidentidad td ON TRIM(td.codtipodocumentoidentidad) = '01'
            RETURNING pkcliente, TRIM(codcliente) AS codcliente, nomcliente
            """
        ),
        {"dni": dni, "celular": celular, "email": email, "nombre": nombre_completo},
    ).mappings().one()
    return dict(row)


def registrar_login_exitoso(conn: Connection, pkusuario: int) -> None:
    """Actualiza ultimo_acceso y resetea intentos_fallidos."""
    conn.execute(
        text(
            """
            UPDATE usuarios_homebanking
            SET ultimo_acceso = now(), intentos_fallidos = 0, fecultactualizacion = now()
            WHERE pkusuario = :pk
            """
        ),
        {"pk": pkusuario},
    )
    conn.commit()


def registrar_login_fallido(conn: Connection, pkusuario: int) -> int:
    """Incrementa intentos_fallidos; tras MAX_INTENTOS marca bloqueado='S'.
    Devuelve el nuevo número de intentos.
    """
    nuevos = conn.execute(
        text(
            """
            UPDATE usuarios_homebanking
            SET intentos_fallidos = intentos_fallidos + 1,
                bloqueado = CASE WHEN intentos_fallidos + 1 >= :maxi THEN 'S' ELSE bloqueado END,
                fecultactualizacion = now()
            WHERE pkusuario = :pk
            RETURNING intentos_fallidos
            """
        ),
        {"pk": pkusuario, "maxi": MAX_INTENTOS},
    ).scalar()
    conn.commit()
    return nuevos
