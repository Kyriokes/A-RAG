# Dataset de evals

Formato de las preguntas de `evals/dataset/*.jsonl`, de `evals/smoke.jsonl` y de las etiquetas de calibración del juez. El esquema está en `evals/schema.py`; `make test` valida el formato de todo lo que haya en esta carpeta (sin corpus, sin red y sin LLM).

## Reglas

- Una pregunta por línea, en JSON. Se puede repartir en varios archivos `.jsonl` (por ejemplo, uno por tipo o por obra). La versión del dataset es el sha256 de los nombres y contenidos de los archivos.
- El runner (`make eval-fast`, `make eval`) ignora las preguntas sin `revisado_por` y dice cuántas ignoró. Solo la persona completa `revisado_por` y `fecha_revision`, después de leer la pregunta contra su pasaje.
- La evidencia es obra y rango Bekker, nunca un ID de fragmento: tiene que sobrevivir a los cambios de chunking.
- La partición entre `dev` y `test` es por pasaje: ningún pasaje de evidencia puede aparecer en los dos conjuntos (lo comprueba `make test`). Las preguntas gemelas (`twin_of`) van en el mismo conjunto.
- Las preguntas del dataset no se usan como ejemplos dentro de ningún prompt.

## Campos de una pregunta

| Campo | Tipo | Qué es |
|---|---|---|
| `id` | texto | Único en todo el dataset. Ej.: `en-0001`. |
| `question` | texto | La pregunta tal como la haría una persona. |
| `question_lang` | `es` o `en` | Idioma de la pregunta. |
| `type` | ver abajo | Tipo de pregunta. |
| `unanswerable_reason` | ver abajo o `null` | Solo y siempre con `type: unanswerable`. |
| `expected_behavior` | `answer`, `abstain`, `correct_premise`, `answer_with_caveat` | Qué tiene que hacer el sistema. `unanswerable` exige `abstain`. `answer_with_caveat`: responde citando una obra dudosa o espuria, con su advertencia. |
| `evidence` | lista de grupos | Evidencia esperada (abajo). Obligatoria con `answer` y `answer_with_caveat`. |
| `key_points` | lista de 2 a 4 textos | Datos clave que la respuesta tiene que contener. Alternativas separadas por `\|`: `"término medio\|justo medio"`. Con `abstain` pueden ser 0. |
| `split` | `dev` o `test` | Conjunto. |
| `source` | `handwritten`, `synthetic_reviewed`, `synthetic_unreviewed` | Origen. Una pregunta revisada no puede seguir como `synthetic_unreviewed`, y las de tipo `unanswerable`, `false_premise`, `cross_work` y `dubious_work` revisadas tienen que ser `handwritten` (las escribe la persona). |
| `revisado_por` | texto o `null` | Quién la revisó contra el pasaje. Sin esto, el runner la ignora. |
| `fecha_revision` | `AAAA-MM-DD` o `null` | Obligatoria si hay `revisado_por`. |
| `corpus_version_checked` | texto o `null` | `corpus_version` con la que se revisó. Las preguntas sin respuesta se vuelven a revisar cuando cambia: si no coincide con la actual, el runner las cuenta como sin revisar ("revisión vencida"). |
| `twin_of` | id o `null` | La pregunta de la que esta es versión en otro idioma. |

Tipos (`type`): `lookup` (búsqueda de un pasaje), `definition`, `synthesis` (varios pasajes), `cross_work` (comparación entre obras), `bekker_ref` (referencia Bekker directa), `unanswerable`, `false_premise`, `dubious_work`, `injection` (inyección de prompt; al menos 10).

Motivos sin respuesta (`unanswerable_reason`): `out_of_domain`, `lost_or_not_ingested` (obra perdida o no incluida), `near_miss` (tema presente, dato ausente), `opinion`.

## Evidencia

`evidence` es una lista de grupos. Cada grupo es una lista de ubicaciones alternativas (pasajes paralelos, por ejemplo el mismo tema en la Ética a Nicómaco y en la Ética Eudemia): basta recuperar una para acertar el grupo. Las preguntas de síntesis y de comparación llevan un grupo por pasaje necesario.

Ubicación con Bekker (`bekker_end` es una referencia completa; una referencia sin línea, como `1106b`, cubre toda la columna):

```json
{"work_id": "eth-nic", "bekker_start": "1106b36", "bekker_end": "1107a2"}
```

Ubicación sin Bekker (ediciones que no la traen): libro y capítulo de una edición concreta, porque la división en capítulos cambia de una edición a otra. `edition_note` dice cuál, para las personas; `edition_id`, opcional, restringe el acierto a los fragmentos de esa edición. `book` es `null` en obras divididas solo en capítulos.

```json
{"work_id": "metaph", "book": "1", "chapter": "2", "edition_note": "Azcárate, 1875", "edition_id": "metaph.es.azcarate"}
```

Un fragmento recuperado acierta una ubicación con Bekker si es de la misma obra y su rango se solapa con el de la ubicación, ensanchado `EVAL_OVERLAP_TOLERANCE_LINES` líneas a cada lado, en al menos `EVAL_OVERLAP_MIN_LINES` líneas (o en todo el fragmento si es más corto). Las líneas siguen la aritmética de `app/bekker.py`, así que el solapamiento cruza bien los saltos de columna. Una ubicación sin Bekker se acierta por obra, libro y capítulo.

## Ejemplo completo

Una línea por pregunta (aquí partida para leerla; el contenido es ilustrativo, no es una pregunta del dataset):

```json
{"id": "ej-0001", "question": "...", "question_lang": "es", "type": "definition",
 "unanswerable_reason": null, "expected_behavior": "answer",
 "evidence": [[{"work_id": "eth-nic", "bekker_start": "1106b36", "bekker_end": "1107a2"}]],
 "key_points": ["dato clave uno|variante", "dato clave dos"], "split": "dev",
 "source": "handwritten", "revisado_por": null, "fecha_revision": null,
 "corpus_version_checked": null, "twin_of": null}
```

## Comprobaciones

- `make test`: esquema, ids únicos, `twin_of`, obras del catálogo, rangos Bekker dentro del rango de la obra y ningún pasaje en `dev` y `test` a la vez.
- `python -m evals.labels`: lo anterior más que toda evidencia resuelva a texto no vacío del corpus procesado (necesita `make corpus`). `--format-only` omite el corpus.
- `make eval-fast` corre las dos antes de medir y se niega a correr si una etiqueta es inválida.

## `evals/smoke.jsonl` (`make smoke`)

Las 10 preguntas fijas de la Etapa 4, una por línea. Dos de ellas sin respuesta (`abstain`).

```json
{"id": "smoke-01", "question": "...", "question_lang": "es", "expected_behavior": "answer", "note": "opcional"}
```

`make smoke` comprueba en cada una: respuesta con esquema válido, que el LLM respondió (sin modo solo búsqueda ni error), al menos una cita si no se abstuvo, toda cita apunta a un fragmento recuperado, y las `abstain` terminan en abstención. `python -m evals.smoke --api URL` hace lo mismo contra `POST URL/ask`.

## Calibración del juez (`evals/calibration/`)

1. `python -m evals.judge_sample --n 50` escribe `pending.jsonl` con respuestas de la primera corrida completa sobre `dev`, sin los veredictos del juez (al menos 15 fallidas y 10 con contexto en otro idioma, si las hay). Los pasajes para leer van a `evals/.cache/calibration/pending_context.md`, que no se sube.
2. La persona copia `pending.jsonl` a `labels.jsonl` y completa cada `labels` con `"pass"` o `"fail"` por criterio, sin mirar los veredictos del juez:

   ```json
   {"sample_id": "s01", "run_id": "...", "case_id": "...", "labels": {"faithfulness": "pass", "key_points": "fail"}, "notes": ""}
   ```

3. `python -m evals.judge_agreement` imprime el acuerdo por criterio, el kappa de Cohen y cuántas fallas humanas detectó el juez, y escribe `agreement.json`. Termina con error si algún criterio queda bajo `EVAL_JUDGE_MIN_AGREEMENT` (85 %). Si cambia el modelo o el prompt del juez, se repite.

`python -m evals.calibrate_abstention` escribe `abstention.json` con el umbral de similitud elegido sobre `dev`; se aplica a mano (`ABSTAIN_MIN_SIMILARITY` en `.env` o en `app/settings.py`).
