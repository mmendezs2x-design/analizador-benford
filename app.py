"""
Analizador Forense de Benford
Aplicación Streamlit para detección de anomalías en transacciones financieras
basada en la metodología de Nigrini (análisis de primer y segundo dígito).

Optimizada para bajo consumo de memoria: los archivos se leen y procesan por
chunks, extrayendo únicamente la columna de montos (y, si aplica, la de
etiqueta) y acumulando solo conteos de dígitos — nunca se retiene el archivo
completo ni la serie completa de montos en memoria.
"""

import gc
import gzip
import zipfile

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from benford import AcumuladorDigitos

st.set_page_config(
    page_title="Analizador Forense de Benford",
    page_icon="🔍",
    layout="wide",
)

UMBRAL_MINIMO = 1.00
TIPOS_ARCHIVO_ACEPTADOS = ["csv", "gz", "zip"]
FILAS_MUESTRA = 20
TAMANO_CHUNK = 200_000
LIMITE_VALORES_UNICOS = 50


# ------------------------- Lectura de archivos por chunks -------------------------

def abrir_flujo_csv(archivo, key_prefix: str = "", zip_interno: str | None = None):
    """Abre un flujo de lectura para un archivo .csv/.csv.gz/.zip sin cargarlo
    completo en memoria. Devuelve (flujo, nombre_zip_interno); el flujo debe
    cerrarse con `cerrar_flujo` tras su uso (salvo que sea el propio `archivo`)."""
    nombre = archivo.name.lower()
    archivo.seek(0)

    if nombre.endswith(".zip"):
        zf = zipfile.ZipFile(archivo)
        csvs = [
            n for n in zf.namelist()
            if n.lower().endswith(".csv") and not n.startswith("__MACOSX")
        ]
        if not csvs:
            raise ValueError("El archivo ZIP no contiene ningún archivo CSV.")
        if zip_interno is not None:
            nombre_csv = zip_interno
        elif len(csvs) == 1:
            nombre_csv = csvs[0]
        else:
            nombre_csv = st.selectbox(
                "El ZIP contiene varios archivos CSV, elige cuál usar:",
                csvs,
                key=f"{key_prefix}_zip_select",
            )
        return zf.open(nombre_csv), nombre_csv

    if nombre.endswith(".gz"):
        return gzip.GzipFile(fileobj=archivo), None

    return archivo, None


def cerrar_flujo(flujo, archivo):
    if flujo is not archivo and hasattr(flujo, "close"):
        flujo.close()


def leer_muestra(archivo, sep: str, decimal: str, key_prefix: str = ""):
    """Lee solo el encabezado y unas pocas filas de muestra (no el archivo
    completo), para poblar los selectores de columnas."""
    flujo, zip_interno = abrir_flujo_csv(archivo, key_prefix=key_prefix)
    try:
        muestra = pd.read_csv(flujo, sep=sep, decimal=decimal, nrows=FILAS_MUESTRA)
    finally:
        cerrar_flujo(flujo, archivo)
    return muestra, zip_interno


def valores_unicos_columna(archivo, sep, decimal, columna, key_prefix, zip_interno, limite=LIMITE_VALORES_UNICOS):
    """Escanea el archivo por chunks para obtener los valores únicos de una
    columna, sin cargar el archivo completo en memoria.

    Deliberadamente sin `st.cache_data`: el hashing de Streamlit sobre un
    `UploadedFile` grande puede terminar copiando el archivo completo en
    memoria para calcular la clave de caché, lo cual sería contraproducente
    aquí. Volver a escanear una sola columna por chunks es barato."""
    flujo, _ = abrir_flujo_csv(archivo, key_prefix=key_prefix, zip_interno=zip_interno)
    valores = set()
    try:
        with pd.read_csv(flujo, sep=sep, decimal=decimal, usecols=[columna], chunksize=TAMANO_CHUNK) as lector:
            for chunk in lector:
                valores.update(chunk[columna].dropna().unique().tolist())
                del chunk
                if len(valores) > limite:
                    break
    finally:
        cerrar_flujo(flujo, archivo)
    gc.collect()
    return valores


def procesar_montos_en_chunks(archivo, sep, decimal, col_monto, key_prefix, zip_interno):
    """Procesa el archivo completo por chunks, extrayendo solo la columna de
    montos y acumulando únicamente conteos de dígitos."""
    flujo, _ = abrir_flujo_csv(archivo, key_prefix=key_prefix, zip_interno=zip_interno)
    acumulador = AcumuladorDigitos(UMBRAL_MINIMO)
    try:
        with pd.read_csv(flujo, sep=sep, decimal=decimal, usecols=[col_monto], chunksize=TAMANO_CHUNK) as lector:
            for chunk in lector:
                acumulador.procesar_chunk(chunk[col_monto])
                del chunk
    finally:
        cerrar_flujo(flujo, archivo)

    resultado = acumulador.finalizar()
    n_original = acumulador.n_original
    del acumulador
    gc.collect()
    return resultado, n_original


def procesar_segmentado_en_chunks(archivo, sep, decimal, col_monto, col_etiqueta, valor_riesgo, key_prefix, zip_interno):
    """Procesa el archivo completo por chunks, separando legítimas vs. riesgo
    y acumulando únicamente conteos de dígitos para cada grupo."""
    flujo, _ = abrir_flujo_csv(archivo, key_prefix=key_prefix, zip_interno=zip_interno)
    acum_global = AcumuladorDigitos(UMBRAL_MINIMO)
    acum_legitimas = AcumuladorDigitos(UMBRAL_MINIMO)
    acum_riesgo = AcumuladorDigitos(UMBRAL_MINIMO)
    try:
        with pd.read_csv(
            flujo, sep=sep, decimal=decimal, usecols=[col_monto, col_etiqueta], chunksize=TAMANO_CHUNK
        ) as lector:
            for chunk in lector:
                acum_global.procesar_chunk(chunk[col_monto])
                es_riesgo = chunk[col_etiqueta] == valor_riesgo
                acum_riesgo.procesar_chunk(chunk.loc[es_riesgo, col_monto])
                acum_legitimas.procesar_chunk(chunk.loc[~es_riesgo, col_monto])
                del chunk, es_riesgo
    finally:
        cerrar_flujo(flujo, archivo)

    resultado_global = acum_global.finalizar()
    resultado_legitimas = acum_legitimas.finalizar() if acum_legitimas.n_valido_primer > 0 else None
    resultado_riesgo = acum_riesgo.finalizar() if acum_riesgo.n_valido_primer > 0 else None
    n_original = acum_global.n_original

    del acum_global, acum_legitimas, acum_riesgo
    gc.collect()
    return resultado_global, resultado_legitimas, resultado_riesgo, n_original


def incremento_pct(base: float, nuevo: float) -> float:
    if base == 0 or np.isnan(base):
        return np.nan
    return (nuevo - base) / base * 100


# ------------------------- Visualización -------------------------

def grafico_comparativo(tabla: pd.DataFrame, titulo: str, x_titulo: str):
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=tabla["digito"],
        y=tabla["freq_observada"],
        name="Frecuencia observada",
        marker_color="#1f77b4",
    ))
    fig.add_trace(go.Scatter(
        x=tabla["digito"],
        y=tabla["freq_esperada"],
        name="Distribución de Benford (esperada)",
        mode="lines+markers",
        line=dict(color="#d62728", width=2),
        marker=dict(size=7),
    ))
    fig.update_layout(
        title=titulo,
        xaxis_title=x_titulo,
        yaxis_title="Frecuencia relativa",
        xaxis=dict(tickmode="linear"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        bargap=0.2,
        height=430,
    )
    return fig


def grafico_zscore(tabla: pd.DataFrame, x_titulo: str, umbral: float = 1.96):
    colores = ["#d62728" if z > umbral else "#2ca02c" for z in tabla["z_score"]]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=tabla["digito"],
        y=tabla["z_score"],
        marker_color=colores,
        name="Z-score",
    ))
    fig.add_hline(y=umbral, line_dash="dash", line_color="gray",
                  annotation_text=f"Umbral crítico (±{umbral})")
    fig.update_layout(
        title="Z-score por dígito (significancia individual, α=0.05)",
        xaxis_title=x_titulo,
        yaxis_title="Z-score",
        xaxis=dict(tickmode="linear"),
        height=350,
    )
    return fig


def mostrar_metricas(resultado_digito: dict, etiqueta: str):
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("N (observaciones)", f"{resultado_digito['n']:,}")
    col2.metric("MAD", f"{resultado_digito['mad']:.5f}")
    col3.metric("Chi-cuadrado (χ²)", f"{resultado_digito['chi2']:.3f}")
    col4.metric("Valor p", f"{resultado_digito['p_valor']:.5f}")

    nivel = resultado_digito["nivel"]
    mensaje = f"**Veredicto ({etiqueta}):** {resultado_digito['veredicto']}"
    if nivel == "success":
        st.success(mensaje)
    elif nivel == "info":
        st.info(mensaje)
    elif nivel == "warning":
        st.warning(mensaje)
    else:
        st.error(mensaje)


def mostrar_analisis(resultado: dict):
    tab1, tab2 = st.tabs(["📊 Primer dígito", "📊 Segundo dígito"])

    with tab1:
        r1 = resultado["primer_digito"]
        mostrar_metricas(r1, "Primer dígito")
        c1, c2 = st.columns([2, 1])
        with c1:
            st.plotly_chart(grafico_comparativo(
                r1["tabla"], "Primer dígito: Observado vs. Benford", "Primer dígito"
            ), use_container_width=True)
        with c2:
            st.plotly_chart(grafico_zscore(r1["tabla"], "Primer dígito"), use_container_width=True)
        with st.expander("Ver tabla de datos — primer dígito"):
            st.dataframe(
                r1["tabla"].style.format({
                    "freq_observada": "{:.4%}",
                    "freq_esperada": "{:.4%}",
                    "conteo_esperado": "{:.1f}",
                    "z_score": "{:.3f}",
                }),
                use_container_width=True,
            )

    with tab2:
        r2 = resultado["segundo_digito"]
        mostrar_metricas(r2, "Segundo dígito")
        c1, c2 = st.columns([2, 1])
        with c1:
            st.plotly_chart(grafico_comparativo(
                r2["tabla"], "Segundo dígito: Observado vs. Benford", "Segundo dígito"
            ), use_container_width=True)
        with c2:
            st.plotly_chart(grafico_zscore(r2["tabla"], "Segundo dígito"), use_container_width=True)
        with st.expander("Ver tabla de datos — segundo dígito"):
            st.dataframe(
                r2["tabla"].style.format({
                    "freq_observada": "{:.4%}",
                    "freq_esperada": "{:.4%}",
                    "conteo_esperado": "{:.1f}",
                    "z_score": "{:.3f}",
                }),
                use_container_width=True,
            )


# ------------------------- Modo: comparación de subconjuntos -------------------------

def modo_comparacion_subconjuntos(separador: str, decimal: str):
    st.header("🧪 Comparación de subconjuntos")
    st.markdown(
        "Carga por separado hasta tres conjuntos de transacciones (cada uno en "
        "**CSV**, **CSV.GZ** o **ZIP**) para comparar su conformidad con la Ley "
        "de Benford. Se necesitan al menos **dos** conjuntos cargados para "
        "generar la comparación. Cada archivo se procesa por chunks, sin "
        "cargarlo completo en memoria."
    )

    slots = ["Conjunto Válido (global)", "Transacciones Legítimas", "Transacciones de Lavado"]
    claves = ["valido", "legitimas", "lavado"]

    columnas_layout = st.columns(3)
    config_subconjuntos = {}

    for columna, nombre, clave in zip(columnas_layout, slots, claves):
        with columna:
            st.subheader(nombre)
            archivo = st.file_uploader(
                "Subir archivo", type=TIPOS_ARCHIVO_ACEPTADOS, key=f"upload_{clave}"
            )
            if archivo is None:
                continue
            try:
                muestra, zip_interno = leer_muestra(archivo, separador, decimal, key_prefix=clave)
            except Exception as e:
                st.error(f"No se pudo leer el archivo: {e}")
                continue
            if muestra.empty:
                st.error("El archivo está vacío.")
                continue

            col_monto = st.selectbox(
                "Columna de montos", list(muestra.columns), key=f"col_monto_{clave}"
            )
            st.caption(f"Vista previa de {len(muestra)} fila(s). El total se calculará al ejecutar.")

            config_subconjuntos[nombre] = {
                "archivo": archivo,
                "col_monto": col_monto,
                "zip_interno": zip_interno,
                "clave": clave,
            }

    if len(config_subconjuntos) < 2:
        st.info("Carga al menos dos conjuntos para ver la tabla comparativa.")
        return

    ejecutar = st.button("🚀 Ejecutar comparación", type="primary")
    if not ejecutar:
        return

    st.markdown("---")
    subconjuntos = {}
    with st.spinner("Procesando archivos por chunks (esto puede tardar para archivos grandes)..."):
        for nombre, cfg in config_subconjuntos.items():
            resultado, n_original = procesar_montos_en_chunks(
                cfg["archivo"], separador, decimal, cfg["col_monto"],
                key_prefix=cfg["clave"], zip_interno=cfg["zip_interno"],
            )
            n_validos = resultado["primer_digito"]["n"]
            st.caption(f"**{nombre}**: {n_validos:,} registros válidos de {n_original:,} totales.")
            if n_validos == 0:
                st.warning(f"'{nombre}': no quedan registros válidos tras el preprocesamiento.")
                continue
            if n_validos < 30:
                st.warning(f"'{nombre}': menos de 30 observaciones válidas ({n_validos}); resultados poco confiables.")
            subconjuntos[nombre] = resultado

    if len(subconjuntos) < 2:
        st.info("Se necesitan al menos dos conjuntos con datos válidos para comparar.")
        return

    st.subheader("📊 Tabla comparativa de conformidad")

    def resaltar_lavado(fila):
        if fila["Conjunto"] == "Transacciones de Lavado":
            return ["background-color: rgba(214, 39, 40, 0.18)"] * len(fila)
        return [""] * len(fila)

    for etiqueta_digito, clave_digito in [
        ("Primer dígito", "primer_digito"),
        ("Segundo dígito", "segundo_digito"),
    ]:
        st.markdown(f"#### {etiqueta_digito}")
        filas = []
        for nombre, resultado in subconjuntos.items():
            r = resultado[clave_digito]
            filas.append({
                "Conjunto": nombre,
                "N": r["n"],
                "MAD": r["mad"],
                "Chi-cuadrado": r["chi2"],
                "Valor p": r["p_valor"],
                "Veredicto": r["veredicto"],
            })
        tabla_comp = pd.DataFrame(filas)
        st.dataframe(
            tabla_comp.style.apply(resaltar_lavado, axis=1).format({
                "MAD": "{:.5f}",
                "Chi-cuadrado": "{:.3f}",
                "Valor p": "{:.5f}",
            }),
            use_container_width=True,
        )

    if "Transacciones Legítimas" in subconjuntos and "Transacciones de Lavado" in subconjuntos:
        st.markdown("---")
        st.subheader("🚩 Incremento del MAD: Transacciones de Lavado vs. Legítimas")

        mad_leg_1 = subconjuntos["Transacciones Legítimas"]["primer_digito"]["mad"]
        mad_lav_1 = subconjuntos["Transacciones de Lavado"]["primer_digito"]["mad"]
        mad_leg_2 = subconjuntos["Transacciones Legítimas"]["segundo_digito"]["mad"]
        mad_lav_2 = subconjuntos["Transacciones de Lavado"]["segundo_digito"]["mad"]

        inc_1 = incremento_pct(mad_leg_1, mad_lav_1)
        inc_2 = incremento_pct(mad_leg_2, mad_lav_2)

        c1, c2 = st.columns(2)
        with c1:
            st.metric("MAD primer dígito — Legítimas", f"{mad_leg_1:.5f}")
            st.metric(
                "MAD primer dígito — Lavado",
                f"{mad_lav_1:.5f}",
                delta=f"{inc_1:+.1f}%" if not np.isnan(inc_1) else "N/D",
                delta_color="inverse",
            )
        with c2:
            st.metric("MAD segundo dígito — Legítimas", f"{mad_leg_2:.5f}")
            st.metric(
                "MAD segundo dígito — Lavado",
                f"{mad_lav_2:.5f}",
                delta=f"{inc_2:+.1f}%" if not np.isnan(inc_2) else "N/D",
                delta_color="inverse",
            )

        if not np.isnan(inc_1) and inc_1 > 0:
            st.error(
                f"🚨 El conjunto de **Transacciones de Lavado** presenta un MAD de "
                f"primer dígito un **{inc_1:.1f}%** más alto que el de **Transacciones "
                "Legítimas**, lo que indica una desviación mucho mayor respecto a la "
                "Ley de Benford y constituye una señal de alerta de posible manipulación."
            )
        elif not np.isnan(inc_1):
            st.info(
                "El conjunto de Lavado no muestra un MAD de primer dígito mayor que "
                "el de Legítimas en esta comparación."
            )

    for nombre, resultado in subconjuntos.items():
        with st.expander(f"Ver detalle completo — {nombre}"):
            mostrar_analisis(resultado)


# ------------------------- Interfaz principal -------------------------

st.title("🔍 Analizador Forense de Benford")
st.markdown(
    "Herramienta de auditoría forense para detectar anomalías en transacciones "
    "financieras aplicando la **Ley de Benford** con la metodología de "
    "**Mark Nigrini** (análisis de primer y segundo dígito, MAD, Chi-cuadrado y Z-scores). "
    "Los archivos se procesan por chunks para soportar CSV de gran tamaño con bajo consumo de memoria."
)

with st.sidebar:
    st.header("⚙️ Configuración")
    modo = st.radio(
        "Modo de análisis",
        ["Archivo único (con etiqueta opcional)", "Comparación de subconjuntos"],
    )
    separador = st.selectbox("Separador de columnas", [",", ";", "\t", "|"], index=0)
    decimal = st.selectbox("Separador decimal", [".", ","], index=0)

    archivo = None
    if modo == "Archivo único (con etiqueta opcional)":
        archivo = st.file_uploader(
            "Sube un archivo de transacciones (CSV, CSV.GZ o ZIP)",
            type=TIPOS_ARCHIVO_ACEPTADOS,
        )

if modo == "Comparación de subconjuntos":
    modo_comparacion_subconjuntos(separador, decimal)
    st.stop()

if archivo is None:
    st.info("👈 Sube un archivo en el panel lateral para comenzar el análisis.")
    st.markdown(
        """
        **Requisitos del archivo:**
        - Formato **CSV**, **CSV.GZ** o **ZIP** (con un CSV dentro), con encabezados de columna.
        - Debe incluir una columna numérica con los **montos** de las transacciones.
        - Opcionalmente, una columna binaria que etiquete cada transacción como
          *legítima* o *de riesgo/fraude* (por ejemplo: 0/1, "Sí"/"No", "Riesgo"/"Legítima").
        - Puede tener millones de filas: se lee y procesa por chunks, sin
          cargarlo completo en memoria.
        """
    )
    st.stop()

try:
    muestra, zip_interno = leer_muestra(archivo, separador, decimal, key_prefix="unico")
except Exception as e:
    st.error(f"No se pudo leer el archivo: {e}")
    st.stop()

if muestra.empty:
    st.error("El archivo está vacío.")
    st.stop()

st.subheader("Vista previa de los datos")
st.dataframe(muestra, use_container_width=True)
st.caption(
    f"Mostrando las primeras {len(muestra):,} fila(s) como vista previa (el archivo "
    "no se carga completo en memoria; el número total de registros se calculará "
    "al ejecutar el análisis)."
)

columnas = list(muestra.columns)

col_a, col_b = st.columns(2)
with col_a:
    col_monto = st.selectbox("Columna de montos (obligatoria)", columnas)
with col_b:
    opciones_etiqueta = ["(Ninguna)"] + columnas
    col_etiqueta = st.selectbox(
        "Columna de etiqueta de riesgo (opcional)", opciones_etiqueta
    )
    col_etiqueta = None if col_etiqueta == "(Ninguna)" else col_etiqueta

valor_riesgo = None
if col_etiqueta:
    with st.spinner("Escaneando valores únicos de la columna de etiqueta..."):
        valores_unicos = valores_unicos_columna(
            archivo, separador, decimal, col_etiqueta, "unico", zip_interno
        )
    if len(valores_unicos) != 2:
        st.warning(
            f"La columna '{col_etiqueta}' tiene {len(valores_unicos)}"
            f"{'+' if len(valores_unicos) > LIMITE_VALORES_UNICOS else ''} valores únicos. "
            "Se espera una columna binaria (2 valores). Elige cuál representa 'riesgo'."
        )
    valor_riesgo = st.selectbox(
        "¿Qué valor de la columna de etiqueta representa transacciones de RIESGO?",
        sorted(valores_unicos, key=str),
    )

ejecutar = st.button("🚀 Ejecutar análisis de Benford", type="primary")

if not ejecutar:
    st.stop()

st.markdown("---")
with st.spinner("Procesando archivo por chunks (esto puede tardar para archivos grandes)..."):
    if col_etiqueta:
        resultado_global, resultado_legitimas, resultado_riesgo, n_original = procesar_segmentado_en_chunks(
            archivo, separador, decimal, col_monto, col_etiqueta, valor_riesgo,
            key_prefix="unico", zip_interno=zip_interno,
        )
    else:
        resultado_global, n_original = procesar_montos_en_chunks(
            archivo, separador, decimal, col_monto, key_prefix="unico", zip_interno=zip_interno,
        )
        resultado_legitimas = resultado_riesgo = None

n_validos = resultado_global["primer_digito"]["n"]
n_excluidos = n_original - n_validos

st.subheader("🧹 Preprocesamiento de datos")
c1, c2, c3 = st.columns(3)
c1.metric("Registros totales", f"{n_original:,}")
c2.metric(f"Excluidos (< USD {UMBRAL_MINIMO:.2f} o no numéricos)", f"{n_excluidos:,}")
c3.metric("Registros válidos para el análisis", f"{n_validos:,}")

if n_validos < 30:
    st.warning(
        "El número de observaciones válidas es muy bajo (< 30). "
        "Los resultados estadísticos pueden no ser confiables."
    )

if n_validos == 0:
    st.error("No quedan registros válidos tras el preprocesamiento.")
    st.stop()

# --- Análisis global ---
st.markdown("---")
st.header("📈 Análisis global (toda la muestra)")
mostrar_analisis(resultado_global)

# --- Análisis segmentado (legítimas vs. riesgo) ---
if col_etiqueta:
    st.markdown("---")
    st.header("⚖️ Análisis segmentado: legítimas vs. riesgo")

    n_legitimas = resultado_legitimas["primer_digito"]["n"] if resultado_legitimas else 0
    n_riesgo = resultado_riesgo["primer_digito"]["n"] if resultado_riesgo else 0

    if n_riesgo < 10 or n_legitimas < 10:
        st.warning(
            "Uno de los dos grupos tiene menos de 10 observaciones válidas; "
            "los resultados segmentados pueden no ser estadísticamente confiables."
        )

    col_leg, col_riesgo = st.columns(2)

    with col_leg:
        st.subheader(f"✅ Legítimas (n = {n_legitimas:,})")
        if resultado_legitimas:
            mostrar_analisis(resultado_legitimas)
        else:
            st.info("Sin observaciones en este grupo.")

    with col_riesgo:
        st.subheader(f"🚨 Riesgo (n = {n_riesgo:,})")
        if resultado_riesgo:
            mostrar_analisis(resultado_riesgo)
        else:
            st.info("Sin observaciones en este grupo.")

    if resultado_legitimas and resultado_riesgo:
        st.markdown("---")
        st.subheader("📐 Incremento porcentual del MAD (riesgo vs. legítimas)")

        mad_leg_1 = resultado_legitimas["primer_digito"]["mad"]
        mad_riesgo_1 = resultado_riesgo["primer_digito"]["mad"]
        mad_leg_2 = resultado_legitimas["segundo_digito"]["mad"]
        mad_riesgo_2 = resultado_riesgo["segundo_digito"]["mad"]

        inc_1 = incremento_pct(mad_leg_1, mad_riesgo_1)
        inc_2 = incremento_pct(mad_leg_2, mad_riesgo_2)

        c1, c2 = st.columns(2)
        with c1:
            st.metric(
                "MAD primer dígito — Legítimas",
                f"{mad_leg_1:.5f}",
            )
            st.metric(
                "MAD primer dígito — Riesgo",
                f"{mad_riesgo_1:.5f}",
                delta=f"{inc_1:+.1f}%" if not np.isnan(inc_1) else "N/D",
                delta_color="inverse",
            )
        with c2:
            st.metric(
                "MAD segundo dígito — Legítimas",
                f"{mad_leg_2:.5f}",
            )
            st.metric(
                "MAD segundo dígito — Riesgo",
                f"{mad_riesgo_2:.5f}",
                delta=f"{inc_2:+.1f}%" if not np.isnan(inc_2) else "N/D",
                delta_color="inverse",
            )

        if inc_1 is not None and not np.isnan(inc_1) and inc_1 > 0:
            st.warning(
                f"El grupo de **riesgo** presenta un MAD de primer dígito un "
                f"**{inc_1:.1f}%** más alto que el grupo de transacciones legítimas, "
                "lo que sugiere una mayor desviación respecto a la Ley de Benford "
                "y podría indicar manipulación o fraude."
            )
        elif not np.isnan(inc_1):
            st.info(
                "El grupo de riesgo no muestra un MAD mayor que el de las transacciones "
                "legítimas en esta muestra."
            )

st.markdown("---")
st.caption(
    "Metodología basada en Nigrini, M. (2012). *Benford's Law: Applications for "
    "Forensic Accounting, Auditing, and Fraud Detection*. Wiley."
)
