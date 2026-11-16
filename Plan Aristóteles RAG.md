# Plan: Aristóteles RAG

Versión 2 (2026-11-16). Reemplaza al plan original, que cubría solo la Ética a Nicómaco.

## Objetivo

Asistente que responde preguntas sobre las obras de Aristóteles disponibles con licencia libre, en inglés o castellano, y cita siempre la fuente exacta: obra, traductor y ubicación. La ubicación es la numeración Bekker cuando la edición la trae. Bekker es la referencia estándar de página, columna y línea (por ejemplo, 1106b36) y no depende de la traducción. Si la edición no la trae, la ubicación es el libro y capítulo de esa edición.

La cita la arma el backend con los metadatos del fragmento recuperado, nunca el modelo. El asistente responde en el idioma de la pregunta y dice que no encuentra la respuesta cuando el corpus no la tiene. Un set de evals mide la calidad: la parte de retrieval corre en cada cambio y el set completo, antes de publicar. El corpus crece por niveles: una obra en un formato ya soportado se agrega con una fila en el manifiesto, y un formato nuevo pide además un adaptador. Pensado para armarse por etapas con Claude Code.

Fuera de alcance en esta versión: texto griego como corpus, comentaristas y bibliografía secundaria, conversación de varios turnos, cuentas de usuario y fine-tuning.

## Qué cambió respecto de la versión 1

- El corpus pasa de una obra a todas las que tengan traducción libre, en cuatro niveles. El piloto sigue siendo la Ética a Nicómaco.
- Los evals de retrieval se arman antes que la generación, y la evidencia esperada se etiqueta por obra y rango Bekker, no por fragmento, para que sobreviva a los cambios de chunking.
- Las citas se arman por código. El modelo solo señala qué fragmentos usó.
- El deploy se prueba en la Etapa 0 y el backend de producción no carga modelos: los embeddings van por un servicio externo.
- Se elimina Chroma: Postgres con pgvector en todos los entornos. Salen del stack Railway y OpenRouter.
- La regla de "mismos pesos" del LLM se cambia por un ID fijado y cifras medidas con el modelo de producción.
- Cada etapa termina con un criterio comprobable, casi siempre un comando. Las revisiones a mano que quedan están nombradas y tienen responsable.
- La etapa de iteración ya no exige una mejora: exige experimentos medidos, y tiene tope.
- El MCP deja de ser parte del deploy y pasa a una etapa opcional.
- Se agregan licencias por texto, política de idiomas, obras dudosas, tests, CI, topes de uso y resiliencia de la demo.

## Qué está verificado y qué no

Este plan se apoya en una investigación hecha el 16 de noviembre de 2026. La sesión solo pudo abrir GitHub y PyPI, y eso define qué quedó comprobado.

- Verificado leyendo la fuente primaria: los archivos de Aristóteles en Perseus y OpenGreekAndLatin, la documentación de Supabase, Cloudflare Workers AI, pgvector y el SDK de MCP publicada en GitHub, y las versiones en PyPI.
- No verificado (viene de resultados de buscador o de memoria): Wikisource, Wikimedia Commons, Internet Archive, Project Gutenberg, filosofia.org, los límites gratuitos de Groq y Render, y Google Cloud Run.

Los datos no verificados que condicionan una decisión llevan la marca "a confirmar" y se confirman en la etapa que los usa, antes de construir sobre ellos. También son a confirmar, aunque no lleven la marca en cada mención, los años de muerte de los traductores, los plazos de dominio público que dependen de ellos y el retiro de modelos en Groq, visto solo en un catálogo de terceros. El diseño de los evals no tuvo una segunda verificación.

## Reglas del proyecto

1. Primero medir, después optimizar. Los evals de retrieval existen antes de cambiar chunking, prompt o modelos.
2. Las citas se arman por código desde los metadatos del fragmento. Una respuesta sin citas válidas se reemplaza por el mensaje de abstención.
3. No inventar datos: si la respuesta no está en el corpus, el sistema dice que no la encuentra.
4. Mismo modelo de embeddings en todos los entornos. El índice guarda el nombre del modelo, el proveedor, la dimensión y la normalización, y el backend no arranca si el modelo o la dimensión no coinciden con su configuración.
5. El LLM se fija por ID y se configura por variables de entorno. Las cifras que se publican son siempre las medidas con el modelo de producción, no con Ollama.
6. Misma base y mismo SQL en local y en producción. Configuración por variables de entorno, sin cambios de código.
7. Ningún texto entra al corpus sin una fila en el manifiesto con traductor, licencia y evidencia. Gratis para leer no es lo mismo que libre.
8. Python para backend y evals. Next.js + TypeScript solo para el front.
9. `make test` corre sin red, sin corpus y sin LLM. CI en cada push.
10. Cada etapa cabe en una o dos sesiones. Termina con su aceptación cumplida, la salida pegada en `PROGRESS.md`, un commit y un tag `etapa-N`. Si no cabe, se parte antes de empezar.
11. Cada sesión arranca leyendo `CLAUDE.md`, `PROGRESS.md`, la etapa en curso y las secciones que esa etapa nombra en "Lee antes".

## Quién hace qué

El agente no puede crear cuentas, abrir consolas web ni ver secretos. Cada etapa empieza comprobando sus requisitos. Si falta uno, el agente lo anota en `PROGRESS.md` y lo pide; no lo simula ni lo saltea.

La persona:

- Antes de la Etapa 0, crea las cuentas en Supabase, Cloudflare (Workers AI), Groq y el hosting del backend, carga los secretos en el hosting y en GitHub Actions, y lee en cada consola los límites del plan gratuito.
- Permite en el entorno de la sesión los hosts que cada etapa necesita, o corre `make corpus` en su máquina. La sesión en la nube de la investigación solo llegaba a GitHub y PyPI.
- Revisa cada pregunta del dataset contra su pasaje. Son unos 5 minutos por pregunta: unas 7 horas para las 80 del piloto, 4 para las 50 del Nivel 1 y 6 para las 70 de los niveles 2 y 3.
- Etiqueta a ciegas los 50 casos que calibran al juez (unas 3 horas) y coteja la muestra de 20 unidades de cada edición nueva.
- Confirma la licencia de cada fuente antes de ingerirla y toma las decisiones abiertas del final.

El agente propone, implementa, corre los comandos y deja todo listo para revisar. No marca preguntas como revisadas, no usa preguntas del dataset como ejemplos dentro de un prompt y no escribe de memoria tablas de referencias.

## Corpus por niveles

| Nivel | Obras | Idioma y traducción | Fuente | Licencia | Bekker | Estado |
|---|---|---|---|---|---|---|
| 0 (piloto) | Ética a Nicómaco | Inglés, Rackham (Loeb, 1926) | Perseus, repo `PerseusDL/canonical-greekLit`, obra `tlg010` | CC BY-SA 4.0 | Página y columna exactas; línea aproximada | Verificado |
| 1 | Ética Eudemia, Política, Metafísica, Retórica, Poética, Económicos | Inglés: Rackham, Tredennick, Freese, Fyfe, Armstrong (Loeb, 1926–1935) | Perseus, mismo repo | CC BY-SA 4.0 | Igual, con cuatro codificaciones distintas | Verificado |
| 2 | Metafísica, Moral a Nicómaco (Ética a Nicómaco), Gran Moral (Magna Moralia), Moral a Eudemo (Ética Eudemia), Política, Tratado del alma (Del alma), Opúsculos (Parva naturalia), Lógica (Órganon) y Poética | Castellano: Azcárate (1873–1875); Poética de Goya y Muniain (1798) | Wikisource en español; escaneos de Wikimedia Commons e Internet Archive con OCR propio | Traducción en dominio público; la transcripción de Wikisource es CC BY-SA 4.0 | No trae; se deriva por capítulo | A confirmar |
| 3 | Órganon, Física, Del cielo, Generación y corrupción, Meteorológicos, Del alma, Parva naturalia, biología, Constitución de los atenienses, Problemas y obras menores | Inglés: traducción de Oxford (1908–1931) y traducciones del siglo XIX | Wikisource en inglés, Internet Archive, Project Gutenberg, OpenGreekAndLatin | Varía por traductor | Marginal o ausente | A confirmar, obra por obra |

Las etapas 1 a 8 usan solo el Nivel 0. Desde la Etapa 8 el proyecto ya es presentable; los niveles 1 a 3 suman sin bloquear.

Notas sobre lo verificado en Perseus:

- Los archivos son XML en formato TEI. Dentro del repo de Perseus, la ruta es `data/tlg0086/<obra>/tlg0086.<obra>.perseus-eng2.xml`. El repo publica versiones con frecuencia (la última, el 13 de noviembre de 2026) y cambió nombres de archivo, así que se fija un tag o un SHA y se guarda con cada fragmento.
- Las marcas Bekker cambian de codificación según el archivo. La página va como `milestone unit="page"` en la mayoría, como `milestone unit="section"` en la Ética Eudemia y como `div subtype="bekker_page"` en la Política, que además repite la página en un `milestone` interno. Las marcas de línea usan el atributo `resp` en unos archivos y `ed` en otros. Metafísica y Poética no empiezan en la línea 1. Un parser escrito solo contra la Ética a Nicómaco pierde referencias en las demás.
- El inglés trae marcas de línea aproximadamente cada 20 líneas. El griego del mismo repo (`perseus-grc2`) las trae cada 5 y sirve como tabla para calcular el rango Bekker de cada capítulo. Esto se comprobó en la Ética a Nicómaco y la Política; en las demás se confirma al ingerirlas. El griego no se indexa.
- La Política no trae capítulos, ni en inglés ni en griego. Se cita por libro y rango Bekker.
- La Ética Eudemia solo trae los libros I, II, III, VII y VIII, porque los libros IV a VI coinciden con Ética a Nicómaco V a VII. Económicos solo trae los libros I y II.
- Las notas del traductor vienen dentro del texto y hay que separarlas.

Notas sobre el castellano (todo a confirmar):

- La Metafísica de Azcárate tiene transcripción completa en Wikisource y sus capítulos coinciden con la división habitual, así que es la primera obra castellana que se suma.
- Para la Moral a Nicómaco y la Política no se encontró transcripción en Wikisource: salen de los escaneos, con OCR propio. La Moral a Nicómaco sigue otra división de capítulos (el libro I tiene 11 en vez de 13) y la Política reordena los libros. La clave para alinear castellano e inglés es el rango Bekker, no el número de capítulo.
- filosofia.org tiene el mejor HTML, pero se declara propietario de su edición digital: se usa solo para cotejar el OCR, o con permiso escrito. La Biblioteca Virtual Miguel de Cervantes no se usa.
- Azcárate tradujo desde versiones francesas y con terminología del siglo XIX. El README lo dice.
- No hay traducción castellana libre de la Física, Del cielo, Generación y corrupción, los Meteorológicos, la biología, los Problemas, la Retórica, la Constitución de los atenienses ni Económicos. Esas obras quedan solo en inglés.

### Regla de admisión

Un texto se indexa y se publica solo si cumple una de estas condiciones:

- La fuente lo publica con licencia abierta explícita.
- Está en dominio público en Estados Unidos y además el traductor murió hace más de 70 años. En 2026, el dominio público en Estados Unidos alcanza a lo publicado hasta 1930; el límite avanza un año cada 1 de enero.

Lo que solo cumple la parte de Estados Unidos queda en espera, fuera del índice público. Por ejemplo, las traducciones de W. D. Ross (muerto en 1971) quedan afuera hasta 2042. La traducción de Del alma de J. A. Smith (publicada en 1931; Smith murió en 1939) entra en dominio público en Estados Unidos el 1 de enero de 2027 y recién ahí puede sumarse.

De los siete textos de Perseus, solo la Ética a Nicómaco y la Retórica (las dos de 1926) cumplen también la segunda condición. Los otros cinco entran solo por la licencia que declara Perseus, que cubre su edición digital y no despeja derechos de terceros sobre la traducción:

- Política (1932) y Ética Eudemia (1935), de Rackham (muerto en 1944). Son posteriores a 1930: su dominio público en Estados Unidos depende de que el copyright no se haya renovado, y eso consta solo en un registro no oficial.
- Poética (Fyfe, 1927). Es libre en Estados Unidos, pero Fyfe murió en 1965 y fuera sigue protegida hasta 2036.
- Metafísica (Tredennick, 1933–1935) y Económicos (Armstrong, 1935). No hay constancia de su estado en Estados Unidos, y Tredennick habría muerto en 1981.

Qué hacer con esos textos es una de las decisiones abiertas del final.

Quedan excluidos:

- MIT Internet Classics Archive como fuente, y el rastreo de perseus.tufts.edu.
- La traducción revisada de Oxford (Barnes, 1984) y los fragmentos de Ross (1952).
- Las ediciones de Gredos, Alianza, Tecnos, CEPC, Aguilar y UNAM, también cuando circulan como PDF en Internet Archive o en sitios de libros gratis.
- Las traducciones de Gallach Palés (hacia 1931–1934), mientras no se documente su año de muerte y no venza el plazo en Estados Unidos.
- Cualquier archivo cuyo traductor no esté identificado.

Obligaciones por usar Perseus: citar traductor y edición, acreditar a Perseus Digital Library con enlace, nombrar el repo de origen y la licencia, y decir qué se cambió. Las correcciones al texto se ofrecen de vuelta a Perseus. Lo que se tome de Wikisource lleva las mismas obligaciones de atribución. Si se publica el corpus procesado, va bajo CC BY-SA 4.0. El código lleva su propia licencia (MIT).

### Ediciones e idiomas

- En el índice entra como máximo una edición por obra e idioma. Si hay varias, se prefiere la que trae Bekker, después la de mejor texto digital y después la de licencia más clara.
- El retrieval deduplica por ubicación: el mismo pasaje en inglés y en castellano cuenta como un solo resultado. Al LLM se le pasa la versión en el idioma de la pregunta si existe, y el front ofrece ver la otra.
- La respuesta va en el idioma de la pregunta. Si el pasaje está en otro idioma, la respuesta lo parafrasea sin comillas, y el texto original se muestra en la cita, sin traducir y con su traductor.

### Obras dudosas y espurias

El catálogo marca cada obra como auténtica, dudosa o espuria según una única fuente declarada en el README. Se indexan todas. Toda cita de una obra dudosa o espuria lleva una etiqueta visible, y un parámetro permite excluirlas. Son de este grupo, entre otras: Económicos, Magna Moralia, Problemas, Del mundo, Retórica a Alejandro y Sobre las virtudes y los vicios.

## Contratos de datos

Se fijan en la Etapa 1 con el esquema completo, aunque el piloto use una sola obra.

- Catálogo, `corpus/works.yaml`, una fila por obra: `work_id`, abreviatura, títulos y alias en castellano, inglés y latín, rango Bekker (nulo si la obra está fuera de Bekker), autenticidad y estructura (libro y capítulo, solo capítulo, libro y página Bekker, libro y problema).
- Manifiesto, `corpus/sources.yaml`, una fila por edición: `edition_id`, `work_id`, idioma, traductor y año de muerte, año de publicación, URL, versión fijada, `sha256`, licencia, evidencia de la licencia, texto de atribución, adaptador, nivel, si trae Bekker y estado (indexada, en espera o excluida, con el motivo).
- Unidad: lo que produce `make corpus`, antes del chunking. Es la división más chica que marca la edición, normalmente el capítulo. Va en `data/processed/<edition_id>.jsonl` con `unit_id` estable, `work_id`, `edition_id`, idioma, divisiones (lista ordenada, sin asumir que hay libro y capítulo), rango Bekker, `text_type` y texto. Las notas al pie del traductor van en un campo aparte. Los sumarios e introducciones son unidades con otro `text_type` y no se indexan.
- Fragmento: lo que produce el chunking dentro de una unidad. Hereda sus campos y agrega `chunk_id` estable, su propio rango Bekker, `bekker_precision` y el texto con cabecera que se embebe.
- Referencia Bekker como entero: página × 200, más 100 si la columna es b, más la línea. Así, 1106b36 es 221.336, y un rango es un par de enteros.
- Rango de un fragmento: la línea de inicio y la de fin se interpolan por cantidad de palabras entre las dos marcas vecinas. `bekker_precision` dice si el rango es interpolado, de página, de capítulo o si no hay. El front indica cuándo las líneas son aproximadas.
- Ubicación canónica: `work_id` más rango Bekker. Sin Bekker, `work_id` más libro y capítulo, indicando a qué edición pertenece esa división. La usan las citas, la deduplicación y la evidencia esperada de los evals.
- Versiones: `corpus_version` es el hash del manifiesto y de los archivos procesados. `index_version` es el hash de la versión del corpus, la configuración de chunking y el modelo de embeddings. El prompt y el dataset tienen su propia versión. Todas quedan en cada resultado de evals.
- Los embeddings se guardan en caché por modelo y hash del texto, así un cambio de chunking solo embebe lo nuevo.
- El texto del corpus no se sube al repo: `make corpus` lo descarga, verifica el `sha256` y lo procesa. Hay dos excepciones, listadas en `ATTRIBUTION.md`: los fixtures de tests (un capítulo como máximo por formato) y las respuestas de ejemplo del front. Los resultados de evals guardan identificadores y rangos, no texto del corpus.

Parámetros iniciales. Viven en `app/settings.py`, se pueden cambiar por variables de entorno y cada corrida de evals los copia a su resultado.

| Parámetro | Valor inicial | Qué es |
|---|---|---|
| `RETRIEVAL_K` | 5 | Fragmentos que se le pasan al LLM. Es el k de "recall de evidencia en k" |
| `RETRIEVAL_CANDIDATES` | 20 | Fragmentos recuperados antes de cortar en k; sirve de diagnóstico |
| `CONTEXT_MAX_TOKENS` | 2.000 | Tope de contexto. Si los k fragmentos no entran, se descartan desde el último |
| `OUTPUT_MAX_TOKENS` | 700 | Tope de salida del LLM, con el razonamiento incluido |
| `CHUNK_MAX_TOKENS` | 512 | Tope por fragmento con su cabecera. La ingesta falla si se supera |
| `ABSTAIN_MIN_SIMILARITY` | Sin umbral hasta la Etapa 5 | Similitud mínima del mejor fragmento para llamar al LLM |
| `RATE_LIMIT_PER_MINUTE` y `RATE_LIMIT_PER_DAY` | 5 y 30 | Consultas a `/ask` por IP |
| `DAILY_TOKEN_BUDGET` | 120.000 | Tokens de LLM por día para la demo. Al agotarse, modo solo búsqueda |

## Stack

Los modelos y proveedores son candidatos al 16 de noviembre de 2026. La Etapa 0 los confirma y deja la decisión fechada en `docs/decisions.md`.

- Backend: Python, FastAPI y Pydantic. Dependencias con `uv` y lockfile. `ruff` y `pytest`.
- Base: Postgres con pgvector en todos los entornos. Docker Compose en local (imagen `pgvector/pgvector`), Supabase en producción, migraciones SQL versionadas. La línea base usa búsqueda vectorial exacta, sin índice aproximado: con unos 10.000 fragmentos es rápida y no pierde vecinos. La búsqueda híbrida (texto completo por idioma combinado con la vectorial) es un experimento de la Etapa 6.
- Embeddings: `bge-m3` (licencia MIT, 1024 dimensiones). El backend no carga el modelo. El índice de producción y las consultas se embeben con el mismo servicio, Cloudflare Workers AI, cuyo cupo gratuito alcanza para unos 9 millones de tokens de embedding por día. DeepInfra queda como respaldo de pago. Ollama embebe solo el índice local.
- LLM: `gpt-oss-20b` (Apache 2.0). En local, Ollama `gpt-oss:20b`, que pide unos 16 GB de RAM; con menos, se desarrolla contra el proveedor. En producción, Groq `openai/gpt-oss-20b`, con Workers AI `@cf/openai/gpt-oss-20b` como respaldo ante un 429. Los tres se llaman con un solo cliente por su endpoint compatible con OpenAI: cambiar de proveedor es cambiar la URL, el ID y la clave. Es un modelo de razonamiento y se usa con esfuerzo bajo.
- Juez de evals: un modelo de una familia distinta de la del generador, más capaz y fijado por ID. Si no hay uno gratis, `gpt-oss-120b`, y el README declara que es de la misma familia.
- Hosting del backend: Render gratuito (512 MB de RAM, se duerme tras 15 minutos sin tráfico; a confirmar). Alternativa: Google Cloud Run, que arranca más rápido pero pide cuenta de facturación (a confirmar).
- Front: Next.js + TypeScript en Vercel.
- Evals: runner propio en Python. Ragas, promptfoo, DeepEval e Inspect AI quedan como referencia: según su documentación, ninguno trae de fábrica puntaje por rango Bekker ni matriz de abstención.
- MCP: SDK oficial `mcp` 2.x con versión fijada. La clase es `MCPServer`; el import `mcp.server.fastmcp` de casi todos los tutoriales ya no existe.

Tres límites condicionan el diseño:

- Supabase gratuito da 500 MB de base y puede pausar los proyectos que tuvieron poca actividad durante 7 días. Hace falta un ping programado.
- El plan gratuito de Groq da 8.000 tokens por minuto y 200.000 por día, por organización (a confirmar en la consola). Con unos 3.000 tokens por consulta son unas 2 respuestas por minuto y unas 65 por día.
- Groq retiró dos modelos Llama de su plan gratuito en agosto de 2026 y rota los modelos en preview en cuestión de semanas (a confirmar). Por eso el ID va en variables de entorno y hay un proveedor de respaldo.

## Estructura del repo

- `corpus/` con `works.yaml`, `sources.yaml` y `expected_structure.yaml`
- `data/` ignorado por git: `raw/`, `processed/` y `corpus_report.md`
- `app/` FastAPI: adaptadores de ingesta, chunking, embeddings, retrieval, generación, endpoints y `settings.py`
- `migrations/` SQL versionado
- `evals/` con `dataset/`, `calibration/`, runner, métricas, `results/`, `EXPERIMENTS.md` y `FAILURES.md`
- `tests/` unitarios con fixtures chicos, uno por formato de fuente
- `mcp_server/` (no `mcp/`, que taparía el import del SDK)
- `web/` front Next.js
- `docs/decisions.md` decisiones con fecha y motivo
- `CLAUDE.md`, `PROGRESS.md`, `Makefile`, `.env.example`, `docker-compose.yml`, `LICENSE`, `ATTRIBUTION.md`, `README.md`

## Diseño de los evals

Dataset:

- Se parte en dos conjuntos. `dev` se mira libremente para iterar y calibrar umbrales. `test` se reserva para las cifras que se publican. La partición se hace por pasaje, no por pregunta, para que ninguna evidencia quede de los dos lados.
- Cada caso tiene `id`, pregunta, idioma, tipo, comportamiento esperado (responder, abstenerse, corregir la premisa, responder con advertencia de atribución), evidencia esperada, entre 2 y 4 datos clave, conjunto, origen (escrita a mano o generada y revisada), `revisado_por` y `fecha_revision`. El runner ignora los casos sin `revisado_por` e informa cuántos ignoró.
- La evidencia esperada es obra y rango Bekker, nunca un ID de fragmento. Es una lista de grupos: cada grupo reúne ubicaciones alternativas (pasajes paralelos, como el mismo tema en la Ética a Nicómaco y en la Ética Eudemia) y basta recuperar una. Las preguntas de síntesis y de comparación llevan un grupo por pasaje necesario.
- Un fragmento recuperado acierta un grupo si su rango se solapa con alguna de sus ubicaciones, sin importar edición, idioma ni tamaño. El solapamiento mínimo se fija una vez en la configuración. Así una sola etiqueta sirve para el inglés y el castellano.
- Tipos: búsqueda de un pasaje, definición, síntesis de varios pasajes, comparación entre obras, referencia Bekker directa, sin respuesta, premisa falsa y obra dudosa. Además, al menos 10 casos de inyección de prompt.
- Las preguntas sin respuesta cubren cuatro casos: fuera de dominio, obra perdida o no incluida, tema presente pero dato ausente, y pregunta de opinión. Cada vez que cambia `corpus_version` se vuelven a revisar, porque varias pasan a tener respuesta con un corpus más grande.
- Tamaño: 80 preguntas en el piloto (55 en `dev`, 25 en `test`), de las cuales al menos 15 son sin respuesta y 8 de premisa falsa. Con el corpus completo, unas 200 (unas 110 en `dev` y 90 en `test`).
- Las de comparación, sin respuesta, premisa falsa y obra dudosa las escribe la persona. Las demás puede proponerlas un modelo que no sea el generador ni el juez, y la persona acepta, corrige o rechaza cada una contra el pasaje.

Métricas principales, siempre con n e intervalo del 95 %:

1. Recall de evidencia en k: proporción de grupos de evidencia esperada que aparecen entre los k fragmentos que ve el LLM, promediada sobre las preguntas. El mismo recall sobre los 20 primeros es solo diagnóstico: alto en 20 y bajo en k indica que falta reordenar; bajo en 20 indica un problema de chunking o de embeddings.
2. Tasa de acierto estricta de punta a punta: comportamiento esperado cumplido, datos clave presentes, al menos una evidencia esperada citada, ninguna cita fuera del contexto y respuesta fiel.
3. Fidelidad: proporción de respuestas en las que todo lo afirmado está respaldado por los fragmentos recuperados.
4. Abstención, como par: respuestas dadas cuando no había que responder y rechazos cuando sí había respuesta.

En segunda línea van el MRR sobre los 10 primeros, latencia p50 y p95, tokens y costo por 1.000 consultas. Se agrega una línea base sin retrieval, porque el modelo ya conoce a Aristóteles por su entrenamiento. Lo que el proyecto tiene que demostrar es que agrega citas verificables y reduce las afirmaciones sin respaldo, no que el modelo "sabe de Aristóteles".

Reglas y juez:

- Con reglas, es decir comprobaciones deterministas en código y sin LLM, se miden la validez de las etiquetas, el retrieval, la abstención, la validez de las citas, las referencias directas, el idioma de la respuesta, la latencia y el costo.
- El juez se usa solo para fidelidad, datos clave conceptuales y corrección de premisas. Da un veredicto binario por criterio, en JSON, con temperatura 0 y respuestas guardadas en caché.
- Antes de publicar una métrica con juez, sus veredictos se comparan con 50 casos etiquetados a ciegas por la persona. La muestra incluye al menos 15 respuestas fallidas y 10 con respuesta y contexto en idiomas distintos. Se exige un acuerdo de 85 % o más, y la cifra se publica junto a la métrica. Si cambia el modelo o el prompt del juez, la calibración se repite.

Estadística:

- Con unas 100 preguntas, el cambio más chico que se puede detectar ronda entre 8 y 11 puntos. Con las 55 de `dev` del piloto ronda los 10 puntos, y con las 25 de `test` una cifra de 80 % tiene un intervalo de 61 a 91 %. Un salto de 3 puntos no se distingue del ruido, y el README lo aclara.
- Los umbrales de 5 y 10 puntos que aparecen en las etapas son disparadores para investigar, no diferencias demostradas.
- Toda comparación entre dos configuraciones informa la diferencia pareada, es decir medida sobre las mismas preguntas, con su intervalo por bootstrap, y cuántas preguntas se arreglaron y cuántas se rompieron.
- La línea base de generación se corre tres veces para medir el ruido entre corridas. Una diferencia menor que ese ruido se ignora.
- `make eval` corre `dev`. `make eval SPLIT=test CONFIRM_TEST=1` corre `test` y suma uno a un contador guardado en el repo. `test` se corre solo en los hitos: línea base, sistema final del piloto y cierre de cada nivel del corpus.

Qué corre y cuándo:

- En cada push, sin modelos ni secretos: `make lint` y `make test`, que incluye el formato del dataset.
- Antes de cada commit que toque retrieval, chunking o el dataset, se corre `make eval-fast` y se sube su resumen a `evals/results/retrieval_dev_latest.json`. El CI no recalcula embeddings: comprueba que ese archivo corresponde a la configuración del commit y que el recall no bajó respecto del de `main`.
- En los PR que tocan la generación o los prompts: 25 preguntas fijas con generación. Solo bloquean las reglas, nunca un umbral de puntaje.
- A mano y antes de publicar: el set completo con juez. El resumen se sube al repo.

Cada corrida guarda en `evals/results/` un `run.json` (commit, versiones de corpus, índice, prompt y dataset, modelos con ID exacto, proveedor que respondió, parámetros), las salidas por pregunta y un resumen.

## Etapa 0: Andamiaje y prueba de deploy

Lee antes: Reglas del proyecto, Quién hace qué, Stack, Estructura del repo.

- `CLAUDE.md` con los comandos, la estructura y las reglas para el agente: no subir secretos ni texto del corpus fuera de las dos excepciones, no modificar umbrales ni marcar preguntas como revisadas sin pedido explícito, y no cerrar una etapa sin pegar la salida de su aceptación.
- `Makefile` con `setup`, `db`, `migrate`, `corpus`, `index`, `parity`, `dev`, `test`, `lint`, `smoke`, `eval-fast`, `eval`, `examples` y `e2e`. Los que todavía no existen terminan con error y el mensaje "se implementa en la Etapa N". Con `ENV=prod` apuntan a producción.
- Lockfiles, `.env.example` sin valores y Docker Compose con Postgres y pgvector.
- CI en GitHub Actions: `make lint` y `make test` en cada push.
- Prueba de deploy: un FastAPI mínimo en el hosting elegido que consulta Supabase, pide un embedding al proveedor y una respuesta corta al LLM.
- `docs/decisions.md` con modelos, proveedores y límites confirmados, con fecha. Incluye cuántos tokens de razonamiento gasta el LLM por respuesta y si el cupo de Groq es por clave o por organización.
- Aceptación: `make setup && make lint && make test` termina en 0, el CI está en verde, y `curl $API/readyz` devuelve 200 con base, embedding y LLM en estado correcto.

## Etapa 1: Corpus piloto con pipeline genérico

Lee antes: Corpus por niveles (Nivel 0, notas sobre Perseus, regla de admisión, obligaciones), Contratos de datos.

- `works.yaml` y `sources.yaml` con el esquema completo y las filas de la Ética a Nicómaco: la traducción inglesa y el texto griego, que se usa solo para las referencias.
- `corpus/expected_structure.yaml`, escrito antes de correr el adaptador y a partir de una fuente que no sea su salida. Para la Ética a Nicómaco son 10 libros con 13, 9, 12, 9, 11, 13, 14, 14, 12 y 9 capítulos (a confirmar). La persona lo confirma.
- `make corpus` descarga a `data/raw/` con la versión fijada, verifica el `sha256` y genera las unidades con el adaptador `perseus_tei`. El adaptador lee los tipos de división del TEI en vez de asumir nombres, recuerda la última marca Bekker leída mientras recorre el texto y separa las notas del traductor.
- Rango Bekker de cada capítulo calculado desde el griego.
- `ATTRIBUTION.md` generado desde el manifiesto.
- Tests del adaptador y del formato de cita con dos fixtures chicos: uno con Bekker y el mismo sin sus marcas.
- Aceptación: `make corpus && python -m app.corpus.validate` termina en 0. El validador comprueba cinco cosas. Toda unidad tiene obra, edición, idioma, ubicación y texto. La cantidad de libros y capítulos coincide con `expected_structure.yaml`. No quedan etiquetas, entidades ni llamadas de nota sueltas. Los rangos Bekker son monótonos, caen dentro del rango de la obra y cubren al menos el 99 % de sus páginas. Toda edición por indexar tiene traductor, licencia y evidencia, y cumple la regla de admisión. El validador escribe `data/corpus_report.md` con unidades, palabras y porcentaje con Bekker.
- Revisión de la persona: `python -m app.corpus.sample --n 20` escribe 20 unidades al azar con su ubicación y el enlace a la fuente. La etapa cierra con las 20 ubicaciones correctas y ninguna nota mezclada en el texto. Cada falla se corrige en el adaptador y se agrega como test.

## Etapa 2: Índice y retrieval

Lee antes: Contratos de datos (fragmento, parámetros), Stack (base y embeddings).

- Chunking guiado por la estructura: fragmentos de 150 a 300 palabras cortados por párrafo, que nunca cruzan capítulo, libro ni obra. El tamaño y el solapamiento son parámetros (valor inicial: sin solapamiento) y entran en `index_version`.
- El texto que se embebe lleva una cabecera fija, por ejemplo "Aristóteles, Ética a Nicómaco, libro II, cap. 6, 1106b36–1107a8". La ingesta cuenta tokens y falla si un fragmento supera `CHUNK_MAX_TOKENS`, porque Ollama trunca en silencio y Workers AI devuelve error.
- `make index` embebe y guarda con metadatos. Correrlo dos veces no duplica nada. El cambio de versión es atómico: construye la versión nueva completa, valida el conteo y solo entonces la activa.
- Retrieval como función importable, la misma que después usan el backend, los evals y el MCP. Las preguntas con una referencia Bekker explícita se resuelven por rango, sin embeddings. Si la pregunta nombra una obra, un diccionario de títulos y alias la detecta y se filtra por ella.
- `make parity` embebe los mismos 50 fragmentos y 20 consultas con Ollama y con el proveedor de producción. Pasa si el coseno medio entre los dos vectores de cada texto es de 0,99 o más y cada consulta devuelve los mismos 5 primeros fragmentos.
- Aceptación: `make index` termina en 0 e imprime la cantidad de fragmentos y la versión de índice, y correrlo dos veces no cambia la versión. `make test` pasa. `make parity` pasa; si no pasa, también el índice local se embebe con el proveedor de producción y la decisión queda en `docs/decisions.md`.

## Etapa 3: Evals v0 y línea base de retrieval

Lee antes: Diseño de los evals (dataset, métrica 1, estadística, qué corre y cuándo), Quién hace qué.

Sin backend HTTP, sin LLM generador y sin front.

- Dataset v0: 80 preguntas sobre la Ética a Nicómaco, la mayoría en castellano. El agente propone candidatas y deja todo listo; la persona las revisa.
- Runner con `make eval-fast`: valida las etiquetas contra el corpus y calcula las métricas de retrieval sobre `dev`.
- Paso de CI que comprueba el resumen subido.
- Aceptación: `make eval-fast` termina en 0 y guarda el recall de evidencia en k y en 20 y el MRR, con n e intervalo del 95 %. Dos corridas seguidas dan números idénticos. Toda evidencia esperada resuelve a texto no vacío del corpus actual. El runner informa 80 casos revisados y ninguno ignorado.

## Etapa 4: Generación y endpoints

Lee antes: Ediciones e idiomas, Obras dudosas y espurias, Contratos de datos (parámetros), Costos y topes.

- Citas por construcción: los fragmentos se pasan numerados, el modelo devuelve JSON con `answerable`, `answer` y la lista de números citados, y el backend convierte cada número en obra, traductor y ubicación. Una cita a un número inexistente se descarta y se registra.
- La llamada usa temperatura 0, esfuerzo de razonamiento bajo y salida con esquema JSON donde el proveedor la soporte. Si el JSON no es válido se reintenta una vez; si vuelve a fallar, se devuelven solo los fragmentos y se registra como error, no como abstención.
- Abstención en tres puntos: la similitud del mejor fragmento queda bajo `ABSTAIN_MIN_SIMILARITY` y no se llama al LLM; el modelo devuelve `answerable` en falso; o el modelo responde sin citas válidas. El umbral usa la similitud de la búsqueda vectorial y queda desactivado hasta calibrarlo en la Etapa 5.
- Autenticidad: cada cita lleva la etiqueta de su obra, un parámetro permite excluir las dudosas y espurias, y la respuesta agrega una advertencia fija cuando cita una. Se prueba con un fixture, porque el piloto no tiene obras dudosas.
- Endpoints: `POST /search` (solo retrieval), `POST /ask`, `GET /passages/{chunk_id}` (el fragmento y sus vecinos), `GET /works`, `GET /healthz` y `GET /readyz`.
- `/ask` acepta filtros opcionales por obra e idioma. Devuelve la respuesta y su idioma, si se abstuvo, y las citas, cada una con obra, ubicación, traductor, texto, licencia y autenticidad. Devuelve también la versión de índice y de prompt, el modelo, los tokens y la latencia.
- Aceptación: `make smoke` pasa con el modelo de producción. Son 10 preguntas fijas: esquema válido en las 10, al menos una cita en toda respuesta no abstenida, toda cita apunta a un fragmento recuperado, y las 2 sin respuesta terminan en abstención. `make test` cubre el JSON inválido y la advertencia de autenticidad.

## Etapa 5: Evals de respuesta y línea base completa

Lee antes: Diseño de los evals (completo), Costos y topes, Quién hace qué.

- Métricas de respuesta por reglas, juez con su prompt versionado, y línea base sin retrieval.
- Calibración del umbral de abstención: `python -m evals.calibrate_abstention` elige, sobre `dev`, el valor más alto que rechaza como máximo el 5 % de las preguntas con respuesta, y lo deja en la configuración.
- Calibración del juez: `python -m evals.judge_sample --n 50` saca 50 respuestas de la primera corrida de `dev`, sin el veredicto del juez. La persona las etiqueta y `python -m evals.judge_agreement` imprime el acuerdo por criterio.
- Tres corridas de la línea base sobre `dev` para medir el ruido.
- Aceptación: `make eval` guarda la línea base con las cuatro métricas principales sobre `dev`, y `make eval SPLIT=test CONFIRM_TEST=1` registra la primera corrida sobre `test`. El acuerdo del juez es de 85 % o más. Si no llega, se corrige la rúbrica y se repite una vez; si sigue por debajo, las métricas con juez no se publican y la etapa cierra con las de reglas.

## Etapa 6: Iterar midiendo

Lee antes: Diseño de los evals (estadística), Stack.

Máximo dos sesiones y cinco experimentos. Cada experimento cambia una sola variable.

- Dos lazos separados. El de retrieval (tamaño de fragmento, cabecera, ampliar el fragmento a su capítulo, búsqueda híbrida, k, reranking) se mide sin LLM. El de generación (prompt, cantidad de contexto) se mide con el retrieval congelado y con el modelo de producción.
- Al comparar tamaños de fragmento se mantiene fijo el presupuesto de tokens de contexto, no k.
- Solo se prueban opciones que se puedan desplegar gratis. El reranking no cabe en un backend de 512 MB: se mide en local y se adopta solo si cumple la regla general, mejora 5 puntos o más el recall de evidencia en k o el MRR, y existe un servicio gratuito donde correrlo.
- Cambiar el modelo de embeddings (candidato: `Qwen3-Embedding-0.6B`) queda fuera, salvo que el recall de evidencia en k de la línea base sea menor que 60 %.
- Cada experimento es una entrada en `evals/EXPERIMENTS.md`: hipótesis, cambio, diferencia con su intervalo y decisión. Regla general: un cambio se adopta si mejora la métrica y el intervalo de la diferencia excluye el cero, o si es neutro en calidad y baja costo o latencia. Lo demás se anota como "sin diferencia detectable".
- `evals/FAILURES.md`: las fallas restantes clasificadas por causa, con conteo y un ejemplo real de cada una.
- `test` no se corre en esta etapa.
- Aceptación: `make eval-fast` y `make eval` terminan en 0 con la configuración elegida. `EXPERIMENTS.md` tiene entre 3 y 5 experimentos con su intervalo, y `FAILURES.md` clasifica todas las fallas de la última corrida sobre `dev`.

## Etapa 7: Deploy del backend

Lee antes: Costos y topes, Contratos de datos (parámetros), Stack (límites).

- Backend en el hosting validado en la Etapa 0 y base en Supabase, con las mismas migraciones. El índice de producción se construye con `make index ENV=prod`.
- Límites: pregunta de 500 caracteres como máximo, k y contexto fijados en el servidor, límite por IP y presupuesto global diario guardado en Postgres. La IP se toma de `X-Forwarded-For` (a confirmar en el hosting). Las corridas de evals se identifican con una clave propia y no cuentan contra el límite por IP.
- Si el LLM falla, devuelve 429 o se agotó el presupuesto diario, `/ask` responde en modo solo búsqueda: los fragmentos con sus citas, sin respuesta generada.
- Caché exacta por pregunta normalizada, versión de índice y modelo. Claves solo en el backend, CORS restringido al front, y la base sin acceso público directo.
- Un log JSON por consulta con fragmentos recuperados, versiones, modelo, tokens, latencia por tramo y si se abstuvo. Por defecto se guarda un hash de la pregunta, no el texto.
- Tarea programada en GitHub Actions: ping a la base cada 2 o 3 días y, una vez por semana, las 25 preguntas con generación contra producción, para detectar un modelo retirado o una regresión.
- Aceptación: `curl $API/readyz` devuelve 200 con la versión de índice esperada. `make eval-fast ENV=prod` coincide con la corrida local sobre la misma versión de índice, con una pregunta de diferencia como máximo. `make eval ENV=prod SPLIT=test CONFIRM_TEST=1` registra el sistema final del piloto. La sexta consulta en un minuto desde la misma IP devuelve 429. Con el LLM simulado en error, `/ask` devuelve 200 en modo solo búsqueda.

## Etapa 8: Front

Primera versión publicable.

Lee antes: Ediciones e idiomas, Obras dudosas y espurias, obligaciones por usar Perseus.

- Una página en Vercel: cuadro de pregunta, 6 preguntas de ejemplo, respuesta, y citas que abren el fragmento original con obra, traductor, licencia y etiqueta de autenticidad. Página de fuentes con la atribución.
- `make examples` genera las respuestas de las 6 preguntas de ejemplo y las deja como JSON estático: funcionan aunque el backend duerma o el LLM esté caído.
- Estados visibles: "despertando el servidor", límite alcanzado y modo solo búsqueda.
- Pestaña de evals generada en el build desde `evals/results/`, sin backend propio.
- Aceptación: `make e2e` pasa. Un test de Playwright hace una pregunta, ve una respuesta con al menos una cita, abre el fragmento y ve la atribución. Con el backend apagado, el test ve la respuesta de una pregunta de ejemplo.

## Etapa 9: Corpus Nivel 1

Lee antes: Corpus por niveles (Nivel 1, notas sobre Perseus), Diseño de los evals (dataset).

Datos y un único cambio de código: generalizar el adaptador.

- Filas nuevas en el catálogo y en el manifiesto para las otras seis obras de Perseus y sus textos griegos. El adaptador se generaliza para las cuatro codificaciones de Bekker, sin ramas especiales para una obra.
- Unas 50 preguntas nuevas: algunas por obra, comparaciones entre obras, y casos sobre la obra dudosa (Económicos) y sobre los libros que faltan de la Ética Eudemia.
- Aceptación: `make corpus && make index && make eval` termina en 0, y se registra una corrida sobre `test`. El diff no toca `app/` fuera del adaptador, salvo un experimento de mitigación registrado en `EXPERIMENTS.md`. Los resultados muestran las preguntas de la Ética a Nicómaco antes y después de agrandar el corpus, y las métricas por obra. Si el recall de la Ética a Nicómaco cae más de 5 puntos, se registra ese experimento (filtro por obra, búsqueda híbrida o reranking).

## Etapa 10: Corpus Niveles 2 y 3

Lee antes: Corpus por niveles (niveles 2 y 3, notas sobre el castellano, regla de admisión), Ediciones e idiomas.

Adaptadores nuevos. Cada obra o tomo es una tarea separada, con su propio commit, y se puede cortar en cualquier punto.

- Antes de ingerir cada fuente, la persona abre su página primaria y confirma licencia, traductor y estado de la transcripción. Lo que no pasa queda en el manifiesto como excluido, con el motivo.
- La descarga usa la API o los volcados de cada fuente, con pausas, nunca rastreo de páginas. Del texto de Project Gutenberg se quitan la cabecera, la licencia y la marca.
- Nivel 2, castellano: primero la Metafísica de Azcárate, después Moral a Nicómaco y Política, y luego el resto de los tomos. Se normaliza la ortografía de época para embeber y se conserva el original para mostrar.
- Tablas de capítulos a rangos Bekker para las traducciones que no traen Bekker: se arman desde un texto griego que sí lo traiga (Perseus, o el repo `OpenGreekAndLatin/First1KGreek` en las obras cuya edición griega trae las marcas) y la persona revisa los pasajes que usan los evals. Sin tabla, la obra se cita por libro y capítulo de su edición.
- Nivel 3, inglés: las obras que falten, según la regla de admisión.
- Con dos idiomas en el índice, el retrieval recupera por idioma y fusiona, para que el sesgo hacia el idioma de la pregunta no deje afuera las obras que solo están en inglés.
- Unas 70 preguntas nuevas: 40 sobre las obras nuevas, con al menos una por obra ingerida, y 30 que son la versión en inglés de una pregunta en castellano ya existente. El dataset llega a unas 200.
- Aceptación: `make corpus && make index && make eval` termina en 0, y se registra una corrida sobre `test`. El diff solo toca `app/` en los adaptadores nuevos, la fusión por idioma, la deduplicación por ubicación y la normalización del texto. Los resultados muestran las preguntas de los niveles anteriores antes y después, las métricas por obra y el recall de evidencia según el idioma de la pregunta y el del pasaje. Si la brecha entre esos pares supera 10 puntos, se registra el experimento de traducir la consulta antes de buscar.

## Etapa 11: MCP (opcional)

- Servidor MCP que expone la búsqueda del corpus a clientes como Claude Code. Es una capa delgada sobre la misma función de retrieval, sin lógica propia y sin llamar al LLM. Herramientas de solo lectura: `search_passages`, `get_passage` y `list_works`.
- Primero por stdio. Si se publica por HTTP, va en el mismo FastAPI con transporte Streamable HTTP sin estado y los mismos límites de uso que `/ask`. Tres cuidados: `streamable_http_app()` se monta en la raíz porque ya sirve `/mcp`, el arranque de FastAPI tiene que iniciar el gestor de sesiones del SDK, y `allowed_hosts` lleva el dominio público.
- Aceptación: un test levanta el servidor, lista las herramientas y llama a `search_passages`. El README trae el comando para agregarlo a Claude Code.

## Etapa 12: README y cierre

El README se actualiza al cerrar cada etapa y sigue un orden fijo, pensado para cinco minutos de lectura:

1. Qué es en una línea, enlace a la demo y un GIF corto de respaldo.
2. Tabla de resultados sobre `test`: línea base sin retrieval, línea base del RAG y sistema final, con n, intervalo, fecha, modelo y tamaño del corpus.
3. Un diagrama de arquitectura.
4. Qué se probó y qué no funcionó.
5. Dónde falla, con tres ejemplos reales.
6. Costos medidos por consulta y por corrida de evals.
7. Corpus: obras, traductores, licencias, qué quedó afuera y por qué.
8. Límites conocidos: traducciones antiguas, obras dudosas, precisión de Bekker, pasajes solo en inglés, tamaño del dataset, acuerdo del juez.
9. Cómo correrlo.

Aceptación: en un clon limpio, con Postgres y un proveedor de embeddings configurados, `make setup && make corpus && make index && make eval-fast` reproduce la cifra de retrieval sobre `dev` guardada en el repo, con una pregunta de diferencia como máximo. El proyecto se agrega al portfolio y al CV con una línea de resultado medible sobre `test`, nunca sobre `dev`.

## Costos y topes

- Objetivo: 0 USD por mes para la demo y el desarrollo. Si un proveedor exige cargar una tarjeta, antes se le configura un tope de gasto.
- Presupuesto por consulta: unos 3.000 tokens (2.000 de contexto, 300 de prompt y hasta 700 de salida).
- Las corridas de evals con LLM no entran en un día de cupo gratuito. La línea base de la Etapa 5 son unas 270 respuestas (tres corridas de `dev`, una de `test` y la línea base sin retrieval): cerca de 600.000 tokens del generador, más las llamadas al juez. Con unas 65 respuestas por día, lleva varios días con reanudación. Pagarla cuesta menos de 1 USD al precio de Groq (a confirmar). Cuál de las dos se usa es una decisión abierta.
- Si se agota el cupo gratuito de la demo, pagar cuesta poco: al precio de Groq para `gpt-oss-20b`, un dólar paga unas 2.600 respuestas (a confirmar).
- El cupo de Groq sería por organización y no por clave, así que separar claves no reparte el cupo. Por eso `DAILY_TOKEN_BUDGET` queda por debajo del cupo diario y deja una parte para los evals programados.
- Antes de correr, el runner de evals imprime la estimación de tokens y de costo, y aborta si supera `EVAL_MAX_USD`. Guarda en caché las respuestas del generador y del juez, respeta los límites con esperas y se reanuda si se corta por cupo.
- Los evals de retrieval no usan LLM y se corren sin restricción.

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Licencias mixtas y derechos que cambian según el país | Regla de admisión por manifiesto, evidencia por texto, estado "en espera" y `ATTRIBUTION.md` generado |
| Fuentes de los niveles 2 y 3 sin verificar | Confirmar la página primaria antes de ingerir; cada obra se puede dejar afuera |
| Ediciones sin Bekker o con otra división de capítulos | Cita por libro y capítulo de la edición, tablas derivadas del griego, `bekker_precision` visible |
| El modelo inventa o confunde referencias | Citas por construcción y validación en el servidor |
| Obras parecidas compiten en el retrieval (Ética Eudemia, Magna Moralia) | Volver a medir las preguntas del nivel anterior, métricas por obra, filtro por obra, grupos de evidencia con alternativas |
| Sesgo del modelo de embeddings hacia el idioma de la pregunta | Recuperar por idioma y fusionar; recall por par de idiomas |
| Vocabulario antiguo de las traducciones | Preguntas con términos modernos en el dataset; búsqueda híbrida como experimento |
| El proveedor retira o cambia el modelo gratuito | ID en variables de entorno, respaldo en un segundo proveedor, evals semanales contra producción |
| El backend se duerme y la base se pausa | Ejemplos precalculados, estado "despertando", ping programado |
| Cupo gratuito agotado por abuso | Límite por IP, presupuesto global diario, caché, modo solo búsqueda |
| Los evals con LLM no entran en el cupo diario | Reanudación en varios días o pago con tope; subconjunto de 25 preguntas en PR y en la corrida semanal |
| Dataset chico o contaminado | Intervalos en cada cifra, partición por pasaje, `test` reservado, revisión de la persona |
| Juez poco confiable | Otra familia de modelo, calibración contra etiquetas humanas, acuerdo publicado |
| Cambiar el modelo de embeddings obliga a reindexar | Índice versionado, caché de embeddings y cambio de versión con vuelta atrás |
| Diferencia entre el modelo local y el de producción | Las cifras publicadas son las de producción; `make parity` para los embeddings |
| El repo de Perseus cambia nombres o texto | Versión fijada y `sha256` en el manifiesto |
| Descarga de fuentes bloqueada en la sesión en la nube | Permitir los hosts en el entorno o correr `make corpus` en local |

## Decisiones abiertas

Cada una tiene un valor por defecto para que el plan se pueda ejecutar sin esperar.

1. Regla de derechos. Por defecto, la regla de admisión de arriba. Conviene revisarla según el país desde el que se publica. Nada de esto es asesoramiento legal.
2. Los cinco textos de Perseus que entran solo por su licencia. Por defecto se incluyen, con una nota en el README. La alternativa estricta deja afuera o reemplaza los tres de mayor riesgo: la Poética por la de Bywater o Butcher, Económicos por la de Forster (Oxford, 1921), y la Metafísica inglesa por la de M'Mahon (1857, sin Bekker y con OCR ruidoso) o solo la castellana de Azcárate. Todas a confirmar.
3. Evals con LLM. Por defecto, gratis y repartidos en varios días. La alternativa es pagar menos de 1 USD por corrida completa, con tope de gasto.
4. Repo público. Por defecto sí: GitHub Actions es gratis para repos públicos.
