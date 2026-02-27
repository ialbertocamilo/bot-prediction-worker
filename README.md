# Bot de Telegram: Estadísticas de fútbol y predicción

Bot que ofrece **estadísticas de partidos de fútbol** y una **predicción del ganador** basada en datos (tabla de posiciones, enfrentamientos directos y forma reciente).

## Requisitos

- Python 3.10 o superior
- Cuenta en Telegram y token de bot ([@BotFather](https://t.me/BotFather))
- (Recomendado) API key gratuita de [football-data.org](https://www.football-data.org/) — datos de la temporada actual

## Instalación

```bash
cd "e:\Prueba Bots Telegram"
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Configuración

1. Copia el archivo de ejemplo y edita con tus claves:

   ```bash
   copy .env.example .env
   ```

2. En `.env` define:

   - **TELEGRAM_BOT_TOKEN**: Token que te da BotFather al crear el bot.
   - **FOOTBALL_API_TOKEN**: (Opcional pero recomendado) Regístrate en [football-data.org](https://www.football-data.org/), obtén tu API key y pégala aquí. Sin ella solo listados básicos; con ella: tablas, partidos y predicciones de la temporada actual.

## Ejecutar el bot

```bash
venv\Scripts\activate
python bot.py
```

## Comandos del bot

| Comando | Descripción |
|--------|-------------|
| `/start` | Bienvenida y lista de comandos |
| `/ligas` | Lista de competiciones disponibles (códigos para /tabla y /partidos) |
| `/tabla <código>` | Tabla de posiciones (ej: `/tabla PL`, `/tabla PD`, `/tabla SA`) |
| `/partidos [código]` | Partidos de hoy o de una competición (ej: `/partidos PL`) |
| `/vivo` | Partidos en vivo en este momento |
| `/partido <id>` | Detalle de un partido (el ID sale en `/partidos`) |
| `/prediccion <id>` | Predicción del ganador según estadísticas (tabla, H2H, forma reciente) |

## Códigos de competiciones (ejemplos)

- **PL** — Premier League  
- **PD** — La Liga  
- **SA** — Serie A  
- **BL1** — Bundesliga  
- **DED** — Eredivisie  
- **CL** — Champions League  

Usa `/ligas` para ver todos los disponibles con tu API.

## Cómo se calcula la predicción

La predicción combina:

1. **Enfrentamientos directos**: historial entre los dos equipos.
2. **Tabla de posiciones**: puntos en la competición (si aplica).
3. **Forma reciente**: resultados de los últimos 5 partidos de cada equipo.

Con eso se obtienen probabilidades aproximadas (local / empate / visitante) y un ganador más probable con un % de confianza.

## Limitaciones API football-data.org

- Plan gratuito: **10 peticiones por minuto**. Acceso a la temporada actual.
- Sin token: solo áreas y listado de competiciones; no tablas ni partidos ni predicciones.

## Estructura del proyecto

```
Prueba Bots Telegram/
├── bot.py           # Bot Telegram y comandos
├── config.py        # Carga de variables de entorno
├── football_api.py  # Cliente API football-data.org v4
├── predictor.py     # Lógica de predicción
├── requirements.txt
├── .env.example
└── README.md
```

## Licencia

Uso libre para aprendizaje y uso personal. Respeta los términos de uso de Telegram y de football-data.org.
