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


# ---------------------------------------------------------------- barra lateral
with st.sidebar:
    st.header("Datos")
    arch_pick = st.file_uploader("Picking (CAJA_PICKEADA)", type="csv", accept_multiple_files=True)
    arch_grua = st.file_uploader("Movimientos de grúa", type="csv", accept_multiple_files=True)
    st.caption("Puedes subir varios archivos de cada tipo; los registros repetidos se eliminan.")

st.title("Torre de control WMS")
st.caption("CD Coquimbo")

if not arch_pick:
    st.info("Sube uno o más archivos CAJA_PICKEADA en la barra lateral para ver los indicadores. "
            "El archivo de grúa es opcional.")
    st.stop()

try:
    listas, brechas, exclusiones, avisos = cargar_picking(tuple(f.getvalue() for f in arch_pick))
except ValueError as e:
    st.error(str(e))
    st.stop()

grua = None
if arch_grua:
    try:
        grua = cargar_grua(tuple(f.getvalue() for f in arch_grua))
    except ValueError as e:
        st.error(str(e))

# ---------------------------------------------------------------- filtros
fechas = sorted(listas["fecha_op"].dt.date.unique())
with st.sidebar:
    st.header("Filtros")
    rango = st.date_input("Fecha operativa", value=(fechas[-1], fechas[-1]),
                          min_value=fechas[0], max_value=fechas[-1])
    turnos_sel = st.multiselect("Turno", sorted(listas["turno"].unique()),
                                default=sorted(listas["turno"].unique()))
    zonas_sel = st.multiselect("Zona de trabajo", sorted(listas["zona"].unique()),
                               default=sorted(listas["zona"].unique()))

if not isinstance(rango, tuple) or len(rango) != 2:
    st.info("Elige la fecha de término del rango.")
    st.stop()
desde, hasta = pd.Timestamp(rango[0]), pd.Timestamp(rango[1])


def filtrar(df):
    m = (df["fecha_op"].between(desde, hasta) & df["turno"].isin(turnos_sel)
         & df["zona"].isin(zonas_sel))
    return df[m]


L, B = filtrar(listas), filtrar(brechas)
if L.empty:
    st.warning("No hay listas para los filtros elegidos. Amplía el rango de fechas o los turnos.")
    st.stop()

op_diario = proc.resumen_operarios(L, B)
operarios = proc.consolidar_operarios(op_diario)
operarios["utilizacion_pct"] = operarios["utilizacion"] * 100
turnos = proc.resumen_turnos(L, B)
zonas = proc.resumen_zonas(L, B)

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
    h_ef = turnos["min_efectivos"].sum() / 60
    cajas = turnos["cajas"].sum()
    prod_total = cajas / hh if hh else 0
    c = st.columns(5)
    c[0].metric("Cajas pickeadas", fmt_num(cajas))
    c[1].metric("Operarios", int(L["usuario"].nunique()))
    c[2].metric("cj/HH total", fmt_num(prod_total),
                delta=f"{fmt_num(prod_total - cfg.META_ICEO)} vs meta {cfg.META_ICEO}")
    c[3].metric("cj/HH efectiva", fmt_num(cajas / h_ef if h_ef else 0))
    c[4].metric("Tiempo en listas", f"{h_ef / hh:.0%}" if hh else "-",
                help="Horas dentro de listas sobre horas-hombre (ventana de picking × operarios), igual que el Power BI.")

    st.markdown("#### Alertas")
    for nivel, texto in proc.generar_alertas(turnos, operarios, zonas, grua_sem):
        getattr(st, nivel)(texto)

    if turnos["fecha_op"].nunique() > 1:
        tend = turnos.groupby("fecha_op").agg(cajas=("cajas", "sum"), hh=("horas_hombre", "sum"),
                                              mef=("min_efectivos", "sum")).reset_index()
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
        turnos[["fecha_op", "turno", "inicio", "fin", "operarios", "listas", "cajas",
                "cj_hh_total", "cj_hh_efectiva", "min_espera", "min_pausa"]],
        hide_index=True, use_container_width=True,
        column_config={
            "fecha_op": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
            "inicio": st.column_config.DatetimeColumn("Inicio picking", format="HH:mm"),
            "fin": st.column_config.DatetimeColumn("Fin picking", format="HH:mm"),
            "cj_hh_total": st.column_config.NumberColumn("cj/HH total", format="%.0f"),
            "cj_hh_efectiva": st.column_config.NumberColumn("cj/HH efectiva", format="%.0f"),
            "min_espera": st.column_config.NumberColumn("Min. espera", format="%.0f"),
            "min_pausa": st.column_config.NumberColumn("Min. pausa", format="%.0f"),
        })

# ---------------------------------------------------------------- operarios
with tab_op:
    st.subheader("Uso del tiempo por operario")
    st.caption("Utilización = minutos dentro de listas / minutos entre su primera y última lista, "
               "descontando colación. Ordenado de menor a mayor.")
    fig = px.bar(operarios, x="utilizacion", y="nombre", orientation="h",
                 color_discrete_sequence=[VERDE],
                 labels={"utilizacion": "Utilización", "nombre": ""})
    fig.add_vline(x=cfg.UMBRAL_UTILIZACION, line_dash="dash", line_color="#C4452F")
    fig.update_layout(xaxis_tickformat=".0%", height=max(300, 28 * len(operarios)),
                      yaxis={"categoryorder": "total descending"})
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        operarios[["nombre", "turnos", "listas", "cajas", "horas_efectivas", "horas_disponibles",
                   "utilizacion_pct", "cj_h_efectiva", "cj_h_disponible", "min_espera", "min_pausa",
                   "min_inicio_tardio"]],
        hide_index=True, use_container_width=True,
        column_config={
            "nombre": "Operario",
            "horas_efectivas": st.column_config.NumberColumn("Horas en listas", format="%.2f"),
            "horas_disponibles": st.column_config.NumberColumn("Horas disponibles", format="%.2f"),
            "utilizacion_pct": st.column_config.ProgressColumn("Utilización", format="%.0f%%",
                                                               min_value=0, max_value=100),
            "cj_h_efectiva": st.column_config.NumberColumn("cj/h en listas", format="%.0f"),
            "cj_h_disponible": st.column_config.NumberColumn("cj/h disponible", format="%.0f"),
            "min_espera": st.column_config.NumberColumn("Min. espera", format="%.0f"),
            "min_pausa": st.column_config.NumberColumn("Min. pausa", format="%.0f"),
            "min_inicio_tardio": st.column_config.NumberColumn(
                "Inicio tardío (min)", format="%.0f",
                help="Minutos promedio entre la primera lista del turno y la primera lista del operario."),
        })

# ---------------------------------------------------------------- zonas
with tab_zona:
    st.subheader("Rendimiento por zona de trabajo")
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
