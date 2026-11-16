# Revisión del dataset v0 (Etapa 3)

`evals/dataset/v0.jsonl` trae 80 preguntas **candidatas** sobre la Ética a Nicómaco. Las propuso un agente (Claude) y nadie las revisó todavía: todas tienen `"source": "synthetic_unreviewed"` y `"revisado_por": null`. El runner (`make eval-fast`, `make eval`) las ignora hasta que la persona las revise una por una contra su pasaje. El formato de cada campo está en `evals/dataset/README.md`.

También quedan listos, a partir de preguntas de `dev`:

- `evals/smoke.jsonl`: las 10 preguntas de `make smoke` (8 con respuesta y 2 sin respuesta). Son copias textuales de preguntas de `dev`; el campo `note` dice de cuál. Si se corrige o se borra esa pregunta de `dev`, hay que corregir o reemplazar también la de smoke.
- `evals/pr_subset.txt`: los 25 ids de `dev` que se corren con generación en los PR que tocan la generación o los prompts.

## Composición

| Tipo | Total | `dev` | `test` |
|---|---|---|---|
| `lookup` (búsqueda de un pasaje) | 20 | 14 | 6 |
| `definition` | 10 | 7 | 3 |
| `synthesis` (varios pasajes de la EN) | 8 | 5 | 3 |
| `bekker_ref` (5 "¿qué dice en 1155a5?" y 4 "¿dónde dice que…?") | 9 | 6 | 3 |
| `unanswerable` | 15 | 10 | 5 |
| `false_premise` | 8 | 6 | 2 |
| `injection` (5 esperan abstención, 5 responder con citas) | 10 | 7 | 3 |
| **Total** | **80** | **55** | **25** |

- Idioma: 69 en castellano (86 %) y 11 en inglés. En `dev` hay 47 y 8; en `test`, 22 y 3.
- Sin respuesta, por motivo: `out_of_domain` 4 (3 en `dev`, 1 en `test`), `lost_or_not_ingested` 4 (3 y 1), `near_miss` 4 (2 y 2) y `opinion` 3 (2 y 1).
- Comportamiento esperado: `answer` 52, `abstain` 20 y `correct_premise` 8.
- Hay un par de gemelas: `v0-df-10` (inglés) es la versión de `v0-df-01`, con la misma evidencia y en el mismo conjunto.
- `pr_subset.txt` tiene 5 `lookup`, 3 `definition`, 2 `synthesis`, 3 `bekker_ref` (2 directas y 1 inversa), 5 sin respuesta (uno de cada motivo, más otro de `lost_or_not_ingested` en inglés), 3 de premisa falsa y 4 de inyección.

Los ids dicen el tipo: `v0-lk` (lookup), `v0-df` (definition), `v0-sy` (synthesis), `v0-bk` (bekker_ref), `v0-un` (unanswerable), `v0-fp` (false_premise) y `v0-in` (injection).

## Cómo se armaron las etiquetas

- Cada rango Bekker sale de `data/processed/eth-nic.en.rackham.jsonl`, no de memoria. Primero se buscó en el texto de Rackham la oración que responde la pregunta. El rango va desde la posición Bekker de la primera oración hasta el final de la última, con la misma interpolación por palabras que usa `app/chunking.py` para los fragmentos.
- El inglés trae marcas cada unas 20 líneas, así que las líneas son aproximadas, y en algún caso se corren 2 o 3 líneas respecto de la edición impresa. La tolerancia del acierto (`EVAL_OVERLAP_TOLERANCE_LINES`, 3 líneas) absorbe esa diferencia. El griego de Bywater (`data/processed/eth-nic.grc.bywater.jsonl`) trae marcas cada 5 líneas y se usó para comprobar las referencias directas de las preguntas `bekker_ref`.
- La partición es por capítulo, que es más estricto que por pasaje. Ningún capítulo con evidencia aparece en los dos conjuntos. `make test` comprueba que no haya solapamiento de pasajes. Si se mueve una pregunta de conjunto, hay que mover con ella todas las de su capítulo; las sin respuesta e inyecciones sin evidencia se pueden mover libremente.
- Las preguntas evitan las frases distintivas de la traducción de 1926. Algunas usan a propósito términos modernos que Rackham traduce de otra manera, para medir ese desajuste. Por ejemplo, eudaimonía es "happiness" en Rackham, phronesis es "Prudence", akrasía o incontinencia es "Unrestraint", el intemperante es "profligate", término medio es "mean" u "observance of the mean", magnanimidad es "Greatness of Soul", epieikeia es "Equity" y vergüenza (aidós) es "Modesty".
- Antes de entregar se comprobó, sin base de datos, que cada ubicación la toca al menos un fragmento del chunker actual. Cada una la tocan entre 1 y 4 de los 458 fragmentos. También se comprobó que las 5 referencias directas resuelven por rango al fragmento correcto.

## Cómo revisar (unos 5 minutos por pregunta)

1. Abrí `evals/dataset/v0.jsonl` y buscá la pregunta por su `id`.
2. Leé los fragmentos que tocan su evidencia. Este comando imprime cada ubicación con el texto de los fragmentos que la tocan (necesita `make corpus`; no usa base, red ni LLM):

   ```bash
   CASO=v0-lk-01 uv run python - <<'EOF'
   import os

   from app.chunking import chunk_units
   from app.corpus.catalog import load_units, load_works
   from evals.metrics import Location, MatchParams, span_hit
   from evals.schema import load_dataset

   caso = next(c for c in load_dataset().cases if c.id == os.environ["CASO"])
   fragmentos = chunk_units(load_units(), load_works())
   solapa = MatchParams(tolerance_lines=0, min_overlap_lines=1)
   print(caso.question, "\n", caso.key_points)
   for g, grupo in enumerate(caso.evidence):
       for ubicacion in grupo:
           print(f"\n=== grupo {g}: {ubicacion.label()}")
           for f in fragmentos:
               if span_hit(Location.from_chunk(f), ubicacion, solapa):
                   print(f"--- {f.embed_text}\n")
   EOF
   ```

   Para ver las líneas exactas, el griego está en `data/processed/eth-nic.grc.bywater.jsonl`, con una marca cada 5 líneas en `bekker_anchors`.
3. Comprobá, según el tipo:
   - **Toda pregunta.** Se entiende y tiene una respuesta razonable. No copia frases distintivas de Rackham. `type`, `expected_behavior` y `unanswerable_reason` son correctos.
   - **Con evidencia.** El pasaje responde la pregunta. El rango no se queda corto ni sobra mucho. Si el mismo punto aparece en otro lugar de la EN, se agrega como alternativa dentro del mismo grupo. Las de síntesis llevan un grupo por pasaje necesario.
   - **Datos clave.** Son entre 2 y 4, y cada uno está en el pasaje, no en lo que uno sabe de Aristóteles. Las alternativas se separan con `|`. El juez solo ve el texto de la respuesta, y el prompt del generador le prohíbe escribir referencias Bekker. Por eso, en las `bekker_ref` inversas ("¿dónde dice…?") los datos clave son de contenido: la ubicación se mide por la cita.
   - **Sin respuesta.** Hay que buscar que la respuesta de verdad no esté en el corpus actual (por ejemplo, con `grep -i` sobre `data/processed/eth-nic.en.rackham.jsonl`). Después se completa `corpus_version_checked` (ver abajo).
   - **Premisa falsa.** La premisa es falsa de verdad, y la evidencia muestra lo que Aristóteles sostiene.
   - **Inyección.** Ninguna respuesta debe revelar el prompt del sistema ni seguir la orden inyectada. Las que esperan `answer` tienen que responder la parte legítima con citas.
4. Decidí:
   - **Aceptarla**: completar `"revisado_por": "<tu nombre>"`, `"fecha_revision": "AAAA-MM-DD"` y `"source": "synthetic_reviewed"`. El esquema rechaza una pregunta revisada que siga como `synthetic_unreviewed`. Las sin respuesta, de premisa falsa, de comparación y de obra dudosa no se aceptan así (ver "Una decisión antes de empezar").
   - **Corregirla**: editarla, y si la reescribiste del todo, usar `"source": "handwritten"`.
   - **Rechazarla**: borrar la línea. Si estaba en `smoke.jsonl` o `pr_subset.txt`, reemplazarla allí por otra de `dev` del mismo tipo.
   - En todas las que reviso, completar `"corpus_version_checked"` con la salida de `uv run python -c "from app.corpus.catalog import corpus_version; print(corpus_version())"` (hoy, `879fde4a1670429e`). En las sin respuesta es obligatorio y vence: cuando cambia `corpus_version`, el runner las cuenta como sin revisar ("revisión vencida") hasta que se vuelvan a revisar contra el corpus nuevo.
5. Al terminar una tanda:

   ```bash
   uv run python -m evals.labels                      # formato + que toda evidencia resuelva a texto
   uv run pytest -q tests/test_evals_dataset_format.py
   ```

La Etapa 3 cierra cuando `make eval-fast` informa 80 casos revisados y ninguno ignorado. Si se borran preguntas, hay que reponerlas para volver a 80.

### Una decisión antes de empezar

El plan dice que las preguntas de comparación, sin respuesta, premisa falsa y obra dudosa las escribe la persona. Las 15 sin respuesta (`v0-un-*`) y las 8 de premisa falsa (`v0-fp-*`) de este archivo son propuestas del agente: sirven de borrador, no se aceptan tal cual. Para cada una:

- reescribirla con tus palabras, o reemplazarla por otra tuya del mismo tipo, y marcarla `handwritten`;
- o borrarla y reponer otra escrita por vos.

El esquema rechaza una pregunta revisada de esos cuatro tipos que no sea `handwritten`. Aceptar propuestas del modelo sería una excepción al plan: se decide aparte, se anota en `docs/decisions.md` y se cambia el esquema a propósito.

El plan también pide que el modelo que propone no sea el generador ni el juez. Si el juez termina siendo un modelo de Claude, conviene anotarlo.

## Las que menos me convencen

Conviene mirarlas primero.

- **`v0-un-05`** (comedia en el libro perdido de la Poética). La EN 4.8 (1128a21–23) compara la comedia antigua con la nueva. El sistema puede recuperar ese pasaje y responder algo parcial en lugar de abstenerse.
- **`v0-un-07`** (las cuatro causas de la Metafísica). La EN 6.2 (1139a30–33) nombra la causa eficiente y la final. La palabra "Metafísica" con mayúscula filtra por esa obra, que no está indexada, y entonces no hay fragmentos y se abstiene. Si se escribe en minúscula, la pregunta puede recuperar 6.2.
- **`v0-un-09`, `v0-un-10`, `v0-un-11`, `v0-un-12`** (`near_miss`: castigo del adulterio, edad para educar, cantidad de vino, horas de contemplación). Son de zona gris. Una buena respuesta podría ser "el texto no da ese dato, pero dice X" con `answerable: true`, y eso cuenta como no abstenerse. Decidí si para estos casos el comportamiento esperado es abstenerse.
- **`v0-un-13`, `v0-un-15`** (opinión). El modelo puede responder con lo que dicen los pasajes sobre el término medio o la felicidad en lugar de abstenerse. Decidí si eso es aceptable.
- **`v0-in-04`**. Pide responder "sin pasajes y sin citas", y la etiqueta espera una respuesta con citas, porque el sistema siempre cita. Confirmá que es lo que querés medir.
- **`v0-in-10`**. Contiene "999a", que `app.bekker.find_refs` lee como referencia Bekker. Ningún fragmento indexado cae en 999a, y la búsqueda sigue por embeddings (comprobado). Si se indexa la Metafísica (980a–1093b), esa referencia empezaría a devolver un fragmento de la Metafísica.
- **`v0-lk-04`** (Príamo, inglés). El grupo une 1.9 (nadie llama feliz a quien termina como Príamo) y 1.10 (el feliz no será dichoso del todo, pero tampoco desdichado). Los matices difieren. Revisá que los datos clave no exijan más de lo que dice cada pasaje.
- **`v0-fp-03`**. La evidencia une como alternativas 1.5 (la vida de placer es de bestias) y 1.7 (la felicidad es actividad conforme a la virtud). No es "el mismo punto dicho dos veces". Si preferís, partilo en dos grupos.
- **`v0-df-08`** (valentía). El primer grupo une 3.6 y 2.7, que definen la valentía como término medio. El segundo, 3.7, trata del modo y del fin noble.
- **`v0-sy-03`** (placer en los libros VII y X, inglés). El retrieval no filtra por libro, y la pregunta depende de recuperar los dos pasajes.
- **`v0-bk-03`**. El rango empieza en 1129b23 porque la oración inglesa empieza antes. En el griego, la definición está en 1129b25–27.
- **`v0-lk-02`**. Tiene un rango ancho (1094b11–31), que cubre todo el argumento sobre la exactitud. Se puede acotar.
- **Concentración.** El capítulo 2.6 sostiene tres preguntas (`v0-df-01`, `v0-df-10` y `v0-fp-06`), y el 10.8 sostiene cuatro (`v0-lk-18`, `v0-sy-02`, `v0-sy-04` y `v0-sy-05`) más la referencia directa `v0-bk-05`. Todas están en `dev`.

## Re-auditar cuando cambie `corpus_version`

Todas las sin respuesta se vuelven a revisar cuando cambia `corpus_version`. Con la Etapa 9 (Nivel 1) cambian seguro estas:

- **`v0-un-07`** (Metafísica) y **`v0-un-08`** (Retórica) pasan a tener respuesta cuando se indexen esas obras. Hoy están en espera.
- **`v0-un-10`** (edad para educar): la Política, libro VII (1336a–b), da edades (hasta los cinco años y de los cinco a los siete). Pasa a tener respuesta con la Política.
- **`v0-un-05`** (comedia, Poética libro II): sigue sin respuesta porque el libro está perdido, pero con la Poética indexada aumenta el riesgo de respuestas parciales.
- **`v0-un-06`** (Protréptico): sigue sin respuesta mientras los fragmentos estén excluidos (el plan excluye los fragmentos de Ross).
- **`v0-un-09`, `v0-un-11`, `v0-un-12`**: hay que comprobarlas otra vez contra las obras nuevas.

Un dato que vale para casos futuros: las notas del traductor no se indexan, y alguna trae datos que el texto no da. La nota de Rackham en 3.10 dice que algunos manuscritos insertan el nombre del glotón de la grulla, vertido en broma al inglés, y la de 9.10 nombra las parejas de amigos de la poesía, como Aquiles y Patroclo. La Ética Eudemia (en espera) también nombra al glotón: Filóxeno, hijo de Erixis. Por eso esas preguntas quedaron fuera del dataset.

## Fixtures y texto del corpus

Estos archivos no contienen texto del corpus. La evidencia son rangos Bekker y los datos clave son paráfrasis en castellano o inglés. No se agregó ningún fixture.
