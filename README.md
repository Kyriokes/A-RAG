# A-RAG: Aristóteles RAG

Asistente que responde preguntas sobre las obras de Aristóteles con licencia libre, en inglés o castellano, y cita obra, traductor y ubicación Bekker. Las citas las arma el backend desde los metadatos, nunca el modelo.

Estado: en construcción. El plan completo está en [`Plan Aristóteles RAG.md`](Plan%20Aristóteles%20RAG.md) y el avance por etapa, en [`PROGRESS.md`](PROGRESS.md).

## Cómo correrlo

```bash
make setup        # dependencias (uv)
make db           # Postgres + pgvector en Docker
make migrate
make corpus       # descarga y procesa el corpus (no se guarda en el repo)
make test
```

## Fuentes y licencias

Ver [`ATTRIBUTION.md`](ATTRIBUTION.md). El código es MIT; los textos conservan su propia licencia.
