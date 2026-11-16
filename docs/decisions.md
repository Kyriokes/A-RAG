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

## Pendiente (requiere a la persona)

- Límites reales del plan gratuito de Groq, Workers AI, Supabase y Render, leídos en sus consolas.
- Si el cupo de Groq es por clave o por organización.
- Cuántos tokens de razonamiento gasta `gpt-oss-20b` por respuesta con esfuerzo bajo.
