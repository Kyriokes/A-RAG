# Decisiones

Cada decisión con fecha y motivo. Lo marcado "a confirmar" se confirma en la consola del proveedor.

## 2026-11-16: corpus piloto desde Perseus, versión fijada

- Repo `PerseusDL/canonical-greekLit`, commit `dacb04909e43dc47738828d33cc3d2b0635bc54c` (HEAD al 16 de noviembre de 2026).
- Motivo: es la única fuente encontrada con Bekker legible por máquina y licencia abierta (CC BY-SA 4.0). El repo cambia seguido, así que se fija el commit.

## 2026-11-16: base local

- Postgres 16 + pgvector 0.8.1 (misma versión que la imagen de `docker-compose.yml`).
- En la sesión en la nube no hay Docker: se usó un Postgres local compilando pgvector 0.8.1 desde GitHub.

## 2026-11-16: candidatos de modelos (a confirmar en la Etapa 0)

- Embeddings: `BAAI/bge-m3` (MIT, 1024 dim). Producción: Cloudflare Workers AI `@cf/baai/bge-m3`. Local: Ollama `bge-m3`.
- LLM: `gpt-oss-20b` (Apache 2.0). Producción: Groq `openai/gpt-oss-20b`; respaldo: Workers AI `@cf/openai/gpt-oss-20b`.
- La máquina de desarrollo tiene 16 GB de RAM y una GTX 1070 (8 GB de VRAM): `gpt-oss:20b` no entra en local. Se desarrolla contra la API y se busca un modelo de hasta ~6 GB que esté gratis en Ollama y en un proveedor.

## 2026-11-16: integración de las etapas 1 a 4 (y la parte de código de la 7)

- `make ... ENV=prod` apunta a producción: `app/settings.py` lee `.env.prod` encima de `.env`, y las variables de entorno pisan a los dos. Motivo: el plan pide que los comandos con `ENV=prod` vayan a producción sin cambios de código; antes `ENV` solo marcaba el entorno.
- `/readyz` comprueba la configuración de embeddings (proveedor, URL, clave) igual que la del LLM, sin llamar al proveedor. Motivo: la aceptación de la Etapa 0 pide base, embedding y LLM en estado correcto, y una llamada real gastaría cupo en cada ping. (Reemplazada más abajo: ahora llama a los proveedores con caché.)
- `python -m evals.run --ids-file evals/pr_subset.txt`: las 25 preguntas fijas de `dev` para los PR que tocan la generación y para la corrida semanal (antes era `--limit 25`, las primeras 25 del archivo).
- `python -m evals.run --include-unreviewed` (solo `dev`): corre también los casos sin revisar para probar el código antes de la revisión. La corrida lleva el aviso en la salida, en `run.json` (`dataset.includes_unreviewed`) y en `summary.md`, y nunca reemplaza `retrieval_dev_latest.json`. Sus resultados no se suben.
- El CI comprueba `evals/results/retrieval_dev_latest.json` contra la configuración del commit y contra el de `main`, y usa la misma imagen de Postgres que `docker-compose.yml` (`pgvector/pgvector:0.8.1-pg16`).

## 2026-11-16: correcciones tras la revisión del código

- `/readyz` ahora sí llama a los proveedores (reemplaza la decisión de arriba): una incrustación corta y `GET /models` del LLM, guardadas `READYZ_PROBE_TTL_S` (300 s) para que los pings no gasten cupo. Motivo: con solo la configuración, un modelo retirado o una clave rechazada pasaban la aceptación de la Etapa 0.
- El backend no arranca con un índice de otro modelo o dimensión (regla 4) ni con modelos falsos en producción; sin base al arrancar, solo lo registra. `retrieve()` comprueba el modelo también en el camino Bekker.
- Límites nuevos, a revisar por la persona: `/search` tiene su propio límite por IP (20 por minuto, 300 por día); cuerpo máximo de 16 KB (`REQUEST_MAX_BYTES`); un cliente IPv6 se cuenta por su /64; a lo sumo 5 referencias Bekker por pregunta (`RETRIEVAL_MAX_BEKKER_REFS`), cada una con un número acotado de filas.
- El presupuesto diario se reserva antes de llamar al LLM (contexto + prompt + salida) y se ajusta después, en una sola sentencia: las consultas simultáneas ya no lo pasan.
- La clave de cliente en `rate_limit_daily` y en los logs es un HMAC con `CLIENT_KEY_SECRET` (Render la genera). El comentario de la migración 002 sigue diciendo sha256: las migraciones aplicadas no se editan (su sha256 se controla).
- La caché de respuestas distingue mayúsculas: la detección de obra y de referencias las lee, así que "la Política" y "la política" pueden recuperar pasajes distintos.
- Chunking (`CHUNKER_VERSION = 2`, cambia `index_version`: hay que volver a correr `make index`): un fragmento que termina justo antes de una marca termina en la línea anterior; el empaquetado busca la partición con el fragmento más corto lo más largo posible y, si los párrafos no alcanzan, vuelve a empaquetar por oraciones. En la EN, los fragmentos de menos de 150 palabras bajaron de 69 a 11; los que quedan están limitados por `chars_per_token = 3.0` (unas 250 palabras como máximo real), cuyo ajuste contra el tokenizador de bge-m3 queda para la persona.
- Evals: sin juez, el acierto estricto no se calcula para ningún caso; la calibración del umbral, la muestra del juez, `evals.compare` y `evals.report --write` rechazan corridas con casos sin revisar, parciales o con modelos falsos, y la tabla del README no publica métricas con juez sin una calibración aprobada del mismo modelo y prompts; las tolerancias Bekker de los evals usan columnas de `BEKKER_COLUMN_LINES` líneas (35, como el chunking) en vez de 40; los casos sin respuesta revisados contra otro `corpus_version` cuentan como sin revisar ("revisión vencida"); las preguntas revisadas sin respuesta, de premisa falsa, de comparación y de obra dudosa tienen que ser `handwritten`.
- Tests: `tests/conftest.py` aísla los tests del entorno (borra las variables de `Settings`, no lee `.env`) y los de base usan `TEST_DATABASE_URL` (o `DATABASE_URL`), nunca con `ENV=prod` ni un host remoto.

## Pendiente (requiere a la persona)

- Límites reales del plan gratuito de Groq, Workers AI, Supabase y Render, leídos en sus consolas.
- Si el cupo de Groq es por clave o por organización.
- Cuántos tokens de razonamiento gasta `gpt-oss-20b` por respuesta con esfuerzo bajo.
