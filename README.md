# Analizador Forense de Benford

Aplicación web construida con **Streamlit** para la detección de anomalías en
transacciones financieras aplicando la **Ley de Benford**, siguiendo la
metodología de auditoría forense de **Mark Nigrini**.

## Funcionalidades

1. **Carga de datos**: sube un archivo de transacciones en formato **CSV**,
   **CSV.GZ** (comprimido con gzip) o **ZIP** (con uno o más CSV dentro; si
   contiene varios, se puede elegir cuál usar). Elige la columna de montos
   a analizar y, opcionalmente, una columna binaria de etiqueta de riesgo
   (por ejemplo: legítima/fraude, 0/1, Sí/No).
2. **Preprocesamiento**: convierte los montos a valores absolutos y excluye
   automáticamente los montos menores a **USD 1,00**.
3. **Análisis de primer y segundo dígito**: compara las frecuencias
   observadas contra la distribución teórica de Benford.
4. **Estadísticos**:
   - **MAD** (Desviación Absoluta Media).
   - **Chi-cuadrado (χ²)** y su **valor p**.
   - **Z-score** por dígito, para identificar qué dígitos individuales se
     desvían de forma significativa.
5. **Gráficas comparativas** (observado vs. esperado) interactivas con
   Plotly, además de un gráfico de Z-scores por dígito.
6. **Análisis segmentado**: si se indica una columna de etiqueta de riesgo,
   se muestran lado a lado los resultados de las transacciones
   **legítimas** vs. las de **riesgo**, incluyendo el **incremento
   porcentual del MAD** del grupo de riesgo respecto al legítimo.
7. **Veredicto de conformidad** según los rangos de MAD definidos por
   Nigrini, tanto para el análisis global como para cada segmento.
8. **Modo "Comparación de subconjuntos"**: en lugar de un único archivo con
   etiqueta, permite cargar hasta tres archivos independientes — "Conjunto
   Válido (global)", "Transacciones Legítimas" y "Transacciones de
   Lavado" — cada uno con su propia columna de montos. Con dos o más
   conjuntos cargados se genera una tabla comparativa (MAD, Chi-cuadrado,
   valor p y veredicto para primer y segundo dígito) que resalta
   visualmente el conjunto de Lavado, además del incremento porcentual de
   su MAD respecto al de Legítimas.
9. **Bajo consumo de memoria**: los archivos se leen y procesan **por
   chunks** (nunca se carga el CSV completo en un DataFrame), pensado para
   soportar archivos de cientos de MB con millones de filas incluso en
   entornos con poca RAM (por ejemplo, Streamlit Community Cloud, ~1 GB).

## Rangos de conformidad de MAD (Nigrini)

**Primer dígito:**

| MAD             | Conformidad                                  |
|-----------------|-----------------------------------------------|
| 0.000 – 0.006    | Conformidad aceptable                          |
| 0.006 – 0.012    | Conformidad aceptable, asociación marginal     |
| 0.012 – 0.015    | Desviación no conforme, asociación marcada     |
| > 0.015          | No conformidad, asociación grave               |

**Segundo dígito:**

| MAD             | Conformidad                                  |
|-----------------|-----------------------------------------------|
| 0.000 – 0.008    | Conformidad aceptable                          |
| 0.008 – 0.010    | Conformidad aceptable, asociación marginal     |
| 0.010 – 0.012    | Desviación no conforme, asociación marcada     |
| > 0.012          | No conformidad, asociación grave               |

## Requisitos

- Python 3.9 o superior

## Instalación

```bash
# (Opcional) crear un entorno virtual
python3 -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt
```

## Ejecución

```bash
streamlit run app.py
```

La aplicación se abrirá automáticamente en tu navegador, normalmente en
`http://localhost:8501`.

## Formato esperado del archivo

El archivo puede subirse como **.csv**, **.csv.gz** o **.zip** (con uno o
más CSV dentro) y debe tener encabezados de columna, incluyendo como
mínimo una columna numérica con los montos de las transacciones. Ejemplo:

```csv
id_transaccion,fecha,monto,etiqueta_riesgo
1,2024-01-01,1234.56,Legitima
2,2024-01-01,4999.00,Riesgo
3,2024-01-02,87.30,Legitima
```

- La columna de **montos** puede contener valores positivos o negativos
  (se toma el valor absoluto) y se excluyen automáticamente los montos
  menores a USD 1,00.
- La columna de **etiqueta de riesgo** (opcional, solo en el modo de
  archivo único) puede tener cualquier par de valores (texto o numéricos);
  en la interfaz se selecciona cuál de los dos valores representa "riesgo".

## Modo "Comparación de subconjuntos"

Como alternativa al archivo único con etiqueta, el modo de comparación
permite subir hasta tres archivos por separado, cada uno ya segmentado:

- **Conjunto Válido (global)**
- **Transacciones Legítimas**
- **Transacciones de Lavado**

Cada archivo puede tener su propia columna de montos. Con al menos dos
conjuntos cargados se muestra una tabla comparativa (MAD, Chi-cuadrado,
valor p y veredicto para primer y segundo dígito), resaltando la fila de
"Transacciones de Lavado", y — si se cargaron "Legítimas" y "Lavado" — el
incremento porcentual del MAD de Lavado respecto a Legítimas.

## Procesamiento por chunks y consumo de memoria

Para soportar archivos de cientos de MB con millones de filas sin agotar la
memoria disponible (por ejemplo, en Streamlit Community Cloud, con ~1 GB de
RAM), la aplicación **nunca carga el archivo completo en un DataFrame**:

- El selector de columnas se llena leyendo solo el **encabezado y ~20 filas
  de muestra** (`pandas.read_csv(..., nrows=20)`), no el archivo entero.
- El análisis completo lee el archivo con `pandas.read_csv(..., usecols=[...],
  chunksize=...)`, extrayendo únicamente la(s) columna(s) necesarias
  (montos y, si aplica, etiqueta) y descartando el resto.
- Por cada chunk solo se acumulan **conteos de primer/segundo dígito**
  (arreglos de tamaño fijo, 9 y 10 posiciones) — nunca se retiene la serie
  completa de montos ni el archivo completo en memoria. El resultado
  estadístico final (MAD, Chi-cuadrado, Z-scores) es matemáticamente
  idéntico a analizar el archivo de una sola vez.
- Tras procesar cada archivo se liberan explícitamente los acumuladores
  (`del` + `gc.collect()`).
- El tamaño de subida máximo por defecto se amplía a 1 GB vía
  `.streamlit/config.toml` (`server.maxUploadSize`); ajústalo según tu
  entorno de despliegue.
- El tamaño de chunk (`TAMANO_CHUNK` en `app.py`, 200.000 filas por
  defecto) puede reducirse si tu entorno tiene menos memoria disponible.

## Estructura del proyecto

```
.
├── app.py                    # Interfaz Streamlit (lectura y procesamiento por chunks)
├── benford.py                # Lógica estadística (Ley de Benford, MAD, Chi², Z-scores, acumulador incremental)
├── requirements.txt          # Dependencias
├── .streamlit/config.toml    # Configuración de Streamlit (tamaño máximo de subida)
└── README.md
```

## Referencia metodológica

Nigrini, M. J. (2012). *Benford's Law: Applications for Forensic Accounting,
Auditing, and Fraud Detection*. John Wiley & Sons.
