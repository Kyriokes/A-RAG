# CLAUDE.md

Proyecto: RAG sobre las obras de Aristóteles con citas Bekker verificables. El plan completo está en `Plan Aristóteles RAG.md`; el avance, en `PROGRESS.md`.

## Al empezar cada sesión

Leer este archivo, `PROGRESS.md`, la etapa en curso del plan y las secciones que esa etapa nombra en "Lee antes".

## Comandos

| Comando | Qué hace |
|---|---|
| `make setup` | Instala dependencias con uv |
| `make db` | Levanta Postgres + pgvector en Docker |
| `make migrate` | Aplica `migrations/*.sql` (con `ENV=prod`, a producción) |
| `make corpus` | Descarga, verifica `sha256` y procesa el corpus a `data/processed/` |
| `make validate` | Valida el corpus procesado y escribe `data/corpus_report.md` |
| `make sample` | 20 unidades al azar para revisión humana |
| `make index` | Chunking + embeddings + carga en Postgres (atómico, idempotente) |
| `make parity` | Compara embeddings de Ollama y del proveedor de producción |
| `make dev` | Levanta la API en local |
| `make test` | Tests sin red, sin corpus y sin LLM |
| `make lint` | ruff check + ruff format --check |
| `make smoke` | 10 preguntas fijas contra `/ask` |
| `make eval-fast` | Métricas de retrieval sobre `dev` (sin LLM) |
| `make eval` | Set completo con generación y juez (`SPLIT=test CONFIRM_TEST=1` para `test`) |

## Estructura

- `app/bekker.py` y `app/models.py`: contratos compartidos. Cambiarlos es cambiar el contrato.
- `app/settings.py`: todos los parámetros y umbrales. Se pisan con variables de entorno.
- `app/corpus/`: catálogo, manifiesto, adaptadores, validación.
- `corpus/`: `works.yaml` (catálogo), `sources.yaml` (manifiesto), `expected_structure.yaml`.
- `evals/`: dataset, runner, métricas, resultados.
- `migrations/`: SQL versionado, igual en local y en Supabase.

## Reglas para el agente

- No subir secretos. No subir texto del corpus fuera de dos excepciones: fixtures de tests (un capítulo como máximo por formato) y respuestas de ejemplo del front.
- No modificar umbrales de `app/settings.py` ni marcar preguntas como revisadas (`revisado_por`) sin pedido explícito.
- No usar preguntas del dataset como ejemplos dentro de un prompt.
- No escribir de memoria tablas de referencias Bekker: se derivan de los textos o las transcribe la persona.
- Las citas las arma el backend desde los metadatos; el modelo solo devuelve números de fragmento.
- No cerrar una etapa sin pegar en `PROGRESS.md` la salida de su comando de aceptación.
- Lo que necesita a la persona (cuentas, secretos, consolas, revisión del dataset) se anota en `PROGRESS.md` y se pide; no se simula.
