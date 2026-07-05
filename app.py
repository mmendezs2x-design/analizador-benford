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
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from benford import AcumuladorDigitos, veredicto_mad_primer_digito, veredicto_mad_segundo_digito

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

RUTA_BASE = Path(__file__).resolve().parent
RUTA_TABLA_PRIMER_DIGITO = RUTA_BASE / "tabla2_benford_primer_digito_resultados.json"
RUTA_TABLA_SEGUNDO_DIGITO = RUTA_BASE / "tabla3_benford_segundo_digito_resultados.json"
RUTA_TABLA_COMPARACION = RUTA_BASE / "tabla4_comparacion_lavado_legitimas.json"


# ------------------------- Paleta e identidad visual -------------------------
# Estética únicamente: nada en esta sección altera cálculos ni la lectura de JSON.

COLOR_FONDO = "#0B1220"
COLOR_TARJETA = "#141B2D"
COLOR_BORDE = "rgba(255, 255, 255, 0.08)"
COLOR_ACENTO = "#22D3EE"
COLOR_TEXTO = "#E6EDF3"
COLOR_TEXTO_SECUNDARIO = "#8B98AC"

COLOR_OBSERVADO = "#22D3EE"
COLOR_BENFORD = "#CBD5E1"
COLOR_OK = "#10B981"
COLOR_AMBAR = "#F59E0B"
COLOR_ALERTA = "#EF4444"

FUENTE_SANS = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
    "'Helvetica Neue', Arial, sans-serif"
)

# Mapea el nivel devuelto por benford.py (4 bandas de Nigrini) al semáforo
# visual de 3 estados que pide la interfaz (verde / ámbar / rojo).
NIVEL_A_SEMAFORO = {
    "success": "verde",
    "info": "ambar",
    "warning": "rojo",
    "error": "rojo",
}


def inyectar_estilos():
    st.markdown(
        f"""
        <style>
        html, body, [class*="css"] {{
            font-family: {FUENTE_SANS};
        }}

        div[data-testid="stMetric"] {{
            background: {COLOR_TARJETA};
            border: 1px solid {COLOR_BORDE};
            border-radius: 12px;
            padding: 0.9rem 1.1rem;
            overflow: visible;
        }}
        div[data-testid="stMetricLabel"] {{
            color: {COLOR_TEXTO_SECUNDARIO};
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        div[data-testid="stMetricValue"] {{
            color: {COLOR_TEXTO};
            font-weight: 600;
            font-size: 1.55rem;
            white-space: normal;
            overflow-wrap: break-word;
            overflow: visible;
        }}
        div[data-testid="stMetricValue"] > div {{
            white-space: normal;
            overflow-wrap: break-word;
            overflow: visible;
            text-overflow: clip;
        }}

        .app-header {{
            margin-bottom: 0.6rem;
        }}
        .app-header-titulo {{
            font-size: 2.1rem;
            font-weight: 700;
            letter-spacing: -0.01em;
            color: {COLOR_TEXTO};
            margin-bottom: 0.15rem;
        }}
        .app-header-subtitulo {{
            color: {COLOR_TEXTO_SECUNDARIO};
            font-size: 1.02rem;
            font-weight: 400;
        }}

        .badge-veredicto {{
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.32rem 0.85rem;
            border-radius: 999px;
            font-size: 0.85rem;
            font-weight: 600;
            border: 1px solid transparent;
            margin: 0.15rem 0 0.4rem 0;
        }}
        .badge-dot {{
            width: 8px;
            height: 8px;
            min-width: 8px;
            border-radius: 50%;
            background: currentColor;
        }}
        .badge-verde {{ background: rgba(16, 185, 129, 0.12); color: #34D399; border-color: rgba(16, 185, 129, 0.35); }}
        .badge-ambar {{ background: rgba(245, 158, 11, 0.12); color: #FBBF24; border-color: rgba(245, 158, 11, 0.35); }}
        .badge-rojo  {{ background: rgba(239, 68, 68, 0.12);  color: #F87171; border-color: rgba(239, 68, 68, 0.35); }}

        .veredicto-etiqueta {{
            color: {COLOR_TEXTO_SECUNDARIO};
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin-bottom: 0.2rem;
        }}

        .hallazgo-card {{
            border-left: 3px solid {COLOR_ALERTA};
            background: rgba(239, 68, 68, 0.07);
            border-radius: 8px;
            padding: 0.9rem 1.1rem;
            margin: 0.5rem 0;
        }}
        .hallazgo-card.ok {{
            border-left-color: {COLOR_OK};
            background: rgba(16, 185, 129, 0.07);
        }}
        .hallazgo-titulo {{
            text-transform: uppercase;
            font-size: 0.7rem;
            letter-spacing: 0.07em;
            color: {COLOR_TEXTO_SECUNDARIO};
            margin-bottom: 0.3rem;
            font-weight: 600;
        }}
        .hallazgo-texto {{
            color: {COLOR_TEXTO};
            font-size: 0.95rem;
            line-height: 1.5;
        }}

        .app-footer {{
            margin-top: 2.5rem;
            padding-top: 1.1rem;
            border-top: 1px solid {COLOR_BORDE};
            color: {COLOR_TEXTO_SECUNDARIO};
            font-size: 0.8rem;
            line-height: 1.7;
        }}
        .app-footer strong {{
            color: {COLOR_TEXTO};
        }}

        .sidebar-seccion {{
            color: {COLOR_TEXTO_SECUNDARIO};
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-weight: 600;
            margin: 0.9rem 0 0.25rem 0;
        }}
        .sidebar-destacado {{
            display: inline-block;
            background: rgba(34, 211, 238, 0.12);
            color: {COLOR_ACENTO};
            border: 1px solid rgba(34, 211, 238, 0.35);
            border-radius: 999px;
            padding: 0.15rem 0.6rem;
            font-size: 0.68rem;
            font-weight: 600;
            letter-spacing: 0.03em;
            text-transform: uppercase;
            margin-bottom: 0.4rem;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_badge_veredicto(texto: str, nivel: str):
    """Muestra el veredicto de conformidad como badge semáforo
    (verde = conforme, ámbar = marginal, rojo = no conforme)."""
    clase = f"badge-{NIVEL_A_SEMAFORO.get(nivel, 'ambar')}"
    st.markdown(
        f'<div class="badge-veredicto {clase}"><span class="badge-dot"></span>{texto}</div>',
        unsafe_allow_html=True,
    )


def render_hallazgo(texto: str, alerta: bool = True, titulo: str = "Hallazgo clave"):
    """Tarjeta destacada para hallazgos (Tabla 17 y comparaciones segmentadas)."""
    clase = "hallazgo-card" if alerta else "hallazgo-card ok"
    st.markdown(
        f'<div class="{clase}"><div class="hallazgo-titulo">{titulo}</div>'
        f'<div class="hallazgo-texto">{texto}</div></div>',
        unsafe_allow_html=True,
    )


def aplicar_tema_grafico(fig):
    """Aplica un tema oscuro coherente a cualquier figura Plotly de la app
    (fondo transparente, tipografía y grillas discretas). No modifica datos."""
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FUENTE_SANS, color=COLOR_TEXTO_SECUNDARIO, size=12),
        title=dict(font=dict(size=14, color=COLOR_TEXTO)),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=COLOR_TEXTO_SECUNDARIO)),
        margin=dict(t=48, l=10, r=10, b=40),
    )
    fig.update_xaxes(gridcolor="rgba(255,255,255,0.06)", zerolinecolor="rgba(255,255,255,0.12)", color=COLOR_TEXTO_SECUNDARIO)
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.06)", zerolinecolor="rgba(255,255,255,0.12)", color=COLOR_TEXTO_SECUNDARIO)
    return fig


def render_footer():
    st.markdown(
        """
        <div class="app-footer">
            <strong>La Ley de Benford como Herramienta de Calibración Forense para la
            Detección de Lavado de Activos: Aplicación para el SRI y la UAFE</strong><br>
            Mauricio Xavier Méndez Silva &middot; Solange Ana Chávez Escalante<br>
            Universidad Estatal de Milagro (UNEMI) &middot; 2026
        </div>
        """,
        unsafe_allow_html=True,
    )


inyectar_estilos()


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
        marker_color=COLOR_OBSERVADO,
    ))
    fig.add_trace(go.Scatter(
        x=tabla["digito"],
        y=tabla["freq_esperada"],
        name="Distribución de Benford (esperada)",
        mode="lines+markers",
        line=dict(color=COLOR_BENFORD, width=2),
        marker=dict(size=7, color=COLOR_BENFORD),
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
    return aplicar_tema_grafico(fig)


def grafico_zscore(tabla: pd.DataFrame, x_titulo: str, umbral: float = 1.96):
    colores = [COLOR_ALERTA if z > umbral else COLOR_OK for z in tabla["z_score"]]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=tabla["digito"],
        y=tabla["z_score"],
        marker_color=colores,
        name="Z-score",
    ))
    fig.add_hline(y=umbral, line_dash="dash", line_color=COLOR_TEXTO_SECUNDARIO,
                  annotation_text=f"Umbral crítico (±{umbral})")
    fig.update_layout(
        title="Z-score por dígito (significancia individual, α=0.05)",
        xaxis_title=x_titulo,
        yaxis_title="Z-score",
        xaxis=dict(tickmode="linear"),
        height=350,
    )
    return aplicar_tema_grafico(fig)


def mostrar_metricas(resultado_digito: dict, etiqueta: str):
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("N (observaciones)", f"{resultado_digito['n']:,}")
    col2.metric("MAD", f"{resultado_digito['mad']:.5f}")
    col3.metric("Chi-cuadrado (χ²)", f"{resultado_digito['chi2']:.3f}")
    col4.metric("Valor p", f"{resultado_digito['p_valor']:.5f}")

    st.markdown(f'<div class="veredicto-etiqueta">Veredicto — {etiqueta}</div>', unsafe_allow_html=True)
    render_badge_veredicto(resultado_digito["veredicto"], resultado_digito["nivel"])


def mostrar_analisis(resultado: dict):
    tab1, tab2 = st.tabs(["Primer dígito", "Segundo dígito"])

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
    st.header("Comparación de subconjuntos")
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

    ejecutar = st.button("Ejecutar comparación", type="primary")
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

    st.subheader("Tabla comparativa de conformidad")

    def resaltar_lavado(fila):
        if fila["Conjunto"] == "Transacciones de Lavado":
            return ["background-color: rgba(239, 68, 68, 0.16)"] * len(fila)
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
        st.subheader("Incremento del MAD: Transacciones de Lavado vs. Legítimas")

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
            render_hallazgo(
                f"El conjunto de <strong>Transacciones de Lavado</strong> presenta un MAD de "
                f"primer dígito un <strong>{inc_1:.1f}%</strong> más alto que el de "
                "<strong>Transacciones Legítimas</strong>, lo que indica una desviación mucho "
                "mayor respecto a la Ley de Benford y constituye una señal de alerta de "
                "posible manipulación.",
                alerta=True,
            )
        elif not np.isnan(inc_1):
            render_hallazgo(
                "El conjunto de Lavado no muestra un MAD de primer dígito mayor que "
                "el de Legítimas en esta comparación.",
                alerta=False,
                titulo="Sin hallazgo relevante",
            )

    for nombre, resultado in subconjuntos.items():
        with st.expander(f"Ver detalle completo — {nombre}"):
            mostrar_analisis(resultado)


# ------------------------- Modo: resultados de la tesis (JSON precalculados) -------------------------

def formato_es(valor, decimales: int = 2) -> str:
    """Formatea un número con convención española: punto para miles, coma
    para decimales (ej. 10736.4 -> '10.736,4')."""
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return "—"
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def formato_es_pct(valor, decimales: int = 1) -> str:
    """Formatea un porcentaje con signo y convención española (ej. 1292.7 -> '+1.292,7%')."""
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return "—"
    texto = formato_es(valor, decimales)
    if valor >= 0 and not texto.startswith("+"):
        texto = f"+{texto}"
    return f"{texto}%"


def formato_es_pvalor(p_valor) -> str:
    if p_valor is None or (isinstance(p_valor, float) and np.isnan(p_valor)):
        return "—"
    if p_valor >= 0.00001:
        return formato_es(p_valor, 5)
    return f"{p_valor:.2e}".replace(".", ",")


NOTA_NOMBRES_ALTERNATIVOS = (
    "El campo de chi-cuadrado acepta tanto `chi2` como cualquier nombre que "
    "empiece con `chi2` (ej. `chi2_8`, `chi2_9`, según los grados de "
    "libertad). El detalle por dígito acepta tanto `resultados` como "
    "`resultados_por_digito`, y este último puede venir como **diccionario** "
    "(clave = dígito en string, ej. `\"1\"`, `\"2\"`...) o como lista de "
    "objetos con un campo `digito`."
)

EJEMPLO_TABLA_PRIMER_DIGITO = {
    "n_valido": 4929615,
    "mad": 0.004913,
    "chi2_8": 10736.4,
    "grados_libertad": 8,
    "p_valor": 0.0,
    "interpretacion_mad": "Conformidad aceptable, con asociación marginal",
    "resultados_por_digito": {
        "1": {"observado_pct": 31.75, "benford_pct": 30.1, "diferencia_abs": 0.0165, "z_score": 79.57, "n_observado": 1565004},
        "2": {"observado_pct": 17.55, "benford_pct": 17.609, "diferencia_abs": 0.059, "z_score": 2.1, "n_observado": 865300},
        "...": "... (una clave por cada dígito del 1 al 9)",
    },
}

EJEMPLO_TABLA_SEGUNDO_DIGITO = {
    "n_valido": 4927977,
    "mad": 0.000324,
    "chi2_9": 109.9,
    "grados_libertad": 9,
    "p_valor": 0.0,
    "interpretacion_mad": "Conformidad aceptable con Benford",
    "resultados_por_digito": {
        "0": {"observado_pct": 12.00, "benford_pct": 11.968, "diferencia_abs": 0.032, "z_score": 0.9, "n_observado": 591300},
        "1": {"observado_pct": 11.40, "benford_pct": 11.389, "diferencia_abs": 0.011, "z_score": 0.3, "n_observado": 561800},
        "...": "... (una clave por cada dígito del 0 al 9)",
    },
}

EJEMPLO_TABLA_COMPARACION = {
    "legitimas": {
        "primer_digito": {
            "n_valido": 4900000, "mad": 0.004905, "chi2_8": 9000.0, "p_valor": 0.0,
            "resultados_por_digito": {"1": "... (mismo formato que en tabla2, una clave por dígito del 1 al 9)"},
        },
        "segundo_digito": {
            "n_valido": 4900000, "mad": 0.000324, "chi2_9": 100.0, "p_valor": 0.5,
            "resultados_por_digito": {"0": "... (mismo formato que en tabla3, una clave por dígito del 0 al 9)"},
        },
    },
    "lavado": {
        "primer_digito": {
            "n_valido": 29615, "mad": 0.020627, "chi2_8": 5000.0, "p_valor": 0.0,
            "resultados_por_digito": {"1": "... (mismo formato que en tabla2, una clave por dígito del 1 al 9)"},
        },
        "segundo_digito": {
            "n_valido": 27977, "mad": 0.004509, "chi2_9": 200.0, "p_valor": 0.0,
            "resultados_por_digito": {"0": "... (mismo formato que en tabla3, una clave por dígito del 0 al 9)"},
        },
    },
    "incremento_porcentual": {
        "delta_mad_1d_pct": 320.6,
        "delta_mad_2d_pct": 1292.7,
    },
}


def cargar_json(ruta: Path):
    """Carga un archivo JSON de resultados. Devuelve (datos, error); error es
    None si la carga fue exitosa."""
    if not ruta.exists():
        return None, "no_encontrado"
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f), None
    except (json.JSONDecodeError, OSError) as e:
        return None, str(e)


def placeholder_json_faltante(nombre_archivo: str, ejemplo: dict, error: str):
    if error == "no_encontrado":
        st.warning(f"No se encontró el archivo **`{nombre_archivo}`** en el repositorio.")
    else:
        st.error(f"No se pudo leer **`{nombre_archivo}`**: {error}")
    st.markdown(
        f"Para mostrar esta sección, agrega un archivo llamado `{nombre_archivo}` "
        "en la raíz del repositorio (junto a `app.py`) con la estructura indicada abajo, "
        "y vuelve a cargar la página."
    )
    with st.expander(f"Ver esquema JSON esperado para `{nombre_archivo}`"):
        st.code(json.dumps(ejemplo, indent=2, ensure_ascii=False), language="json")
        st.caption(NOTA_NOMBRES_ALTERNATIVOS)


CLAVES_RESULTADOS_POR_DIGITO = ["resultados", "resultados_por_digito"]
COLUMNAS_TABLA_RESULTADOS = ["digito", "observado_pct", "benford_pct", "diferencia_abs", "z_score", "n_observado"]


def obtener_resultados_por_digito(datos: dict):
    """Devuelve la lista de resultados por dígito, aceptando tanto 'resultados'
    como 'resultados_por_digito' (nombre usado en los JSON reales de las
    Tablas 15 y 16). Devuelve None si no se encuentra ninguna de las dos."""
    for clave in CLAVES_RESULTADOS_POR_DIGITO:
        if clave in datos:
            return datos[clave]
    return None


def obtener_chi2(datos: dict):
    """Devuelve el valor de chi-cuadrado, aceptando 'chi2' o cualquier campo
    que empiece con 'chi2' (ej. 'chi2_8', 'chi2_9', usados en los JSON reales
    según los grados de libertad). Devuelve None si no se encuentra."""
    if "chi2" in datos:
        return datos["chi2"]
    for clave, valor in datos.items():
        if isinstance(clave, str) and clave.startswith("chi2"):
            return valor
    return None


def tabla_desde_resultados(resultados):
    """Construye un DataFrame con el detalle por dígito del JSON, usando los
    campos (observado_pct, benford_pct, diferencia_abs, z_score, n_observado)
    tal cual vienen, sin recalcular nada.

    Soporta los dos formatos que puede traer 'resultados'/'resultados_por_digito':
      - dict: {"1": {"observado_pct": ..., "benford_pct": ..., ...}, "2": {...}}
        (formato real de los JSON de la tesis: la clave del diccionario es el
        dígito, como string).
      - list: [{"digito": 1, "observado_pct": ..., "benford_pct": ..., ...}, ...]

    Se fuerzan las columnas esperadas explícitamente (vía `columns=`) para
    que el DataFrame nunca quede sin ellas —ni siquiera si `resultados`
    viene vacío o con elementos inválidos—, evitando un KeyError aguas
    abajo en los gráficos. Las filas quedan ordenadas por dígito."""
    filas = []

    if isinstance(resultados, dict):
        for clave_digito, valores in resultados.items():
            if not isinstance(valores, dict):
                continue
            try:
                digito = int(clave_digito)
            except (TypeError, ValueError):
                digito = clave_digito
            filas.append({
                "digito": digito,
                "observado_pct": valores["observado_pct"],
                "benford_pct": valores["benford_pct"],
                "diferencia_abs": valores.get("diferencia_abs"),
                "z_score": valores.get("z_score"),
                "n_observado": valores.get("n_observado"),
            })
    else:
        for item in resultados or []:
            if not isinstance(item, dict):
                continue
            filas.append({
                "digito": item["digito"],
                "observado_pct": item["observado_pct"],
                "benford_pct": item["benford_pct"],
                "diferencia_abs": item.get("diferencia_abs"),
                "z_score": item.get("z_score"),
                "n_observado": item.get("n_observado"),
            })

    tabla = pd.DataFrame(filas, columns=COLUMNAS_TABLA_RESULTADOS)
    if not tabla.empty:
        tabla = tabla.sort_values("digito", kind="stable").reset_index(drop=True)
    return tabla


def grafico_comparativo_tesis(tabla: pd.DataFrame, titulo: str, x_titulo: str):
    """Igual que grafico_comparativo, pero usando directamente los porcentajes
    observado_pct/benford_pct del JSON (sin convertirlos a fracción)."""
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=tabla["digito"], y=tabla["observado_pct"],
        name="Observado (%)", marker_color=COLOR_OBSERVADO,
    ))
    fig.add_trace(go.Scatter(
        x=tabla["digito"], y=tabla["benford_pct"],
        name="Benford esperado (%)", mode="lines+markers",
        line=dict(color=COLOR_BENFORD, width=2), marker=dict(size=7, color=COLOR_BENFORD),
    ))
    fig.update_layout(
        title=titulo,
        xaxis_title=x_titulo,
        yaxis_title="Frecuencia (%)",
        xaxis=dict(tickmode="linear"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        bargap=0.2,
        height=430,
    )
    return aplicar_tema_grafico(fig)


def grafico_zscore_tesis(tabla: pd.DataFrame, x_titulo: str, umbral: float = 1.96):
    """Igual que grafico_zscore, pero usando el z_score ya provisto por el JSON."""
    colores = [COLOR_ALERTA if (pd.notna(z) and z > umbral) else COLOR_OK for z in tabla["z_score"]]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=tabla["digito"], y=tabla["z_score"], marker_color=colores, name="Z-score"))
    fig.add_hline(y=umbral, line_dash="dash", line_color=COLOR_TEXTO_SECUNDARIO,
                  annotation_text=f"Umbral crítico (±{umbral})")
    fig.update_layout(
        title="Z-score por dígito (según JSON)",
        xaxis_title=x_titulo,
        yaxis_title="Z-score",
        xaxis=dict(tickmode="linear"),
        height=350,
    )
    return aplicar_tema_grafico(fig)


def render_metricas_digito(datos: dict, funcion_veredicto=None):
    n_valido = datos["n_valido"]
    mad = datos["mad"]
    chi2 = obtener_chi2(datos)
    p_valor = datos["p_valor"]
    gl = datos.get("grados_libertad")
    etiqueta_chi2 = f"Chi-cuadrado (gl={gl})" if gl is not None else "Chi-cuadrado"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("N (válidos)", formato_es(n_valido, 0))
    c2.metric("MAD", formato_es(mad, 6))
    c3.metric(etiqueta_chi2, formato_es(chi2, 1) if chi2 is not None else "—")
    c4.metric("Valor p", formato_es_pvalor(p_valor))

    interpretacion = datos.get("interpretacion_mad")
    nivel = None
    if funcion_veredicto is not None:
        veredicto_calculado, nivel = funcion_veredicto(mad)
        if not interpretacion:
            interpretacion = veredicto_calculado

    if interpretacion:
        st.markdown('<div class="veredicto-etiqueta">Interpretación del MAD</div>', unsafe_allow_html=True)
        render_badge_veredicto(interpretacion, nivel or "info")


def render_grafico_digito(datos: dict, titulo_figura: str, x_titulo: str, nombre_tabla: str):
    resultados = obtener_resultados_por_digito(datos)
    if resultados is None:
        st.error(
            f"Al archivo de **{nombre_tabla}** le falta el campo 'resultados' "
            "(o 'resultados_por_digito')."
        )
        return
    try:
        tabla = tabla_desde_resultados(resultados)
    except (KeyError, TypeError) as e:
        st.error(
            "Cada elemento de 'resultados'/'resultados_por_digito' debe incluir "
            f"'digito', 'observado_pct' y 'benford_pct' (falta {e})."
        )
        return

    if tabla.empty:
        st.warning(f"'{nombre_tabla}': la lista de resultados por dígito está vacía.")
        return

    c1, c2 = st.columns([2, 1])
    with c1:
        st.plotly_chart(grafico_comparativo_tesis(tabla, titulo_figura, x_titulo), use_container_width=True)
    with c2:
        if tabla["z_score"].notna().any():
            st.plotly_chart(grafico_zscore_tesis(tabla, x_titulo), use_container_width=True)

    with st.expander(f"Ver tabla de datos — {nombre_tabla}"):
        tabla_mostrar = tabla.rename(columns={
            "digito": "Dígito", "observado_pct": "Observado (%)", "benford_pct": "Benford (%)",
            "diferencia_abs": "Diferencia abs.", "z_score": "Z-score", "n_observado": "N observado",
        })
        st.dataframe(
            tabla_mostrar.style.format({
                "Observado (%)": lambda v: formato_es(v, 4),
                "Benford (%)": lambda v: formato_es(v, 4),
                "Diferencia abs.": lambda v: formato_es(v, 4) if pd.notna(v) else "—",
                "Z-score": lambda v: formato_es(v, 3) if pd.notna(v) else "—",
                "N observado": lambda v: formato_es(v, 0) if pd.notna(v) else "—",
            }),
            use_container_width=True,
        )


def render_tabla_digito(datos: dict, nombre_tabla: str, titulo_figura: str, x_titulo: str, funcion_veredicto):
    campos_base = ["n_valido", "mad", "p_valor"]
    faltantes = [c for c in campos_base if c not in datos]
    if obtener_chi2(datos) is None:
        faltantes.append("chi2 (o chi2_N)")
    if obtener_resultados_por_digito(datos) is None:
        faltantes.append("resultados (o resultados_por_digito)")
    if faltantes:
        st.error(f"Al archivo de **{nombre_tabla}** le faltan los campos: {', '.join(faltantes)}.")
        return
    render_metricas_digito(datos, funcion_veredicto)
    render_grafico_digito(datos, titulo_figura, x_titulo, nombre_tabla)


def render_tabla_comparacion(datos: dict):
    campos_requeridos = ["legitimas", "lavado", "incremento_porcentual"]
    faltantes = [c for c in campos_requeridos if c not in datos]
    if faltantes:
        st.error(f"Al archivo de comparación le faltan los campos: {', '.join(faltantes)}.")
        return

    claves_incremento = {"primer_digito": "delta_mad_1d_pct", "segundo_digito": "delta_mad_2d_pct"}
    faltan_inc = [v for v in claves_incremento.values() if v not in datos["incremento_porcentual"]]
    if faltan_inc:
        st.error(f"A 'incremento_porcentual' le falta: {', '.join(faltan_inc)}.")
        return

    etiquetas = {"primer_digito": "Primer dígito", "segundo_digito": "Segundo dígito"}
    for grupo in ["legitimas", "lavado"]:
        faltan_pos = [p for p in etiquetas if p not in datos[grupo]]
        if faltan_pos:
            st.error(f"A la sección '{grupo}' le falta: {', '.join(faltan_pos)}.")
            return

    incremento = datos["incremento_porcentual"]
    filas = []
    mads_leg = []
    mads_lav = []

    for clave, etiqueta in etiquetas.items():
        leg = datos["legitimas"][clave]
        lav = datos["lavado"][clave]
        mads_leg.append(leg["mad"])
        mads_lav.append(lav["mad"])
        inc = incremento[claves_incremento[clave]]
        filas.append({
            "Posición": etiqueta,
            "N Legítimas": leg.get("n_valido"),
            "MAD Legítimas": leg["mad"],
            "χ² Legítimas": obtener_chi2(leg),
            "N Lavado": lav.get("n_valido"),
            "MAD Lavado": lav["mad"],
            "χ² Lavado": obtener_chi2(lav),
            "Δ% MAD": inc,
        })

    tabla_resumen = pd.DataFrame(filas)
    st.dataframe(
        tabla_resumen.style.format({
            "N Legítimas": lambda v: formato_es(v, 0) if pd.notna(v) else "—",
            "MAD Legítimas": lambda v: formato_es(v, 6),
            "χ² Legítimas": lambda v: formato_es(v, 1) if pd.notna(v) else "—",
            "N Lavado": lambda v: formato_es(v, 0) if pd.notna(v) else "—",
            "MAD Lavado": lambda v: formato_es(v, 6),
            "χ² Lavado": lambda v: formato_es(v, 1) if pd.notna(v) else "—",
            "Δ% MAD": lambda v: formato_es_pct(v, 1),
        }),
        use_container_width=True,
    )

    fig = go.Figure()
    posiciones = [etiquetas["primer_digito"], etiquetas["segundo_digito"]]
    fig.add_trace(go.Bar(
        x=posiciones, y=mads_leg, name="Legítimas", marker_color=COLOR_OK,
        text=[formato_es(v, 6) for v in mads_leg], textposition="outside",
    ))
    fig.add_trace(go.Bar(
        x=posiciones, y=mads_lav, name="Lavado", marker_color=COLOR_ALERTA,
        text=[formato_es(v, 6) for v in mads_lav], textposition="outside",
    ))
    for i, clave in enumerate(["primer_digito", "segundo_digito"]):
        inc = incremento[claves_incremento[clave]]
        fig.add_annotation(
            x=posiciones[i], y=max(mads_leg[i], mads_lav[i]),
            text=formato_es_pct(inc, 1), showarrow=True, arrowhead=2, ay=-40,
            font=dict(color=COLOR_ALERTA, size=14),
        )
    fig.update_layout(
        title="Figura 3: MAD — Legítimas vs. Lavado",
        yaxis_title="MAD",
        barmode="group",
        height=430,
    )
    st.plotly_chart(aplicar_tema_grafico(fig), use_container_width=True)

    for clave, etiqueta in etiquetas.items():
        inc = incremento[claves_incremento[clave]]
        if inc > 0:
            render_hallazgo(
                f"En <strong>{etiqueta.lower()}</strong>, el MAD de <strong>Lavado</strong> es un "
                f"<strong>{formato_es_pct(inc, 1)}</strong> más alto que el de <strong>Legítimas</strong> "
                "— señal de alerta de posible manipulación.",
                alerta=True,
            )
        else:
            render_hallazgo(
                f"En {etiqueta.lower()}, el MAD de Lavado no es mayor que el de "
                f"Legítimas ({formato_es_pct(inc, 1)}).",
                alerta=False,
                titulo="Sin hallazgo relevante",
            )

    with st.expander("Ver detalle por dígito — Legítimas vs. Lavado"):
        for clave, etiqueta in etiquetas.items():
            st.markdown(f"**{etiqueta}**")
            col_leg, col_lav = st.columns(2)
            with col_leg:
                st.caption("Legítimas")
                render_metricas_digito(datos["legitimas"][clave])
                render_grafico_digito(
                    datos["legitimas"][clave], f"{etiqueta} — Legítimas", etiqueta,
                    f"{etiqueta} (Legítimas)",
                )
            with col_lav:
                st.caption("Lavado")
                render_metricas_digito(datos["lavado"][clave])
                render_grafico_digito(
                    datos["lavado"][clave], f"{etiqueta} — Lavado", etiqueta,
                    f"{etiqueta} (Lavado)",
                )


def modo_resultados_tesis():
    st.markdown(
        '<span class="sidebar-destacado">Vista principal</span>',
        unsafe_allow_html=True,
    )
    st.header("Resultados de la tesis")
    st.markdown(
        "Resultados **pre-calculados** del estudio, leídos directamente de archivos "
        "JSON incluidos en el repositorio (no se recalculan en la app)."
    )

    datos_primer, error_primer = cargar_json(RUTA_TABLA_PRIMER_DIGITO)
    st.subheader("Tabla 15 — Análisis de primer dígito (global)")
    if datos_primer is None:
        placeholder_json_faltante(RUTA_TABLA_PRIMER_DIGITO.name, EJEMPLO_TABLA_PRIMER_DIGITO, error_primer)
    else:
        render_tabla_digito(
            datos_primer, "Tabla 15 (primer dígito)",
            "Figura 1: Primer dígito — Observado vs. Benford", "Primer dígito",
            veredicto_mad_primer_digito,
        )

    st.markdown("---")
    datos_segundo, error_segundo = cargar_json(RUTA_TABLA_SEGUNDO_DIGITO)
    st.subheader("Tabla 16 — Análisis de segundo dígito (global)")
    if datos_segundo is None:
        placeholder_json_faltante(RUTA_TABLA_SEGUNDO_DIGITO.name, EJEMPLO_TABLA_SEGUNDO_DIGITO, error_segundo)
    else:
        render_tabla_digito(
            datos_segundo, "Tabla 16 (segundo dígito)",
            "Figura 2: Segundo dígito — Observado vs. Benford", "Segundo dígito",
            veredicto_mad_segundo_digito,
        )

    st.markdown("---")
    datos_comp, error_comp = cargar_json(RUTA_TABLA_COMPARACION)
    st.subheader("Tabla 17 — Comparación: Legítimas vs. Lavado")
    if datos_comp is None:
        placeholder_json_faltante(RUTA_TABLA_COMPARACION.name, EJEMPLO_TABLA_COMPARACION, error_comp)
    else:
        render_tabla_comparacion(datos_comp)


# ------------------------- Interfaz principal -------------------------

st.markdown(
    """
    <div class="app-header">
        <div class="app-header-titulo">Analizador Forense de Benford</div>
        <div class="app-header-subtitulo">Plataforma de detección de anomalías transaccionales</div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.caption(
    "Metodología de Mark Nigrini — análisis de primer y segundo dígito, MAD, Chi-cuadrado "
    "y Z-scores. Los archivos CSV se procesan por chunks para soportar grandes volúmenes "
    "con bajo consumo de memoria."
)

MODO_TESIS = "Resultados de la tesis"
MODO_ARCHIVO_UNICO = "Archivo único (con etiqueta opcional)"
MODO_COMPARACION = "Comparación de subconjuntos"

with st.sidebar:
    st.markdown('<div class="sidebar-seccion">Modo de análisis</div>', unsafe_allow_html=True)
    st.markdown('<span class="sidebar-destacado">Recomendado</span>', unsafe_allow_html=True)
    modo = st.radio(
        "Modo de análisis",
        [MODO_TESIS, MODO_ARCHIVO_UNICO, MODO_COMPARACION],
        label_visibility="collapsed",
    )

    separador = decimal = None
    archivo = None
    if modo != MODO_TESIS:
        st.markdown('<div class="sidebar-seccion">Formato del archivo</div>', unsafe_allow_html=True)
        separador = st.selectbox("Separador de columnas", [",", ";", "\t", "|"], index=0)
        decimal = st.selectbox("Separador decimal", [".", ","], index=0)

    if modo == MODO_ARCHIVO_UNICO:
        st.markdown('<div class="sidebar-seccion">Archivo</div>', unsafe_allow_html=True)
        archivo = st.file_uploader(
            "Sube un archivo de transacciones (CSV, CSV.GZ o ZIP)",
            type=TIPOS_ARCHIVO_ACEPTADOS,
        )

if modo == MODO_TESIS:
    modo_resultados_tesis()
    render_footer()
    st.stop()

if modo == MODO_COMPARACION:
    modo_comparacion_subconjuntos(separador, decimal)
    render_footer()
    st.stop()

if archivo is None:
    st.info("Sube un archivo en el panel lateral para comenzar el análisis.")
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

ejecutar = st.button("Ejecutar análisis de Benford", type="primary")

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

st.subheader("Preprocesamiento de datos")
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
st.header("Análisis global (toda la muestra)")
mostrar_analisis(resultado_global)

# --- Análisis segmentado (legítimas vs. riesgo) ---
if col_etiqueta:
    st.markdown("---")
    st.header("Análisis segmentado: legítimas vs. riesgo")

    n_legitimas = resultado_legitimas["primer_digito"]["n"] if resultado_legitimas else 0
    n_riesgo = resultado_riesgo["primer_digito"]["n"] if resultado_riesgo else 0

    if n_riesgo < 10 or n_legitimas < 10:
        st.warning(
            "Uno de los dos grupos tiene menos de 10 observaciones válidas; "
            "los resultados segmentados pueden no ser estadísticamente confiables."
        )

    col_leg, col_riesgo = st.columns(2)

    with col_leg:
        st.subheader(f"Legítimas (n = {n_legitimas:,})")
        if resultado_legitimas:
            mostrar_analisis(resultado_legitimas)
        else:
            st.info("Sin observaciones en este grupo.")

    with col_riesgo:
        st.subheader(f"Riesgo (n = {n_riesgo:,})")
        if resultado_riesgo:
            mostrar_analisis(resultado_riesgo)
        else:
            st.info("Sin observaciones en este grupo.")

    if resultado_legitimas and resultado_riesgo:
        st.markdown("---")
        st.subheader("Incremento porcentual del MAD (riesgo vs. legítimas)")

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
            render_hallazgo(
                f"El grupo de <strong>riesgo</strong> presenta un MAD de primer dígito un "
                f"<strong>{inc_1:.1f}%</strong> más alto que el grupo de transacciones legítimas, "
                "lo que sugiere una mayor desviación respecto a la Ley de Benford "
                "y podría indicar manipulación o fraude.",
                alerta=True,
            )
        elif not np.isnan(inc_1):
            render_hallazgo(
                "El grupo de riesgo no muestra un MAD mayor que el de las transacciones "
                "legítimas en esta muestra.",
                alerta=False,
                titulo="Sin hallazgo relevante",
            )

st.markdown("---")
st.caption(
    "Metodología basada en Nigrini, M. (2012). *Benford's Law: Applications for "
    "Forensic Accounting, Auditing, and Fraud Detection*. Wiley."
)
render_footer()
