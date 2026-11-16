# Plan: Aristóteles RAG

Versión 2, 2026-11-16. Reemplaza al plan original, que cubría solo la Ética a Nicómaco.

## Objetivo

Asistente que responde preguntas sobre las obras de Aristóteles disponibles con licencia libre, en inglés o castellano, y cita siempre la fuente exacta: obra, traductor y ubicación. La ubicación es la numeración Bekker cuando la edición la trae y, si no, libro y capítulo de la edición citada. La cita la arma el backend con los metadatos del fragmento recuperado, nunca el modelo. El asistente responde en el idioma de la pregunta y dice que no encuentra la respuesta cuando el corpus no la tiene. Un set de evals mide la calidad y se corre en cada cambio. El corpus crece por niveles sin cambios de código. Pensado para armarse por etapas con Claude Code.

Fuera de alcance en esta versión: texto griego como corpus, comentaristas y bibliografía secundaria, conversación de varios turnos, cuentas de usuario y fine-tuning.

## Qué cambió respecto de la versión 1

- El corpus pasa de una obra a todas las que tengan traducción libre, en cuatro niveles. El piloto sigue siendo la Ética a Nicómaco.
- Los evals de retrieval se arman antes que el RAG (Etapa 2), y las respuestas esperadas se etiquetan por obra y rango Bekker, no por fragmento, para que sobrevivan a los cambios de chunking.
- Las citas se arman por código. El modelo solo señala qué fragmentos usó.
- El deploy se prueba en la Etapa 0 y el backend de producción no carga modelos: los embeddings de consulta van por API.
- Se elimina Chroma. Postgres con pgvector en todos los entornos.
- Cada etapa termina con un comando de aceptación, no con una revisión a mano.
- Se agregan licencias por texto, política de idiomas, obras dudosas, tests, CI, topes de uso y resiliencia de la demo.

## Qué está verificado y qué no

Este plan se apoya en una investigación hecha el 2026-11-16. La sesión solo pudo abrir GitHub y PyPI, y eso define qué quedó comprobado.

- Verificado leyendo la fuente primaria: los archivos de Aristóteles en Perseus y OpenGreekAndLatin, la documentación de Supabase, Cloudflare Workers AI, pgvector y el SDK de MCP publicada en GitHub, y las versiones en PyPI.
- No verificado (viene de resultados de buscador o de memoria): Wikisource, Internet Archive, Project Gutenberg, filosofia.org, los límites gratuitos de Groq y Render, y la variante gratuita de OpenRouter.

Todo dato no verificado lleva la marca "a confirmar" y se confirma en la etapa que lo usa, antes de construir sobre él.

## Reglas del proyecto

1. Primero medir, después optimizar. Los evals de retrieval existen antes de elegir chunking, prompt o modelos.
2. Las citas se arman por código desde los metadatos del fragmento. Una respuesta sin citas válidas se reemplaza por el mensaje de abstención.
3. No inventar datos: si la respuesta no está en el corpus, el sistema dice que no la encuentra.
4. Mismo modelo de embeddings en todos los entornos: mismo ID, revisión, prefijos y normalización. El índice guarda esos datos y la API no arranca si su configuración no coincide.
5. El LLM se fija por ID y se configura por variables de entorno. Los números oficiales son siempre los medidos contra producción; Ollama es para iterar en local.
6. Misma base y mismo SQL en local y en producción. Configuración por variables de entorno, sin cambios de código.
7. Ningún texto entra al corpus sin una fila en el manifiesto con traductor, licencia y evidencia. Gratis para leer no es lo mismo que libre.
8. Python para backend y evals. Next.js + TypeScript solo para el front.
9. `make test` corre sin red y sin LLM. CI en cada push.
10. Cada etapa cabe en una o dos sesiones. Termina con un comando de aceptación que pasa, su salida pegada en `PROGRESS.md`, un commit y un tag `etapa-N`. Si no cabe, se parte antes de empezar.

## Corpus por niveles

| Nivel | Obras | Idioma y traducción | Fuente | Licencia | Bekker | Estado |
|---|---|---|---|---|---|---|
| 0 (piloto) | Ética a Nicómaco | Inglés, Rackham (Loeb, 1926) | Perseus, `PerseusDL/canonical-greekLit`, `tlg010` | CC BY-SA 4.0 | Página, columna y línea | Verificado |
| 1 | Ética Eudemia, Política, Metafísica, Retórica, Poética, Económicos | Inglés: Rackham, Tredennick, Freese, Fyfe, Armstrong (Loeb, 1926–1935) | Perseus, mismo repo | CC BY-SA 4.0 | Sí, con cuatro codificaciones distintas | Verificado |
| 2 | Metafísica, Moral a Nicómaco, Gran Moral, Moral a Eudemo, Política, Tratado del alma, Opúsculos, Lógica (Órganon), Poética | Castellano: Azcárate (1873–1875); Poética de Goya y Muniain (1798) | Wikisource en español y escaneos de Wikimedia Commons con OCR propio | Dominio público | No trae; se deriva por capítulo | A confirmar |
| 3 | Órganon, Física, Del cielo, Generación y corrupción, Meteorológicos, Del alma, Parva naturalia, biología, Constitución de los atenienses, Problemas y obras menores | Inglés: traducción de Oxford (1908–1931) y traducciones del siglo XIX | Wikisource en inglés, Internet Archive, Project Gutenberg, OpenGreekAndLatin | Varía por traductor | Marginal o ausente | A confirmar, obra por obra |

Las etapas 1 a 5 usan solo el Nivel 0. Desde la Etapa 5 el proyecto ya es presentable; los niveles 1 a 3 suman sin bloquear.

Notas sobre lo verificado en Perseus:

- La ruta es `data/tlg0086/<obra>/tlg0086.<obra>.perseus-eng2.xml`. El repo publica versiones seguido (la última, el 13-11-2026) y cambió nombres de archivo, así que se fija un tag o un SHA y se guarda con cada fragmento.
- Las marcas Bekker cambian de codificación según el archivo: `milestone unit="page"` en la mayoría, `unit="section"` en la Ética Eudemia y `div subtype="bekker_page"` en la Política. Un parser escrito solo contra la Ética a Nicómaco pierde referencias en las demás.
- El inglés trae marcas de línea cada unas 20 líneas. El griego del mismo repo (`perseus-grc2`) las trae cada 5 y sirve como tabla para calcular el rango Bekker de cada capítulo. El griego no se indexa.
- La Política no trae capítulos. La Ética Eudemia solo trae los libros I, II, III, VII y VIII, porque los libros IV a VI coinciden con Ética a Nicómaco V a VII. Económicos solo trae los libros I y II.
- Las notas del traductor vienen dentro del texto y hay que separarlas.

Notas sobre el castellano (todo a confirmar):

- La Metafísica de Azcárate tendría transcripción completa en Wikisource y sus capítulos coincidirían con la división habitual, así que es la primera obra castellana a sumar.
- La Moral a Nicómaco sigue otra división de capítulos (el libro I tendría 11 en vez de 13) y la Política reordena los libros. La clave para alinear castellano e inglés es el rango Bekker, no el número de capítulo.
- filosofia.org tiene el mejor HTML, pero se declara propietario de su edición digital: se usa solo para cotejar el OCR, o con permiso escrito. La Biblioteca Virtual Miguel de Cervantes no se usa.
- Azcárate tradujo desde versiones francesas y con terminología del siglo XIX. El README lo dice.
- No habría traducción castellana libre de la Física, la biología, la Retórica ni los Meteorológicos. Esas obras quedan solo en inglés.

### Regla de admisión

Un texto se indexa y se publica solo si cumple una de estas condiciones:

- La fuente lo publica con licencia abierta explícita.
- Es dominio público en Estados Unidos (publicado en 1930 o antes, contando desde 2026) y además el traductor murió hace más de 70 años.

Lo que solo cumple la parte de Estados Unidos queda en una lista de espera, fuera del índice público. Por ejemplo, las traducciones de W. D. Ross (muerto en 1971) quedan afuera hasta 2042. El Del alma de J. A. Smith (1931) entra en dominio público en Estados Unidos el 01-01-2027 y recién ahí puede sumarse.

Tres textos de Perseus pasan por la primera condición aunque su traductor murió hace menos de 70 años o no se sabe cuándo: la Metafísica (Tredennick), la Poética (Fyfe, muerto en 1965) y Económicos (Armstrong). La licencia de Perseus cubre su edición digital y no despeja derechos de terceros sobre la traducción. Qué hacer con ellos es una de las decisiones abiertas del final.

Quedan excluidos: MIT Internet Classics Archive como fuente, el rastreo de perseus.tufts.edu, la traducción revisada de Oxford (Barnes, 1984), los fragmentos de Ross (1952), las ediciones de Gredos, Alianza, Tecnos, CEPC, Aguilar y UNAM, las traducciones de Gallach Palés mientras no se documente su año de muerte, y cualquier archivo cuyo traductor no esté identificado. Esto vale también para los PDF de esas editoriales subidos a Internet Archive o a sitios de libros gratis.

Obligaciones por usar Perseus: citar traductor y edición, acreditar a Perseus Digital Library con enlace, nombrar el repo de origen y la licencia, y decir qué se cambió. Las correcciones al texto se ofrecen de vuelta a Perseus. Si se publica el corpus procesado, va bajo CC BY-SA 4.0. El código lleva su propia licencia (MIT).

### Ediciones e idiomas

- En el índice entra como máximo una edición por obra e idioma. Si hay varias, se prefiere la que trae Bekker, después la de mejor texto digital y después la de licencia más clara.
- El retrieval deduplica por ubicación: el mismo pasaje en inglés y en castellano cuenta como un solo resultado. Al LLM se le pasa la versión en el idioma de la pregunta si existe, y el front ofrece ver la otra.
- La respuesta va en el idioma de la pregunta. Las citas textuales van en el idioma de la edición, sin traducir y con el traductor indicado. Si el pasaje solo existe en inglés, la respuesta lo parafrasea sin comillas.

### Obras dudosas y espurias

El catálogo marca cada obra como auténtica, dudosa o espuria según una única fuente declarada en el README. Se indexan todas. Toda cita de una obra dudosa o espuria lleva una etiqueta visible, y un parámetro permite excluirlas. Son de este grupo, entre otras: Económicos, Magna Moralia, Problemas, Del mundo, Retórica a Alejandro y Sobre las virtudes y los vicios.

## Contratos de datos

Se fijan en la Etapa 1 con el esquema completo, aunque el piloto use una sola obra. Agregar una obra de un formato ya soportado es agregar una fila.

- `corpus/works.yaml`, una fila por obra: `work_id`, abreviatura, títulos y alias en castellano, inglés y latín, rango Bekker (nulo si la obra está fuera de Bekker), autenticidad y estructura (libro y capítulo, solo capítulo, libro y problema).
- `corpus/sources.yaml`, una fila por edición: `edition_id`, `work_id`, idioma, traductor y año de muerte, año de publicación, URL, versión fijada, `sha256`, licencia, evidencia de la licencia, texto de atribución, adaptador, nivel, si trae Bekker y si se indexa.
- Fragmento: `chunk_id` estable, `work_id`, `edition_id`, idioma, divisiones (lista ordenada, sin asumir que hay libro y capítulo), `bekker_start` y `bekker_end` como enteros (nulos si no hay), `bekker_precision` (línea, página, capítulo o ninguna), `text_type` (Aristóteles, nota, sumario, introducción), texto original y texto con cabecera para embeber.
- Ubicación canónica: `work_id` más rango Bekker. Sin Bekker, `work_id` más libro y capítulo, nombrando el sistema de numeración. La usan las citas, la deduplicación y las respuestas esperadas de los evals.
- Versiones: `corpus_version` es el hash del manifiesto y de los archivos procesados. `index_version` es el hash de la versión del corpus, la configuración de chunking y el modelo de embeddings. El prompt y el dataset tienen su propia versión. Todas quedan en cada resultado de evals.
- El texto del corpus no se sube al repo. `make corpus` lo descarga, verifica el `sha256` y lo procesa.

## Stack

Los modelos y proveedores son candidatos al 2026-11-16. La Etapa 0 los confirma y deja la decisión fechada en `docs/decisions.md`.

- Backend: Python, FastAPI y Pydantic. Dependencias con `uv` y lockfile. `ruff` y `pytest`.
- Base: Postgres con pgvector en todos los entornos. Docker Compose en local (imagen `pgvector/pgvector`), Supabase en producción, migraciones SQL versionadas. Búsqueda exacta al principio: con unos 10.000 fragmentos alcanza y da recall completo. Búsqueda híbrida con `tsvector` por idioma, `unaccent` y fusión por rangos (RRF).
- Embeddings: `bge-m3` (licencia MIT, 1024 dimensiones). En local con Ollama. En producción el corpus se embebe offline y la consulta se embebe por API: Cloudflare Workers AI (10.000 neurons gratis por día) o DeepInfra como respaldo de pago.
- LLM: `gpt-oss-20b` (Apache 2.0). En local, Ollama `gpt-oss:20b`, que pide unos 16 GB de RAM. En producción, Groq `openai/gpt-oss-20b`, con Workers AI `@cf/openai/gpt-oss-20b` como respaldo ante un 429. Con menos de 16 GB se desarrolla contra la API. Groq retiró los modelos Llama de su plan gratuito en agosto de 2026, así que el ID va en variables de entorno y no en el código.
- Juez de evals: un modelo de otra familia que el generador, más capaz y fijado por ID. Si no hay uno gratis, `gpt-oss-120b`, y el README declara que es de la misma familia.
- Hosting del backend: Render gratuito (512 MB, se duerme a los 15 minutos; a confirmar). Alternativa: Google Cloud Run, que arranca más rápido pero pide cuenta de facturación.
- Front: Next.js + TypeScript en Vercel.
- Evals: runner propio en Python. Ragas, promptfoo, DeepEval e Inspect AI quedan como referencia; ninguno trae puntaje por rango Bekker, matriz de abstención ni bootstrap pareado.
- MCP: SDK oficial `mcp` 2.x con versión fijada. La clase es `MCPServer`; el import `mcp.server.fastmcp` de casi todos los tutoriales ya no existe.

Dos límites condicionan el diseño:

- Supabase gratuito da 500 MB y pausa los proyectos con poca actividad en 7 días. Hace falta un ping programado.
- El plan gratuito de Groq daría 8.000 tokens por minuto y 200.000 por día (a confirmar en la consola). Con un presupuesto de 3.000 tokens por consulta son unas 2 respuestas por minuto y unas 65 por día.

## Estructura del repo

- `corpus/` con `works.yaml`, `sources.yaml` y `expected_structure.yaml`
- `data/` ignorado por git: `raw/`, `processed/` y `corpus_report.md`
- `app/` FastAPI: adaptadores de ingesta, chunking, embeddings, retrieval, generación, API
- `migrations/` SQL versionado
- `evals/` con `dataset/`, runner, métricas, `results/`, `EXPERIMENTS.md` y `FAILURES.md`
- `tests/` unitarios con fixtures chicos, uno por formato de fuente
- `mcp_server/` (no `mcp/`, que taparía el import del SDK)
- `web/` front Next.js
- `docs/decisions.md` decisiones con fecha y motivo
- `CLAUDE.md`, `PROGRESS.md`, `Makefile`, `.env.example`, `docker-compose.yml`, `LICENSE`, `ATTRIBUTION.md`, `README.md`

## Etapa 0: Andamiaje y prueba de deploy

- `CLAUDE.md` con los comandos, la estructura y las reglas para el agente: no subir texto del corpus ni secretos, no modificar `evals/dataset/` ni umbrales sin pedido explícito, no cerrar una etapa sin pegar la salida del comando de aceptación.
- `Makefile` con `setup`, `corpus`, `index`, `dev`, `test`, `lint`, `smoke`, `eval-fast` y `eval`. Lockfiles, `.env.example` sin valores y Docker Compose con Postgres y pgvector.
- CI en GitHub Actions: `make lint` y `make test` en cada push.
- Prueba de deploy: un FastAPI mínimo en el hosting elegido que consulta Supabase, pide un embedding a la API y una respuesta corta al LLM. Sirve para leer los límites reales en las consolas de cada proveedor.
- `docs/decisions.md` con modelos, proveedores y límites confirmados, con fecha.
- Aceptación: `make setup && make lint && make test` termina en 0, el CI está en verde, y `curl $API/readyz` devuelve 200 con base, embedding y LLM en estado correcto.

## Etapa 1: Corpus piloto con pipeline genérico

- `works.yaml` y `sources.yaml` con el esquema completo y la fila de la Ética a Nicómaco.
- `make corpus` descarga a `data/raw/` con la versión fijada, verifica el `sha256` y genera las unidades con el adaptador `perseus_tei`. El adaptador lee los tipos de división del TEI en vez de asumir nombres, lleva la posición Bekker a medida que avanza y deja las notas del traductor en un campo aparte.
- Rango Bekker de cada capítulo calculado desde el griego del mismo repo.
- `ATTRIBUTION.md` generado desde el manifiesto.
- Tests del adaptador y del formato de cita con fixtures chicos: uno con Bekker y uno sin Bekker.
- Aceptación: `make corpus && python -m app.corpus.validate` termina en 0. El validador comprueba que toda unidad tiene obra, edición, idioma, ubicación y texto; que la cantidad de libros y capítulos coincide con `expected_structure.yaml`; que no quedan etiquetas, entidades ni llamadas de nota sueltas; y que los rangos Bekker son monótonos, caen dentro del rango de la obra y cubren al menos el 99 % de sus páginas. Escribe `data/corpus_report.md` con unidades, palabras y porcentaje con Bekker. Aparte, 20 unidades al azar se cotejan a mano contra la fuente.

## Etapa 2: Evals v0 y línea base de retrieval

Sin API, sin LLM y sin front.

- Chunking guiado por la estructura: fragmentos de 150 a 300 palabras cortados por párrafo, que nunca cruzan capítulo, libro ni obra. El texto que se embebe lleva una cabecera fija, por ejemplo "Aristóteles, Ética a Nicómaco, libro II, cap. 6, 1106b36–1107a8". La ingesta cuenta tokens y falla si un fragmento supera el límite, porque Ollama trunca en silencio y Workers AI devuelve error.
- `make index` embebe y guarda con metadatos. Es idempotente: construye la versión nueva completa, valida el conteo y recién entonces la activa.
- Dataset v0: 80 preguntas sobre la Ética a Nicómaco, la mayoría en castellano, revisadas a mano (ver "Diseño de los evals").
- Retrieval como función importable, la misma que después usan la API, los evals y el MCP. Las preguntas con una referencia Bekker explícita se resuelven por rango, sin embeddings.
- Test de paridad de embeddings entre Ollama y la API de producción: 50 fragmentos y 20 consultas, coseno medio de 0,99 o más y el mismo top 5.
- Aceptación: `make eval-fast` termina en 0 y guarda en `evals/results/` el recall de evidencia en k y en 20, y el MRR, con n e intervalo del 95 %, sobre `dev`. Dos corridas seguidas dan números idénticos. El test de etiquetas pasa: toda respuesta esperada resuelve a texto no vacío del corpus actual.

## Etapa 3: Generación y línea base completa

- Citas por construcción: los fragmentos se pasan numerados, el modelo devuelve JSON con `answerable`, `answer` y la lista de números citados, y el backend convierte cada número en obra, traductor y ubicación. Una cita a un número inexistente se descarta y se registra.
- Abstención en dos capas: si el mejor puntaje de retrieval queda bajo un umbral calibrado en `dev`, el sistema se abstiene sin llamar al LLM; si el modelo responde sin citas válidas, también.
- Contrato de API: `POST /search` (solo retrieval), `POST /ask`, `GET /passages/{chunk_id}` (el fragmento y sus vecinos), `GET /works`, `GET /healthz` y `GET /readyz`. `/ask` acepta filtros opcionales por obra e idioma y devuelve respuesta, idioma, si se abstuvo, citas con texto, traductor, licencia y autenticidad, y metadatos de versión, modelo, tokens y latencia.
- Detector de obra por diccionario de títulos y alias: si la pregunta nombra una obra, se filtra por ella.
- Métricas de respuesta, juez calibrado y línea base sin retrieval (ver "Diseño de los evals").
- Aceptación: `make smoke` pasa (10 preguntas fijas: esquema válido en todas, toda cita apunta a un fragmento recuperado, las 2 sin respuesta terminan en abstención). `make eval` guarda la línea base con las cuatro métricas principales sobre `dev` y una corrida registrada sobre `test`. El acuerdo del juez con las etiquetas humanas es de 85 % o más.

## Etapa 4: Iterar midiendo

Máximo dos sesiones y cinco experimentos.

- Dos lazos separados. El de retrieval (tamaño de fragmento, cabecera, búsqueda híbrida, k, reranking) se mide sin LLM. El de generación (prompt, cantidad de contexto) se mide con el retrieval congelado y contra el modelo de producción.
- Al comparar tamaños de fragmento se mantiene fijo el presupuesto de tokens de contexto, no k.
- Solo se prueban opciones que se puedan desplegar gratis. El reranking no entra en un backend de 512 MB: se mide en local y solo se adopta si mejora el recall en 5 o el MRR en 5 puntos o más. Cambiar el modelo de embeddings (candidato: `Qwen3-Embedding-0.6B`) queda fuera salvo que el recall de la línea base sea bajo.
- Cada experimento es una entrada en `evals/EXPERIMENTS.md`: hipótesis, cambio, diferencia con su intervalo, decisión. Un cambio queda si el intervalo de la diferencia excluye el cero, o si es neutro en calidad y baja costo o latencia. Lo demás se anota como "sin diferencia detectable".
- `evals/FAILURES.md`: las fallas restantes clasificadas por causa, con conteo y un ejemplo real de cada una.
- El split `test` no se toca en esta etapa.
- Aceptación: `EXPERIMENTS.md` tiene entre 3 y 5 experimentos con su intervalo y `FAILURES.md` clasifica todas las fallas de la última corrida sobre `dev`.

## Etapa 5: Deploy y front mínimo con el piloto

Primera versión publicable.

- Backend en el hosting validado en la Etapa 0, base en Supabase y front de una página en Vercel: cuadro de pregunta, 6 preguntas de ejemplo, respuesta, y citas que abren el fragmento original con obra, traductor y licencia. Página de fuentes con la atribución.
- Las 6 preguntas de ejemplo tienen la respuesta precalculada y servida como JSON estático: funcionan aunque el backend duerma o el LLM esté caído.
- Estados visibles: "despertando el servidor", límite alcanzado y modo solo búsqueda, que muestra los fragmentos con sus citas si el LLM falla o devuelve 429.
- Límites: pregunta de 500 caracteres como máximo, salida de 700 tokens como máximo, k y contexto fijados en el servidor. Límite por IP y presupuesto global diario guardado en Postgres. Caché exacta por pregunta normalizada, versión de índice y modelo. Claves solo en el backend, CORS restringido al front.
- Un log JSON por consulta con fragmentos recuperados, versiones, modelo, tokens, latencia por tramo y si se abstuvo. Por defecto se guarda un hash de la pregunta, no el texto.
- Tarea programada en GitHub Actions: ping a la base cada 2 o 3 días y evals semanales contra producción, para detectar un modelo retirado o una regresión.
- Pestaña de evals generada en el build desde `evals/results/`, sin backend propio.
- Aceptación: `curl $API/readyz` devuelve 200 con la versión de índice esperada. `make eval ENV=prod` guarda el resultado de producción; la diferencia con local es de 5 puntos o menos por métrica, o está explicada. La consulta siguiente al límite por minuto devuelve 429. Un test de Playwright hace una pregunta, ve una respuesta con al menos una cita, abre el fragmento y ve la atribución.

## Etapa 6: Corpus Nivel 1

Solo datos: las otras seis obras de Perseus.

- Seis filas nuevas en el manifiesto. El adaptador se generaliza para las cuatro codificaciones de Bekker; no se agregan casos por obra. La Política necesita una tabla de capítulos a rangos Bekker hecha aparte.
- Unas 50 preguntas nuevas: algunas por obra, comparaciones entre obras, y casos sobre la obra dudosa (Económicos) y sobre los libros que faltan de la Ética Eudemia. Se re-auditan las preguntas sin respuesta, porque varias pasan a tener respuesta con el corpus más grande.
- Aceptación: `make corpus && make index && make eval` termina en 0. El diff no toca `app/` fuera del adaptador. Los resultados muestran las preguntas de la Ética a Nicómaco antes y después de agrandar el corpus, y las métricas por obra. Si el recall de la Ética a Nicómaco cae más de 5 puntos, se registra un experimento de mitigación (filtro por obra, híbrida o reranking).

## Etapa 7: Corpus Niveles 2 y 3

Adaptadores nuevos. Cada obra es una tarea separada y se puede cortar en cualquier punto.

- Antes de ingerir cada fuente se abre su página primaria y se confirma licencia, traductor y estado de la transcripción. Lo que no pasa queda en la lista de excluidas con el motivo.
- Nivel 2, castellano: primero la Metafísica de Azcárate. Después Moral a Nicómaco y Política con tabla de concordancia a Bekker, y el resto de los tomos con OCR propio. Se normaliza la ortografía de época para embeber y se conserva el original para mostrar. Los sumarios y notas del traductor se marcan con `text_type` para que no se citen como texto de Aristóteles.
- Nivel 3, inglés: las obras que falten, según la regla de admisión. Para traducciones sin Bekker, la tabla de capítulos se arma desde el griego de First1KGreek cuando la edición lo trae.
- Con dos idiomas en el índice, el retrieval recupera por idioma y fusiona, para que el sesgo hacia el idioma de la pregunta no deje afuera las obras que solo están en inglés.
- Unas 70 preguntas nuevas, entre ellas 30 pares de la misma pregunta en castellano y en inglés. El dataset llega a unas 200.
- Aceptación: la misma de la Etapa 6, más el recall por par de idiomas (castellano a castellano, castellano a inglés, inglés a inglés). Si la brecha entre pares supera 10 puntos, se registra el experimento de traducir la consulta antes de buscar.

## Etapa 8: MCP (opcional)

- Adaptador fino sobre la misma función de retrieval, sin lógica propia y sin llamar al LLM. Herramientas de solo lectura: `search_passages`, `get_passage` y `list_works`.
- Primero por stdio. Si se publica por HTTP, va montado en el mismo FastAPI con transporte Streamable HTTP sin estado, con la lista de hosts permitidos configurada y los mismos límites de uso que `/ask`.
- Aceptación: un test levanta el servidor, lista las herramientas y llama a `search_passages`. El README trae el comando para agregarlo a Claude Code.

## README y cierre

El README se actualiza al cerrar cada etapa y sigue un orden fijo, pensado para cinco minutos de lectura:

1. Qué es en una línea, enlace a la demo y un GIF corto de respaldo.
2. Tabla de resultados sobre `test`: línea base sin retrieval, línea base del RAG y sistema final, con n, intervalo, fecha, modelo y tamaño del corpus.
3. Un diagrama de arquitectura.
4. Qué se probó y qué no funcionó.
5. Dónde falla, con tres ejemplos reales.
6. Costos medidos por consulta y por corrida de evals.
7. Corpus: obras, traductores, licencias, qué quedó afuera y por qué.
8. Límites conocidos: traducciones antiguas, obras dudosas, precisión de Bekker, pasajes solo en inglés, tamaño del dataset.
9. Cómo correrlo.

Aceptación: en un clon limpio, `make setup && make corpus && make index && make eval-fast` reproduce la cifra de retrieval publicada. El proyecto se agrega al portfolio y al CV con una línea de resultado medible sobre `test`, nunca sobre `dev`.

## Diseño de los evals

Dataset:

- Cada caso tiene pregunta, idioma, tipo, comportamiento esperado (responder, abstenerse, corregir la premisa, responder con advertencia de atribución), evidencia esperada, entre 2 y 4 datos clave, split y origen (escrita a mano o generada y revisada).
- La evidencia esperada es obra y rango Bekker, nunca un ID de fragmento. Un fragmento recuperado acierta si su rango se solapa con el esperado, sin importar edición, idioma ni tamaño. Así una sola etiqueta sirve para el inglés y el castellano.
- Tipos: búsqueda de un pasaje, definición, síntesis de varios pasajes, comparación entre obras, referencia Bekker directa, sin respuesta, premisa falsa, obra dudosa y al menos 10 casos adversariales.
- Las preguntas sin respuesta se escriben a mano y cubren cuatro casos: fuera de dominio, obra perdida o no incluida, tema presente pero dato ausente, y pregunta de opinión.
- Tamaño: 80 preguntas en la Etapa 2 (55 en `dev`, 25 en `test`) y unas 200 con el corpus completo. El split se hace por pasaje, no por pregunta, para que ninguna evidencia quede de los dos lados.
- Las preguntas generadas por un modelo se revisan todas a mano contra el pasaje. Las de comparación, sin respuesta, premisa falsa y obra dudosa se escriben a mano. El modelo que propone preguntas no es el generador ni el juez.

Métricas principales, siempre con n e intervalo del 95 %:

1. Recall de evidencia en k.
2. Tasa de acierto estricta de punta a punta: comportamiento esperado cumplido, datos clave presentes, al menos una evidencia esperada citada, ninguna cita fuera del contexto y respuesta fiel.
3. Fidelidad: proporción de respuestas en las que todo lo afirmado está respaldado por los fragmentos recuperados.
4. Abstención, como par: respuestas dadas cuando no había que responder y rechazos cuando sí había respuesta.

En segunda línea van latencia p50 y p95, tokens y costo por 1.000 consultas. Se agrega una línea base sin retrieval, porque el modelo ya conoce a Aristóteles por su entrenamiento: lo que el proyecto demuestra son citas verificables y menos afirmaciones sin respaldo, no que "sabe de Aristóteles".

Reglas y juez:

- Con reglas se miden las etiquetas, el retrieval, la abstención, la validez de las citas, las referencias directas, el idioma de la respuesta, la latencia y el costo.
- El juez se usa solo para fidelidad, datos clave conceptuales y corrección de premisas. Da un veredicto binario por criterio, en JSON, con temperatura 0 y respuestas cacheadas.
- Antes de publicar una métrica con juez, se comparan sus veredictos con al menos 50 casos etiquetados a mano y a ciegas, 10 de ellos con respuesta y contexto en idiomas distintos. Se exige un acuerdo de 85 % o más, y la cifra se publica junto a la métrica.

Estadística:

- Con unas 100 preguntas solo se detectan cambios de entre 8 y 11 puntos. Un salto de 3 puntos es ruido, y el README lo dice.
- Toda comparación entre dos configuraciones reporta la diferencia pareada con su intervalo por bootstrap y cuántas preguntas se arreglaron y cuántas se rompieron.
- La línea base de generación se corre tres veces para medir el ruido entre corridas. Una diferencia menor que ese ruido se ignora.
- `test` se corre solo en los hitos (línea base, antes de publicar) y se anota cuántas veces se corrió.

Qué corre y cuándo:

- En cada push, sin LLM ni secretos: lint, tests, validez de etiquetas y métricas de retrieval sobre `dev`. Falla si el recall baja respecto del resultado guardado en `main`.
- En PR que tocan `app/` o los prompts: 25 preguntas con generación. Solo bloquean las reglas, nunca un umbral de puntaje.
- A mano y antes de publicar: el set completo con juez. El resultado se sube al repo.

Cada corrida guarda en `evals/results/` un manifiesto (commit, versiones de corpus, índice, prompt y dataset, modelos con ID exacto, proveedor que respondió, parámetros), las salidas por pregunta y un resumen.

## Costos y topes

- Objetivo: 0 USD por mes. Ningún proveedor con tarjeta cargada sin tope de gasto configurado.
- Presupuesto por consulta: unos 3.000 tokens (2.000 de contexto, 300 de prompt y hasta 700 de salida). De ahí salen k y el tamaño de fragmento.
- Si se agota lo gratuito, la salida es barata: al precio de Groq para `gpt-oss-20b`, un dólar paga unas 2.600 respuestas (a confirmar).
- Antes de correr, el runner de evals imprime la estimación (casos × tokens × precio por millón) y aborta si supera `EVAL_MAX_USD` o el cupo diario. Cachea respuestas del generador y del juez, respeta los límites con esperas y se reanuda si se corta.
- Los evals de retrieval no usan LLM y se corren sin restricción.
- Claves separadas para la demo y para los evals, para que el tráfico público no deje a los evals sin cupo.

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Licencias mixtas y derechos que cambian según el país | Regla de admisión por manifiesto, evidencia por texto, lista de espera y `ATTRIBUTION.md` generado |
| Fuentes de los niveles 2 y 3 sin verificar | Confirmar la página primaria antes de ingerir; cada obra se puede dejar afuera |
| Ediciones sin Bekker o con otra división de capítulos | Cita de respaldo por libro y capítulo, tablas derivadas del griego, `bekker_precision` visible |
| El modelo inventa o confunde referencias | Citas por construcción y validación en el servidor |
| Obras parecidas compiten en el retrieval (Ética Eudemia, Magna Moralia) | Re-medir las preguntas del nivel anterior, métricas por obra, filtro por obra |
| Sesgo del modelo de embeddings hacia el idioma de la pregunta | Recuperar por idioma y fusionar; matriz de recall por par de idiomas |
| Vocabulario antiguo de las traducciones | Preguntas con términos modernos en el dataset; búsqueda híbrida |
| El proveedor retira o cambia el modelo gratuito | ID en variables de entorno, respaldo en un segundo proveedor, evals semanales contra producción |
| El backend se duerme y la base se pausa | Ejemplos precalculados, estado "despertando", ping programado |
| Cupo gratuito agotado por abuso | Límite por IP, presupuesto global diario, caché, modo solo búsqueda |
| Dataset chico o contaminado | Intervalos en cada cifra, split por pasaje, `test` reservado, preguntas escritas a mano |
| Juez poco confiable | Otra familia de modelo, calibración contra etiquetas humanas, acuerdo publicado |
| Cambiar el modelo de embeddings obliga a reindexar | Índice versionado y cambio de versión con vuelta atrás |
| Diferencia entre el modelo local cuantizado y el de la API | Los números oficiales son los de producción; test de paridad de embeddings |
| El repo de Perseus cambia nombres o texto | Versión fijada y `sha256` en el manifiesto |
| Descarga de fuentes bloqueada en la sesión en la nube | Permitir los hosts en el entorno o correr `make corpus` en local |

## Decisiones abiertas

Cada una tiene un valor por defecto para que el plan se pueda ejecutar sin esperar.

1. Regla de derechos. Por defecto: dominio público en Estados Unidos y traductor muerto hace más de 70 años, o licencia abierta de la fuente. Conviene revisarla según el país desde el que se publica. Nada de esto es asesoramiento legal.
2. Los tres textos de Perseus con derechos dudosos fuera de Estados Unidos (Metafísica de Tredennick, Poética de Fyfe, Económicos de Armstrong). Por defecto: se incluyen con la licencia que declara Perseus y una nota en el README. La alternativa estricta es reemplazar la Poética por la de Bywater o Butcher, y la Metafísica por la castellana de Azcárate.
3. Máquina de desarrollo. Con 16 GB de RAM o más se usa Ollama; con menos, se desarrolla contra la API.
4. Repo público. Por defecto sí: GitHub Actions es gratis para repos públicos.
