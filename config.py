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
