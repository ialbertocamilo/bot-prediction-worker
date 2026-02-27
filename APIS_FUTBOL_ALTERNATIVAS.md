# APIs de fútbol alternativas (más fiables que football-data.org)

Resumen de opciones usadas por casas de apuestas o con buena reputación para datos en vivo.

---

## Opciones usadas por casas de apuestas (profesionales, de pago)

Estas son las que usan operadores de apuestas; datos en tiempo real y muy fiables, pero con precios altos.

| Proveedor | Qué ofrece | Notas |
|-----------|------------|--------|
| **LSports** | API de datos en vivo, cuotas baja latencia (&lt;1 s), 2000+ ligas, 550+ mercados | Muy usada por bookmakers. Contacto comercial. |
| **OddsMatrix** | Datos verificados, 14.900 competiciones, liquidaciones automáticas 99,99% | Enfocada a operadores. |
| **STATSCORE / SportsAPI** | 28 deportes, 14.000+ competiciones, datos 24/7 verificados | Proveedor serio para apuestas. |
| **BetsAPI / BetsAPI.Live** | Resultados en vivo, eventos, cuotas multi-bookmaker | Desde ~299 USD/mes, hasta 3600 req/h. |
| **Odds API (odds-api.io)** | 12.000+ ligas fútbol, 250+ bookmakers, latencia &lt;150 ms, WebSocket | Muy orientada a cuotas y live. |

**Conclusión:** Si en el futuro necesitas datos “de nivel casa de apuestas”, estos son los nombres a buscar. No suelen tener plan gratuito para desarrollo.

---

## Opciones para desarrolladores (con plan gratis o barato)

### 1. **API-Football** (api-football.com / RapidAPI) — **Recomendada**

- **Plan gratis:** 100 peticiones/día, sin tarjeta.
- **Datos:** Livescore, fixtures, equipos, tablas, estadísticas, predicciones, cuotas, lesiones, alineaciones.
- **En vivo:** Datos de partido actualizados cada **15 segundos**; estados claros: 1H, HT, 2H, FT, etc.
- **Cobertura:** 1.100+ ligas y copas.
- **URLs:**  
  - RapidAPI: `https://api-football-v1.p.rapidapi.com/v3/`  
  - Directo: `https://v3.football.api-sports.io/`
- **Planes de pago:** Pro 19 USD/mes (7.500 req/día), Ultra 29 USD (75.000), Mega 39 USD (150.000).

Muy usada y con documentación clara; suele ser más fiable que football-data.org para estado “en vivo” y finalización.

### 2. **SportDB.dev**

- API gratuita: 1.000 peticiones (límite por periodo).
- Fútbol, baloncesto, tenis, hockey.
- Resultados en tiempo real, calendario, tablas, jugadores.
- Buena opción si quieres probar algo distinto sin gastar.

### 3. **SportDataAPI** (sportdataapi.com)

- Plan gratis: 500 llamadas/día para 2 ligas a elegir.
- 800+ ligas, 100+ países.
- Live scores, fixtures, tablas, alineaciones, plantillas.
- Útil si te centras en pocas ligas.

### 4. **ESPN API** (espnapi.com)

- Datos en vivo de ESPN (NFL, NBA, MLB, fútbol, etc.).
- Acceso vía endpoints web.
- Menos enfocada solo en fútbol; más “multi-deporte USA” pero incluye soccer.

---

## Recomendación práctica para tu bot

1. **Probar primero API-Football**  
   - Plan gratis 100 req/día suele bastar para un bot de Telegram con /vivo, /partidos y algo de tablas.  
   - Los estados de partido (1H, 2H, FT) y la actualización cada 15 s suelen dar menos problemas que football-data.org con partidos “en vivo” que ya terminaron.

2. **Mantener football-data.org como respaldo**  
   - Puedes dejar tu código actual y añadir un cliente para API-Football; por configuración elegir qué API usar (o intentar una y, si falla, la otra).

3. **Si solo quieres “más fiable” sin cambiar de API**  
   - Las heurísticas que ya tienes (minuto ≥ 96, `lastUpdated`) siguen siendo útiles con football-data.org mientras no cambies de proveedor.

Si quieres, el siguiente paso puede ser: definir en `config` una API key de API-Football y un módulo `football_api_apifootball.py` que implemente los mismos métodos que usan `/vivo` y `/partidos`, para poder cambiar de proveedor con una variable de configuración.
