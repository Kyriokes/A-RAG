# Plan: Aristóteles RAG

Objetivo: asistente que responde preguntas sobre la Ética a Nicómaco citando la fuente exacta (libro, capítulo, Bekker), con un set de evals que mide la calidad y se corre en cada cambio. Pensado para armarse por etapas con Claude Code.

## Reglas del proyecto

- Primero medir, después optimizar: los evals se arman antes de tocar chunking, prompts o modelos.
- Python para backend y evals. Next.js + TypeScript solo para el front.
- Mismo modelo de embeddings y mismo modelo LLM (mismos pesos) en local y en producción. Configuración por variables de entorno, sin cambios de código.
- Cada etapa termina con un criterio de aceptación verificable y un commit.
- No inventar datos: si una respuesta no está en el corpus, el sistema debe decir que no la encuentra.

## Stack

- Backend: Python + FastAPI + Pydantic.
- Base vectorial: Postgres con pgvector (Supabase) en producción, Chroma o pgvector local para desarrollo.
- Embeddings: un modelo open-weight multilingüe, el mismo en todos los entornos.
- LLM: Ollama en desarrollo, API del mismo modelo (Groq u OpenRouter) en producción, vía endpoint compatible con OpenAI.
- Evals: script propio en Python (opcional promptfoo o Ragas), reglas simples más LLM como juez.
- MCP: servidor que expone la búsqueda.
- Front: Next.js + TypeScript en Vercel. FastAPI en Render o Railway.

## Estructura sugerida del repo

- `corpus/` texto crudo y procesado (con licencia y fuente documentadas)
- `app/` FastAPI (ingesta, retrieval, generación)
- `evals/` dataset, runner, métricas, resultados históricos
- `mcp/` servidor MCP
- `web/` front Next.js
- `README.md` resultados reales y límites

## Etapa 1: Corpus

- Elegir una traducción de la Ética a Nicómaco en dominio público y confirmar la licencia de esa edición. Registrar la fuente en el README.
- Limpiar el texto y dividirlo por libro y capítulo, conservando la referencia Bekker cuando exista.
- Guardar cada unidad con metadatos: obra, libro, capítulo, Bekker, texto.
- Aceptación: un archivo estructurado con todos los libros, sin ruido de formato, y un conteo por libro.

## Etapa 2: RAG mínimo

- Chunking respetando capítulos, con tamaño y solapamiento configurables.
- Generar embeddings y guardarlos con sus metadatos.
- Endpoint `POST /ask`: recupera los k fragmentos más cercanos y genera la respuesta citando fuente.
- Prompt que obliga a responder solo con el contexto y a decir cuando no hay información.
- Aceptación: preguntas de prueba devuelven respuesta con citas correctas a mano, funcionando con el modelo local.

## Etapa 3: Evals

- Dataset de 30 preguntas sobre la Ética a Nicómaco, cada una con respuesta esperada y fuente esperada. Incluir al menos 5 preguntas sin respuesta en el corpus.
- Métricas de retrieval: si el fragmento correcto aparece en los k primeros (recall@k).
- Métricas de respuesta: cita correcta (regla), datos clave presentes (regla), fidelidad al texto (LLM juez).
- Runner con un solo comando que guarda resultados por versión en `evals/results/`.
- Aceptación: línea base registrada con puntaje por métrica.

## Etapa 4: Iterar midiendo

- Probar una variable por vez: tamaño de chunk, k, reranking, prompt, modelo de embeddings.
- Registrar cada cambio con su puntaje antes y después. Descartar lo que no mejora.
- Aceptación: mejora medible sobre la línea base y un registro de qué funcionó y qué no.

## Etapa 5: MCP, front y deploy

- Servidor MCP con herramienta de búsqueda que devuelve fragmentos con citas.
- Front con cuadro de pregunta, respuesta, citas clickeables que muestran el fragmento original, y una pestaña pública de evals con el historial.
- Deploy: base en Supabase, FastAPI en Render o Railway, front en Vercel. Correr los evals contra el modelo de la API de producción.
- Límites de uso (rate limit y tope de tokens por consulta) para controlar costos.
- Aceptación: URL pública funcionando y puntaje de producción documentado.

## Etapa 6: README y presentación

- Qué hace, arquitectura, resultados de evals, casos donde falla y por qué, costos aproximados.
- Agregar el proyecto al portfolio y al CV con una línea de resultado medible.

## Costos

- Desarrollo local sin costo por token. En producción, estimar: casos × tokens por caso × precio por millón de tokens, y usar reglas simples antes que LLM juez para ahorrar.
- Correr el set completo solo antes de merge o publicación, y un subconjunto en cambios chicos.

## Riesgos a vigilar

- Licencia de la traducción del corpus.
- Cambiar el modelo de embeddings obliga a reindexar todo.
- Diferencias entre el modelo local cuantizado y el de la API: medir siempre en producción antes de publicar.
