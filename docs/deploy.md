# Deploy del backend (Etapa 7)

Guía paso a paso para poner en producción el backend: FastAPI en Render (plan gratuito), Postgres con pgvector en Supabase, embeddings en Cloudflare Workers AI, el LLM en Groq con Workers AI como respaldo, y dos tareas programadas en GitHub Actions.

Estado: el código del deploy (límites, presupuesto, caché, logs, `render.yaml` y `.github/workflows/scheduled.yml`) está probado en local con modelos simulados. Todavía no se probó contra las cuentas reales. Lo marcado "a confirmar" se comprueba en el primer deploy y se anota en `docs/decisions.md`.

## Qué queda andando

| Pieza | Dónde | Qué hace |
|---|---|---|
| Backend | Render, servicio web gratuito, `render.yaml` | `/search`, `/ask`, `/passages`, `/works`, `/healthz`, `/readyz` |
| Base | Supabase (Postgres + pgvector) | Índice, caché de embeddings, contadores por IP, presupuesto diario y caché de respuestas |
| Embeddings | Cloudflare Workers AI, `@cf/baai/bge-m3` | Embeben las consultas (y el índice, al construirlo) |
| LLM | Groq, `openai/gpt-oss-20b`; respaldo Workers AI, `@cf/openai/gpt-oss-20b` | Genera la respuesta |
| Tareas programadas | GitHub Actions, `scheduled.yml` | Ping cada 2 días y evals semanales contra producción |

## 1. Cuentas y valores

Lo hace la persona. Cada valor va en dos lugares a lo sumo: Render (el backend) y los secretos de GitHub Actions (las tareas programadas). Nunca en el repo.

| Valor | De dónde sale | Va en |
|---|---|---|
| `DATABASE_URL` | Supabase (paso 2) | Render, GitHub (`PROD_DATABASE_URL`) y tu `.env.prod` |
| `EMBEDDING_BASE_URL` | Cloudflare: `https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/ai/v1` | Render, GitHub y `.env.prod` |
| `EMBEDDING_API_KEY` | Cloudflare: token de API con permiso de Workers AI (a confirmar el nombre exacto del permiso) | Render, GitHub y `.env.prod` |
| `LLM_API_KEY` | Groq: API key | Render, GitHub y `.env.prod` |
| `LLM_FALLBACK_BASE_URL` | La misma URL de Workers AI | Render, GitHub y `.env.prod` |
| `LLM_FALLBACK_API_KEY` | El mismo token de Cloudflare | Render, GitHub y `.env.prod` |
| `EVAL_KEY` | La genera Render al crear el servicio | Render y GitHub |
| `CLIENT_KEY_SECRET` | La genera Render al crear el servicio | Solo Render |
| `CORS_ORIGINS` | La URL del front en Vercel (Etapa 8) | Render |
| `API_URL` | La URL pública del servicio en Render | GitHub |

## 2. Supabase

1. Crear un proyecto. Elegir la región más cercana a la de Render (el servicio queda en Oregon si no se cambia; a confirmar). Guardar la contraseña de la base.
2. Copiar la cadena de conexión del **Session pooler** (botón "Connect"). Tiene la forma `postgresql://postgres.<ref>:<contraseña>@aws-0-<región>.pooler.supabase.com:5432/postgres` y se le agrega `?sslmode=require`.
   - No usar la conexión directa: en el plan gratuito es solo IPv6, y Render sale por IPv4 (a confirmar).
   - No usar el *transaction pooler* (puerto 6543): psycopg prepara sentencias que se repiten, y ese modo no las admite.
3. Desactivar la Data API (Project Settings → Data API), o al menos sacar `public` de los esquemas expuestos. Las migraciones igual activan *row level security* en todas las tablas (`migrations/003_row_level_security.sql`): sin políticas, los roles `anon` y `authenticated` no ven ninguna fila. El backend se conecta como dueño de las tablas y no lo afecta.
4. La migración `001` crea la extensión `vector`. Si Supabase la rechaza por permisos, activarla en Database → Extensions y volver a migrar.

## 3. Archivo local `.env.prod`

En la raíz del repo, para correr comandos contra producción desde tu máquina. `.gitignore` ya excluye `.env.*`.

```bash
ENV=prod
DATABASE_URL=postgresql://postgres.<ref>:<contraseña>@aws-0-<región>.pooler.supabase.com:5432/postgres?sslmode=require
EMBEDDING_PROVIDER=openai_compat
EMBEDDING_PROVIDER_MODEL=@cf/baai/bge-m3
EMBEDDING_BASE_URL=https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/ai/v1
EMBEDDING_API_KEY=<token de Cloudflare>
LLM_API_KEY=<clave de Groq>
LLM_FALLBACK_BASE_URL=https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/ai/v1
LLM_FALLBACK_MODEL=@cf/openai/gpt-oss-20b
LLM_FALLBACK_API_KEY=<token de Cloudflare>
EVAL_KEY=<el valor que generó Render, paso 5>
```

Con `ENV=prod` (por ejemplo `make migrate ENV=prod`), `app/settings.py` lee `.env.prod` encima de `.env`; las variables de entorno de la terminal pisan a las dos. Lo que decide a qué base se conecta es `DATABASE_URL`. Para otros comandos en una terminal: `set -a; source .env.prod; set +a`.

## 4. Migrar e indexar producción

Desde tu máquina, con `.env.prod` escrito:

```bash
make migrate ENV=prod   # Aplicada: 001_init.sql, 002_limits_cache.sql, 003_row_level_security.sql
make migrate ENV=prod   # Sin migraciones pendientes.
make corpus             # si data/processed/ no existe
make parity             # Etapa 2: Ollama contra Workers AI, si todavía no se corrió
make index ENV=prod     # embebe con Workers AI, activa la versión e imprime fragmentos y versión
make index ENV=prod     # la segunda vez no cambia la versión
```

Anotar la versión de índice que imprime `make index`: es la que `/readyz` tiene que mostrar.

## 5. Render

1. En el panel de Render: New → Blueprint → elegir el repo. Render lee `render.yaml` y crea el servicio `a-rag-api` en el plan gratuito.
2. Completar las variables con `sync: false`: `DATABASE_URL`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `LLM_API_KEY`, `LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_API_KEY` y `CORS_ORIGINS`. `CORS_ORIGINS` es una lista JSON, por ejemplo `["https://a-rag.vercel.app"]`; hasta que exista el front, `["http://localhost:3000"]`.
3. `EVAL_KEY` la genera Render. Copiarla de Environment a los secretos de GitHub y a `.env.prod`.
4. Opcional: en Settings, que el auto-deploy espere a que pase el CI ("After CI checks pass"; a confirmar el nombre de la opción).
5. Si el build falla por la versión de Python, cambiar `PYTHON_VERSION` en `render.yaml` por una que Render ofrezca (3.12 o 3.13).

Lo que fija `render.yaml`:

- Build: `pip install uv==0.11.32 && uv sync --locked --no-dev`.
- Arranque: `uvicorn` con un solo worker, porque el límite por minuto vive en la memoria del proceso. Con `--proxy-headers`, uvicorn toma la IP del cliente de `X-Forwarded-For` solo si la conexión viene de una dirección de `FORWARDED_ALLOW_IPS`. Va con `--no-access-log`: cada consulta ya deja su línea JSON, sin la IP.
- Health check: `/healthz`, que no toca la base.
- `FORWARDED_ALLOW_IPS` con las redes privadas, porque el proxy de Render llega desde una dirección privada (a confirmar en el paso 7).

## 6. Secretos de GitHub Actions

En el repo: Settings → Secrets and variables → Actions → New repository secret.

| Secreto | Lo usa |
|---|---|
| `API_URL` | Ping y smoke semanal |
| `EVAL_KEY` | Smoke semanal (header `X-Eval-Key`) |
| `PROD_DATABASE_URL` | Corrida semanal de 25 preguntas |
| `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY` | Corrida semanal |
| `LLM_API_KEY`, `LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_API_KEY` | Corrida semanal (el respaldo es opcional) |

`.github/workflows/scheduled.yml` tiene dos trabajos:

- `ping`, cada dos días: `GET /readyz`. Despierta el backend y hace que consulte la base, así Supabase no pausa el proyecto por inactividad. Si `/readyz` no devuelve 200, el trabajo falla y GitHub avisa por correo.
- `evals`, los lunes: `python -m evals.smoke --api $API_URL` (las 10 preguntas fijas por HTTP contra el backend desplegado) y `python -m evals.run --mode full --split dev --ids-file evals/pr_subset.txt --no-judge` (las 25 preguntas fijas de `dev` que lista `evals/pr_subset.txt`, en el runner, con la base, los embeddings y el LLM de producción). Guarda `evals/results/` como artefacto. Sirve para detectar un modelo retirado o una regresión.

Si falta un secreto, el paso correspondiente se saltea con un aviso y el trabajo queda en verde. Para correrlos a mano: Actions → Scheduled → Run workflow. GitHub desactiva las tareas programadas de un repo público después de 60 días sin actividad (a confirmar); se reactivan desde la misma pantalla.

## 7. Verificación

Con `export API=https://<servicio>.onrender.com`. El primer request después de 15 minutos sin tráfico tarda en despertar el servicio (a confirmar cuánto).

```bash
curl -s $API/healthz
curl -s $API/readyz | python -m json.tool
```

`/readyz` tiene que devolver 200, `"ready": true`, la versión de índice del paso 4 y `"warnings": []` (o solo el aviso de CORS mientras no exista el front). Además de la base y el índice, `/readyz` prueba los proveedores: una incrustación corta (que tenga la dimensión configurada) y `GET /models` del LLM (que liste `LLM_MODEL`; no gasta tokens). El resultado se guarda `READYZ_PROBE_TTL_S` segundos (300), así los pings no gastan cupo. Los avisos señalan configuraciones que funcionan pero probablemente están mal: `FORWARDED_ALLOW_IPS` sin configurar, `EVAL_KEY` o `CLIENT_KEY_SECRET` vacías, `CORS_ORIGINS` solo local, el presupuesto desactivado, o un proveedor de LLM que no lista sus modelos.

El servicio no arranca (y el deploy falla) con `EMBEDDING_PROVIDER=fake` o `LLM_PROVIDER=fake` en producción, ni con un índice cuyo modelo o dimensión de embeddings no coincide con la configuración (regla 4): el log de Render dice cuál de las dos. Sin base de datos al arrancar, arranca y `/readyz` da 503 hasta que la base vuelva.

```bash
curl -s -X POST $API/ask -H 'Content-Type: application/json' \
  -d '{"question": "¿Qué es la virtud según Aristóteles?"}' | python -m json.tool
uv run python -m evals.smoke --api $API     # con EVAL_KEY cargada (.env.prod)
```

### Límite por IP: la sexta consulta en un minuto devuelve 429

```bash
for i in 1 2 3 4 5 6; do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST $API/ask \
    -H 'Content-Type: application/json' -d '{"question": "¿Qué es la virtud?"}'
done
```

Se espera `200` cinco veces y después `429`, con el header `Retry-After` y un mensaje en castellano. Las cinco preguntas iguales llaman al LLM una sola vez: las otras cuatro salen de la caché (`"cached": true`).

### Verificar la IP del cliente

Esperar un minuto entre prueba y prueba.

1. Otra red: repetir el bucle anterior y, apenas aparezca el 429, mandar la misma pregunta desde otra red (por ejemplo, el celular con datos). Tiene que dar 200. Si da 429, todos los clientes comparten un mismo límite: el proxy de Render no está en `FORWARDED_ALLOW_IPS`, o hay un segundo proxy delante. En los logs (paso 8), el campo `client` es igual para las dos redes. Solución: poner `FORWARDED_ALLOW_IPS=*` en Render y repetir. Con `*` la IP sale del primer valor de `X-Forwarded-For`, que un cliente puede falsificar; el presupuesto diario sigue poniendo el tope de gasto.
2. Header falsificado: repetir el bucle agregando `-H "X-Forwarded-For: 203.0.113.$i"`. La sexta tiene que seguir dando 429. Si no, el límite por IP se puede saltear: revisar `FORWARDED_ALLOW_IPS`.

### Clave de evals, CORS y modo solo búsqueda

```bash
# Con X-Eval-Key no hay límite por IP: siete 200 seguidos.
for i in 1 2 3 4 5 6 7; do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST $API/ask -H "X-Eval-Key: $EVAL_KEY" \
    -H 'Content-Type: application/json' -d '{"question": "¿Qué es la amistad?"}'
done

# CORS: el origen del front recibe access-control-allow-origin; cualquier otro, 400.
curl -s -o /dev/null -D - -X OPTIONS $API/ask -H 'Origin: https://a-rag.vercel.app' \
  -H 'Access-Control-Request-Method: POST' | grep -i access-control-allow-origin
```

Modo solo búsqueda con el LLM caído, en local (sin tocar producción):

```bash
LLM_BASE_URL=http://127.0.0.1:9/v1 LLM_API_KEY=x make dev
curl -s -X POST localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"question": "¿Qué es la amistad?"}'      # 200, "search_only": true, "error": "llm_unavailable"
```

Presupuesto agotado, en local: `DAILY_TOKEN_BUDGET=1 make dev`. La primera respuesta gasta tokens y la siguiente pregunta nueva vuelve con `"search_only": true` y `"error": "budget_exhausted"`.

### Aceptación de la Etapa 7

Pegar en `PROGRESS.md` la salida de:

1. `curl -s $API/readyz`: 200 con la versión de índice esperada.
2. `make eval-fast ENV=prod` y `make eval-fast` en local sobre la misma versión de índice: coinciden con una pregunta de diferencia como máximo.
3. `make eval ENV=prod SPLIT=test CONFIRM_TEST=1`: registra el sistema final del piloto.
4. El bucle de seis consultas: la sexta devuelve 429.
5. Con el LLM simulado en error, `/ask` devuelve 200 en modo solo búsqueda (local, arriba; también lo cubre `make test`).

## 8. Logs y uso

En Render → Logs, cada consulta a `/ask` y `/search` deja una línea JSON con `event: "query"`:

| Campo | Qué es |
|---|---|
| `request_id` | También va en el header `X-Request-ID` de la respuesta |
| `endpoint`, `status` | `/ask` o `/search`; 200 o 429 |
| `question_sha256`, `question_chars` | Hash de la pregunta normalizada y su largo. El texto no se guarda (`LOG_QUESTION_TEXT=true` lo agrega; solo para depurar en local) |
| `client` | HMAC (clave `CLIENT_KEY_SECRET`) de la IP y el día, recortado; para IPv6, de su red /64. Sin la clave no se puede volver a la IP |
| `eval` | Si vino con `X-Eval-Key` |
| `lang`, `filters` | Idioma detectado y filtros (obras, idioma, dudosas) |
| `retrieved`, `citations` | Fragmentos recuperados con rango, puntaje y método; fragmentos citados |
| `index_version`, `prompt_version`, `model`, `provider` | Versiones y modelo que respondió |
| `tokens_in`, `tokens_out` | Tokens del LLM en esta consulta (0 si salió de la caché) |
| `latency_ms` | Por tramo: `limits`, `cache`, `retrieve`, `llm`, `total` y `request` |
| `abstained`, `abstain_reason`, `search_only`, `error` | Abstención, modo solo búsqueda y el error completo del proveedor (en la respuesta pública va solo el código) |
| `cache` | `hit`, `stored`, `miss` o `skip` |
| `rate_limited` | En los 429: `minute` o `day` |

Uso acumulado, desde el editor SQL de Supabase:

```sql
SELECT * FROM token_usage_daily ORDER BY day DESC;          -- tokens por día: public y eval
SELECT day, count(*) AS clientes, sum(requests) AS consultas
FROM rate_limit_daily GROUP BY day ORDER BY day DESC;
SELECT count(*) AS respuestas, sum(hits) AS aciertos FROM answer_cache;
```

## 9. Parámetros

Todos viven en `app/settings.py` y se cambian con variables de entorno en Render. Para los límites, el presupuesto y la caché, 0 los desactiva.

| Variable | Valor inicial | Qué es |
|---|---|---|
| `RATE_LIMIT_PER_MINUTE` | 5 | Consultas a `/ask` por IP y minuto (en memoria) |
| `RATE_LIMIT_PER_DAY` | 30 | Consultas a `/ask` por IP y día UTC (en Postgres) |
| `SEARCH_RATE_LIMIT_PER_MINUTE`, `SEARCH_RATE_LIMIT_PER_DAY` | 20, 300 | Lo mismo para `/search`, contado aparte: cada búsqueda gasta cupo de embeddings |
| `RATE_LIMIT_IPV6_PREFIX` | 64 | Un cliente IPv6 se cuenta por su red de este largo (suele controlar una /64 entera) |
| `REQUEST_MAX_BYTES` | 16.384 | Cuerpo máximo de una consulta; más grande, 413 sin leerlo |
| `CLIENT_KEY_SECRET` | vacía (una al azar por proceso) | Clave del HMAC de los clientes; en Render la genera el Blueprint |
| `DAILY_TOKEN_BUDGET` | 120.000 | Tokens del LLM por día UTC para el público. Cada `/ask` reserva antes de llamar al LLM una estimación (contexto + prompt + salida) y después la cambia por lo que gastó, así las consultas simultáneas no lo pasan. Sin lugar para la reserva, modo solo búsqueda. Las corridas con `X-Eval-Key` no cuentan: el tope queda por debajo del cupo de Groq para dejarles lugar |
| `QUESTION_MAX_CHARS` | 500 | Largo máximo de la pregunta |
| `ANSWER_CACHE_TTL_DAYS` | 30 | Vida de una respuesta en la caché |
| `RATE_LIMIT_RETENTION_DAYS` | 7 | Días que se guardan los contadores por IP |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1,::1` (en Render, las redes privadas) | Proxies cuyo `X-Forwarded-For` se cree |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Orígenes que pueden llamar desde un navegador |
| `EVAL_KEY` | vacía | Clave del header `X-Eval-Key` |
| `ACTIVE_INDEX_VERSION` | vacía (la activa) | Fija la versión de índice servida: es la vuelta atrás |
| `LOG_LEVEL`, `LOG_QUESTION_TEXT` | `INFO`, `false` | Logs |

La caché de respuestas se invalida sola cuando cambia la versión de índice, el modelo, la versión del prompt, los filtros o los parámetros que cambian una respuesta (k, contexto, salida, umbral de abstención, esfuerzo de razonamiento). Solo guarda respuestas completas del modelo principal: no el modo solo búsqueda, ni errores, ni respuestas del respaldo.

## 10. Problemas frecuentes

- `/readyz` da 503 con "faltan las tablas …": falta `make migrate ENV=prod`.
- `/readyz` da 503 con un problema de índice: falta `make index ENV=prod`, o el modelo o la dimensión del índice no coinciden con la configuración (regla 4).
- Supabase pausó el proyecto igual: reanudarlo desde el panel y revisar que el trabajo `ping` esté corriendo.
- Todos reciben 429 enseguida: ver "Verificar la IP del cliente".
- El front recibe errores de CORS: revisar que `CORS_ORIGINS` sea una lista JSON con el origen exacto (esquema y dominio, sin barra final).
- El smoke semanal falla con `llm_unavailable` o `llm_error`: puede ser un modelo retirado en Groq. Cambiar `LLM_MODEL` en Render y en `docs/decisions.md`.
- Volver atrás: `ACTIVE_INDEX_VERSION=<versión anterior>` en Render, o "Rollback" al deploy anterior.
- Toda migración nueva que cree una tabla tiene que activar *row level security* en ella, como `003_row_level_security.sql`.
