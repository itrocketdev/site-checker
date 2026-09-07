import os
import sys
import json
import time
from datetime import datetime
import requests

# Cargar variables de entorno desde .env si existe (útil en desarrollo local)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    if os.path.exists(".env"):
        print("[AVISO] Se detectó un archivo .env pero 'python-dotenv' no está instalado.")
        print("Instálalo ejecutando: pip install -r requirements.txt\n")

WP_ERROR_PATTERNS = [
    "critical error on this website",
    "Error establishing a database connection",
    "Fatal error:",
    "Briefly unavailable for scheduled maintenance",
]

def load_sites_config():
    """
    Carga la configuración de sitios desde la variable de entorno SITES_CONFIG
    o desde un archivo sites.json local como fallback.
    """
    sites_raw = os.environ.get("SITES_CONFIG")
    
    if sites_raw:
        try:
            sites = json.loads(sites_raw)
            if isinstance(sites, list):
                return sites
            print("Error: SITES_CONFIG debe ser un array JSON de sitios.")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"Error parseando el JSON de SITES_CONFIG: {e}")
            sys.exit(1)

    # Fallback para entorno local: buscar archivo sites.json
    if os.path.exists("sites.json"):
        try:
            with open("sites.json", "r", encoding="utf-8") as f:
                sites = json.load(f)
                if isinstance(sites, list):
                    return sites
                print("Error: sites.json debe contener un array JSON.")
                sys.exit(1)
        except Exception as e:
            print(f"Error leyendo sites.json: {e}")
            sys.exit(1)

    print("Error: No se encontró la configuración de sitios.")
    print("Asegúrate de definir la variable de entorno SITES_CONFIG o crear un archivo sites.json.")
    sys.exit(1)

def check_site(site):
    url = site.get("url")
    client = site.get("client", url)
    site_type = site.get("type", "wordpress").lower()

    if not url:
        return False, "Configuración inválida: falta el campo 'url'", 0

    start = time.time()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 (SiteHealthChecker/1.0)"
    }
    try:
        response = requests.get(url, timeout=12, headers=headers)
        latency_ms = int((time.time() - start) * 1000)
        
        # 1. Validar Código de Estado (Permitir 200 OK, 202 Accepted y cualquier código 2xx exitoso)
        if not (200 <= response.status_code < 300):
            return False, f"Status Code: {response.status_code}", latency_ms
        
        # 2. Validar Errores Silenciosos de WordPress
        if site_type == "wordpress":
            response_text = response.text.lower()
            for pattern in WP_ERROR_PATTERNS:
                if pattern.lower() in response_text:
                    return False, f"WordPress Error Detectado: '{pattern}'", latency_ms
                    
        status_msg = "OK" if response.status_code == 200 else f"OK ({response.status_code})"
        return True, status_msg, latency_ms

    except requests.exceptions.Timeout:
        return False, "Timeout (>12s)", 12000
    except requests.exceptions.SSLError:
        return False, "Error de Certificado SSL / Vencido", 0
    except requests.exceptions.RequestException as e:
        return False, f"Fallo de Conexión: {str(e)[:100]}", 0

def run_monitor():
    webhook_url = os.environ.get("MAKE_ALERT_WEBHOOK") or os.environ.get("MAKE_WEBHOOK_ALERT_URL")
    sites = load_sites_config()

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iniciando monitoreo de {len(sites)} sitio(s)...")

    for site in sites:
        client = site.get("client", site.get("url", "Desconocido"))
        url = site.get("url", "")
        is_up, message, latency = check_site(site)
        
        # Si falló, realizar 1 reintento tras 3 segundos para confirmar que no sea un falso positivo
        if not is_up:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ {client}: Advertencia '{message}'. Reintentando en 3s...")
            time.sleep(3)
            is_up_retry, message_retry, latency_retry = check_site(site)
            if is_up_retry:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ {client}: Recuperado en reintento ({message_retry})")
                is_up, message, latency = True, message_retry, latency_retry
            else:
                message = message_retry
                latency = latency_retry

        status_label = "UP" if is_up else "DOWN"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {client} ({url}): {status_label} - {message} ({latency}ms)")
        
        if not is_up:
            if not webhook_url:
                print(f"  [AVISO] {client} está DOWN pero no se configuró MAKE_ALERT_WEBHOOK.")
                continue

            # Notificar a Make / Slack para que envíe alertas y registre el reporte
            payload = {
                "client": client,
                "url": url,
                "status": "DOWN",
                "reason": message,
                "latency_ms": latency,
                "timestamp": datetime.now().isoformat()
            }
            try:
                res = requests.post(webhook_url, json=payload, timeout=5)
                if res.status_code >= 400:
                    print(f"  [ALERTA] Webhook respondió con código {res.status_code}")
            except Exception as e:
                print(f"  [ERROR] Error enviando webhook para {client}: {e}")

if __name__ == "__main__":
    run_monitor()