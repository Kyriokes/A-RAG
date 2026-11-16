# A-RAG: Aristóteles RAG

Asistente que responde preguntas sobre las obras de Aristóteles con licencia libre, en inglés o castellano, y cita obra, traductor y ubicación Bekker. Las citas las arma el backend desde los metadatos, nunca el modelo.

Estado: en construcción. El plan completo está en [`Plan Aristóteles RAG.md`](Plan%20Aristóteles%20RAG.md) y el avance por etapa, en [`PROGRESS.md`](PROGRESS.md).

## Cómo correrlo

```bash
make setup        # dependencias (uv)
make db           # Postgres + pgvector en Docker
make migrate      # tablas, en orden e idempotente
make corpus       # descarga y procesa el corpus (no se guarda en el repo)
make validate     # valida el corpus y escribe data/corpus_report.md
make index        # fragmentos + embeddings bge-m3 (Ollama en local)
make test         # sin red, sin corpus y sin LLM
make dev          # API en http://localhost:8000 (/search, /ask, /passages, /works, /readyz)
```

Sin modelos a mano, `EMBEDDING_PROVIDER=fake` y `LLM_PROVIDER=fake` recorren todo el sistema con resultados simulados: sirven para probar el código, no miden nada, y las corridas de evals lo marcan como aviso.

## Fuentes y licencias

Ver [`ATTRIBUTION.md`](ATTRIBUTION.md). El código es MIT; los textos conservan su propia licencia.
