# 🚀 Site Health & Uptime Checker

Sistema automatizado de monitoreo de disponibilidad (uptime) y salud de sitios web, ejecutado automáticamente mediante **GitHub Actions** cada 10 minutos (y bajo demanda). Si algún sitio falla o presenta errores críticos de WordPress, envía una alerta inmediata a través de un webhook de **Make (Integromat)** hacia **Slack** u otros canales.

El código está diseñado para ser alojado en un **repositorio público** de forma 100% segura, manteniendo las URLs de clientes y las credenciales de webhook completamente aisladas mediante **GitHub Secrets** y variables de entorno.

---

## 🛠️ Características

- **Monitoreo Periódico y Serverless**: Corre en GitHub Actions cada 10 minutos sin necesidad de un servidor dedicado.
- **Tolerancia a Fallos y Prevención de Falsos Positivos**:
  - **Timeout extendido**: 30 segundos por defecto (personalizable globalmente o por cliente) para soportar sitios con CRMs pesados o cachés frías.
  - **Triple Intento con Backoff Progresivo**: Si un sitio demora o falla, se realizan hasta 2 reintentos con esperas progresivas (5s y 10s) antes de confirmar una caída dentro de la misma corrida.
  - **Confirmación entre Ejecuciones**: Una caída solo genera alerta si el sitio también falló en la ejecución programada anterior (por defecto 2 corridas consecutivas, `CHECK_CONFIRM_FAILURES`). Esto filtra falsos positivos causados por lentitud puntual de WAFs (Cloudflare/LiteSpeed) contra la IP efímera del runner de GitHub Actions, que normalmente se resuelve sola en la siguiente corrida.
  - **Cabeceras de Navegador Real**: Evasión de bloqueos o retardos generados por sistemas WAF/Anti-Bot de Cloudflare y LiteSpeed.
  - **Lectura Optimizada (Streaming)**: Inspección rápida de los primeros 256 KB sin descargar megabytes de imágenes o assets innecesarios.
- **Detección de Caídas y Errores HTTP**: Alerta ante códigos fuera del rango 2xx (404, 500, 502, etc.), timeouts o fallos de certificados SSL.
- **Detección de Errores Silenciosos en WordPress**: Detecta fallos comunes que a veces responden HTTP 200 pero muestran pantallas blancas o mensajes de error:
  - *"critical error on this website"*
  - *"Error establishing a database connection"*
  - *"Fatal error:"*
  - *"Briefly unavailable for scheduled maintenance"*
- **Alertas en Tiempo Real**: Envío de payload JSON a Make/Slack con nombre del cliente, URL, motivo del fallo, latencia y marca de tiempo.
- **Seguridad y Privacidad**: Ningún dato de clientes o webhook queda hardcodeado en el repositorio público.

---

## ⚙️ Configuración en GitHub Actions (Secrets y Variables)

Para que el script funcione en GitHub sin exponer la información de tus clientes:

1. Ve a tu repositorio en GitHub: `https://github.com/itrocketdev/site-checker`.
2. Dirígete a **Settings** > **Secrets and variables** > **Actions**.
3. Añade los siguientes secretos y variables opcionales:

### 1. `MAKE_ALERT_WEBHOOK` (Secret Requerido)
- **Nombre**: `MAKE_ALERT_WEBHOOK`
- **Valor**: La URL completa del webhook de Make (ej. `https://hook.us2.make.com/xxxxxxxxxxxxxxxxx`).

### 2. `SITES_CONFIG` (Secret Requerido)
- **Nombre**: `SITES_CONFIG`
- **Valor**: Lista de sitios en formato JSON (como un array de objetos):

```json
[
  {
    "client": "Cliente Ejemplo 1",
    "url": "https://ejemplo-wordpress.com/",
    "type": "wordpress",
    "timeout": 35
  },
  {
    "client": "Cliente Ejemplo 2",
    "url": "https://ejemplo-tienda.com/",
    "type": "wordpress"
  },
  {
    "client": "Mi Web Estática o API",
    "url": "https://ejemplo-api.com/",
    "type": "standard"
  }
]
```

> **Campos opcionales por cliente:**
> - `type`: `"wordpress"` (habilita detección de errores silenciosos de WP) o `"standard"` (valida solo código HTTP y conectividad).
> - `timeout`: Tiempo de espera en segundos específico para ese cliente (por defecto `30`). Útil para sitios con CRM o backends pesados.
> - `retries`: Cantidad de reintentos específicos antes de considerarlo caído (por defecto `2`).

### 3. `CHECK_CONFIRM_FAILURES` (Variable Opcional)
- **Valor por defecto**: `2`.
- Número de ejecuciones programadas **consecutivas** en las que un sitio debe salir DOWN antes de enviar la alerta a Make. Con el valor por defecto, una caída detectada en una sola corrida (p. ej. por lentitud momentánea de un WAF) no dispara alerta; debe repetirse en la corrida siguiente (~10 min después) para confirmarse. El conteo se guarda entre corridas mediante la caché de GitHub Actions (`state.json`).

---

## 📩 Payload Enviado a Make / Slack

Cuando un sitio está caído (`DOWN`), se envía una petición `POST` al webhook con el siguiente formato JSON:

```json
{
  "client": "Nombre del Cliente",
  "url": "https://sitio-ejemplo.com/",
  "status": "DOWN",
  "reason": "WordPress Error Detectado: 'Error establishing a database connection'",
  "latency_ms": 145,
  "consecutive_failures": 2,
  "timestamp": "2026-09-07T14:30:00.000000"
}
```

En Make puedes capturar este JSON con un módulo **Custom Webhook** y canalizarlo a un módulo de **Slack** (`Create a Message`), email o SMS.

---

## 💻 Ejecución Local y Pruebas

Para probar o ejecutar el monitor en tu máquina local:

### 1. Clonar e instalar dependencias
```bash
git clone git@github.com:itrocketdev/site-checker.git
cd site-checker
pip install -r requirements.txt
```

### 2. Configurar variables locales
Puedes crear un archivo `.env` (el cual está protegido por `.gitignore` para no ser subido):

```bash
cp .env.example .env
```

Edita `.env` con tus valores reales:
```env
MAKE_ALERT_WEBHOOK="https://hook.us2.make.com/tu_webhook"
SITES_CONFIG='[{"client": "Test", "url": "https://ejemplo-wordpress.com/", "type": "wordpress"}]'
```

*Alternativa local:* También puedes crear un archivo `sites.json` en la raíz con el array JSON de sitios, el script lo detectará automáticamente si `SITES_CONFIG` no está definido en el entorno.

### 3. Ejecutar el monitor
```bash
python main.py
```

Salida esperada en consola:
```text
[2026-09-07 09:30:00] Iniciando monitoreo de 2 sitio(s)...
[09:30:01] Cliente Ejemplo 1 (https://ejemplo-wordpress.com/): UP - OK (230ms)
[09:30:02] Cliente Ejemplo 2 (https://ejemplo-tienda.com/): UP - OK (410ms)
```

---

## 🔄 Ejecución Manual en GitHub Actions

Si deseas forzar un chequeo inmediato sin esperar el ciclo de 10 minutos:
1. En GitHub, ve a la pestaña **Actions**.
2. Selecciona el workflow **"Tech Support - Uptime Monitor"** en el panel lateral izquierdo.
3. Haz clic en el botón desplegable **Run workflow** y confirma con **Run workflow**.

---

## ⚠️ Nota sobre Repositorios Públicos en GitHub Actions

GitHub Actions desactiva automáticamente los flujos programados (`cron`) en repositorios públicos si no ha habido actividad de commits durante **60 días consecutivos**. Si esto ocurre, GitHub enviará un correo notificándolo y bastará con hacer clic en **"Enable workflow"** desde la pestaña Actions o realizar un commit en el repositorio.
