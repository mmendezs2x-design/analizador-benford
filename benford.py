"""Lógica estadística para el análisis de la Ley de Benford (Nigrini)."""

import numpy as np
import pandas as pd
from scipy import stats

# --- Distribuciones teóricas de Benford ---

PRIMER_DIGITO = np.arange(1, 10)
SEGUNDO_DIGITO = np.arange(0, 10)


def prob_primer_digito(d):
    return np.log10(1 + 1 / d)


def prob_segundo_digito(d):
    total = 0.0
    for k in range(1, 10):
        total += np.log10(1 + 1 / (10 * k + d))
    return total


DIST_PRIMER_DIGITO = {d: prob_primer_digito(d) for d in PRIMER_DIGITO}
DIST_SEGUNDO_DIGITO = {d: prob_segundo_digito(d) for d in SEGUNDO_DIGITO}


# --- Preprocesamiento ---

def preprocesar_montos(serie: pd.Series, umbral_minimo: float = 1.0):
    """Limpia la serie de montos: numérico, valor absoluto, excluye < umbral."""
    montos = pd.to_numeric(serie, errors="coerce")
    montos = montos.dropna()
    montos = montos.abs()
    montos = montos[montos >= umbral_minimo]
    return montos


def extraer_primer_digito(montos: pd.Series):
    montos = montos[montos > 0]
    texto = montos.astype(str).str.replace(".", "", regex=False).str.lstrip("0")
    primer = texto.str[0]
    primer = pd.to_numeric(primer, errors="coerce")
    return primer.dropna().astype(int)


def extraer_segundo_digito(montos: pd.Series):
    montos = montos[montos > 0]
    texto = montos.astype(str).str.replace(".", "", regex=False).str.lstrip("0")
    texto = texto[texto.str.len() >= 2]
    segundo = texto.str[1]
    segundo = pd.to_numeric(segundo, errors="coerce")
    return segundo.dropna().astype(int)


# --- Cálculo de frecuencias y estadísticos ---

def tabla_frecuencias(digitos: pd.Series, digitos_posibles, distribucion_teorica):
    n = len(digitos)
    conteo = digitos.value_counts().reindex(digitos_posibles, fill_value=0).sort_index()
    freq_observada = conteo / n if n > 0 else conteo * 0.0
    freq_esperada = pd.Series({d: distribucion_teorica[d] for d in digitos_posibles})

    tabla = pd.DataFrame({
        "digito": digitos_posibles,
        "conteo_observado": conteo.values,
        "freq_observada": freq_observada.values,
        "freq_esperada": freq_esperada.values,
    })
    tabla["conteo_esperado"] = tabla["freq_esperada"] * n

    # Z-score por dígito (Nigrini)
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = np.sqrt(tabla["freq_esperada"] * (1 - tabla["freq_esperada"]) / n) if n > 0 else np.nan
        continuidad = 1 / (2 * n) if n > 0 else 0
        diff = (tabla["freq_observada"] - tabla["freq_esperada"]).abs() - continuidad
        diff = diff.clip(lower=0)
        tabla["z_score"] = diff / denom

    tabla["z_score"] = tabla["z_score"].replace([np.inf, -np.inf], np.nan).fillna(0)
    return tabla, n


def calcular_mad(tabla: pd.DataFrame):
    return (tabla["freq_observada"] - tabla["freq_esperada"]).abs().mean()


def calcular_chi_cuadrado(tabla: pd.DataFrame, n: int):
    if n == 0:
        return np.nan, np.nan
    observado = tabla["conteo_observado"].values
    esperado = tabla["conteo_esperado"].values
    chi2, p = stats.chisquare(f_obs=observado, f_exp=esperado)
    return chi2, p


# --- Veredicto de conformidad según Nigrini (para primer dígito) ---

def veredicto_mad_primer_digito(mad: float):
    if mad < 0.006:
        return "Conformidad aceptable con Benford", "success"
    elif mad < 0.012:
        return "Conformidad aceptable, con asociación marginal", "info"
    elif mad < 0.015:
        return "Desviación no conforme: asociación marcada", "warning"
    else:
        return "No conformidad: asociación grave (posible anomalía)", "error"


def veredicto_mad_segundo_digito(mad: float):
    if mad < 0.008:
        return "Conformidad aceptable con Benford", "success"
    elif mad < 0.010:
        return "Conformidad aceptable, con asociación marginal", "info"
    elif mad < 0.012:
        return "Desviación no conforme: asociación marcada", "warning"
    else:
        return "No conformidad: asociación grave (posible anomalía)", "error"


def analisis_completo(montos: pd.Series):
    """Ejecuta el análisis de primer y segundo dígito sobre una serie de montos."""
    resultado = {}

    primeros = extraer_primer_digito(montos)
    tabla1, n1 = tabla_frecuencias(primeros, PRIMER_DIGITO, DIST_PRIMER_DIGITO)
    mad1 = calcular_mad(tabla1)
    chi2_1, p1 = calcular_chi_cuadrado(tabla1, n1)
    veredicto1, nivel1 = veredicto_mad_primer_digito(mad1)

    resultado["primer_digito"] = {
        "tabla": tabla1,
        "n": n1,
        "mad": mad1,
        "chi2": chi2_1,
        "p_valor": p1,
        "veredicto": veredicto1,
        "nivel": nivel1,
    }

    segundos = extraer_segundo_digito(montos)
    tabla2, n2 = tabla_frecuencias(segundos, SEGUNDO_DIGITO, DIST_SEGUNDO_DIGITO)
    mad2 = calcular_mad(tabla2)
    chi2_2, p2 = calcular_chi_cuadrado(tabla2, n2)
    veredicto2, nivel2 = veredicto_mad_segundo_digito(mad2)

    resultado["segundo_digito"] = {
        "tabla": tabla2,
        "n": n2,
        "mad": mad2,
        "chi2": chi2_2,
        "p_valor": p2,
        "veredicto": veredicto2,
        "nivel": nivel2,
    }

    return resultado
