import pandas as pd
import plotly.express as px
import streamlit as st

import config as cfg
import procesamiento as proc

st.set_page_config(page_title="Torre de control WMS", page_icon="🏭", layout="wide")

VERDE = "#1F5F4A"
COLORES_TIPO = {"Normal": "#9DB8AE", "Espera": "#E0A526", "Pausa": "#C4452F", "Colación": "#8A8F98"}
COLORES_GRUA = {"No dirigido": "#C4452F", "Reabastecimiento dirigido": VERDE,
                "Recogida almacenamiento": "#6F8FAF", "Otro": "#B8BDC4"}


def fmt_num(x, dec=0):
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


@st.cache_data(show_spinner="Procesando picking...")
def cargar_picking(contenidos: tuple):
    df = pd.concat([proc.leer_csv(c) for c in contenidos], ignore_index=True)
    listas, exclusiones, avisos = proc.preparar_picking(df)
    brechas = proc.calcular_brechas(listas)
    return listas, brechas, exclusiones, avisos, proc.preparar_lpns(df)


@st.cache_data(show_spinner="Procesando movimientos de grúa...")
def cargar_grua(contenidos: tuple):
    df = pd.concat([proc.leer_csv(c) for c in contenidos], ignore_index=True)
    return proc.preparar_grua(df.drop_duplicates())


# ---------------------------------------------------------------- origen de datos
def secretos_drive():
    try:
        return st.secrets["gcp_json"], st.secrets["drive_folder_id"]
    except (KeyError, FileNotFoundError):
        return None


@st.cache_resource
def cliente_drive(credenciales_json):
    import drive
    return drive.conectar(credenciales_json)


@st.cache_data(ttl=3600, show_spinner="Buscando archivos en Drive...")
def listar_drive(credenciales_json, carpeta_id):
    import drive
    return drive.listar_archivos(cliente_drive(credenciales_json), carpeta_id)


@st.cache_data(show_spinner=False, max_entries=200)
def bajar_drive(credenciales_json, archivo_id, modificado):
    # "modificado" forma parte de la clave de caché: si el archivo cambia en Drive, se vuelve a bajar
    import drive
    return drive.descargar(cliente_drive(credenciales_json), archivo_id)


@st.cache_data(show_spinner="Analizando posiciones de picking...")
def cargar_posiciones(ventas: tuple, maestro: bytes, factor: tuple | None):
    import posiciones as pos
    v = pd.concat([pos.leer_tabla(b, n, columnas=pos.COLS_VENTA) for b, n in ventas], ignore_index=True)
    v = pos.preparar_venta(v.drop_duplicates(subset=["documento material", "material"]))
    f = pos.leer_factor_pallet(*factor) if factor else None
    r, dias = pos.analizar(v, pos.leer_maestro(maestro), f)
    esperado = pos.pallet_esperado(v, f) if f is not None else None
    return r, pos.resumen_zonas(r), dias.min(), dias.max(), len(dias), esperado


def nombre_norm(nombre):
    return nombre.upper().replace("_", " ")


conf_drive = secretos_drive()
contenidos_pick, contenidos_grua, contenidos_venta, contenido_maestro, factor_pallet = (), (), (), None, None

with st.sidebar:
    st.header("Datos")
    opciones = (["Google Drive", "Subir archivos"] if conf_drive else ["Subir archivos"])
    origen = st.radio("Origen", opciones, horizontal=True)

    if origen == "Google Drive":
        cred, carpeta = conf_drive
        if st.button("Actualizar desde Drive", use_container_width=True):
            listar_drive.clear()
        try:
            archivos = listar_drive(cred, carpeta)
        except Exception as e:  # credenciales, permisos o red
            st.error(f"No pude leer la carpeta de Drive: {e}")
            st.stop()
        es_csv = lambda f: f["name"].lower().endswith(".csv")
        a_pick = [f for f in archivos if cfg.PATRON_PICKING in nombre_norm(f["name"]) and es_csv(f)]
        a_grua = [f for f in archivos if cfg.PATRON_GRUA in nombre_norm(f["name"]) and es_csv(f)]
        a_venta = [f for f in archivos if cfg.PATRON_VENTA in nombre_norm(f["name"])]
        a_maestro = sorted([f for f in archivos if cfg.PATRON_MAESTRO in nombre_norm(f["name"])
                            and f["name"].lower().endswith(".xlsx")], key=lambda f: f["modifiedTime"])[-1:]
        a_factor = sorted([f for f in archivos if cfg.PATRON_FACTOR_PALLET in nombre_norm(f["name"])],
                          key=lambda f: f["modifiedTime"])[-1:]
        a_venta = [f for f in a_venta if f not in a_factor]
        todos = a_pick + a_grua + a_venta + a_maestro + a_factor
        with st.spinner(f"Descargando {len(todos)} archivos..."):
            contenidos_pick = tuple(bajar_drive(cred, f["id"], f["modifiedTime"]) for f in a_pick)
            contenidos_grua = tuple(bajar_drive(cred, f["id"], f["modifiedTime"]) for f in a_grua)
            contenidos_venta = tuple((bajar_drive(cred, f["id"], f["modifiedTime"]), f["name"]) for f in a_venta)
            if a_maestro:
                contenido_maestro = bajar_drive(cred, a_maestro[0]["id"], a_maestro[0]["modifiedTime"])
            if a_factor:
                factor_pallet = (bajar_drive(cred, a_factor[0]["id"], a_factor[0]["modifiedTime"]),
                                 a_factor[0]["name"])
        st.caption(f"En Drive: {len(a_pick)} de picking, {len(a_grua)} de grúa, {len(a_venta)} de venta, "
                   f"{len(a_maestro)} maestro de ubicaciones y {len(a_factor)} de cajas por pallet. "
                   "La lista se refresca sola cada hora o con el botón.")
        with st.expander("Ver archivos"):
            for f in todos:
                st.caption(f"{f['carpeta']}{f['name']}")
    else:
        arch_pick = st.file_uploader("Picking (CAJA_PICKEADA)", type="csv", accept_multiple_files=True)
        arch_grua = st.file_uploader("Movimientos de grúa", type="csv", accept_multiple_files=True)
        st.caption("Puedes subir varios archivos de cada tipo; los registros repetidos se eliminan.")
        contenidos_pick = tuple(f.getvalue() for f in arch_pick or [])
        contenidos_grua = tuple(f.getvalue() for f in arch_grua or [])
        arch_venta = st.file_uploader("Venta (para posiciones)", type=["csv", "xlsx"], accept_multiple_files=True)
        arch_maestro = st.file_uploader("Maestro de ubicaciones (Datos para armar)", type="xlsx")
        contenidos_venta = tuple((f.getvalue(), f.name) for f in arch_venta or [])
        contenido_maestro = arch_maestro.getvalue() if arch_maestro else None
        arch_factor = st.file_uploader("Cajas por pallet (CAJAS_X_PALLET)", type=["csv", "xlsx"])
        factor_pallet = (arch_factor.getvalue(), arch_factor.name) if arch_factor else None

st.title("Torre de control WMS")
st.caption(f"CD Coquimbo · Turno {', '.join(cfg.TURNOS_ANALIZADOS)}")

if not contenidos_pick:
    st.info("No hay archivos de picking. Sube uno o más CAJA_PICKEADA en la barra lateral, "
            "o déjalos en la carpeta de Drive. El archivo de grúa es opcional.")
    st.stop()

try:
    listas, brechas, exclusiones, avisos, lpns = cargar_picking(contenidos_pick)
except ValueError as e:
    st.error(str(e))
    st.stop()

grua = None
if contenidos_grua:
    try:
        grua = cargar_grua(contenidos_grua)
    except ValueError as e:
        st.error(str(e))

# ---------------------------------------------------------------- filtros
foco = listas["turno"].isin(cfg.TURNOS_ANALIZADOS)
if not foco.any():
    st.warning(f"El archivo no tiene listas del turno {', '.join(cfg.TURNOS_ANALIZADOS)}.")
    st.stop()
fechas = sorted(listas.loc[foco, "fecha_op"].dt.date.unique())
with st.sidebar:
    st.header("Filtros")
    modo = st.radio("Período", ["Un día", "Rango de fechas"], horizontal=True)
    if modo == "Un día":
        dia = st.selectbox("Fecha operativa", fechas[::-1],
                           format_func=lambda f: f.strftime("%d/%m/%Y"))
        rango = (dia, dia)
    else:
        rango = st.date_input("Fecha operativa", value=(fechas[0], fechas[-1]),
                              min_value=fechas[0], max_value=fechas[-1],
                              format="DD/MM/YYYY")

if not isinstance(rango, tuple) or len(rango) != 2:
    st.info("Elige la fecha de término del rango.")
    st.stop()
desde, hasta = pd.Timestamp(rango[0]), pd.Timestamp(rango[1])


def filtrar(df):
    m = (df["fecha_op"].between(desde, hasta) & df["turno"].isin(cfg.TURNOS_ANALIZADOS)
         & ~df["zona"].isin(cfg.ZONAS_EXCLUIDAS))
    return df[m]


L, B = filtrar(listas), filtrar(brechas)
if L.empty:
    st.warning("No hay listas para el período elegido. Amplía el rango de fechas.")
    st.stop()

op_diario = proc.resumen_operarios(L, B)
operarios = proc.consolidar_operarios(op_diario)
operarios["utilizacion_pct"] = operarios["utilizacion"] * 100
turnos = proc.resumen_turnos(L, B)
turnos["pct_pallet_pct"] = turnos["pct_pallet"] * 100
L_man = L[L["tipo_picking"] == proc.MANUAL]
L_pal = L[L["tipo_picking"] == proc.PALLET]
zonas = proc.resumen_zonas(L_man, B)
zonas_pallet = proc.resumen_zonas(L_pal, B)

G, grua_sem = None, None
if grua is not None:
    G = grua[grua["fecha"].between(desde, hasta)]
    grua_sem = proc.resumen_grua_semanal(grua[grua["fecha"] <= hasta])

periodo = (desde.strftime("%d/%m/%Y") if desde == hasta
           else f"{desde:%d/%m/%Y} al {hasta:%d/%m/%Y}")

tab_res, tab_op, tab_zona, tab_tiempo, tab_pos, tab_grua, tab_calidad = st.tabs(
    ["Resumen", "Operarios", "Zonas", "Tiempo no efectivo", "Posiciones", "Grúa", "Calidad de datos"])

# ---------------------------------------------------------------- resumen
with tab_res:
    st.subheader(f"Resumen {periodo}")
    hh = turnos["horas_hombre"].sum()
    h_ef = turnos["min_manual"].sum() / 60
    cajas = turnos["cajas_manual"].sum()
    prod_total = cajas / hh if hh else 0
    st.markdown("##### Picking manual")
    c = st.columns(5)
    c[0].metric("Cajas pickeadas", fmt_num(cajas))
    c[1].metric("Operarios", int(L_man["usuario"].nunique()))
    c[2].metric("cj/HH total", fmt_num(prod_total),
                delta=f"{fmt_num(prod_total - cfg.META_ICEO)} vs meta {cfg.META_ICEO}")
    c[3].metric("cj/HH efectiva", fmt_num(cajas / h_ef if h_ef else 0))
    c[4].metric("Tiempo en listas", f"{h_ef / hh:.0%}" if hh else "-",
                help="Horas dentro de listas manuales sobre horas-hombre (ventana de picking manual × operarios), igual que el Power BI.")

    st.markdown("##### Pallet completo")
    cajas_pal = turnos["cajas_pallet"].sum()
    pallets = turnos["lpns_pallet"].sum()
    c = st.columns(5)
    c[0].metric("Cajas en pallet completo", fmt_num(cajas_pal))
    c[1].metric("Pallets", fmt_num(pallets),
                help="LPN recogidos en zonas de pallet completo.")
    c[2].metric("% de cajas en pallet completo",
                f"{cajas_pal / (cajas + cajas_pal):.1%}" if cajas + cajas_pal else "-")
    c[3].metric("Min. por pallet", fmt_num(turnos["min_pallet"].sum() / pallets, 1) if pallets else "-")
    c[4].metric("Operarios", int(L_pal["usuario"].nunique()))

    st.markdown("#### Alertas")
    for nivel, texto in proc.generar_alertas(turnos, operarios, zonas, grua_sem):
        getattr(st, nivel)(texto)

    if turnos["fecha_op"].nunique() > 1:
        tend = turnos.groupby("fecha_op").agg(cajas=("cajas_manual", "sum"), hh=("horas_hombre", "sum"),
                                              mef=("min_manual", "sum")).reset_index()
        tend["cj/HH total"] = tend["cajas"] / tend["hh"]
        tend["cj/HH efectiva"] = tend["cajas"] / (tend["mef"] / 60)
        fig = px.line(tend, x="fecha_op", y=["cj/HH total", "cj/HH efectiva"], markers=True,
                      color_discrete_sequence=[VERDE, "#6F8FAF"],
                      labels={"fecha_op": "", "value": "cajas por hora", "variable": ""})
        fig.add_hline(y=cfg.META_ICEO, line_dash="dash", line_color="#C4452F",
                      annotation_text=f"Meta ICEO {cfg.META_ICEO}")
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Detalle por turno")
    st.dataframe(
        turnos[["fecha_op", "turno", "inicio", "fin", "operarios_manual", "listas_manual", "cajas_manual",
                "cj_hh_total", "cj_hh_efectiva", "cajas_pallet", "pct_pallet_pct", "min_espera", "min_pausa"]],
        hide_index=True, use_container_width=True,
        column_config={
            "fecha_op": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
            "inicio": st.column_config.DatetimeColumn("Inicio picking", format="HH:mm"),
            "fin": st.column_config.DatetimeColumn("Fin picking", format="HH:mm"),
            "operarios_manual": "Operarios",
            "listas_manual": "Listas",
            "cajas_manual": "Cajas manual",
            "cajas_pallet": "Cajas pallet completo",
            "pct_pallet_pct": st.column_config.NumberColumn("% pallet completo", format="%.1f%%"),
            "cj_hh_total": st.column_config.NumberColumn("cj/HH total", format="%.0f"),
            "cj_hh_efectiva": st.column_config.NumberColumn("cj/HH efectiva", format="%.0f"),
            "min_espera": st.column_config.NumberColumn("Min. espera", format="%.0f"),
            "min_pausa": st.column_config.NumberColumn("Min. pausa", format="%.0f"),
        })

# ---------------------------------------------------------------- operarios
with tab_op:
    st.subheader("Uso del tiempo por operario")
    st.caption("Utilización = minutos dentro de listas (manuales y de pallet completo) / minutos entre "
               "su primera y última lista, descontando colación. Ordenado de menor a mayor.")
    fig = px.bar(operarios, x="utilizacion", y="nombre", orientation="h",
                 color_discrete_sequence=[VERDE],
                 labels={"utilizacion": "Utilización", "nombre": ""})
    fig.add_vline(x=cfg.UMBRAL_UTILIZACION, line_dash="dash", line_color="#C4452F")
    fig.update_layout(xaxis_tickformat=".0%", height=max(300, 28 * len(operarios)),
                      yaxis={"categoryorder": "total descending"})
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        operarios[["nombre", "turnos", "listas_manual", "cajas_manual", "listas_pallet", "cajas_pallet",
                   "horas_efectivas", "horas_disponibles", "utilizacion_pct", "cj_h_manual",
                   "min_espera", "min_pausa", "min_inicio_tardio"]],
        hide_index=True, use_container_width=True,
        column_config={
            "nombre": "Operario",
            "horas_efectivas": st.column_config.NumberColumn("Horas en listas", format="%.2f"),
            "horas_disponibles": st.column_config.NumberColumn("Horas disponibles", format="%.2f"),
            "utilizacion_pct": st.column_config.ProgressColumn("Utilización", format="%.0f%%",
                                                               min_value=0, max_value=100),
            "listas_manual": "Listas manual",
            "cajas_manual": "Cajas manual",
            "listas_pallet": "Listas pallet",
            "cajas_pallet": "Cajas pallet",
            "cj_h_manual": st.column_config.NumberColumn(
                "cj/h manual", format="%.0f",
                help="Cajas de picking manual por hora dentro de listas manuales."),
            "min_espera": st.column_config.NumberColumn("Min. espera", format="%.0f"),
            "min_pausa": st.column_config.NumberColumn("Min. pausa", format="%.0f"),
            "min_inicio_tardio": st.column_config.NumberColumn(
                "Inicio tardío (min)", format="%.0f",
                help="Minutos promedio entre la primera lista del turno y la primera lista del operario."),
        })

# ---------------------------------------------------------------- zonas
with tab_zona:
    st.subheader("Picking manual por zona de trabajo")
    st.caption("Productividad en tiempo efectivo: si una zona queda bajo la meta aquí, "
               "el problema está dentro de la lista (recorrido, ubicación, tipo de producto), no en las esperas.")
    fig = px.bar(zonas, x="zona", y="cj_h_efectiva", color_discrete_sequence=[VERDE],
                 text_auto=".0f", labels={"zona": "", "cj_h_efectiva": "cj/h en tiempo efectivo"})
    fig.add_hline(y=cfg.META_ICEO, line_dash="dash", line_color="#C4452F",
                  annotation_text=f"Meta ICEO {cfg.META_ICEO}")
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(
        zonas[["zona", "listas", "cajas", "lineas", "cj_h_efectiva", "min_por_lista",
               "cajas_por_lista", "cajas_por_linea", "min_espera_antes"]],
        hide_index=True, use_container_width=True,
        column_config={
            "zona": "Zona",
            "cj_h_efectiva": st.column_config.NumberColumn("cj/h efectiva", format="%.0f"),
            "min_por_lista": st.column_config.NumberColumn("Min. por lista", format="%.1f"),
            "cajas_por_lista": st.column_config.NumberColumn("Cajas por lista", format="%.1f"),
            "cajas_por_linea": st.column_config.NumberColumn("Cajas por línea", format="%.1f"),
            "min_espera_antes": st.column_config.NumberColumn(
                "Min. espera previa", format="%.0f",
                help="Esperas y pausas justo antes de iniciar listas de esta zona."),
        })

    st.subheader("Pallets armados en surtido")
    if lpns is None:
        st.info("El archivo de picking no trae nivel_lpn: no se pueden identificar los LPN de surtido.")
    else:
        Lp = lpns[lpns["fecha_op"].between(desde, hasta) & lpns["turno"].isin(cfg.TURNOS_ANALIZADOS)
                  & ~lpns["zona"].isin(cfg.ZONAS_EXCLUIDAS)]
        cp = proc.surtido_casi_pallet(Lp)
        st.caption(f"LPN de surtido (nivel S) con una sola línea y {cfg.UMBRAL_LPN_CASI_PALLET} cajas o más: "
                   "casi un pallet de un mismo artículo pickeado desde la posición, que después hay que "
                   "consolidar en parrilla. 'Cajas típicas' es el tamaño más repetido de esos LPN; si es "
                   "menor que las cajas por pallet del artículo, revisa la volumetría en el WMS.")
        if cp.empty:
            st.success("No hay LPN de surtido de ese tamaño en el período elegido.")
        else:
            c = st.columns(3)
            c[0].metric("LPN casi pallet", fmt_num(cp["lpns_casi_pallet"].sum()))
            c[1].metric("Cajas en esos LPN", fmt_num(cp["cajas_casi_pallet"].sum()))
            c[2].metric("% de las cajas de surtido",
                        f"{cp['cajas_casi_pallet'].sum() / Lp.loc[Lp['nivel'] == 'S', 'cajas'].sum():.1%}")
            cp["pct_pct"] = cp["pct_cajas_casi_pallet"] * 100
            st.dataframe(cp[["zona", "lpns_casi_pallet", "cajas_casi_pallet", "cajas_tipicas", "lpns_surtido", "pct_pct"]],
                         hide_index=True, use_container_width=True, column_config={
                             "zona": "Zona", "lpns_casi_pallet": "LPN casi pallet",
                             "cajas_casi_pallet": st.column_config.NumberColumn("Cajas", format="%.0f"),
                             "cajas_tipicas": "Cajas típicas por LPN",
                             "lpns_surtido": "LPN de surtido totales",
                             "pct_pct": st.column_config.NumberColumn("% cajas de surtido de la zona", format="%.1f%%"),
                         })

    st.subheader("Pallet completo por zona")
    st.caption("No se compara con la meta ICEO: un pallet completo mueve muchas cajas en pocos minutos.")
    if zonas_pallet.empty:
        st.info("No hay listas de pallet completo en el período elegido.")
    else:
        st.dataframe(
            zonas_pallet[["zona", "listas", "lpns", "cajas", "min_por_lpn", "min_espera_antes"]],
            hide_index=True, use_container_width=True,
            column_config={
                "zona": "Zona",
                "lpns": "Pallets",
                "min_por_lpn": st.column_config.NumberColumn("Min. por pallet", format="%.1f"),
                "min_espera_antes": st.column_config.NumberColumn("Min. espera previa", format="%.0f"),
            })

# ---------------------------------------------------------------- tiempo no efectivo
with tab_tiempo:
    st.subheader("Tiempo entre listas")
    st.caption(f"Normal: menos de {cfg.UMBRAL_ESPERA_MIN} min. Espera: {cfg.UMBRAL_ESPERA_MIN} a "
               f"{cfg.UMBRAL_PAUSA_MIN} min. Pausa: más de {cfg.UMBRAL_PAUSA_MIN} min. "
               f"Colación: la pausa más larga de cada operario entre {cfg.COLACION_MIN_MIN} y "
               f"{cfg.COLACION_MAX_MIN} min.")
    if B.empty:
        st.info("No hay tiempos entre listas para los filtros elegidos.")
    else:
        resumen_tipo = B.groupby("tipo")["brecha_min"].agg(["sum", "count"]).reset_index()
        c = st.columns(len(resumen_tipo))
        for col, (_, r) in zip(c, resumen_tipo.iterrows()):
            col.metric(r["tipo"], f"{fmt_num(r['sum'] / 60, 1)} h", f"{int(r['count'])} veces",
                       delta_color="off")

        por_hora = B.groupby(["hora", "tipo"])["brecha_min"].sum().reset_index()
        fig = px.bar(por_hora, x="hora", y="brecha_min", color="tipo",
                     color_discrete_map=COLORES_TIPO,
                     category_orders={"tipo": ["Normal", "Espera", "Pausa", "Colación"]},
                     labels={"hora": "Hora del día", "brecha_min": "Minutos-hombre", "tipo": ""})
        fig.update_xaxes(dtick=1)
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Esperas y pausas más largas")
        top = B[B["tipo"].isin(["Espera", "Pausa"])].nlargest(15, "brecha_min")
        st.dataframe(
            top[["fecha_op", "turno", "nombre", "fin_anterior", "inicio", "brecha_min", "zona"]],
            hide_index=True, use_container_width=True,
            column_config={
                "fecha_op": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                "nombre": "Operario",
                "fin_anterior": st.column_config.DatetimeColumn("Terminó lista", format="HH:mm"),
                "inicio": st.column_config.DatetimeColumn("Inició siguiente", format="HH:mm"),
                "brecha_min": st.column_config.NumberColumn("Minutos", format="%.0f"),
                "zona": "Zona siguiente lista",
            })

# ---------------------------------------------------------------- posiciones
with tab_pos:
    st.subheader("Posiciones de picking")
    if not contenidos_venta or contenido_maestro is None:
        st.info("Para esta sección deja en Drive (o sube) el archivo de venta, con 'VENTA' en el nombre, "
                "y el maestro de ubicaciones 'Datos para armar.xlsx'. Para la venta conviene CSV: "
                "un Excel grande tarda bastante en leerse.")
    else:
        import posiciones as posmod
        try:
            det, zonas_pos, v_desde, v_hasta, n_dias, esperado = cargar_posiciones(contenidos_venta, contenido_maestro, factor_pallet)
        except ValueError as e:
            st.error(str(e))
            det = None
    if contenidos_venta and contenido_maestro is not None and det is not None:
        st.caption(f"Venta del {v_desde:%d/%m/%Y} al {v_hasta:%d/%m/%Y} ({n_dias} días operativos). "
                   "No depende del filtro de fecha. Día alto = percentil "
                   f"{cfg.PERCENTIL_DIA_ALTO:.0%} de la venta diaria de cada SKU; capacidad en cajas.")
        if factor_pallet:
            st.caption("Se descuentan las cajas que salen como pallet completo (según cajas por pallet), "
                       "suponiendo stock en ZT ALMACENAMIENTO. Si no hay stock, la demanda real sobre la "
                       "posición es mayor.")
        else:
            st.warning("Falta el archivo de cajas por pallet: toda la venta se trata como si saliera de la "
                       "posición de picking, y los SKUs con pedidos grandes aparecen más críticos de lo que son.")
        res_d = posmod.resumen_diagnostico(det)
        cuenta = res_d.set_index("diagnostico")
        c = st.columns(4)
        for col, d, txt in zip(c, posmod.ORDEN[:3] + ["Posición sin venta"],
                               ["Críticos", "Reponen en turno", "Sin posición", "Posiciones sin venta"]):
            fila = cuenta.loc[d]
            col.metric(txt, int(fila["skus"]),
                       f"{int(fila['skus_a'])} clase A" if d != "Posición sin venta" else None,
                       delta_color="off")
        afectadas = cuenta.loc[posmod.ORDEN[:2], "lineas_dia"].sum()
        st.warning(f"Los SKUs críticos y los que reponen en turno concentran {fmt_num(afectadas)} líneas "
                   f"por día ({afectadas / det['lineas_dia_prom'].sum():.0%} del picking).")

        st.dataframe(res_d, hide_index=True, use_container_width=True, column_config={
            "diagnostico": "Diagnóstico", "skus": "SKUs", "skus_a": "Clase A",
            "lineas_dia": st.column_config.NumberColumn("Líneas/día", format="%.0f"),
            "accion": st.column_config.TextColumn("Acción sugerida", width="large")})

        ver = st.selectbox("Ver detalle de", posmod.ORDEN)
        sub = det[det["diagnostico"] == ver].copy()
        sub["pct_dias"] = sub["pct_dias_con_venta"] * 100
        sub["pct_pallet_pct"] = sub["pct_pallet"] * 100
        st.dataframe(
            sub[["sku", "descripcion", "abc", "zona_trabajo", "zona_movimiento", "ubicaciones", "capacidad",
                 "posiciones", "posiciones_necesarias_p90",
                 "lineas_dia_prom", "cajas_dia_prom", "cajas_dia_p90", "reposiciones_dia_p90",
                 "pct_pallet_pct", "cajas_por_linea", "pct_dias"]],
            hide_index=True, use_container_width=True, column_config={
                "sku": "SKU", "descripcion": "Descripción", "abc": "ABC",
                "zona_trabajo": "Zona", "zona_movimiento": "Zona mov.", "ubicaciones": "Ubicación",
                "capacidad": st.column_config.NumberColumn("Capacidad", format="%.0f"),
                "posiciones": st.column_config.NumberColumn("Posiciones", format="%.0f"),
                "posiciones_necesarias_p90": st.column_config.NumberColumn(
                    "Posiciones para día alto", format="%.0f",
                    help="Posiciones del tamaño actual que necesitaría para no reponer en un día alto."),
                "lineas_dia_prom": st.column_config.NumberColumn("Líneas/día", format="%.1f"),
                "cajas_dia_prom": st.column_config.NumberColumn("Cajas/día", format="%.0f"),
                "cajas_dia_p90": st.column_config.NumberColumn(
                    "Cajas día alto", format="%.0f", help="Cajas que salen desde la posición de picking en un día alto."),
                "reposiciones_dia_p90": st.column_config.NumberColumn(
                    "Reposiciones día alto", format="%.1f",
                    help="Cajas en un día alto ÷ capacidad. Sobre 1: se vacía dentro del turno."),
                "cajas_por_linea": st.column_config.NumberColumn(
                    "Cajas/línea", format="%.1f",
                    help="Si es alto, parte del volumen puede salir como pallet completo y la criticidad estar sobreestimada."),
                "pct_dias": st.column_config.NumberColumn("% días con venta", format="%.0f%%"),
                "pct_pallet_pct": st.column_config.NumberColumn(
                    "% en pallet completo", format="%.0f%%",
                    help="Parte de la venta del SKU que sale como pallet completo y no pasa por la posición."),
            })

        st.markdown("#### Carga de reposición por zona")
        fig = px.bar(zonas_pos, x="zona_trabajo", y="reposiciones_dia", text_auto=".0f",
                     color_discrete_sequence=[VERDE],
                     labels={"zona_trabajo": "", "reposiciones_dia": "Reposiciones por día (promedio)"})
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Pallet completo: esperado vs real")
        if esperado is None:
            st.info("Agrega el archivo de cajas por pallet para comparar el pallet completo esperado con el real.")
        elif lpns is None:
            st.info("El archivo de picking no trae nivel_lpn, así que no se puede medir el pallet completo real.")
        else:
            comp = posmod.comparar_pallet(esperado, lpns, cfg.TURNOS_ANALIZADOS)
            if comp.empty:
                st.info("No hay días de picking que calcen con la venta cargada: la venta de un día se "
                        "pickea el día operativo siguiente. Carga picking de fechas posteriores a la venta.")
            else:
                st.caption("Esperado: cajas que según la venta y las cajas por pallet deberían salir como pallet "
                           "completo. Real: cajas en LPN nivel L. Si el real es menor, esos pallets se armaron "
                           "desde las posiciones de picking (probable falta de stock en almacenamiento o "
                           "volumetría mal cargada).")
                c = st.columns(3)
                c[0].metric("Cajas esperadas en pallet", fmt_num(comp["cajas_esperadas"].sum()))
                c[1].metric("Cajas reales en pallet (L)", fmt_num(comp["cajas_reales"].sum()))
                tot = comp["cajas_esperadas"].sum()
                c[2].metric("Cumplimiento", f"{comp['cajas_reales'].sum() / tot:.0%}" if tot else "-")
                larga = comp.melt(id_vars="fecha_op", value_vars=["cajas_esperadas", "cajas_reales"],
                                  var_name="serie", value_name="cajas")
                larga["serie"] = larga["serie"].map({"cajas_esperadas": "Esperadas", "cajas_reales": "Reales (L)"})
                fig = px.bar(larga, x="fecha_op", y="cajas", color="serie", barmode="group",
                             color_discrete_sequence=["#B8BDC4", VERDE],
                             labels={"fecha_op": "Fecha picking", "cajas": "Cajas en pallet completo", "serie": ""})
                st.plotly_chart(fig, use_container_width=True)
                comp["cumplimiento_pct"] = comp["cumplimiento"] * 100
                st.dataframe(comp[["fecha_op", "pallets_esperados", "pallets_reales", "cajas_esperadas",
                                   "cajas_reales", "cajas_faltantes", "cumplimiento_pct"]],
                             hide_index=True, use_container_width=True, column_config={
                                 "fecha_op": st.column_config.DateColumn("Fecha picking", format="DD/MM/YYYY"),
                                 "pallets_esperados": st.column_config.NumberColumn("Pallets esperados", format="%.0f"),
                                 "pallets_reales": "Pallets reales",
                                 "cajas_esperadas": st.column_config.NumberColumn("Cajas esperadas", format="%.0f"),
                                 "cajas_reales": st.column_config.NumberColumn("Cajas reales", format="%.0f"),
                                 "cajas_faltantes": st.column_config.NumberColumn("Cajas armadas en surtido", format="%.0f"),
                                 "cumplimiento_pct": st.column_config.NumberColumn("Cumplimiento", format="%.0f%%"),
                             })

        st.download_button("Descargar diagnóstico en Excel", posmod.a_excel(det, zonas_pos),
                           file_name=f"posiciones_picking_{v_hasta:%Y%m%d}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------------------------------------------------------------- grúa
with tab_grua:
    st.subheader("Movimientos de grúa")
    st.caption("El exporte de grúa no trae la hora, así que esta sección muestra el día completo (todos los turnos).")
    if G is None:
        st.info("Sube el archivo de movimientos de grúa en la barra lateral para ver esta sección.")
    elif G.empty:
        st.warning("No hay movimientos de grúa en el rango de fechas elegido.")
    else:
        total = G["movimientos"].sum()
        nd = G.loc[G["tipo"] == "No dirigido", "movimientos"].sum()
        rd = G.loc[G["tipo"] == "Reabastecimiento dirigido", "movimientos"].sum()
        c = st.columns(3)
        c[0].metric("Movimientos", fmt_num(total))
        c[1].metric("No dirigidos", f"{nd / total:.0%}")
        c[2].metric("Reabastecimiento dirigido", f"{rd / total:.0%}")

        diario = G.groupby(["fecha", "tipo"])["movimientos"].sum().reset_index()
        fig = px.bar(diario, x="fecha", y="movimientos", color="tipo",
                     color_discrete_map=COLORES_GRUA,
                     labels={"fecha": "", "movimientos": "Movimientos", "tipo": ""})
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Tendencia semanal de movimientos no dirigidos")
        fig = px.line(grua_sem, x="semana", y="pct_no_dirigido", markers=True,
                      color_discrete_sequence=["#C4452F"],
                      labels={"semana": "", "pct_no_dirigido": "% no dirigido"})
        fig.update_layout(yaxis_tickformat=".0%")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("La última semana puede estar incompleta.")

        st.markdown("#### Por operador")
        gu = proc.resumen_grua_usuarios(G)
        gu["pct_no_dirigido"] = gu["pct_no_dirigido"] * 100
        st.dataframe(gu, hide_index=True, use_container_width=True,
                     column_config={"pct_no_dirigido": st.column_config.ProgressColumn(
                         "% no dirigido", format="%.0f%%", min_value=0, max_value=100)})

# ---------------------------------------------------------------- calidad
with tab_calidad:
    st.subheader("Calidad de los datos cargados")
    st.caption("Todo el archivo, sin filtros.")
    st.metric("Listas válidas", fmt_num(len(listas)))
    if exclusiones:
        st.markdown("**Registros excluidos**")
        st.dataframe(pd.DataFrame(exclusiones), hide_index=True, use_container_width=True)
    else:
        st.success("No se excluyó ningún registro.")
    for aviso in avisos:
        st.warning(aviso)
    n_sol = int(brechas["solapada"].sum())
    if n_sol:
        st.warning(f"{n_sol} veces un operario inició una lista antes de terminar la anterior. "
                   "Se contaron como 0 minutos entre listas.")
