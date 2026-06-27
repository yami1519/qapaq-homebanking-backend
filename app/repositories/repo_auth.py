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

            asegurar_cuenta_ahorro_cliente(conn, cliente["pkcliente"])

            return {**cliente, "pkusuario": usuario["pkusuario"], "username": dni}
    except IntegrityError as exc:
        if conn.in_transaction():
            conn.rollback()
        mensaje = str(getattr(exc, "orig", exc)).lower()
        if "usuarios_homebanking" not in mensaje:
            raise
        raise CuentaHomebankingExistente from exc


def asegurar_cuenta_ahorro_cliente(conn: Connection, pkcliente: int) -> dict:
    """Asegura una cuenta AH activa para el cliente durante el registro."""
    cuenta_existente = _buscar_cuenta_ah_activa(conn, pkcliente)
    if cuenta_existente is not None:
        return cuenta_existente

    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('dcuentaahorro:AH'))"))

    cuenta_existente = _buscar_cuenta_ah_activa(conn, pkcliente)
    if cuenta_existente is not None:
        return cuenta_existente

    nuevo_codigo = conn.execute(
        text(
            """
            SELECT 'AH' || LPAD(
                (
                    COALESCE(
                        MAX(
                            NULLIF(
                                REGEXP_REPLACE(codcuentaahorro, '[^0-9]', '', 'g'),
                                ''
                            )::int
                        ),
                        0
                    ) + 1
                )::text,
                8,
                '0'
            ) AS nuevo_codigo
            FROM dcuentaahorro
            WHERE UPPER(TRIM(codcuentaahorro)) LIKE 'AH%'
              AND UPPER(TRIM(codcuentaahorro)) ~ '^AH[0-9]{8}$'
            """
        )
    ).scalar_one()

    nueva_cuenta = conn.execute(
        text(
            """
            INSERT INTO dcuentaahorro (codcuentaahorro, pkcliente)
            VALUES (:codcuentaahorro, :pkcliente)
            RETURNING pkcuentaahorro, codcuentaahorro, pkcliente
            """
        ),
        {"codcuentaahorro": nuevo_codigo, "pkcliente": pkcliente},
    ).mappings().one()

    resultado = conn.execute(
        text(
            """
            WITH plantilla AS (
                SELECT f.*
                FROM fcuentaahorro f
                JOIN dcuentaahorro ca ON ca.pkcuentaahorro = f.pkcuentaahorro
                JOIN destadocuenta ec ON ec.pkestadocuenta = f.pkestadocuenta
                JOIN dtipocuentaahorro tca ON tca.pktipocuentaahorro = f.pktipocuentaahorro
                WHERE UPPER(TRIM(ca.codcuentaahorro)) LIKE 'AH%'
                  AND UPPER(TRIM(ca.codcuentaahorro)) ~ '^AH[0-9]{8}$'
                  AND f.periododia = (
                      SELECT MAX(f2.periododia)
                      FROM fcuentaahorro f2
                      WHERE f2.pkcuentaahorro = ca.pkcuentaahorro
                  )
                  AND TRIM(ec.codestadocuenta) = '01'
                  AND TRIM(tca.codtipocuentaahorro) = 'AC'
                ORDER BY f.periododia DESC, ca.pkcuentaahorro ASC
                LIMIT 1
            )
            INSERT INTO fcuentaahorro (
                periododia,
                pkcuentaahorro,
                pkproductoahorro,
                pkmoneda,
                pktipocuentaahorro,
                pktipotasaahorro,
                pkcliente,
                pkauxiliar,
                pkoperador,
                pkadministrador,
                pkjeferegional,
                pkagencia,
                pkestadocuenta,
                tipocambio,
                montosaldocapitaltotal,
                montosaldointerestotal,
                montosaldopromediototal,
                fechaaperturacuenta,
                montodepositoapertura,
                tasainterescuenta,
                tasaefectivaanual,
                nrotitulares,
                nrofirmas,
                flagexoneracionimpuesto,
                flagexoneracioncomision,
                flagcuentapromocion,
                nrooperacioneslibres,
                fechaultimaconsulta,
                flag_ac,
                montosaldodisponible_ac,
                montosaldominimo_ac,
                montosaldocontable_ac,
                montointeresacuantcap_ac,
                nrooperaciones_ac,
                flag_pf,
                fechavigencia_pf,
                montosaldocapital_pf,
                nrodiasplazofijo_pf,
                montointerespactado_pf,
                montointerespagado_pf,
                montointeresdevengado_pf,
                tasapagada_pf,
                numerorenovacion_pf,
                flag_cts,
                montocapital_cts,
                montointeres_cts,
                montocapitalintangible_cts,
                montointeresintangible_cts,
                codempleador_cts,
                codbancoorigentraslado_cts,
                codbancodestinotraslado_cts,
                flag_ap,
                montocapital_ap,
                montocuota_ap,
                nrocuota_ap,
                tasaincentivo_ap,
                fechavigencia_ap,
                fecultactualizacion
            )
            SELECT
                p.periododia,
                :pkcuentaahorro,
                p.pkproductoahorro,
                p.pkmoneda,
                p.pktipocuentaahorro,
                p.pktipotasaahorro,
                :pkcliente,
                p.pkauxiliar,
                p.pkoperador,
                p.pkadministrador,
                p.pkjeferegional,
                p.pkagencia,
                p.pkestadocuenta,
                p.tipocambio,
                0,
                0,
                0,
                CURRENT_DATE,
                0,
                p.tasainterescuenta,
                p.tasaefectivaanual,
                1,
                1,
                p.flagexoneracionimpuesto,
                p.flagexoneracioncomision,
                p.flagcuentapromocion,
                0,
                NULL,
                'S',
                0,
                p.montosaldominimo_ac,
                0,
                0,
                0,
                'N',
                NULL,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                'N',
                0,
                0,
                0,
                0,
                NULL,
                NULL,
                NULL,
                'N',
                0,
                0,
                0,
                0,
                NULL,
                now()
            FROM plantilla p
            RETURNING pkcuentaahorro
            """
        ),
        {"pkcuentaahorro": nueva_cuenta["pkcuentaahorro"], "pkcliente": pkcliente},
    ).mappings().first()
    if resultado is None:
        raise RuntimeError("No existe una plantilla AH activa para crear cuenta de ahorro")

    return dict(nueva_cuenta)


def _buscar_cuenta_ah_activa(conn: Connection, pkcliente: int) -> dict | None:
    row = conn.execute(
        text(
            """
            SELECT ca.pkcuentaahorro, ca.codcuentaahorro, ca.pkcliente
            FROM dcuentaahorro ca
            JOIN fcuentaahorro f ON f.pkcuentaahorro = ca.pkcuentaahorro
            JOIN destadocuenta ec ON ec.pkestadocuenta = f.pkestadocuenta
            WHERE ca.pkcliente = :pkcliente
              AND UPPER(TRIM(ca.codcuentaahorro)) LIKE 'AH%'
              AND UPPER(TRIM(ca.codcuentaahorro)) ~ '^AH[0-9]{8}$'
              AND f.periododia = (
                  SELECT MAX(f2.periododia)
                  FROM fcuentaahorro f2
                  WHERE f2.pkcuentaahorro = ca.pkcuentaahorro
              )
              AND TRIM(ec.codestadocuenta) = '01'
            LIMIT 1
            """
        ),
        {"pkcliente": pkcliente},
    ).mappings().first()
    return dict(row) if row else None


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
