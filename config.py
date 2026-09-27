"""Parámetros de la torre de control. Ajusta estos valores a la operación real del CD."""

# Meta de productividad sobre tiempo total (cajas por hora-hombre)
META_ICEO = 240

# Turnos: (nombre, hora inicio, hora fin). Un turno que cruza medianoche
# (ej. 22:00 a 06:00) se asigna a la fecha del día en que empezó.
TURNOS = [
    ("TA", "08:00", "16:00"),
    ("TB", "16:00", "24:00"),
    ("TC", "00:00", "08:00"),
]

# Nombres de archivo que la app busca en Drive (en mayúsculas, basta con que el nombre lo contenga)
PATRON_PICKING = "CAJA_PICKEADA"
PATRON_GRUA = "MOVIMIENTO_DE_GRUA"

# LPN de surtido (nivel S) de un solo artículo con al menos estas cajas se consideran
# "pallets armados en surtido" (debieron salir como pallet completo desde almacenamiento)
UMBRAL_LPN_CASI_PALLET = 50

# Días de la semana sin picking (0 = lunes ... 6 = domingo). La venta del sábado se pickea el lunes.
DIAS_SIN_PICKING = [6]

# Posiciones de picking
PATRON_VENTA = "VENTA"                    # archivos de venta (CSV recomendado; Excel es lento)
PATRON_MAESTRO = "DATOS PARA ARMAR"
PATRON_FACTOR_PALLET = "CAJAS X PALLET"   # columnas ID_SKU_INV y CAJAS_POR_PALLET       # maestro de ubicaciones (Excel con hojas UBICACIONES, PREFERENCIA, ZM)
VENTANA_VENTA_SEMANAS = 8                # el diagnóstico usa solo las últimas N semanas de venta (None = toda)
MIN_LINEAS_DIA_OPERATIVO = 1000           # días con menos líneas de venta no se consideran
PERCENTIL_DIA_ALTO = 0.9                  # "día alto" = percentil 90 de la venta diaria del SKU

# Turnos que analiza la app (la app muestra solo estos)
TURNOS_ANALIZADOS = ["TC"]

# El pallet completo se identifica por la columna nivel_lpn del exporte (L = pallet, S = surtido).
# Solo si el archivo no trae esa columna se usa el nombre de la zona:
# cualquier zona que empiece con estos textos cuenta como pallet completo.
PREFIJOS_PALLET_COMPLETO = ["ZT ALMACENAMIENTO"]

# Zonas que no son picking y se excluyen de los indicadores.
# Escribe el nombre exacto como aparece en el archivo, ej. "ZT ALMACENAMIENTO".
ZONAS_EXCLUIDAS = []

# Clasificación del tiempo entre listas (minutos)
UMBRAL_ESPERA_MIN = 5    # bajo esto: normal (traslado, tomar la siguiente lista)
UMBRAL_PAUSA_MIN = 15    # entre espera y pausa: espera; sobre esto: pausa

# Colación: en cada operario y turno, la pausa más larga dentro de este rango
# se marca como colación y no se cuenta como tiempo perdido.
DESCONTAR_COLACION = True
COLACION_MIN_MIN = 25
COLACION_MAX_MIN = 60

# Listas con duración mayor a esto se excluyen (probable lista abandonada o error)
DURACION_MAX_LISTA_MIN = 120

# El WMS registra hora:minuto, así que una lista de 0 minutos duró algo menos
# de un minuto. Se le asigna esta duración para no inflar la productividad.
DURACION_MIN_LISTA_MIN = 0.5

# Alertas
UMBRAL_UTILIZACION = 0.50      # operario con menos % de su tiempo en listas
UMBRAL_NO_DIRIGIDO = 0.50      # % de movimientos de grúa no dirigidos
