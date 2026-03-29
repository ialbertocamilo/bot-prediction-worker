"""Crear usuario de prueba de Mercado Pago para sandbox."""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

with open("test_user_output.txt", "w") as f:
    r = requests.post(
        "https://api.mercadopago.com/users/test_user",
        headers=headers,
        json={"site_id": "MPE", "description": "Comprador test"},
        timeout=15,
    )
    f.write("=== COMPRADOR DE PRUEBA (MPE) ===\n")
    f.write(f"HTTP Status: {r.status_code}\n")
    if r.status_code == 201:
        d = r.json()
        f.write(f"Email: {d['email']}\n")
        f.write(f"Password: {d['password']}\n")
        f.write(f"User ID: {d['id']}\n")
        f.write(f"Nickname: {d.get('nickname','')}\n")
        f.write(f"Site: {d.get('site_id','')}\n")
    else:
        f.write(f"Response: {r.text[:500]}\n")

print("Listo → test_user_output.txt")
