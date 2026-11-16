# Atribución

Generado por `python -m app.corpus.attribution` desde `corpus/sources.yaml` y `tests/fixtures/perseus/fixtures.yaml`. No editar a mano.

El código del proyecto se publica con licencia MIT (`LICENSE`). Los textos conservan su propia licencia, indicada abajo. El texto del corpus no se sube al repositorio: `make corpus` lo descarga y verifica su `sha256`. Las únicas excepciones son los fixtures de tests y las respuestas de ejemplo del front, listados al final. Si se publica el corpus procesado, va bajo CC BY-SA 4.0.

## Perseus Digital Library

Los textos vienen de Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio [PerseusDL/canonical-greekLit](https://github.com/PerseusDL/canonical-greekLit), con licencia CC BY-SA 4.0. Obligaciones: citar traductor y edición, acreditar a Perseus con enlace, nombrar el repositorio de origen y la licencia, decir qué se cambió y ofrecer a Perseus las correcciones al texto.

## Qué se cambió

- Se extrae el texto del XML TEI; las etiquetas no se conservan.
- Las notas del traductor (`<note>`) y las referencias bibliográficas sueltas (`<bibl>`) se separan del texto y se guardan aparte.
- Se omiten los títulos de libro (`<head>`).
- Las secciones y subsecciones de la edición Loeb se unen en una unidad por capítulo (o por página Bekker en la Política), con un párrafo por bloque del TEI.
- Las citas marcadas (`<q>`) llevan comillas tipográficas según el TEI; los suplementos editoriales entre corchetes angulares se muestran como ⟨…⟩, las lagunas (`<gap>`) como …, y en `<choice>` se usa la lectura corregida.
- Se normalizan los espacios (repetidos, antes de la puntuación, después de una comilla de apertura y los que deja una nota quitada).
- Las marcas Bekker se conservan como metadatos. El rango Bekker de cada capítulo de una traducción se calcula desde el texto griego de Perseus del mismo commit.
- No se corrigió el texto. Si se corrige algo, la corrección se ofrece a Perseus.

## Ediciones

| edición | obra | traductor o editor | año | licencia | estado |
|---|---|---|---|---|---|
| `eth-nic.en.rackham` | Ética a Nicómaco | Harris Rackham | 1926 | CC BY-SA 4.0 | indexada (se publica) |
| `eth-nic.grc.bywater` | Ética a Nicómaco | texto griego | 1894 | CC BY-SA 4.0 | referencia (no se publica) |
| `eth-eud.en.rackham` | Ética Eudemia | Harris Rackham | 1935 | CC BY-SA 4.0 | en espera (no se publica) |
| `eth-eud.grc.susemihl` | Ética Eudemia | texto griego | 1884 | CC BY-SA 4.0 | en espera (no se publica) |
| `pol.en.rackham` | Política | Harris Rackham | 1944 | CC BY-SA 4.0 | en espera (no se publica) |
| `pol.grc.ross` | Política | texto griego | 1957 | CC BY-SA 4.0 | en espera (no se publica) |
| `metaph.en.tredennick` | Metafísica | Hugh Tredennick | 1933 | CC BY-SA 4.0 | en espera (no se publica) |
| `metaph.grc.ross` | Metafísica | texto griego | 1924 | CC BY-SA 4.0 | en espera (no se publica) |
| `rhet.en.freese` | Retórica | John Henry Freese | 1926 | CC BY-SA 4.0 | en espera (no se publica) |
| `rhet.grc.ross` | Retórica | texto griego | 1959 | CC BY-SA 4.0 | en espera (no se publica) |
| `poet.en.fyfe` | Poética | William Hamilton Fyfe | 1939 | CC BY-SA 4.0 | en espera (no se publica) |
| `poet.grc.kassel` | Poética | texto griego | 1965 | CC BY-SA 4.0 | en espera (no se publica) |
| `oec.en.armstrong` | Económicos | George Cyril Armstrong | 1935 | CC BY-SA 4.0 | en espera (no se publica) |
| `oec.grc.armstrong` | Económicos | texto griego | 1935 | CC BY-SA 4.0 | en espera (no se publica) |

### Ética a Nicómaco — `eth-nic.en.rackham`

- Obra: Ética a Nicómaco (Nicomachean Ethics; Ethica Nicomachea)
- Idioma: en
- Traductor: Harris Rackham (m. 1944, a confirmar)
- Año de publicación: 1926
- Fuente: https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/dacb04909e43dc47738828d33cc3d2b0635bc54c/data/tlg0086/tlg010/tlg0086.tlg010.perseus-eng2.xml
- Versión fijada: `dacb04909e43dc47738828d33cc3d2b0635bc54c` · sha256 `ffbd530e99b34ebab4332f98aa456aa930a671e6125db26e18d4d7c68bc3948f`
- Licencia: CC BY-SA 4.0
- Evidencia de la licencia: Cabecera TEI del archivo (publicationStmt/availability): &lt;licence target="https://creativecommons.org/licenses/by-sa/4.0/"&gt;Available under a Creative Commons Attribution-ShareAlike (CC BY-SA) license.&lt;/licence&gt; y "Reuse must credit the Perseus Digital Library in a standard bibliographic format, including a link to https://www.perseus.tufts.edu/. Each text reused should be individually identified and linked to its source GitHub repository whenever possible, either in its citation or in an accompanying source list. All corrections must be offered to the Perseus Digital Library." README.md del repo en el commit fijado: "Unless otherwise indicated, all contents of this repository are licensed under a Creative Commons Attribution-ShareAlike 4.0 International License. You must offer Perseus any modifications you make."
- Crédito requerido: Aristóteles, The Nicomachean Ethics, trad. Harris Rackham, Loeb Classical Library (Londres: William Heinemann; Nueva York: G. P. Putnam's Sons, 1926). Edición digital: Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio PerseusDL/canonical-greekLit, archivo data/tlg0086/tlg010/tlg0086.tlg010.perseus-eng2.xml, commit dacb049. Licencia CC BY-SA 4.0. Texto extraído del TEI y normalizado; los cambios están en ATTRIBUTION.md.
- Estado: indexada (se publica). Nivel 0 (piloto). Cumple la regla de admisión por las dos vías: licencia abierta explícita de Perseus (CC BY-SA 4.0) y dominio público en EE. UU. (1926) con el traductor muerto en 1944, hace más de 70 años (año de muerte a confirmar).

### Ética a Nicómaco — `eth-nic.grc.bywater`

- Obra: Ética a Nicómaco (Nicomachean Ethics; Ethica Nicomachea)
- Idioma: grc
- Traductor: texto original (sin traductor)
- Año de publicación: 1894
- Fuente: https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/dacb04909e43dc47738828d33cc3d2b0635bc54c/data/tlg0086/tlg010/tlg0086.tlg010.perseus-grc2.xml
- Versión fijada: `dacb04909e43dc47738828d33cc3d2b0635bc54c` · sha256 `4c86c8f734876d2a19b0b58fa2aea08f002ce145c7c547d428b7662098a7bba8`
- Licencia: CC BY-SA 4.0
- Evidencia de la licencia: Cabecera TEI del archivo (publicationStmt/availability): &lt;licence target="https://creativecommons.org/licenses/by-sa/4.0/"&gt;Available under a Creative Commons Attribution-ShareAlike (CC BY-SA) license.&lt;/licence&gt; y "Reuse must credit the Perseus Digital Library in a standard bibliographic format, including a link to https://www.perseus.tufts.edu/. Each text reused should be individually identified and linked to its source GitHub repository whenever possible, either in its citation or in an accompanying source list. All corrections must be offered to the Perseus Digital Library." README.md del repo en el commit fijado: "Unless otherwise indicated, all contents of this repository are licensed under a Creative Commons Attribution-ShareAlike 4.0 International License. You must offer Perseus any modifications you make."
- Crédito requerido: Aristóteles, Aristotelis Ethica Nicomachea, ed. Ingram Bywater, Oxford Classical Texts (Oxford: Clarendon Press, 1894). Edición digital: Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio PerseusDL/canonical-greekLit, archivo data/tlg0086/tlg010/tlg0086.tlg010.perseus-grc2.xml, commit dacb049. Licencia CC BY-SA 4.0. Solo se usa para derivar la numeración Bekker; el texto no se publica.
- Estado: referencia (no se publica). Texto griego (Bywater), solo para calcular el rango Bekker de cada capítulo (marcas cada 5 líneas). No se indexa ni se publica.

### Ética Eudemia — `eth-eud.en.rackham`

- Obra: Ética Eudemia (Eudemian Ethics; Ethica Eudemia)
- Idioma: en
- Traductor: Harris Rackham (m. 1944, a confirmar)
- Año de publicación: 1935
- Fuente: https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/dacb04909e43dc47738828d33cc3d2b0635bc54c/data/tlg0086/tlg009/tlg0086.tlg009.perseus-eng2.xml
- Versión fijada: `dacb04909e43dc47738828d33cc3d2b0635bc54c` · sha256 `9e98b239c23def2aef8d3b398049356ea4577bb856818bef79a626b6dd5609cd`
- Licencia: CC BY-SA 4.0
- Evidencia de la licencia: Cabecera TEI del archivo (publicationStmt/availability): &lt;licence target="https://creativecommons.org/licenses/by-sa/4.0/"&gt;Available under a Creative Commons Attribution-ShareAlike (CC BY-SA) license.&lt;/licence&gt; y "Reuse must credit the Perseus Digital Library in a standard bibliographic format, including a link to https://www.perseus.tufts.edu/. Each text reused should be individually identified and linked to its source GitHub repository whenever possible, either in its citation or in an accompanying source list. All corrections must be offered to the Perseus Digital Library." README.md del repo en el commit fijado: "Unless otherwise indicated, all contents of this repository are licensed under a Creative Commons Attribution-ShareAlike 4.0 International License. You must offer Perseus any modifications you make."
- Crédito requerido: Aristóteles, The Athenian Constitution. The Eudemian Ethics. On Virtues and Vices, trad. Harris Rackham, Loeb Classical Library (Londres: William Heinemann; Cambridge, MA: Harvard University Press, 1935). Edición digital: Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio PerseusDL/canonical-greekLit, archivo data/tlg0086/tlg009/tlg0086.tlg009.perseus-eng2.xml, commit dacb049. Licencia CC BY-SA 4.0. Texto extraído del TEI y normalizado; los cambios están en ATTRIBUTION.md.
- Estado: en espera (no se publica). Se indexa en la Etapa 9. Entra solo por la licencia que declara Perseus: publicada en 1935, posterior a 1930, su dominio público en EE. UU. depende de que el copyright no se haya renovado (decisión abierta 2 del plan). Trae solo los libros I, II, III, VII y VIII.

### Ética Eudemia — `eth-eud.grc.susemihl`

- Obra: Ética Eudemia (Eudemian Ethics; Ethica Eudemia)
- Idioma: grc
- Traductor: texto original (sin traductor)
- Año de publicación: 1884
- Fuente: https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/dacb04909e43dc47738828d33cc3d2b0635bc54c/data/tlg0086/tlg009/tlg0086.tlg009.perseus-grc2.xml
- Versión fijada: `dacb04909e43dc47738828d33cc3d2b0635bc54c` · sha256 `4fc07ad4775209f5381835e2636e587c83050d8513648b6d7bfaee9cbfbacae6`
- Licencia: CC BY-SA 4.0
- Evidencia de la licencia: Cabecera TEI del archivo (publicationStmt/availability): &lt;licence target="https://creativecommons.org/licenses/by-sa/4.0/"&gt;Available under a Creative Commons Attribution-ShareAlike (CC BY-SA) license.&lt;/licence&gt; y "Reuse must credit the Perseus Digital Library in a standard bibliographic format, including a link to https://www.perseus.tufts.edu/. Each text reused should be individually identified and linked to its source GitHub repository whenever possible, either in its citation or in an accompanying source list. All corrections must be offered to the Perseus Digital Library." README.md del repo en el commit fijado: "Unless otherwise indicated, all contents of this repository are licensed under a Creative Commons Attribution-ShareAlike 4.0 International License. You must offer Perseus any modifications you make."
- Crédito requerido: Aristóteles, Aristotelis Ethica Eudemia. Eudemi Rhodii Ethica, ed. Franz Susemihl (Leipzig: Teubner, 1884). Edición digital: Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio PerseusDL/canonical-greekLit, archivo data/tlg0086/tlg009/tlg0086.tlg009.perseus-grc2.xml, commit dacb049. Licencia CC BY-SA 4.0. Solo se usa para derivar la numeración Bekker; el texto no se publica.
- Estado: en espera (no se publica). Se usa en la Etapa 9, solo como referencia Bekker (texto griego, no se indexa ni se publica).

### Política — `pol.en.rackham`

- Obra: Política (Politics; Politica)
- Idioma: en
- Traductor: Harris Rackham (m. 1944, a confirmar)
- Año de publicación: 1944
- Fuente: https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/dacb04909e43dc47738828d33cc3d2b0635bc54c/data/tlg0086/tlg035/tlg0086.tlg035.perseus-eng2.xml
- Versión fijada: `dacb04909e43dc47738828d33cc3d2b0635bc54c` · sha256 `c4546ed5e2ab75a1ab33f2c84a32cec53fa98a537dfc6714882e5859d638f92b`
- Licencia: CC BY-SA 4.0
- Evidencia de la licencia: Cabecera TEI del archivo (publicationStmt/availability): &lt;licence target="https://creativecommons.org/licenses/by-sa/4.0/"&gt;Available under a Creative Commons Attribution-ShareAlike (CC BY-SA) license.&lt;/licence&gt; y "Reuse must credit the Perseus Digital Library in a standard bibliographic format, including a link to https://www.perseus.tufts.edu/. Each text reused should be individually identified and linked to its source GitHub repository whenever possible, either in its citation or in an accompanying source list. All corrections must be offered to the Perseus Digital Library." README.md del repo en el commit fijado: "Unless otherwise indicated, all contents of this repository are licensed under a Creative Commons Attribution-ShareAlike 4.0 International License. You must offer Perseus any modifications you make."
- Crédito requerido: Aristóteles, Politics, trad. Harris Rackham, Loeb Classical Library (Londres: William Heinemann; Cambridge, MA: Harvard University Press, 1944). Edición digital: Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio PerseusDL/canonical-greekLit, archivo data/tlg0086/tlg035/tlg0086.tlg035.perseus-eng2.xml, commit dacb049. Licencia CC BY-SA 4.0. Texto extraído del TEI y normalizado; los cambios están en ATTRIBUTION.md.
- Estado: en espera (no se publica). Se indexa en la Etapa 9. Entra solo por la licencia que declara Perseus: la cabecera TEI fecha la impresión en 1944 (el plan da 1932 como primera edición), posterior a 1930; su dominio público en EE. UU. depende de la no renovación del copyright (decisión abierta 2 del plan). Sin capítulos: se cita por libro y página Bekker.

### Política — `pol.grc.ross`

- Obra: Política (Politics; Politica)
- Idioma: grc
- Traductor: texto original (sin traductor)
- Año de publicación: 1957
- Fuente: https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/dacb04909e43dc47738828d33cc3d2b0635bc54c/data/tlg0086/tlg035/tlg0086.tlg035.perseus-grc2.xml
- Versión fijada: `dacb04909e43dc47738828d33cc3d2b0635bc54c` · sha256 `2098a515dae41d04d2272dc999bf587ca3bb2f7b613f13664a5fa8827878a141`
- Licencia: CC BY-SA 4.0
- Evidencia de la licencia: Cabecera TEI del archivo (publicationStmt/availability): &lt;licence target="https://creativecommons.org/licenses/by-sa/4.0/"&gt;Available under a Creative Commons Attribution-ShareAlike (CC BY-SA) license.&lt;/licence&gt; y "Reuse must credit the Perseus Digital Library in a standard bibliographic format, including a link to https://www.perseus.tufts.edu/. Each text reused should be individually identified and linked to its source GitHub repository whenever possible, either in its citation or in an accompanying source list. All corrections must be offered to the Perseus Digital Library." README.md del repo en el commit fijado: "Unless otherwise indicated, all contents of this repository are licensed under a Creative Commons Attribution-ShareAlike 4.0 International License. You must offer Perseus any modifications you make."
- Crédito requerido: Aristóteles, Aristotelis Politica, ed. William David Ross, Oxford Classical Texts (Oxford: Clarendon Press, 1957). Edición digital: Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio PerseusDL/canonical-greekLit, archivo data/tlg0086/tlg035/tlg0086.tlg035.perseus-grc2.xml, commit dacb049. Licencia CC BY-SA 4.0. Solo se usa para derivar la numeración Bekker; el texto no se publica.
- Estado: en espera (no se publica). Se usa en la Etapa 9, solo como referencia Bekker (texto griego, no se indexa ni se publica).

### Metafísica — `metaph.en.tredennick`

- Obra: Metafísica (Metaphysics; Metaphysica)
- Idioma: en
- Traductor: Hugh Tredennick (m. 1981, a confirmar)
- Año de publicación: 1933
- Fuente: https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/dacb04909e43dc47738828d33cc3d2b0635bc54c/data/tlg0086/tlg025/tlg0086.tlg025.perseus-eng2.xml
- Versión fijada: `dacb04909e43dc47738828d33cc3d2b0635bc54c` · sha256 `7dacbcd178f338ec5f15ddf8fd3e262cb31af7c27c9af2a0294019cd281d4721`
- Licencia: CC BY-SA 4.0
- Evidencia de la licencia: Cabecera TEI del archivo (publicationStmt/availability): &lt;licence target="https://creativecommons.org/licenses/by-sa/4.0/"&gt;Available under a Creative Commons Attribution-ShareAlike (CC BY-SA) license.&lt;/licence&gt; y "Reuse must credit the Perseus Digital Library in a standard bibliographic format, including a link to https://www.perseus.tufts.edu/. Each text reused should be individually identified and linked to its source GitHub repository whenever possible, either in its citation or in an accompanying source list. All corrections must be offered to the Perseus Digital Library." README.md del repo en el commit fijado: "Unless otherwise indicated, all contents of this repository are licensed under a Creative Commons Attribution-ShareAlike 4.0 International License. You must offer Perseus any modifications you make."
- Crédito requerido: Aristóteles, The Metaphysics, trad. Hugh Tredennick, Loeb Classical Library, 2 vols. (Londres: William Heinemann; Cambridge, MA: Harvard University Press, 1933-1935). Edición digital: Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio PerseusDL/canonical-greekLit, archivo data/tlg0086/tlg025/tlg0086.tlg025.perseus-eng2.xml, commit dacb049. Licencia CC BY-SA 4.0. Texto extraído del TEI y normalizado; los cambios están en ATTRIBUTION.md.
- Estado: en espera (no se publica). Se indexa en la Etapa 9. Entra solo por la licencia que declara Perseus: no hay constancia de su estado en EE. UU. (1933-1935) y Tredennick habría muerto en 1981 (decisión abierta 2 del plan).

### Metafísica — `metaph.grc.ross`

- Obra: Metafísica (Metaphysics; Metaphysica)
- Idioma: grc
- Traductor: texto original (sin traductor)
- Año de publicación: 1924
- Fuente: https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/dacb04909e43dc47738828d33cc3d2b0635bc54c/data/tlg0086/tlg025/tlg0086.tlg025.perseus-grc2.xml
- Versión fijada: `dacb04909e43dc47738828d33cc3d2b0635bc54c` · sha256 `81df0f59585259ea5f5312b4e4540e7b64e659b04614e5c32187420b90ecd528`
- Licencia: CC BY-SA 4.0
- Evidencia de la licencia: Cabecera TEI del archivo (publicationStmt/availability): &lt;licence target="https://creativecommons.org/licenses/by-sa/4.0/"&gt;Available under a Creative Commons Attribution-ShareAlike (CC BY-SA) license.&lt;/licence&gt; y "Reuse must credit the Perseus Digital Library in a standard bibliographic format, including a link to https://www.perseus.tufts.edu/. Each text reused should be individually identified and linked to its source GitHub repository whenever possible, either in its citation or in an accompanying source list. All corrections must be offered to the Perseus Digital Library." README.md del repo en el commit fijado: "Unless otherwise indicated, all contents of this repository are licensed under a Creative Commons Attribution-ShareAlike 4.0 International License. You must offer Perseus any modifications you make."
- Crédito requerido: Aristóteles, Aristotle's Metaphysics, ed. William David Ross, 2 vols. (Oxford: Clarendon Press, 1924). Edición digital: Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio PerseusDL/canonical-greekLit, archivo data/tlg0086/tlg025/tlg0086.tlg025.perseus-grc2.xml, commit dacb049. Licencia CC BY-SA 4.0. Solo se usa para derivar la numeración Bekker; el texto no se publica.
- Estado: en espera (no se publica). Se usa en la Etapa 9, solo como referencia Bekker (texto griego, no se indexa ni se publica).

### Retórica — `rhet.en.freese`

- Obra: Retórica (Rhetoric; Rhetorica)
- Idioma: en
- Traductor: John Henry Freese (m. 1930, a confirmar)
- Año de publicación: 1926
- Fuente: https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/dacb04909e43dc47738828d33cc3d2b0635bc54c/data/tlg0086/tlg038/tlg0086.tlg038.perseus-eng2.xml
- Versión fijada: `dacb04909e43dc47738828d33cc3d2b0635bc54c` · sha256 `9b376a0eb82ceab13d4783e446a729240ff7c0468dd1c8a43bb48d4c48896bfd`
- Licencia: CC BY-SA 4.0
- Evidencia de la licencia: Cabecera TEI del archivo (publicationStmt/availability): &lt;licence target="https://creativecommons.org/licenses/by-sa/4.0/"&gt;Available under a Creative Commons Attribution-ShareAlike (CC BY-SA) license.&lt;/licence&gt; y "Reuse must credit the Perseus Digital Library in a standard bibliographic format, including a link to https://www.perseus.tufts.edu/. Each text reused should be individually identified and linked to its source GitHub repository whenever possible, either in its citation or in an accompanying source list. All corrections must be offered to the Perseus Digital Library." README.md del repo en el commit fijado: "Unless otherwise indicated, all contents of this repository are licensed under a Creative Commons Attribution-ShareAlike 4.0 International License. You must offer Perseus any modifications you make."
- Crédito requerido: Aristóteles, The "Art" of Rhetoric, trad. John Henry Freese, Loeb Classical Library (Londres: William Heinemann; Cambridge, MA: Harvard University Press, 1926). Edición digital: Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio PerseusDL/canonical-greekLit, archivo data/tlg0086/tlg038/tlg0086.tlg038.perseus-eng2.xml, commit dacb049. Licencia CC BY-SA 4.0. Texto extraído del TEI y normalizado; los cambios están en ATTRIBUTION.md.
- Estado: en espera (no se publica). Se indexa en la Etapa 9. Cumple también la vía de dominio público: publicada en 1926 y Freese murió en 1930 (a confirmar).

### Retórica — `rhet.grc.ross`

- Obra: Retórica (Rhetoric; Rhetorica)
- Idioma: grc
- Traductor: texto original (sin traductor)
- Año de publicación: 1959
- Fuente: https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/dacb04909e43dc47738828d33cc3d2b0635bc54c/data/tlg0086/tlg038/tlg0086.tlg038.perseus-grc2.xml
- Versión fijada: `dacb04909e43dc47738828d33cc3d2b0635bc54c` · sha256 `fae178ee6be622231d89b0208d98c7d141397a21f504270bfa040fb24fb2ccfd`
- Licencia: CC BY-SA 4.0
- Evidencia de la licencia: Cabecera TEI del archivo (publicationStmt/availability): &lt;licence target="https://creativecommons.org/licenses/by-sa/4.0/"&gt;Available under a Creative Commons Attribution-ShareAlike (CC BY-SA) license.&lt;/licence&gt; y "Reuse must credit the Perseus Digital Library in a standard bibliographic format, including a link to https://www.perseus.tufts.edu/. Each text reused should be individually identified and linked to its source GitHub repository whenever possible, either in its citation or in an accompanying source list. All corrections must be offered to the Perseus Digital Library." README.md del repo en el commit fijado: "Unless otherwise indicated, all contents of this repository are licensed under a Creative Commons Attribution-ShareAlike 4.0 International License. You must offer Perseus any modifications you make."
- Crédito requerido: Aristóteles, Aristotelis Ars Rhetorica, ed. William David Ross, Oxford Classical Texts (Oxford: Clarendon Press, 1959). Edición digital: Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio PerseusDL/canonical-greekLit, archivo data/tlg0086/tlg038/tlg0086.tlg038.perseus-grc2.xml, commit dacb049. Licencia CC BY-SA 4.0. Solo se usa para derivar la numeración Bekker; el texto no se publica.
- Estado: en espera (no se publica). Se usa en la Etapa 9, solo como referencia Bekker (texto griego, no se indexa ni se publica).

### Poética — `poet.en.fyfe`

- Obra: Poética (Poetics; Poetica)
- Idioma: en
- Traductor: William Hamilton Fyfe (m. 1965, a confirmar)
- Año de publicación: 1939
- Fuente: https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/dacb04909e43dc47738828d33cc3d2b0635bc54c/data/tlg0086/tlg034/tlg0086.tlg034.perseus-eng2.xml
- Versión fijada: `dacb04909e43dc47738828d33cc3d2b0635bc54c` · sha256 `590fc532d04c8c054edce5b7f2bba4a15028710623fcd2fc459a38fdf23e933b`
- Licencia: CC BY-SA 4.0
- Evidencia de la licencia: Cabecera TEI del archivo (publicationStmt/availability): &lt;licence target="https://creativecommons.org/licenses/by-sa/4.0/"&gt;Available under a Creative Commons Attribution-ShareAlike (CC BY-SA) license.&lt;/licence&gt; y "Reuse must credit the Perseus Digital Library in a standard bibliographic format, including a link to https://www.perseus.tufts.edu/. Each text reused should be individually identified and linked to its source GitHub repository whenever possible, either in its citation or in an accompanying source list. All corrections must be offered to the Perseus Digital Library." README.md del repo en el commit fijado: "Unless otherwise indicated, all contents of this repository are licensed under a Creative Commons Attribution-ShareAlike 4.0 International License. You must offer Perseus any modifications you make."
- Crédito requerido: Aristóteles, The Poetics (en Aristotle, The Poetics. "Longinus," On the Sublime. Demetrius, On Style), trad. William Hamilton Fyfe, Loeb Classical Library (Londres: William Heinemann; Cambridge, MA: Harvard University Press, 1939). Edición digital: Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio PerseusDL/canonical-greekLit, archivo data/tlg0086/tlg034/tlg0086.tlg034.perseus-eng2.xml, commit dacb049. Licencia CC BY-SA 4.0. Texto extraído del TEI y normalizado; los cambios están en ATTRIBUTION.md.
- Estado: en espera (no se publica). Se indexa en la Etapa 9. Entra solo por la licencia que declara Perseus: Fyfe murió en 1965 (a confirmar) y fuera de EE. UU. sigue protegida hasta 2036 (decisión abierta 2 del plan). La cabecera TEI fecha la impresión en 1939; el plan da 1927.

### Poética — `poet.grc.kassel`

- Obra: Poética (Poetics; Poetica)
- Idioma: grc
- Traductor: texto original (sin traductor)
- Año de publicación: 1965
- Fuente: https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/dacb04909e43dc47738828d33cc3d2b0635bc54c/data/tlg0086/tlg034/tlg0086.tlg034.perseus-grc2.xml
- Versión fijada: `dacb04909e43dc47738828d33cc3d2b0635bc54c` · sha256 `3bcb460e5ecb684a5929df7d5df3a1535a835520d930705366b7168f393196aa`
- Licencia: CC BY-SA 4.0
- Evidencia de la licencia: Cabecera TEI del archivo (publicationStmt/availability): &lt;licence target="https://creativecommons.org/licenses/by-sa/4.0/"&gt;Available under a Creative Commons Attribution-ShareAlike (CC BY-SA) license.&lt;/licence&gt; y "Reuse must credit the Perseus Digital Library in a standard bibliographic format, including a link to https://www.perseus.tufts.edu/. Each text reused should be individually identified and linked to its source GitHub repository whenever possible, either in its citation or in an accompanying source list. All corrections must be offered to the Perseus Digital Library." README.md del repo en el commit fijado: "Unless otherwise indicated, all contents of this repository are licensed under a Creative Commons Attribution-ShareAlike 4.0 International License. You must offer Perseus any modifications you make."
- Crédito requerido: Aristóteles, Aristotelis De arte poetica liber, ed. Rudolf Kassel (Oxford: Clarendon Press, 1965). Edición digital: Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio PerseusDL/canonical-greekLit, archivo data/tlg0086/tlg034/tlg0086.tlg034.perseus-grc2.xml, commit dacb049. Licencia CC BY-SA 4.0. Solo se usa para derivar la numeración Bekker; el texto no se publica.
- Estado: en espera (no se publica). Se usa en la Etapa 9, solo como referencia Bekker (texto griego, no se indexa ni se publica).

### Económicos — `oec.en.armstrong`

- Obra: Económicos (Economics; Oeconomica)
- Idioma: en
- Traductor: George Cyril Armstrong (año de muerte desconocido)
- Año de publicación: 1935
- Fuente: https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/dacb04909e43dc47738828d33cc3d2b0635bc54c/data/tlg0086/tlg029/tlg0086.tlg029.perseus-eng2.xml
- Versión fijada: `dacb04909e43dc47738828d33cc3d2b0635bc54c` · sha256 `88df30e40d0b70a3ed9196f98162a91eebef4e6434de40d996fe624b31d959ca`
- Licencia: CC BY-SA 4.0
- Evidencia de la licencia: Cabecera TEI del archivo (publicationStmt/availability): &lt;licence target="https://creativecommons.org/licenses/by-sa/4.0/"&gt;Available under a Creative Commons Attribution-ShareAlike (CC BY-SA) license.&lt;/licence&gt; y "Reuse must credit the Perseus Digital Library in a standard bibliographic format, including a link to https://www.perseus.tufts.edu/. Each text reused should be individually identified and linked to its source GitHub repository whenever possible, either in its citation or in an accompanying source list. All corrections must be offered to the Perseus Digital Library." README.md del repo en el commit fijado: "Unless otherwise indicated, all contents of this repository are licensed under a Creative Commons Attribution-ShareAlike 4.0 International License. You must offer Perseus any modifications you make."
- Crédito requerido: Aristóteles, Oeconomica (en The Metaphysics; The Oeconomica and The Magna Moralia), trad. George Cyril Armstrong, Loeb Classical Library (Londres: William Heinemann; Cambridge, MA: Harvard University Press, 1935). Edición digital: Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio PerseusDL/canonical-greekLit, archivo data/tlg0086/tlg029/tlg0086.tlg029.perseus-eng2.xml, commit dacb049. Licencia CC BY-SA 4.0. Texto extraído del TEI y normalizado; los cambios están en ATTRIBUTION.md.
- Estado: en espera (no se publica). Se indexa en la Etapa 9. Entra solo por la licencia que declara Perseus: no hay constancia de su estado en EE. UU. (1935) y el año de muerte de Armstrong es desconocido (decisión abierta 2 del plan). Obra dudosa: solo libros I y II.

### Económicos — `oec.grc.armstrong`

- Obra: Económicos (Economics; Oeconomica)
- Idioma: grc
- Traductor: texto original (sin traductor)
- Año de publicación: 1935
- Fuente: https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/dacb04909e43dc47738828d33cc3d2b0635bc54c/data/tlg0086/tlg029/tlg0086.tlg029.perseus-grc2.xml
- Versión fijada: `dacb04909e43dc47738828d33cc3d2b0635bc54c` · sha256 `0d2e57bbc903d541f5d8360c36ca59d1cd5307fffe180aa4740135a271a204d1`
- Licencia: CC BY-SA 4.0
- Evidencia de la licencia: Cabecera TEI del archivo (publicationStmt/availability): &lt;licence target="https://creativecommons.org/licenses/by-sa/4.0/"&gt;Available under a Creative Commons Attribution-ShareAlike (CC BY-SA) license.&lt;/licence&gt; y "Reuse must credit the Perseus Digital Library in a standard bibliographic format, including a link to https://www.perseus.tufts.edu/. Each text reused should be individually identified and linked to its source GitHub repository whenever possible, either in its citation or in an accompanying source list. All corrections must be offered to the Perseus Digital Library." README.md del repo en el commit fijado: "Unless otherwise indicated, all contents of this repository are licensed under a Creative Commons Attribution-ShareAlike 4.0 International License. You must offer Perseus any modifications you make."
- Crédito requerido: Aristóteles, Oeconomica, texto griego de la edición Loeb de George Cyril Armstrong (en The Metaphysics; The Oeconomica and The Magna Moralia; Londres: William Heinemann; Cambridge, MA: Harvard University Press, 1935). Edición digital: Perseus Digital Library, Tufts University (https://www.perseus.tufts.edu/), repositorio PerseusDL/canonical-greekLit, archivo data/tlg0086/tlg029/tlg0086.tlg029.perseus-grc2.xml, commit dacb049. Licencia CC BY-SA 4.0. Solo se usa para derivar la numeración Bekker; el texto no se publica.
- Estado: en espera (no se publica). Se usa en la Etapa 9, solo como referencia Bekker (texto griego, no se indexa ni se publica). Trae las mismas marcas de línea que el inglés (45), no cada 5 líneas, así que no afina los rangos de capítulo.

## Fixtures de tests en el repositorio

| archivo | edición | contenido | licencia |
|---|---|---|---|
| `tests/fixtures/perseus/ne_7_11_eng.xml` | `eth-nic.en.rackham` | Ética a Nicómaco, libro VII, capítulo 11 (1152b1–24), trad. Harris Rackham (1926). Copia literal del TEI de Perseus (commit dacb049) con la cabecera acortada. | CC BY-SA 4.0 |
| `tests/fixtures/perseus/ne_7_11_eng_nomarkers.xml` | `eth-nic.en.rackham` | El mismo capítulo sin sus marcas Bekker (se quitaron los elementos milestone). | CC BY-SA 4.0 |

## Respuestas de ejemplo del front

Todavía no hay (Etapa 8).
