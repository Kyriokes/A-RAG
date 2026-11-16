# Progreso

Registro por etapa: qué se hizo, salida del comando de aceptación y qué falta. Lo que necesita a la persona va en "Pendiente de la persona".

Estado al 2026-11-16: el código de las etapas 1 a 4 (y la parte de código de las etapas 5 y 7) está integrado y probado con modelos falsos. Ninguna etapa está cerrada: todas esperan algo de la persona (cuentas, red hacia los proveedores o revisión). La sesión en la nube no llega a ningún proveedor de modelos, así que todo lo de abajo que dice "con modelos falsos" usó `EMBEDDING_PROVIDER=fake` y `LLM_PROVIDER=fake`: prueba el código, no mide calidad.

## Pendiente de la persona

Cuentas y red:

- Crear cuentas en Supabase, Cloudflare (Workers AI), Groq y Render; cargar los secretos en Render y en GitHub Actions (lista en `docs/deploy.md`).
- Leer en cada consola los límites del plan gratuito y anotarlos en `docs/decisions.md`; confirmar si el cupo de Groq es por clave o por organización y cuántos tokens de razonamiento gasta `gpt-oss-20b` por respuesta.
- Para embeddings reales: `ollama pull bge-m3` en la máquina local y `PARITY_PROD_BASE_URL` + `PARITY_PROD_API_KEY` (Workers AI) para `make parity`; o permitir en el entorno de la sesión los hosts del proveedor.
- Para el LLM: `LLM_API_KEY` (Groq) y `LLM_FALLBACK_*` (Workers AI); para el juez, `JUDGE_*`. Confirmar en los proveedores que aceptan `response_format` con esquema JSON y `reasoning_effort`.
- Desde la máquina local, con `.env.prod`: `make migrate ENV=prod` y `make index ENV=prod`. Apagar la Data API de Supabase.
- Confirmar en Render el rango de IP del proxy y `X-Forwarded-For`, que ofrece Python 3.13.4 y que el `.venv` de uv persiste; en Supabase, el pooler y el requisito de IPv4.

Revisión:

- Corpus: confirmar `corpus/expected_structure.yaml` (`confirmed_by: null`) y revisar las 20 unidades de `data/sample_review.md` (`make sample`).
- Corpus: confirmar años de muerte de los traductores y años de publicación (Política 1944 y Poética 1939 en el TEI, contra 1932 y 1927 del plan), elegir la fuente única de autenticidad para el README y tomar la decisión abierta 2.
- Dataset: revisar las 80 preguntas de `evals/dataset/v0.jsonl` (guía en `evals/dataset/REVIEW.md`), con `revisado_por`, `fecha_revision`, `source` y `corpus_version_checked`. Las 23 sin respuesta y de premisa falsa (`v0-un-*`, `v0-fp-*`) las propuso un modelo, no la persona como pide el plan: reescribirlas o reemplazarlas (el esquema rechaza una revisada de esos tipos que no sea `handwritten`). Decidir si abstenerse es lo esperado en los casos `near_miss` y `opinion` (`v0-un-09` a `v0-un-15`).
- Juez: etiquetar a ciegas los 50 casos de calibración (Etapa 5) y aplicar a mano `ABSTAIN_MIN_SIMILARITY` desde `evals/calibration/abstention.json`.
- Revisar las decisiones que tomaron los agentes: filtro por obra nombrada en la pregunta blando y filtro explícito duro; `dedup_min_overlap_lines=3`; las corridas con `X-Eval-Key` no cuentan contra el límite por IP, el presupuesto ni la caché; texto del 429; redacción de `app/prompts/answer_v1.md`; precios "a confirmar" de `eval_*_usd_per_mtok_*` y `EVAL_MAX_USD`. Y las de la ronda de correcciones (`docs/decisions.md`, "correcciones tras la revisión del código"): límites de `/search` (20 por minuto, 300 por día), cuerpo máximo de 16 KB, IPv6 por /64, 5 referencias Bekker por pregunta, prueba de proveedores en `/readyz` cada 300 s, y si conviene ajustar `chars_per_token` al tokenizador de bge-m3 (hoy limita los fragmentos a unas 250 palabras).
- El chunking cambió (`CHUNKER_VERSION = 2`): la versión de índice de la Etapa 2 ya no corresponde; volver a correr `make index` (local y `ENV=prod`). En Render, cargar `CLIENT_KEY_SECRET` si el servicio ya existía (el Blueprint la genera al crearlo).
- Erratas del texto de Perseus para ofrecer de vuelta: "pIeurisy", "PhiIoctetes", "CalIicrates", "DieIs".

## Etapa 0: andamiaje y prueba de deploy

- Hecho: `CLAUDE.md`, `Makefile` (con `ENV=prod` lee `.env.prod`), `uv.lock`, `.env.example`, `docker-compose.yml`, CI (`make lint`, `make test` con Postgres + pgvector, y el control de `retrieval_dev_latest.json`).
- Falta (persona): la prueba de deploy en Render con Supabase, Workers AI y Groq; `docs/decisions.md` con límites confirmados.
- CI en GitHub: en verde para `4a5f14b`, en la rama de trabajo (corrida #2) y en `main` (corrida #3).
- Aceptación, parte local (2026-11-16, `DATABASE_URL` de una base local de pruebas):

```
$ make setup && make lint && make test
uv run ruff check .
All checks passed!
uv run ruff format --check .
92 files already formatted
556 passed, 1 warning in 7.52s
```

El paso de CI que compara con `main` no tiene línea base mientras `main` no tenga `evals/results/retrieval_dev_latest.json` (hasta que haya preguntas revisadas): `evals.run --check-latest` sin línea base termina en 0. La rama por defecto en GitHub sigue siendo `ccr-f194c57c-onuwkn`; se cambia a `main` en Settings → General → Default branch.

Sin base de datos alcanzable (o con `ENV=prod`, o un `DATABASE_URL` remoto, que los tests de base se niegan a usar): `506 passed, 50 skipped`. Los tests ya no dependen del entorno (`tests/conftest.py`): con `EMBEDDING_PROVIDER=fake`, `ACTIVE_INDEX_VERSION` o `RETRIEVAL_K` en la terminal dan lo mismo.

## Etapa 1: corpus piloto con pipeline genérico

- Hecho: catálogo y manifiesto de las 7 obras de Perseus con sus textos griegos (solo `eth-nic.en.rackham` indexada), adaptador `perseus_tei` genérico, rangos por capítulo desde el griego, validador, muestra, `ATTRIBUTION.md` generado, fixtures de NE VII.11 con y sin marcas.
- Falta (persona): confirmar `expected_structure.yaml` y revisar la muestra de 20 unidades.
- Aceptación (cumplida; falta la revisión de la persona):

```
$ make corpus
eth-nic.en.rackham: 116 unidades, 89503 palabras, con Bekker 100.0% (interpolated 111, line 5), avisos 1
    aviso: límite 6.1|6.2: la traducción divide distinto que el griego; se toma de sus propias marcas (1139a17 | 1139a17)
eth-nic.grc.bywater: 116 unidades, 56573 palabras, con Bekker 100.0% (interpolated 110, line 6), avisos 0
$ make validate
eth-nic.en.rackham [indexed]: campos=ok estructura=ok ruido=ok bekker=ok manifiesto=ok
eth-nic.grc.bywater [reference]: campos=ok estructura=ok ruido=ok bekker=ok manifiesto=n/a
(las 12 ediciones en espera: campos=ok ruido=ok bekker=ok)
Reporte: /home/user/A-RAG/data/corpus_report.md
OK
```

## Etapa 2: índice y retrieval

- Hecho: chunking por estructura, migraciones, `make index` atómico e idempotente con caché de embeddings, retrieval por referencia Bekker y por similitud, detección de obra, deduplicación, `make parity`.
- Falta (persona): `make parity` (Ollama y Workers AI) y el índice real con `bge-m3`.
- Aceptación con embeddings falsos (el modelo del índice es `fake/hash-embedder-v1`, no `bge-m3`):

```
$ EMBEDDING_PROVIDER=fake make index      # primera vez con CHUNKER_VERSION = 2
Fragmentos: 431
Versión de índice: 1e86a6e1f8ad6a8d
Embebidos ahora: 211 (el resto vino de la caché)
nueva versión creada y activada
$ EMBEDDING_PROVIDER=fake make index      # segunda vez
Fragmentos: 431
Versión de índice: 1e86a6e1f8ad6a8d
Embebidos ahora: 0 (el resto vino de la caché)
sin cambios
$ make parity
make parity no pudo correr: falta PARITY_PROD_BASE_URL (endpoint OpenAI-compatible del proveedor de producción, p. ej. Workers AI) y su clave en PARITY_PROD_API_KEY.
```

## Etapa 3: evals v0 y línea base de retrieval

- Hecho: runner, métricas con intervalos, validación de etiquetas contra el corpus, 80 preguntas candidatas (55 `dev`, 25 `test`), `evals/smoke.jsonl`, `evals/pr_subset.txt`, paso de CI.
- Falta (persona): revisar las 80 preguntas; después, `make eval-fast` con embeddings reales y subir `evals/results/retrieval_dev_latest.json`.
- Estado: `python -m evals.labels` termina en 0 ("80 casos … Etiquetas válidas"). `make eval-fast` termina en 1 con "No hay casos revisados en dev", como corresponde. Con `ARGS=--include-unreviewed` y embeddings falsos el camino completo corre, dos corridas dan números idénticos y la corrida queda marcada como "INCLUYE CASOS SIN REVISAR"; esos resultados se borraron.

## Etapa 4: generación y endpoints

- Hecho: cliente LLM compatible con OpenAI con respaldo, citas por construcción, tres puntos de abstención, reintento ante JSON inválido, advertencia de autenticidad (probada con un fixture), endpoints `/search`, `/ask`, `/passages/{id}`, `/works`, `/healthz`, `/readyz`.
- Falta (persona): `make smoke` con el modelo de producción.
- Con modelos falsos: uvicorn sirvió `/healthz` 200, `/readyz` 200, `/works`, `/search` ("¿Qué dice 1106b36?" → "EN II.6, 1106b27–1107a9 (aprox.)", Harris Rackham, CC BY-SA 4.0), `/ask` con citas, `/passages` con vecinos, caché en la segunda pregunta y 429 en la sexta del mismo minuto. `make smoke` con `LLM_PROVIDER=fake` da 8/10: el LLM falso nunca se abstiene, así que fallan las dos preguntas sin respuesta. No acepta la etapa.

## Etapas 5 y 7 (solo código)

- Etapa 5: juez, calibración del umbral y del juez, línea base sin retrieval, costos y caché. Probado con modelos falsos; espera el dataset revisado, el juez configurado y las etiquetas de la persona.
- Etapa 7: límites por IP, presupuesto diario, caché de respuestas, logs JSON, RLS, `render.yaml`, tarea programada y `docs/deploy.md`. Probado en local con uvicorn; espera las cuentas y el deploy.
