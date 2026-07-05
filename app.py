"""
Analizador Forense de Benford
Aplicación Streamlit para detección de anomalías en transacciones financieras
basada en la metodología de Nigrini (análisis de primer y segundo dígito).
"""

import gzip
import io
import zipfile

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from benford import (
    DIST_PRIMER_DIGITO,
    DIST_SEGUNDO_DIGITO,
    analisis_completo,
    calcular_mad,
    preprocesar_montos,
)

st.set_page_config(
    page_title="Analizador Forense de Benford",
    page_icon="🔍",
    layout="wide",
)

UMBRAL_MINIMO = 1.00
TIPOS_ARCHIVO_ACEPTADOS = ["csv", "gz", "zip"]


def leer_csv_subido(archivo, sep: str, decimal: str, key_prefix: str = "") -> pd.DataFrame:
    """Lee un archivo subido en formato .csv, .csv.gz o .zip (con uno o más CSV)."""
    nombre = archivo.name.lower()
    contenido = archivo.read()

    if nombre.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
            csvs = [
                n for n in zf.namelist()
                if n.lower().endswith(".csv") and not n.startswith("__MACOSX")
            ]
            if not csvs:
                raise ValueError("El archivo ZIP no contiene ningún archivo CSV.")
            if len(csvs) == 1:
                nombre_csv = csvs[0]
            else:
                nombre_csv = st.selectbox(
                    "El ZIP contiene varios archivos CSV, elige cuál usar:",
                    csvs,
                    key=f"{key_prefix}_zip_select",
                )
            with zf.open(nombre_csv) as f:
                return pd.read_csv(f, sep=sep, decimal=decimal)
    elif nombre.endswith(".gz"):
        with gzip.GzipFile(fileobj=io.BytesIO(contenido)) as f:
            return pd.read_csv(f, sep=sep, decimal=decimal)
    else:
        return pd.read_csv(io.BytesIO(contenido), sep=sep, decimal=decimal)


def incremento_pct(base: float, nuevo: float) -> float:
    if base == 0 or np.isnan(base):
        return np.nan
    return (nuevo - base) / base * 100


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


def modo_comparacion_subconjuntos(separador: str, decimal: str):
    st.header("🧪 Comparación de subconjuntos")
    st.markdown(
        "Carga por separado hasta tres conjuntos de transacciones (cada uno en "
        "**CSV**, **CSV.GZ** o **ZIP**) para comparar su conformidad con la Ley "
        "de Benford. Se necesitan al menos **dos** conjuntos cargados para "
        "generar la comparación."
    )

    slots = ["Conjunto Válido (global)", "Transacciones Legítimas", "Transacciones de Lavado"]
    claves = ["valido", "legitimas", "lavado"]

    columnas_layout = st.columns(3)
    subconjuntos = {}

    for columna, nombre, clave in zip(columnas_layout, slots, claves):
        with columna:
            st.subheader(nombre)
            archivo = st.file_uploader(
                "Subir archivo", type=TIPOS_ARCHIVO_ACEPTADOS, key=f"upload_{clave}"
            )
            if archivo is None:
                continue
            try:
                df_sub = leer_csv_subido(archivo, separador, decimal, key_prefix=clave)
            except Exception as e:
                st.error(f"No se pudo leer el archivo: {e}")
                continue
            if df_sub.empty:
                st.error("El archivo está vacío.")
                continue

            col_monto = st.selectbox(
                "Columna de montos", list(df_sub.columns), key=f"col_monto_{clave}"
            )
            montos = preprocesar_montos(df_sub[col_monto], UMBRAL_MINIMO)
            st.caption(f"{len(montos):,} registros válidos de {len(df_sub):,} totales.")

            if len(montos) == 0:
                st.warning("No quedan registros válidos tras el preprocesamiento.")
                continue
            if len(montos) < 30:
                st.warning("Menos de 30 observaciones válidas: resultados poco confiables.")

            subconjuntos[nombre] = analisis_completo(montos)

    if len(subconjuntos) < 2:
        st.info("Carga al menos dos conjuntos para ver la tabla comparativa.")
        return

    st.markdown("---")
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
    "**Mark Nigrini** (análisis de primer y segundo dígito, MAD, Chi-cuadrado y Z-scores)."
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
        """
    )
    st.stop()

try:
    df = leer_csv_subido(archivo, separador, decimal, key_prefix="unico")
except Exception as e:
    st.error(f"No se pudo leer el archivo: {e}")
    st.stop()

if df.empty:
    st.error("El archivo CSV está vacío.")
    st.stop()

st.subheader("Vista previa de los datos")
st.dataframe(df.head(20), use_container_width=True)
st.caption(f"El archivo contiene {len(df):,} filas y {len(df.columns)} columnas.")

columnas = list(df.columns)

col_a, col_b = st.columns(2)
with col_a:
    col_monto = st.selectbox("Columna de montos (obligatoria)", columnas)
with col_b:
    opciones_etiqueta = ["(Ninguna)"] + columnas
    col_etiqueta = st.selectbox(
        "Columna de etiqueta de riesgo (opcional)", opciones_etiqueta
    )
    col_etiqueta = None if col_etiqueta == "(Ninguna)" else col_etiqueta

if col_etiqueta:
    valores_unicos = df[col_etiqueta].dropna().unique()
    if len(valores_unicos) != 2:
        st.warning(
            f"La columna '{col_etiqueta}' tiene {len(valores_unicos)} valores únicos. "
            "Se espera una columna binaria (2 valores). Elige cuál representa 'riesgo'."
        )
    valor_riesgo = st.selectbox(
        "¿Qué valor de la columna de etiqueta representa transacciones de RIESGO?",
        sorted(valores_unicos.tolist(), key=str),
    )

ejecutar = st.button("🚀 Ejecutar análisis de Benford", type="primary")

if not ejecutar:
    st.stop()

# --- Preprocesamiento ---
montos_originales = df[col_monto]
montos = preprocesar_montos(montos_originales, UMBRAL_MINIMO)

n_original = len(montos_originales)
n_excluidos = n_original - len(montos)

st.markdown("---")
st.subheader("🧹 Preprocesamiento de datos")
c1, c2, c3 = st.columns(3)
c1.metric("Registros totales", f"{n_original:,}")
c2.metric(f"Excluidos (< USD {UMBRAL_MINIMO:.2f} o no numéricos)", f"{n_excluidos:,}")
c3.metric("Registros válidos para el análisis", f"{len(montos):,}")

if len(montos) < 30:
    st.warning(
        "El número de observaciones válidas es muy bajo (< 30). "
        "Los resultados estadísticos pueden no ser confiables."
    )

if len(montos) == 0:
    st.error("No quedan registros válidos tras el preprocesamiento.")
    st.stop()

# --- Análisis global ---
st.markdown("---")
st.header("📈 Análisis global (toda la muestra)")
resultado_global = analisis_completo(montos)
mostrar_analisis(resultado_global)

# --- Análisis segmentado (legítimas vs. riesgo) ---
if col_etiqueta:
    st.markdown("---")
    st.header("⚖️ Análisis segmentado: legítimas vs. riesgo")

    df_valido = df.loc[montos.index]
    es_riesgo = df_valido[col_etiqueta] == valor_riesgo

    montos_riesgo = montos[es_riesgo]
    montos_legitimas = montos[~es_riesgo]

    if len(montos_riesgo) < 10 or len(montos_legitimas) < 10:
        st.warning(
            "Uno de los dos grupos tiene menos de 10 observaciones válidas; "
            "los resultados segmentados pueden no ser estadísticamente confiables."
        )

    col_leg, col_riesgo = st.columns(2)

    if len(montos_legitimas) > 0:
        resultado_legitimas = analisis_completo(montos_legitimas)
    else:
        resultado_legitimas = None

    if len(montos_riesgo) > 0:
        resultado_riesgo = analisis_completo(montos_riesgo)
    else:
        resultado_riesgo = None

    with col_leg:
        st.subheader(f"✅ Legítimas (n = {len(montos_legitimas):,})")
        if resultado_legitimas:
            mostrar_analisis(resultado_legitimas)
        else:
            st.info("Sin observaciones en este grupo.")

    with col_riesgo:
        st.subheader(f"🚨 Riesgo (n = {len(montos_riesgo):,})")
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
