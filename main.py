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

# Parámetros por defecto configurables vía variables de entorno
DEFAULT_TIMEOUT = int(os.environ.get("CHECK_TIMEOUT", "30"))
DEFAULT_RETRIES = int(os.environ.get("CHECK_RETRIES", "2"))
DEFAULT_RETRY_DELAY = int(os.environ.get("CHECK_RETRY_DELAY", "5"))

# Confirmación entre ejecuciones: un sitio debe fallar en N ejecuciones programadas
# consecutivas (no solo en los reintentos de una misma corrida) antes de avisar a Make.
# Esto evita falsos positivos causados por lentitud puntual de WAFs (Cloudflare/LiteSpeed)
# contra la IP efímera del runner de GitHub Actions, que normalmente se resuelve sola
# en la siguiente corrida (10 min después, con un runner distinto).
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
CHECK_CONFIRM_FAILURES = max(1, int(os.environ.get("CHECK_CONFIRM_FAILURES", "2")))

# Cabeceras estándar de navegador moderno para evitar bloqueos/retardos de WAF (Cloudflare/LiteSpeed)
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

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

def load_state():
    """Carga el conteo de fallos consecutivos por sitio (persistido entre ejecuciones)."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[AVISO] No se pudo guardar el estado de monitoreo en {STATE_FILE}: {e}")

def check_site(site):
    url = site.get("url")
    client = site.get("client", url)
    site_type = site.get("type", "wordpress").lower()
    timeout = int(site.get("timeout", DEFAULT_TIMEOUT))

    if not url:
        return False, "Configuración inválida: falta el campo 'url'", 0

    start = time.time()
    try:
        # Timeout granular: (connect_timeout=10, read_timeout=timeout)
        # stream=True para evitar descargar archivos gigantes si la web es pesada
        with requests.get(url, timeout=(10, timeout), headers=BROWSER_HEADERS, stream=True) as response:
            latency_ms = int((time.time() - start) * 1000)
            
            # 1. Validar Código de Estado (Permitir códigos 2xx exitosos: 200, 202, etc.)
            if not (200 <= response.status_code < 300):
                return False, f"Status Code: {response.status_code}", latency_ms
            
            # 2. Validar Errores Silenciosos de WordPress leyendo solo los primeros 256 KB
            if site_type == "wordpress":
                content_chunks = []
                total_bytes = 0
                for chunk in response.iter_content(chunk_size=32768, decode_unicode=True):
                    if chunk:
                        content_chunks.append(chunk)
                        total_bytes += len(chunk)
                        if total_bytes >= 262144: # 256 KB
                            break
                response_text = "".join(content_chunks).lower()
                for pattern in WP_ERROR_PATTERNS:
                    if pattern.lower() in response_text:
                        return False, f"WordPress Error Detectado: '{pattern}'", latency_ms
                        
            status_msg = "OK" if response.status_code == 200 else f"OK ({response.status_code})"
            return True, status_msg, latency_ms

    except requests.exceptions.Timeout:
        return False, f"Tiempo de espera agotado (>{timeout}s)", timeout * 1000
    except requests.exceptions.SSLError:
        return False, "Error de Certificado SSL / Vencido", 0
    except requests.exceptions.ConnectionError as e:
        err_str = str(e).lower()
        if "reset" in err_str:
            reason = "Conexión reseteada por el servidor/firewall"
        elif "refused" in err_str:
            reason = "Conexión rechazada por el servidor"
        elif "name or service not known" in err_str or "getaddrinfo failed" in err_str:
            reason = "Error DNS / Dominio no encontrado"
        else:
            reason = "Fallo de Conexión de Red / Servidor no respondió"
        return False, reason, 0
    except requests.exceptions.RequestException as e:
        return False, f"Fallo en la petición: {str(e)[:80]}", 0

def run_monitor():
    webhook_url = os.environ.get("MAKE_ALERT_WEBHOOK") or os.environ.get("MAKE_WEBHOOK_ALERT_URL")
    sites = load_sites_config()
    state = load_state()

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iniciando monitoreo de {len(sites)} sitio(s)...")

    for site in sites:
        client = site.get("client", site.get("url", "Desconocido"))
        url = site.get("url", "")
        state_key = url or client
        max_retries = int(site.get("retries", DEFAULT_RETRIES))

        is_up = False
        message = ""
        latency = 0

        # Ciclo de intentos: intento inicial + reintentos con backoff progresivo
        for attempt in range(1, max_retries + 2):
            is_up, message, latency = check_site(site)
            if is_up:
                if attempt > 1:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ {client}: Recuperado exitosamente en el intento {attempt}/{max_retries + 1} ({message})")
                break

            # Si falló y aún quedan reintentos, esperar con backoff progresivo
            if attempt <= max_retries:
                delay = DEFAULT_RETRY_DELAY * attempt
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ {client} (Intento {attempt}/{max_retries + 1}): Fallo temporal '{message}'. Reintentando en {delay}s...")
                time.sleep(delay)

        status_label = "UP" if is_up else "DOWN"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {client} ({url}): {status_label} - {message} ({latency}ms)")

        if is_up:
            state[state_key] = 0
            continue

        # Sitio falló todos los intentos de esta corrida: solo cuenta como una
        # "ejecución fallida", no como caída confirmada todavía.
        consecutive_failures = state.get(state_key, 0) + 1
        state[state_key] = consecutive_failures

        if consecutive_failures < CHECK_CONFIRM_FAILURES:
            print(f"  [AVISO] {client}: falla sin confirmar ({consecutive_failures}/{CHECK_CONFIRM_FAILURES} ejecuciones). "
                  f"Se confirmará en la próxima corrida antes de alertar.")
            continue

        if not webhook_url:
            print(f"  [AVISO] {client} está DOWN (confirmado en {consecutive_failures} ejecuciones) pero no se configuró MAKE_ALERT_WEBHOOK.")
            continue

        # Notificar a Make / Slack para que envíe alertas y registre el reporte
        payload = {
            "client": client,
            "url": url,
            "status": "DOWN",
            "reason": message,
            "latency_ms": latency,
            "consecutive_failures": consecutive_failures,
            "timestamp": datetime.now().isoformat()
        }
        try:
            res = requests.post(webhook_url, json=payload, timeout=5)
            if res.status_code >= 400:
                print(f"  [ALERTA] Webhook respondió con código {res.status_code}")
        except Exception as e:
            print(f"  [ERROR] Error enviando webhook para {client}: {e}")

    save_state(state)

if __name__ == "__main__":
    run_monitor()