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
    return listas, brechas, exclusiones, avisos


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
    return drive.listar_csv(cliente_drive(credenciales_json), carpeta_id)


@st.cache_data(show_spinner=False, max_entries=200)
def bajar_drive(credenciales_json, archivo_id, modificado):
    # "modificado" forma parte de la clave de caché: si el archivo cambia en Drive, se vuelve a bajar
    import drive
    return drive.descargar(cliente_drive(credenciales_json), archivo_id)


conf_drive = secretos_drive()
contenidos_pick, contenidos_grua = (), ()

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
        a_pick = [f for f in archivos if cfg.PATRON_PICKING in f["name"].upper()]
        a_grua = [f for f in archivos if cfg.PATRON_GRUA in f["name"].upper()]
        with st.spinner(f"Descargando {len(a_pick) + len(a_grua)} archivos..."):
            contenidos_pick = tuple(bajar_drive(cred, f["id"], f["modifiedTime"]) for f in a_pick)
            contenidos_grua = tuple(bajar_drive(cred, f["id"], f["modifiedTime"]) for f in a_grua)
        st.caption(f"{len(a_pick)} archivos de picking y {len(a_grua)} de grúa encontrados en Drive. "
                   "La lista se refresca sola cada hora o con el botón.")
        with st.expander("Ver archivos"):
            for f in a_pick + a_grua:
                st.caption(f"{f['carpeta']}{f['name']}")
    else:
        arch_pick = st.file_uploader("Picking (CAJA_PICKEADA)", type="csv", accept_multiple_files=True)
        arch_grua = st.file_uploader("Movimientos de grúa", type="csv", accept_multiple_files=True)
        st.caption("Puedes subir varios archivos de cada tipo; los registros repetidos se eliminan.")
        contenidos_pick = tuple(f.getvalue() for f in arch_pick or [])
        contenidos_grua = tuple(f.getvalue() for f in arch_grua or [])

st.title("Torre de control WMS")
st.caption(f"CD Coquimbo · Turno {', '.join(cfg.TURNOS_ANALIZADOS)}")

if not contenidos_pick:
    st.info("No hay archivos de picking. Sube uno o más CAJA_PICKEADA en la barra lateral, "
            "o déjalos en la carpeta de Drive. El archivo de grúa es opcional.")
    st.stop()

try:
    listas, brechas, exclusiones, avisos = cargar_picking(contenidos_pick)
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

tab_res, tab_op, tab_zona, tab_tiempo, tab_grua, tab_calidad = st.tabs(
    ["Resumen", "Operarios", "Zonas", "Tiempo no efectivo", "Grúa", "Calidad de datos"])

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
