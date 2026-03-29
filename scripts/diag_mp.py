"""Diagnóstico de Mercado Pago — ejecutar una sola vez para depurar."""
import os
import sys
import mercadopago
from dotenv import load_dotenv

load_dotenv()

# Redirigir toda la salida a un archivo
f = open("diag_mp_output.txt", "w", encoding="utf-8")

def p(msg=""):
    print(msg)
    f.write(msg + "\n")
    f.flush()

token = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")
webhook_url = os.getenv("APP_BASE_URL", "")
sandbox = os.getenv("MP_SANDBOX", "true")

p("=" * 50)
p("DIAGNÓSTICO MERCADO PAGO")
p("=" * 50)

p(f"\n1. ACCESS_TOKEN: ...{token[-10:]}" if len(token) > 10 else f"\n1. ACCESS_TOKEN: {token or 'VACÍO!'}")
p(f"2. WEBHOOK_BASE_URL: {webhook_url}")
p(f"3. SANDBOX: {sandbox}")

sdk = mercadopago.SDK(token)

# Test credenciales buscando pagos recientes
p("\n--- Test credenciales (search payments) ---")
r = sdk.payment().search({
    "sort": "date_created",
    "criteria": "desc",
    "range": "date_created",
    "begin_date": "NOW-7DAYS",
    "end_date": "NOW",
})
p(f"Status: {r['status']}")
if r["status"] == 200:
    results = r["response"].get("results", [])
    p(f"Pagos en últimos 7 días: {len(results)}")
    for pay in results[:5]:
        p(f"  id={pay['id']}, status={pay.get('status')}, "
          f"ext_ref={pay.get('external_reference')}, "
          f"amount={pay.get('transaction_amount')} {pay.get('currency_id')}, "
          f"date={pay.get('date_created','')[:19]}")
else:
    p(f"Error: {r['response']}")

# Test crear una preference de prueba
p("\n--- Test crear preference ---")
pref_data = {
    "items": [{
        "title": "Test diagnóstico",
        "quantity": 1,
        "unit_price": 50.0,
        "currency_id": os.getenv("MP_ITEM_CURRENCY", "PEN"),
    }],
    "external_reference": "test_diag_99999",
    "notification_url": f"{webhook_url}/payments/webhook",
    "back_urls": {
        "success": f"{webhook_url}/payments/result?status=ok",
        "failure": f"{webhook_url}/payments/result?status=fail",
        "pending": f"{webhook_url}/payments/result?status=pending",
    },
    "auto_return": "approved",
}
r2 = sdk.preference().create(pref_data)
p(f"Status: {r2['status']}")
if r2["status"] == 201:
    body = r2["response"]
    p(f"Preference ID: {body['id']}")
    p(f"init_point: {body.get('init_point','N/A')}")
    p(f"sandbox_init_point: {body.get('sandbox_init_point','N/A')}")
    p(f"notification_url: {body.get('notification_url','N/A')}")
else:
    p(f"Error response: {r2['response']}")

# Test webhook URL accesible
p("\n--- Test webhook URL ---")
try:
    import requests
    url = f"{webhook_url}/payments/result?status=test"
    resp = requests.get(url, timeout=10)
    p(f"GET {url} → {resp.status_code} {resp.text[:100]}")
except Exception as e:
    p(f"ERROR: No se pudo acceder al webhook: {e}")
    p("¿Está corriendo uvicorn? ¿Está activo ngrok?")

p("\n" + "=" * 50)
p("FIN DIAGNÓSTICO")
f.close()
