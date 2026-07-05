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

def tabla_desde_conteo(conteo, digitos_posibles, distribucion_teorica, n: int):
    """Construye la tabla de frecuencias y Z-scores a partir de conteos ya acumulados
    (por ejemplo, sumados de forma incremental a través de varios chunks)."""
    conteo = np.asarray(conteo, dtype=np.int64)
    freq_observada = conteo / n if n > 0 else conteo.astype(float)
    freq_esperada = np.array([distribucion_teorica[d] for d in digitos_posibles])

    tabla = pd.DataFrame({
        "digito": list(digitos_posibles),
        "conteo_observado": conteo,
        "freq_observada": freq_observada,
        "freq_esperada": freq_esperada,
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
    return tabla


def tabla_frecuencias(digitos: pd.Series, digitos_posibles, distribucion_teorica):
    n = len(digitos)
    conteo = digitos.value_counts().reindex(digitos_posibles, fill_value=0).sort_index().values
    return tabla_desde_conteo(conteo, digitos_posibles, distribucion_teorica, n), n


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


class AcumuladorDigitos:
    """Acumula conteos de primer y segundo dígito de forma incremental (por chunks),
    sin retener nunca la serie completa de montos en memoria.

    Uso: se llama `procesar_chunk` una vez por cada trozo crudo leído del archivo
    (por ejemplo, con `pandas.read_csv(..., chunksize=...)`), y al final `finalizar()`
    produce el mismo resultado que si se hubiera analizado el archivo completo de una vez.
    """

    def __init__(self, umbral_minimo: float = 1.0):
        self.umbral_minimo = umbral_minimo
        self.conteo_primer = np.zeros(len(PRIMER_DIGITO), dtype=np.int64)
        self.conteo_segundo = np.zeros(len(SEGUNDO_DIGITO), dtype=np.int64)
        self.n_valido_primer = 0
        self.n_valido_segundo = 0
        self.n_original = 0

    def procesar_chunk(self, serie: pd.Series):
        """Preprocesa y extrae dígitos de un chunk crudo, acumulando solo los conteos."""
        self.n_original += len(serie)
        montos = preprocesar_montos(serie, self.umbral_minimo)

        primeros = extraer_primer_digito(montos)
        self.n_valido_primer += len(primeros)
        if len(primeros):
            conteo1 = primeros.value_counts()
            for d in PRIMER_DIGITO:
                self.conteo_primer[d - 1] += int(conteo1.get(d, 0))
            del conteo1

        segundos = extraer_segundo_digito(montos)
        self.n_valido_segundo += len(segundos)
        if len(segundos):
            conteo2 = segundos.value_counts()
            for d in SEGUNDO_DIGITO:
                self.conteo_segundo[d] += int(conteo2.get(d, 0))
            del conteo2

        del montos, primeros, segundos

    def finalizar(self):
        """Construye el resultado final (tablas, MAD, Chi², Z-scores, veredictos)
        a partir de los conteos acumulados."""
        tabla1 = tabla_desde_conteo(self.conteo_primer, PRIMER_DIGITO, DIST_PRIMER_DIGITO, self.n_valido_primer)
        mad1 = calcular_mad(tabla1)
        chi2_1, p1 = calcular_chi_cuadrado(tabla1, self.n_valido_primer)
        veredicto1, nivel1 = veredicto_mad_primer_digito(mad1)

        tabla2 = tabla_desde_conteo(self.conteo_segundo, SEGUNDO_DIGITO, DIST_SEGUNDO_DIGITO, self.n_valido_segundo)
        mad2 = calcular_mad(tabla2)
        chi2_2, p2 = calcular_chi_cuadrado(tabla2, self.n_valido_segundo)
        veredicto2, nivel2 = veredicto_mad_segundo_digito(mad2)

        return {
            "primer_digito": {
                "tabla": tabla1,
                "n": self.n_valido_primer,
                "mad": mad1,
                "chi2": chi2_1,
                "p_valor": p1,
                "veredicto": veredicto1,
                "nivel": nivel1,
            },
            "segundo_digito": {
                "tabla": tabla2,
                "n": self.n_valido_segundo,
                "mad": mad2,
                "chi2": chi2_2,
                "p_valor": p2,
                "veredicto": veredicto2,
                "nivel": nivel2,
            },
        }


def analisis_completo(montos: pd.Series):
    """Ejecuta el análisis de primer y segundo dígito sobre una serie de montos
    ya preprocesada (ver `preprocesar_montos`). Atajo de conveniencia sobre
    `AcumuladorDigitos` para analizar una serie completa ya en memoria."""
    acumulador = AcumuladorDigitos(umbral_minimo=0.0)
    acumulador.procesar_chunk(montos)
    return acumulador.finalizar()
