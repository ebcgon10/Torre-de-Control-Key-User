"""Limpieza de exportes del WMS y cálculo de indicadores.

No depende de Streamlit: el mismo módulo lo usará el reporte automático (fase 3).
"""
import io

import numpy as np
import pandas as pd

import config as cfg

COLS_PICKING = [
    "id_de_lista", "id_de_usuario_ultima_recogida", "descripcion_usuario",
    "h_inicio", "h_termino", "cajas", "lineas", "zona_de_trabajo",
]
COLS_GRUA = [
    "codigo_de_operacion", "codigo_de_actividad", "usuario",
    "nombre_usuario", "fecha_de_transaccion", "movimientos",
]
CLAVE_OPERARIO = ["fecha_op", "turno", "usuario"]


# ---------------------------------------------------------------- lectura

def leer_csv(contenido: bytes) -> pd.DataFrame:
    """Lee un CSV del WMS detectando codificación y separador."""
    for codificacion in ("utf-8-sig", "latin-1"):
        try:
            texto = contenido.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    primera_linea = texto.split("\n", 1)[0]
    sep = ";" if primera_linea.count(";") > primera_linea.count(",") else ","
    df = pd.read_csv(io.StringIO(texto), sep=sep, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    return df


def _validar_columnas(df, requeridas, nombre):
    faltan = [c for c in requeridas if c not in df.columns]
    if faltan:
        raise ValueError(
            f"Al archivo de {nombre} le faltan columnas: {', '.join(faltan)}. "
            "Revisa que sea el exporte correcto."
        )


def _minutos(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def asignar_turno(ts: pd.Series):
    """Devuelve (turno, fecha operativa) según config.TURNOS."""
    minutos = ts.dt.hour * 60 + ts.dt.minute
    fecha = ts.dt.normalize()
    turno = pd.Series("Sin turno", index=ts.index, dtype="object")
    fecha_op = fecha.copy()
    for nombre, ini, fin in cfg.TURNOS:
        s, e = _minutos(ini), _minutos(fin)
        if s < e:
            m = (minutos >= s) & (minutos < e)
            turno[m] = nombre
        else:  # cruza medianoche
            antes = minutos >= s
            despues = minutos < e
            turno[antes | despues] = nombre
            fecha_op[despues] = fecha[despues] - pd.Timedelta(days=1)
    return turno, fecha_op


# ---------------------------------------------------------------- picking

MANUAL, PALLET = "Manual", "Pallet completo"


def clasificar_tipo(zona: pd.Series) -> pd.Series:
    """Pallet completo si la zona empieza con alguno de config.PREFIJOS_PALLET_COMPLETO."""
    z = zona.str.upper()
    es_pallet = pd.Series(False, index=zona.index)
    for prefijo in cfg.PREFIJOS_PALLET_COMPLETO:
        es_pallet |= z.str.startswith(prefijo.upper())
    return np.where(es_pallet, PALLET, MANUAL)

def preparar_picking(df: pd.DataFrame):
    """Limpia CAJA_PICKEADA y la deja a nivel de lista.

    Devuelve (listas, exclusiones, avisos).
    """
    _validar_columnas(df, COLS_PICKING, "picking")
    exclusiones, avisos = [], []

    def excluir(mask, motivo):
        n = int(mask.sum())
        if n:
            exclusiones.append({"motivo": motivo, "registros": n})
        return n

    d = df.copy()
    antes = len(d)
    clave = ["id_de_lista", "lpn"] if "lpn" in d.columns else None
    d = d.drop_duplicates(subset=clave)
    if antes - len(d):
        exclusiones.append({"motivo": "Filas duplicadas (misma lista y LPN)",
                            "registros": antes - len(d)})

    d["inicio"] = pd.to_datetime(d["h_inicio"].str.strip(), format="%d/%m/%Y %H:%M", errors="coerce")
    d["termino"] = pd.to_datetime(d["h_termino"].str.strip(), format="%d/%m/%Y %H:%M", errors="coerce")
    m = d["inicio"].isna() | d["termino"].isna()
    excluir(m, "Hora de inicio o término vacía o con formato inválido")
    d = d[~m]

    d["usuario"] = d["id_de_usuario_ultima_recogida"].fillna("").str.strip()
    m = d["usuario"] == ""
    excluir(m, "Sin usuario")
    d = d[~m].copy()

    d["nombre"] = d["descripcion_usuario"].fillna("").str.strip()
    d.loc[d["nombre"] == "", "nombre"] = d["usuario"]
    d["zona"] = d["zona_de_trabajo"].fillna("").str.strip().replace("", "Sin zona")
    for c in ("cajas", "lineas"):
        d[c] = pd.to_numeric(d[c].str.strip(), errors="coerce").fillna(0)

    agregaciones = dict(
        usuario=("usuario", "first"), nombre=("nombre", "first"), zona=("zona", "first"),
        inicio=("inicio", "min"), termino=("termino", "max"),
        cajas=("cajas", "sum"), lineas=("lineas", "sum"), lpns=("usuario", "size"),
    )
    if "numero_de_viaje" in d.columns:
        agregaciones["viaje"] = ("numero_de_viaje", "first")
    listas = d.groupby("id_de_lista", as_index=False).agg(**agregaciones)

    listas["dur_min"] = (listas["termino"] - listas["inicio"]).dt.total_seconds() / 60
    m = listas["dur_min"] < 0
    excluir(m, "Término anterior al inicio (listas)")
    listas = listas[~m]
    m = listas["dur_min"] > cfg.DURACION_MAX_LISTA_MIN
    excluir(m, f"Duración mayor a {cfg.DURACION_MAX_LISTA_MIN} min (listas)")
    listas = listas[~m].copy()

    n_cero = int((listas["dur_min"] == 0).sum())
    if n_cero:
        avisos.append(
            f"{n_cero} listas duran 0 minutos (el WMS registra solo hora:minuto). "
            f"Se les asignan {cfg.DURACION_MIN_LISTA_MIN} min."
        )
    listas["dur_ef_min"] = listas["dur_min"].clip(lower=cfg.DURACION_MIN_LISTA_MIN)

    listas["tipo_picking"] = clasificar_tipo(listas["zona"])
    listas["turno"], listas["fecha_op"] = asignar_turno(listas["inicio"])
    n_sin = int((listas["turno"] == "Sin turno").sum())
    if n_sin:
        avisos.append(f"{n_sin} listas empiezan fuera de los turnos definidos en config.py.")

    return listas.reset_index(drop=True), exclusiones, avisos


def calcular_brechas(listas: pd.DataFrame) -> pd.DataFrame:
    """Tiempo entre el fin de una lista y el inicio de la siguiente, por operario y turno."""
    d = listas.sort_values(CLAVE_OPERARIO + ["inicio"]).copy()
    d["fin_anterior"] = d.groupby(CLAVE_OPERARIO)["termino"].shift()
    b = d[d["fin_anterior"].notna()].copy()
    b["brecha_min"] = (b["inicio"] - b["fin_anterior"]).dt.total_seconds() / 60
    b["solapada"] = b["brecha_min"] < 0
    b["brecha_min"] = b["brecha_min"].clip(lower=0)
    b["tipo"] = np.select(
        [b["brecha_min"] < cfg.UMBRAL_ESPERA_MIN, b["brecha_min"] < cfg.UMBRAL_PAUSA_MIN],
        ["Normal", "Espera"], default="Pausa",
    )
    if cfg.DESCONTAR_COLACION:
        cand = b[(b["tipo"] == "Pausa")
                 & b["brecha_min"].between(cfg.COLACION_MIN_MIN, cfg.COLACION_MAX_MIN)]
        if len(cand):
            idx = cand.groupby(CLAVE_OPERARIO)["brecha_min"].idxmax()
            b.loc[idx, "tipo"] = "Colación"
    b["hora"] = b["fin_anterior"].dt.hour
    return b[CLAVE_OPERARIO + ["nombre", "zona", "id_de_lista", "fin_anterior", "inicio",
                               "brecha_min", "tipo", "solapada", "hora"]]


def _minutos_por_tipo(brechas, claves):
    if brechas.empty:
        return pd.DataFrame(columns=claves)
    p = brechas.pivot_table(index=claves, columns="tipo", values="brecha_min",
                            aggfunc="sum", fill_value=0)
    p.columns = [f"min_{c.lower().replace('ó', 'o')}" for c in p.columns]
    return p.reset_index()


def _asegurar_columnas(df, columnas):
    for c in columnas:
        if c not in df.columns:
            df[c] = 0.0
    return df


TIPOS_MIN = ["min_normal", "min_espera", "min_pausa", "min_colacion"]


def _por_tipo(listas, claves):
    """Listas, cajas y minutos separados en manual y pallet completo."""
    partes = []
    for tipo, suf in ((MANUAL, "manual"), (PALLET, "pallet")):
        sub = listas[listas["tipo_picking"] == tipo]
        partes.append(sub.groupby(claves).agg(**{
            f"listas_{suf}": ("id_de_lista", "size"),
            f"cajas_{suf}": ("cajas", "sum"),
            f"lineas_{suf}": ("lineas", "sum"),
            f"lpns_{suf}": ("lpns", "sum"),
            f"min_{suf}": ("dur_ef_min", "sum"),
            f"operarios_{suf}": ("usuario", "nunique"),
        }))
    return pd.concat(partes, axis=1).fillna(0)


def resumen_operarios(listas, brechas):
    """Una fila por operario, fecha operativa y turno. Considera todas sus listas."""
    op = listas.groupby(CLAVE_OPERARIO + ["nombre"]).agg(
        primera=("inicio", "min"), ultima=("termino", "max"),
    ).reset_index()
    op = op.join(_por_tipo(listas, CLAVE_OPERARIO), on=CLAVE_OPERARIO)
    op["min_efectivos"] = op["min_manual"] + op["min_pallet"]
    op = op.merge(_minutos_por_tipo(brechas, CLAVE_OPERARIO), on=CLAVE_OPERARIO, how="left")
    op = _asegurar_columnas(op, TIPOS_MIN).fillna({c: 0 for c in TIPOS_MIN})

    inicio_turno = listas.groupby(["fecha_op", "turno"])["inicio"].min().rename("inicio_turno")
    op = op.join(inicio_turno, on=["fecha_op", "turno"])
    op["min_inicio_tardio"] = (op["primera"] - op["inicio_turno"]).dt.total_seconds() / 60
    op["min_en_piso"] = (op["ultima"] - op["primera"]).dt.total_seconds() / 60
    op["min_disponibles"] = (op["min_en_piso"] - op["min_colacion"]).clip(lower=0)
    return op.drop(columns="inicio_turno")


def consolidar_operarios(op):
    """Suma el detalle diario por operario para el período filtrado."""
    g = op.groupby(["usuario", "nombre"]).agg(
        turnos=("fecha_op", "size"),
        listas_manual=("listas_manual", "sum"), cajas_manual=("cajas_manual", "sum"),
        listas_pallet=("listas_pallet", "sum"), cajas_pallet=("cajas_pallet", "sum"),
        min_manual=("min_manual", "sum"), min_efectivos=("min_efectivos", "sum"),
        min_disponibles=("min_disponibles", "sum"), min_espera=("min_espera", "sum"),
        min_pausa=("min_pausa", "sum"), min_inicio_tardio=("min_inicio_tardio", "mean"),
    ).reset_index()
    g["horas_efectivas"] = g["min_efectivos"] / 60
    g["horas_disponibles"] = g["min_disponibles"] / 60
    g["utilizacion"] = np.where(g["min_disponibles"] > 0,
                                g["min_efectivos"] / g["min_disponibles"], np.nan)
    g["cj_h_manual"] = np.where(g["min_manual"] > 0,
                                g["cajas_manual"] / (g["min_manual"] / 60), np.nan)
    return g.sort_values("utilizacion")


def resumen_turnos(listas, brechas):
    """Una fila por fecha operativa y turno.

    La productividad (cj/HH) se calcula solo sobre picking manual, igual que el
    Power BI; el pallet completo se informa aparte.
    """
    claves = ["fecha_op", "turno"]
    man = listas[listas["tipo_picking"] == MANUAL]
    t = listas.groupby(claves).agg(inicio_total=("inicio", "min"), fin_total=("termino", "max"))
    t = t.join(man.groupby(claves).agg(inicio=("inicio", "min"), fin=("termino", "max")))
    t = t.join(_por_tipo(listas, claves)).reset_index()
    t = t.merge(_minutos_por_tipo(brechas, claves), on=claves, how="left")
    t = _asegurar_columnas(t, TIPOS_MIN).fillna({c: 0 for c in TIPOS_MIN})

    t["horas_ventana"] = ((t["fin"] - t["inicio"]).dt.total_seconds() / 3600).fillna(0)
    # Igual que el BI: horas-hombre = ventana de picking manual x operarios de picking manual
    t["horas_hombre"] = t["horas_ventana"] * t["operarios_manual"]
    t["cj_hh_total"] = np.where(t["horas_hombre"] > 0, t["cajas_manual"] / t["horas_hombre"], np.nan)
    t["cj_hh_efectiva"] = np.where(t["min_manual"] > 0,
                                   t["cajas_manual"] / (t["min_manual"] / 60), np.nan)
    total = t["cajas_manual"] + t["cajas_pallet"]
    t["pct_pallet"] = np.where(total > 0, t["cajas_pallet"] / total, np.nan)
    return t


def resumen_zonas(listas, brechas):
    z = listas.groupby("zona").agg(
        listas=("id_de_lista", "size"), cajas=("cajas", "sum"), lineas=("lineas", "sum"),
        lpns=("lpns", "sum"), min_efectivos=("dur_ef_min", "sum"), min_por_lista=("dur_min", "mean"),
    ).reset_index()
    # La espera antes de una lista se atribuye a la zona de esa lista
    esp = brechas[brechas["tipo"].isin(["Espera", "Pausa"])].groupby("zona")["brecha_min"].sum()
    z["min_espera_antes"] = z["zona"].map(esp).fillna(0)
    z["cj_h_efectiva"] = z["cajas"] / (z["min_efectivos"] / 60)
    z["cajas_por_lista"] = z["cajas"] / z["listas"]
    z["cajas_por_linea"] = np.where(z["lineas"] > 0, z["cajas"] / z["lineas"], np.nan)
    z["min_por_lpn"] = z["min_efectivos"] / z["lpns"]
    return z.sort_values("cj_h_efectiva")


# ---------------------------------------------------------------- grúa

def preparar_grua(df: pd.DataFrame) -> pd.DataFrame:
    _validar_columnas(df, COLS_GRUA, "movimientos de grúa")
    d = df.copy()
    d["fecha"] = pd.to_datetime(d["fecha_de_transaccion"].str.strip(), format="%d/%m/%Y", errors="coerce")
    d = d[d["fecha"].notna()].copy()
    d["movimientos"] = pd.to_numeric(d["movimientos"], errors="coerce").fillna(0)
    op = d["codigo_de_operacion"].fillna("").str.lower()
    act = d["codigo_de_actividad"].fillna("").str.lower()
    d["tipo"] = np.select(
        [op.str.contains("no dirigid") | act.str.contains("no dirigid"),
         op.str.contains("reabast") | act.str.contains("reabastecimiento"),
         op.str.contains("recogida")],
        ["No dirigido", "Reabastecimiento dirigido", "Recogida almacenamiento"],
        default="Otro",
    )
    d["nombre"] = d["nombre_usuario"].fillna(d["usuario"]).str.strip()
    iso = d["fecha"].dt.isocalendar()
    d["semana"] = iso["year"].astype(str) + "-S" + iso["week"].astype(str).str.zfill(2)
    return d


def resumen_grua_semanal(g):
    s = g.pivot_table(index="semana", columns="tipo", values="movimientos",
                      aggfunc="sum", fill_value=0)
    s["total"] = s.sum(axis=1)
    s["pct_no_dirigido"] = s.get("No dirigido", 0) / s["total"]
    return s.reset_index()


def resumen_grua_usuarios(g):
    u = g.pivot_table(index=["usuario", "nombre"], columns="tipo", values="movimientos",
                      aggfunc="sum", fill_value=0)
    u["total"] = u.sum(axis=1)
    u["pct_no_dirigido"] = u.get("No dirigido", 0) / u["total"]
    return u.reset_index().sort_values("total", ascending=False)


# ---------------------------------------------------------------- alertas

def generar_alertas(turnos, operarios, zonas, grua_semanal=None):
    """Lista de (nivel, mensaje). nivel: 'error' | 'warning' | 'success'."""
    alertas = []

    hh = turnos["horas_hombre"].sum()
    if hh > 0:
        prod = turnos["cajas_manual"].sum() / hh
        if prod < cfg.META_ICEO:
            alertas.append(("error", f"Productividad total de {prod:.0f} cj/HH, bajo la meta ICEO de {cfg.META_ICEO}."))
        elif prod < cfg.META_ICEO * 1.05:
            alertas.append(("warning", f"Productividad total de {prod:.0f} cj/HH, apenas sobre la meta ICEO de {cfg.META_ICEO}."))

        pct_ef = turnos["min_manual"].sum() / 60 / hh
        if pct_ef < 0.6:
            alertas.append(("warning", f"Solo el {pct_ef:.0%} de las horas-hombre de picking manual se usan dentro de listas."))

    esperas = turnos["min_espera"].sum() + turnos["min_pausa"].sum()
    if esperas > 0:
        alertas.append(("warning", f"{esperas / 60:.1f} horas-hombre en esperas y pausas entre listas (sin contar colación)."))

    bajo = zonas[zonas["cj_h_efectiva"] < cfg.META_ICEO]
    for _, z in bajo.iterrows():
        alertas.append(("error", f"{z['zona']} rinde {z['cj_h_efectiva']:.0f} cj/h en tiempo efectivo: bajo la meta incluso sin contar esperas."))

    bajos = operarios[operarios["utilizacion"] < cfg.UMBRAL_UTILIZACION]
    if len(bajos):
        nombres = ", ".join(bajos["nombre"].head(5))
        extra = f" y {len(bajos) - 5} más" if len(bajos) > 5 else ""
        alertas.append(("warning", f"{len(bajos)} operarios con menos del {cfg.UMBRAL_UTILIZACION:.0%} de su tiempo en listas: {nombres}{extra}."))

    if grua_semanal is not None and len(grua_semanal):
        ult = grua_semanal.iloc[-1]
        if ult["pct_no_dirigido"] > cfg.UMBRAL_NO_DIRIGIDO:
            alertas.append(("error", f"Semana {ult['semana']}: {ult['pct_no_dirigido']:.0%} de los movimientos de grúa son no dirigidos."))
        if len(grua_semanal) > 1:
            prev = grua_semanal.iloc[-2]
            nd_ult, nd_prev = ult.get("No dirigido", 0), prev.get("No dirigido", 0)
            if nd_prev > 0 and nd_ult / nd_prev > 1.1:
                alertas.append(("warning", f"Movimientos no dirigidos subieron {nd_ult / nd_prev - 1:.0%} respecto a la semana {prev['semana']}."))

    if not alertas:
        alertas.append(("success", "Sin alertas para el período seleccionado."))
    return alertas
