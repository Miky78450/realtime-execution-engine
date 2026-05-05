"""
build_nq_full_clean.py — v2
============================
Fusionne tous les exports NT8 (.txt) en un seul NQ_full_clean.csv.
Supporte les vieux exports (2008+) avec encodages latin-1/cp1252 et
formats de date variés.

Usage :
    py build_nq_full_clean.py
    py build_nq_full_clean.py --input NT8_Data
    py build_nq_full_clean.py --input NT8_Data --output .
"""

import argparse
import glob
import os
import sys

import pandas as pd

# ── Paramètres ────────────────────────────────────────────────────────────────
OUTLIER_PRICE_MIN = 500
OUTLIER_PRICE_MAX = 100_000
MAX_BAR_MOVE_PCT  = 0.10

ENCODINGS = ["utf-8", "latin-1", "cp1252", "utf-8-sig"]

DATE_FORMATS = [
    "%Y%m%d %H%M%S",
    "%Y%m%d%H%M%S",
    "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%Y%m%d %H:%M:%S",
]

# ── Arguments ─────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--input",  default=".",  help="Dossier contenant les fichiers NT8 .txt")
parser.add_argument("--output", default=None, help="Dossier de sortie (defaut = --input)")
args = parser.parse_args()

INPUT_DIR  = args.input
OUTPUT_DIR = args.output if args.output else INPUT_DIR
OUTPUT_CSV = os.path.join(OUTPUT_DIR, "NQ_full_clean.csv")

# ── Detection des fichiers ────────────────────────────────────────────────────
patterns = [
    os.path.join(INPUT_DIR, "NQ_*.txt"),
    os.path.join(INPUT_DIR, "MNQ_*.txt"),
    os.path.join(INPUT_DIR, "NQ *.txt"),
    os.path.join(INPUT_DIR, "MNQ *.txt"),
    os.path.join(INPUT_DIR, "*.Last.txt"),
    os.path.join(INPUT_DIR, "*.txt"),
]

files = []
for p in patterns:
    files.extend(glob.glob(p))
files = sorted(set(files))

if not files:
    print("Aucun fichier .txt trouve dans : " + INPUT_DIR)
    sys.exit(1)

print(str(len(files)) + " fichier(s) detecte(s)\n")


# ── Lecture robuste ───────────────────────────────────────────────────────────
def try_parse_date(series):
    try:
        parsed = pd.to_datetime(series, infer_datetime_format=True, errors="coerce")
        if parsed.notna().mean() > 0.95:
            return parsed
    except Exception:
        pass
    for fmt in DATE_FORMATS:
        try:
            parsed = pd.to_datetime(series.str.strip(), format=fmt, errors="coerce")
            if parsed.notna().mean() > 0.95:
                return parsed
        except Exception:
            continue
    return None


def read_nt8_file(path):
    basename = os.path.basename(path)

    # Trouver l'encodage
    raw_first = None
    working_encoding = None
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as f:
                raw_first = f.readline().strip()
            working_encoding = enc
            break
        except Exception:
            continue

    if raw_first is None:
        print("   ERREUR " + basename + " : encodage inconnu")
        return None

    sep = ";" if ";" in raw_first else ","

    try:
        if sep == ";":
            df = pd.read_csv(
                path, sep=";", header=None,
                names=["dt_raw", "Open", "High", "Low", "Close", "Volume"],
                dtype=str,
                low_memory=False,
                encoding=working_encoding
            )
            dt_series = df["dt_raw"].str.strip()
            dt_no_space = dt_series.str.replace(" ", "")
            parsed = try_parse_date(dt_no_space)
            if parsed is None:
                parsed = try_parse_date(dt_series)
            if parsed is None:
                sample = dt_series.iloc[0] if len(dt_series) > 0 else "?"
                print("   ERREUR " + basename + " : format de date inconnu : " + str(sample))
                return None
            df["dt"] = parsed
            df.drop(columns=["dt_raw"], inplace=True)
            for col in ["Open", "High", "Low", "Close", "Volume"]:
                if col in df.columns:
                    df[col] = df[col].str.replace(",", ".").astype(float, errors="ignore")

        else:
            df = pd.read_csv(
                path, low_memory=False,
                encoding=working_encoding
            )
            df.columns = [c.strip() for c in df.columns]
            rename = {}
            for c in df.columns:
                cl = c.lower().replace(" ", "")
                if cl in ("datetime", "date", "time", "timestamp"): rename[c] = "dt"
                elif cl == "open":   rename[c] = "Open"
                elif cl == "high":   rename[c] = "High"
                elif cl == "low":    rename[c] = "Low"
                elif cl in ("close", "last"): rename[c] = "Close"
                elif cl in ("volume", "vol"): rename[c] = "Volume"
            df.rename(columns=rename, inplace=True)
            if "dt" not in df.columns:
                print("   ERREUR " + basename + " : colonne date introuvable")
                return None
            df["dt"] = try_parse_date(df["dt"].astype(str))

    except Exception as e:
        print("   ERREUR " + basename + " : " + str(e))
        return None

    required = {"dt", "Open", "High", "Low", "Close"}
    if not required.issubset(df.columns):
        print("   ERREUR " + basename + " : colonnes manquantes")
        return None

    if "Volume" not in df.columns:
        df["Volume"] = 0

    df = df[["dt", "Open", "High", "Low", "Close", "Volume"]].copy()

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    n_before = len(df)
    df.dropna(subset=["dt", "Open", "High", "Low", "Close"], inplace=True)
    n_dropped = n_before - len(df)

    if len(df) == 0:
        print("   VIDE " + basename + " : aucune ligne valide")
        return None

    extra = (" (" + str(n_dropped) + " NaN)") if n_dropped else ""
    print("   OK " + basename
          + " [" + working_encoding + "]"
          + " : " + str(len(df)) + " barres"
          + "  " + str(df["dt"].min().date()) + " -> " + str(df["dt"].max().date())
          + extra)
    return df


# ── Lecture ───────────────────────────────────────────────────────────────────
print("Lecture des fichiers...")
frames = [read_nt8_file(f) for f in files]
frames = [df for df in frames if df is not None and not df.empty]

if not frames:
    print("\nAucune donnee valide lue.")
    sys.exit(1)

# ── Fusion ────────────────────────────────────────────────────────────────────
print("\nFusion de " + str(len(frames)) + " fichier(s)...")
combined = pd.concat(frames, ignore_index=True)
combined.sort_values("dt", inplace=True)
combined.drop_duplicates(subset=["dt"], keep="last", inplace=True)
combined.reset_index(drop=True, inplace=True)
print("Barres apres fusion : " + str(len(combined)))

# ── Outliers ──────────────────────────────────────────────────────────────────
print("Nettoyage des outliers...")
mask_bad = (
    (combined["Close"] < OUTLIER_PRICE_MIN) |
    (combined["Close"] > OUTLIER_PRICE_MAX) |
    (combined["High"]  < combined["Low"]) |
    (combined["Close"].pct_change().abs() > MAX_BAR_MOVE_PCT)
)
n_bad = int(mask_bad.sum())
if n_bad:
    print(str(n_bad) + " barre(s) aberrante(s) retiree(s)")
    combined = combined[~mask_bad].copy()
    combined.reset_index(drop=True, inplace=True)
else:
    print("Aucun outlier")

# ── Resume ────────────────────────────────────────────────────────────────────
span = (combined["dt"].max() - combined["dt"].min()).days
print("")
print("=== RESUME ===")
print("Periode  : " + str(combined["dt"].min().date()) + " -> " + str(combined["dt"].max().date()))
print("Duree    : " + str(span) + " jours (~" + str(round(span/365.25, 1)) + " ans)")
print("Barres   : " + str(len(combined)))
print("Prix min : " + str(round(combined["Low"].min(), 2)))
print("Prix max : " + str(round(combined["High"].max(), 2)))

# ── Export ────────────────────────────────────────────────────────────────────
os.makedirs(OUTPUT_DIR, exist_ok=True)
combined.to_csv(OUTPUT_CSV, index=False)
size_mb = round(os.path.getsize(OUTPUT_CSV) / 1_048_576, 1)
print("")
print("Fichier cree : " + OUTPUT_CSV + "  (" + str(size_mb) + " MB)")
print("Pour lancer le backtest :")
print("   py nq_ict_backtester.py --csv " + os.path.basename(OUTPUT_CSV))