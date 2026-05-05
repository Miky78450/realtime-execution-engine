#!/usr/bin/env python3
"""
NQ ICT BACKTESTER — S1 Bear + S2 Bull
S1 Bear : Sweep BSL H1 + RSI(7)<60 → IFVG bearish 1m → Short
S2 Bull : Sweep SSL H1 + RSI(7)>40 → IFVG bullish 1m → Long

Usage :
  python nq_ict_backtester.py --csv NQ_full_clean.csv
  python nq_ict_backtester.py --live

CHANGELOG v2 :
  - Colonne "bias" ajoutée dans chaque trade (bullish / bearish)
  - Affichage du biais dans les 10 derniers trades
"""
import os, sys, warnings
import numpy as np
import pandas as pd
import pytz
from datetime import time

# Force UTF-8 output on Windows (avoids cp1252 UnicodeEncodeError on emoji/box chars)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────
# PARAMÈTRES
# ─────────────────────────────────────────────────────────
TICKER        = "NQ=F"
PARIS_TZ      = pytz.timezone("Europe/Paris")

TRADE_START   = time(2, 0)
TRADE_END     = time(20, 0)

SWING_LB_1H   = 3
SWEEP_BUF     = 3.0
SWEEP_WIN     = 8          # barres 15m
SWEEP_MIN_PTS = 5.0
MAX_ENTRY_DIST= 200.0

FVG_MIN_PTS   = 1.0
FVG_MAX_AGE   = 200
IFVG_SEARCH   = "3h"

MIN_RR        = 2.5
MAX_RR        = 1.5
MIN_RISK_PTS  = 10.0
MAX_SL_PTS    = 20.0

RSI_PERIOD    = 7
RSI_S1_MAX    = 60
RSI_S2_MIN    = 40

POINT_VALUE   = 2.0
RISK_DOLLARS  = 500.0
DAILY_STOP_LOSS = -1000.0
COMMISSION_RT = 1.08
SLIPPAGE_PTS  = 1.0
APPLY_COSTS   = True
LIMIT_ORDER   = True
LIMIT_EXPIRY  = 9   # 9 barres 1m ≈ même fenêtre que 3 barres 3m (9 min)

# ─── CONFIRMATIONS (cibles le pattern de fausses pertes) ─────────
# Activer/désactiver ces flags pour tester l'impact de chaque confirmation
# sur le PF et le Win Rate.

# Confirmation #1 : exiger que la M15 sweepée se ferme du bon côté du level
# Pour S1 Bear : à la clôture M15, c15_final doit être < BSL (sinon faux sweep)
# Pour S2 Bull : à la clôture M15, c15_final doit être > SSL
# Annule les ordres limits placés pendant la M15 si la close finale invalide.
USE_M15_CONFIRMATION = True

# Confirmation #2 : filtre par session
# True = exclut la session asia (02h-09h Paris) qui a un PF marginal
EXCLUDE_ASIA = False
ASIA_END = time(15, 0)  # session asia = TRADE_START → ASIA_END

# Confirmation #3 : distance minimum entry-sweep level
# Évite les IFVG trop proches du wick du sweep (faux signal)
MIN_ENTRY_DIST_PTS = 5.0   # mettre à 0 pour désactiver

CSV_RESULTS   = "nq_ict_backtest_results_1m.csv"

NT8_DATA_DIR  = r"C:\Users\natha\Documents\Backtest\Trading MGB\NT8_Data"
NT8_PATTERN   = "NQ 06-26.Last.txt"

# ─────────────────────────────────────────────────────────
# CHARGEMENT DES DONNÉES
# ─────────────────────────────────────────────────────────
def load_csv(path):
    print(f"📂 Chargement {path}...")
    with open(path) as f:
        first = f.readline().strip()
    if first.startswith("dt,"):
        df = pd.read_csv(path, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True).tz_convert(PARIS_TZ)
    else:
        df = pd.read_csv(path, sep=";", header=None,
                         names=["dt","Open","High","Low","Close","Volume"],
                         on_bad_lines="skip")
        df = df[df["dt"].astype(str).str.match(r"^\d{8}")]
        df["dt"] = pd.to_datetime(df["dt"], format="%Y%m%d %H%M%S")
        df = df.set_index("dt")
        # NT8 exporte en heure LOCALE du PC (Europe/Paris).
        df.index = df.index.tz_localize(PARIS_TZ,
                    ambiguous="infer", nonexistent="shift_forward")
    df = df.sort_index()[~df.index.duplicated()]
    return resample_all(df)


def load_nt8_file(path):
    df = pd.read_csv(path, sep=";", header=None,
                     names=["dt","Open","High","Low","Close","Volume"],
                     on_bad_lines="skip")
    df = df[df["dt"].astype(str).str.match(r"^\d{8}")]
    df["dt"] = pd.to_datetime(df["dt"], format="%Y%m%d %H%M%S")
    df = df.set_index("dt")
    # NT8 exporte en heure LOCALE du PC qui est en Europe/Paris.
    # On localise directement en Paris (pas de conversion depuis Chicago).
    df.index = df.index.tz_localize(PARIS_TZ,
                ambiguous="infer", nonexistent="shift_forward")
    return df.sort_index()[~df.index.duplicated(keep="last")]


def load_nt8_live():
    import glob
    print(f"📂 Recherche fichiers NT8 dans {NT8_DATA_DIR}...")
    pattern_path = os.path.join(NT8_DATA_DIR, NT8_PATTERN)
    files = sorted(glob.glob(pattern_path))
    if not files:
        raise FileNotFoundError(
            f"❌ Aucun fichier NT8 trouvé dans {NT8_DATA_DIR}\n"
            f"   File → Utilities → Historical Data → Export\n"
            f"   Sauvegarde-les dans : {NT8_DATA_DIR}")
    print(f"  ✓ {len(files)} fichier(s) NT8 trouvé(s) :")
    for f in files:
        print(f"    • {os.path.basename(f)}")
    dfs = []
    for f in files:
        try:
            d = load_nt8_file(f)
            print(f"    {os.path.basename(f):<30} {d.index[0].date()} → {d.index[-1].date()} ({len(d):,} barres)")
            dfs.append(d)
        except Exception as e:
            print(f"    ⚠ Erreur lecture {f}: {e}")
    if not dfs:
        raise RuntimeError("❌ Aucun fichier NT8 chargé avec succès")
    d1m = pd.concat(dfs).sort_index()
    d1m = d1m[~d1m.index.duplicated(keep="last")]
    print(f"  ✓ Total : {len(d1m):,} barres 1m | {d1m.index[0].date()} → {d1m.index[-1].date()}")

    cutoff = None
    if os.path.exists(CSV_RESULTS):
        prev = pd.read_csv(CSV_RESULTS)
        if len(prev) > 0:
            prev["entry_time"] = pd.to_datetime(prev["entry_time"], utc=True)
            cutoff = prev["entry_time"].max().tz_convert(PARIS_TZ)
            print(f"  📅 Dernier trade connu : {cutoff}")

    csv_base = "NQ_full_clean.csv"
    hist_for_h1 = None
    if os.path.exists(csv_base):
        print(f"  📂 Fusion avec {csv_base}...")
        hist = pd.read_csv(csv_base, index_col=0)
        hist.index = pd.to_datetime(hist.index, utc=True).tz_convert(PARIS_TZ)
        hist = hist.sort_index()[~hist.index.duplicated()]
        hist_for_h1 = hist  # garder pour d1h
        d1m = pd.concat([hist, d1m]).sort_index()
        d1m = d1m[~d1m.index.duplicated(keep="last")]
        print(f"  ✓ Après fusion : {len(d1m):,} barres | {d1m.index[0].date()} → {d1m.index[-1].date()}")

    agg = {"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}

    # d1h : construit depuis NQ_full_clean.csv directement pour garantir
    # la cohérence avec --csv (le resample de la fusion NT8 donne des bougies H1
    # légèrement différentes qui cassent la détection Dow Theory → neutral partout)
    if hist_for_h1 is not None:
        d1h = hist_for_h1.resample("1h").agg(agg).dropna()
    else:
        d1h = d1m.resample("1h").agg(agg).dropna()

    d15 = d1m.resample("15min").agg(agg).dropna()
    d5  = d1m.resample("5min").agg(agg).dropna()
    d3m = d1m.resample("3min").agg(agg).dropna()

    if cutoff is not None:
        buffer_start = cutoff - pd.Timedelta(hours=48)
        # d1h : PAS de filtre — historique complet nécessaire pour precompute_bias (window=50)
        d15 = d15[d15.index > buffer_start]
        d5  = d5 [d5.index  > buffer_start]
        d3m = d3m[d3m.index > buffer_start]
        d1m = d1m[d1m.index > buffer_start]
        print(f"  🔍 Backtest après {cutoff.date()} (buffer 48h)")

    print(f"  1H:{len(d1h)} 15m:{len(d15)} 5m:{len(d5)} 3m:{len(d3m)} 1m:{len(d1m)}")
    return d1h, d15, d5, d3m, d1m, cutoff


download_live = load_nt8_live  # retourne (d1h, d15, d5, d3m, d1m, cutoff)


def resample_all(df):
    agg = {"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}
    d1h = df.resample("1h").agg(agg).dropna()
    d15 = df.resample("15min").agg(agg).dropna()
    d5  = df.resample("5min").agg(agg).dropna()
    d3m = df.resample("3min").agg(agg).dropna()
    d1m = df.resample("1min").agg(agg).dropna()
    print(f"  1H:{len(d1h)} 15m:{len(d15)} 5m:{len(d5)} 3m:{len(d3m)} 1m:{len(d1m)}")
    print(f"  Période : {d1m.index[0].date()} → {d1m.index[-1].date()}")
    return d1h, d15, d5, d3m, d1m

# ─────────────────────────────────────────────────────────
# LOGIQUE ICT
# ─────────────────────────────────────────────────────────
def calc_rsi(series, period=14):
    # RSI Wilder (EMA alpha=1/period) — aligné sur C# RecalcRsi()
    # Remplace l'ancienne version Cutler (rolling SMA) pour que les valeurs
    # du filtre RSI(7) soient identiques entre backtester et bot NT8.
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta.clip(upper=0))
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def precompute_bias(df1h, window=50, lb=SWING_LB_1H):
    """
    Biais H1 par Dow Theory.
    HH+HL = bullish | LH+LL = bearish | sinon = neutral

    Algorithme exact :
      sub_h = h[i-window : i]   (50 barres glissantes)
      range(lb, m-lb)           (lb=3 inclus, m-lb exclu)
      swing si sub_h[j] == sub_h[max(0,j-lb) : min(m,j+lb+1)].max()
      (égalité stricte numpy — double égal retient les deux)
    """
    h = df1h["High"].values
    l = df1h["Low"].values
    n = len(df1h)
    labels = np.full(n, "neutral", dtype=object)
    for i in range(window, n):
        sub_h = h[max(0,i-window):i]
        sub_l = l[max(0,i-window):i]
        m = len(sub_h)
        sh, sl = [], []
        for j in range(lb, m-lb):
            lo = max(0,j-lb); hi = min(m,j+lb+1)
            if sub_h[j] == sub_h[lo:hi].max(): sh.append(float(sub_h[j]))
            if sub_l[j] == sub_l[lo:hi].min(): sl.append(float(sub_l[j]))
        if len(sh)>=2 and len(sl)>=2:
            hh = sh[-1]>sh[-2]; hl = sl[-1]>sl[-2]
            lh = sh[-1]<sh[-2]; ll = sl[-1]<sl[-2]
            if   hh and hl: labels[i] = "bullish"
            elif lh and ll: labels[i] = "bearish"
    return pd.Series(labels, index=df1h.index)


def get_bias(bias_series, ts):
    idx = bias_series.index.searchsorted(ts, side="right") - 1
    if idx < 0:
        return "neutral"
    return bias_series.iloc[idx]


def precompute_h1_levels(df1h, lb=SWING_LB_1H, lookback=100):
    h = df1h["High"].values
    l = df1h["Low"].values
    n = len(df1h)
    is_sh = np.zeros(n, dtype=bool)
    is_sl = np.zeros(n, dtype=bool)
    for j in range(lb, n-lb):
        lo, hi = max(0,j-lb), min(n,j+lb+1)
        if h[j] == h[lo:hi].max(): is_sh[j] = True
        if l[j] == l[lo:hi].min(): is_sl[j] = True
    bsl_list = []
    ssl_list = []
    for i in range(n):
        start = max(0, i-lookback)
        bsl_list.append(sorted(set(float(h[j]) for j in range(start, i) if is_sh[j])))
        ssl_list.append(sorted(set(float(l[j]) for j in range(start, i) if is_sl[j]), reverse=True))
    return bsl_list, ssl_list, df1h.index


def get_h1_levels_fast(bsl_list, ssl_list, h1_index, ts):
    idx = h1_index.searchsorted(ts, side="right") - 1
    if idx < 0:
        return [], []
    return bsl_list[idx], ssl_list[idx]


def get_h1_levels(df1h, ts, lb=SWING_LB_1H):
    """Pour mode --live."""
    sub = df1h[df1h.index <= ts].tail(100)
    h = sub["High"].values
    l = sub["Low"].values
    bsl, ssl = [], []
    for j in range(lb, len(h)-lb):
        lo, hi = max(0,j-lb), min(len(h),j+lb+1)
        if h[j] == h[lo:hi].max(): bsl.append(float(h[j]))
        if l[j] == l[lo:hi].min(): ssl.append(float(l[j]))
    return sorted(set(bsl)), sorted(set(ssl), reverse=True)


def find_ifvg(df_ltf, ts_start, ts_end, direction):
    mask = (df_ltf.index >= ts_start) & (df_ltf.index < ts_end)
    bars = df_ltf[mask]
    if bars.empty:
        return None, None, None

    h = df_ltf["High"].values
    l = df_ltf["Low"].values
    o = df_ltf["Open"].values
    c = df_ltf["Close"].values
    df_ltf = df_ltf[~df_ltf.index.duplicated(keep="last")]
    s = df_ltf.index.get_indexer([ts_start], method="nearest")[0]

    fvgs = []
    for k in range(2, min(s, len(h))):
        if direction == "bear":
            if h[k-2] < l[k] and (l[k]-h[k-2]) >= FVG_MIN_PTS:
                fvgs.append({"top":l[k], "bot":h[k-2], "idx":k})
        else:
            if l[k-2] > h[k] and (l[k-2]-h[k]) >= FVG_MIN_PTS:
                fvgs.append({"top":l[k-2], "bot":h[k], "idx":k})
    fvgs = [f for f in fvgs if s-f["idx"] <= FVG_MAX_AGE]

    for _, bar in bars.iterrows():
        bi = df_ltf.index.get_loc(bar.name)
        if bi < 2:
            continue
        price = float(bar["Close"])
        o_bar = float(bar["Open"])
        is_bear = price < o_bar
        is_bull = price > o_bar

        if direction == "bear":
            if h[bi-2] < l[bi] and (l[bi]-h[bi-2]) >= FVG_MIN_PTS:
                fvgs.append({"top":l[bi], "bot":h[bi-2], "idx":bi})
        else:
            if l[bi-2] > h[bi] and (l[bi-2]-h[bi]) >= FVG_MIN_PTS:
                fvgs.append({"top":l[bi-2], "bot":h[bi], "idx":bi})

        for f in fvgs:
            if f["idx"] >= bi:
                continue
            if direction == "bear" and is_bear and price < f["bot"]-0.25 and f["bot"]-price <= 50:
                limit_price = price
                sl_price    = max(o_bar, price)+0.25
                if LIMIT_ORDER:
                    for k in range(1, LIMIT_EXPIRY+1):
                        if bi+k >= len(h): break
                        if l[bi+k] <= limit_price:
                            return limit_price, df_ltf.index[bi+k], sl_price
                    return "MISSED", None, None
                return price, bar.name, sl_price

            if direction == "bull" and is_bull and price > f["top"]+0.25 and price-f["top"] <= 50:
                limit_price = price
                sl_price    = min(o_bar, price)-0.25
                if LIMIT_ORDER:
                    for k in range(1, LIMIT_EXPIRY+1):
                        if bi+k >= len(h): break
                        if h[bi+k] >= limit_price:
                            return limit_price, df_ltf.index[bi+k], sl_price
                    return "MISSED", None, None
                return price, bar.name, sl_price

    return None, None, None


def calc_tp(entry, sl, levels, direction):
    risk = max(abs(sl-entry), MIN_RISK_PTS)
    if direction == "bear":
        return entry - risk * MIN_RR, MIN_RR
    else:
        return entry + risk * MIN_RR, MIN_RR


def qty(entry, sl):
    return max(1, int(RISK_DOLLARS / (max(abs(sl-entry), MIN_RISK_PTS) * POINT_VALUE)))


def get_session(ts):
    t = ts.time()
    if time(9,0) <= t < time(15,30): return "london"
    if time(15,30) <= t < time(22,0): return "ny"
    return "asia"


# ─────────────────────────────────────────────────────────
# DÉSAMBIGUÏSATION SL/TP MÊME BOUGIE via 1m
# ─────────────────────────────────────────────────────────
def resolve_same_candle_1m(df1m, candle_ts, sl, tp, direction, slip):
    end_ts = candle_ts + pd.Timedelta(minutes=15)
    mask = (df1m.index >= candle_ts) & (df1m.index < end_ts)
    bars_1m = df1m[mask]
    if bars_1m.empty:
        if direction == "short":
            return "loss", sl + slip, "1m_fallback_SL"
        else:
            return "loss", sl - slip, "1m_fallback_SL"
    for _, bar in bars_1m.iterrows():
        h1 = float(bar["High"])
        l1 = float(bar["Low"])
        if direction == "short":
            tp_hit = l1 <= tp
            sl_hit = h1 >= sl
            if sl_hit and tp_hit:
                o1 = float(bar["Open"])
                if o1 <= tp:
                    return "win", tp + slip, "1m_TP_first"
                else:
                    return "loss", sl + slip, "1m_SL_first"
            elif tp_hit:
                return "win", tp + slip, "1m_TP_first"
            elif sl_hit:
                return "loss", sl + slip, "1m_SL_first"
        else:
            tp_hit = h1 >= tp
            sl_hit = l1 <= sl
            if sl_hit and tp_hit:
                o1 = float(bar["Open"])
                if o1 >= tp:
                    return "win", tp - slip, "1m_TP_first"
                else:
                    return "loss", sl - slip, "1m_SL_first"
            elif tp_hit:
                return "win", tp - slip, "1m_TP_first"
            elif sl_hit:
                return "loss", sl - slip, "1m_SL_first"
    if direction == "short":
        return "loss", sl + slip, "1m_fallback_SL"
    else:
        return "loss", sl - slip, "1m_fallback_SL"


# ─────────────────────────────────────────────────────────
# BACKTEST
# ─────────────────────────────────────────────────────────
def run_backtest(df1h, df15, df5, df3m, df1m=None):
    print("\n🔄 Pré-calcul biais H1...")
    bmap = precompute_bias(df1h)
    print("🔄 Pré-calcul niveaux BSL/SSL H1...")
    bsl_precomp, ssl_precomp, h1_index = precompute_h1_levels(df1h)

    df15 = df15[df15.index >= df5.index[0]] if not df5.empty else df15
    if df15.empty:
        print("⚠ Pas de données 15m"); return pd.DataFrame(), bmap

    print(f"  Période : {df15.index[0].date()} → {df15.index[-1].date()}")

    h15 = df15["High"].values
    l15 = df15["Low"].values
    o15 = df15["Open"].values
    c15 = df15["Close"].values
    n15 = len(df15)

    rsi_15m = calc_rsi(df15["Close"], period=RSI_PERIOD)

    # ─── DÉTECTION SWEEP RÉALISTE (mode "M15 partielle") ───────────
    # Au lieu de détecter le sweep avec les valeurs FINALES de la M15
    # (qui contiennent le futur jusqu'à la clôture M15), on simule le live :
    # on itère sur les M3, et à chaque M3 on construit un h15/l15/c15 PARTIEL
    # depuis le début de la M15 en cours jusqu'à la M3 courante.
    # Le sweep peut alors être détecté dès la 1ère M3 si les conditions sont
    # remplies — exactement comme NT8 le fait en live.
    h3m = df3m["High"].values
    l3m = df3m["Low"].values
    c3m = df3m["Close"].values
    o3m = df3m["Open"].values
    ts3m = df3m.index

    # Pour chaque M3, calculer son index de M15 et sa position dans la M15 (0-4)
    # M15 contient 5 M3 (0:00, 0:03, 0:06, 0:09, 0:12)
    m15_open_per_3m = ts3m.floor("15min")
    # idx M15 correspondant à chaque M3
    m15_idx_per_3m = df15.index.searchsorted(m15_open_per_3m, side="right") - 1
    # rang de la M3 dans la M15 (0 = première, 4 = dernière)
    n3_per_3m = ((ts3m - m15_open_per_3m).total_seconds() / 60 / 3).astype(int)

    trades        = []
    active_trade  = None
    recent_sweeps = []
    diag = {"kz":0, "neutral":0, "s1_sw":0, "s1_tr":0, "s2_sw":0, "s2_tr":0}
    daily_pnl     = {}   # PnL réalisé par jour
    daily_entries = {}   # nb de trades entrés par jour (exit pas forcément clôturé)
    daily_losses  = {}   # nb de pertes comptées au moment de l'entrée suivante
    blocked_dates = set()   # dates où le stop journalier a été atteint

    print("\n🔄 Simulation...")
    n_total = n15 - SWING_LB_1H - 11
    for i in range(SWING_LB_1H+10, n15-1):
        if (i - SWING_LB_1H - 10) % 20000 == 0:
            pct = (i - SWING_LB_1H - 10) / max(1, n_total) * 100
            print(f"  ... {pct:.0f}% ({df15.index[i].date()})", flush=True)
        ts = df15.index[i]
        if active_trade:
            tr = active_trade
            # ── FORCE CLOSE TIMING : si la bougie courante ts est APRÈS
            # 20h le jour de l'entry, on clôture peu importe la date/heure.
            # Ça gère les trous de data (pauses CME) qui laissaient des trades ouverts.
            entry_dt = tr["entry_time"]
            end_of_session = entry_dt.replace(hour=TRADE_END.hour, minute=0, second=0, microsecond=0)
            # Cas weekend : si l'entry est un vendredi, le trade doit impérativement
            # être fermé avant la fin de la session vendredi
            if ts >= end_of_session and i > tr.get("entry_i15", -1) and tr.get("exit") is None:
                exit_px = c15[i]
                if tr["direction"] == "short":
                    pnl_pts = tr["entry"] - exit_px
                else:
                    pnl_pts = exit_px - tr["entry"]
                risk_pts = abs(tr["entry"] - tr["sl"])
                r_mult = pnl_pts / risk_pts if risk_pts > 0 else 0
                result = "win" if r_mult > 0 else "loss"
                tr.update({"exit": exit_px, "exit_time": end_of_session,
                           "result": result, "exit_note": "EOD_CLOSE", "rr": abs(r_mult)})
                pnl = r_mult * RISK_DOLLARS
                daily_pnl[end_of_session.date()] = daily_pnl.get(end_of_session.date(), 0) + pnl
                trades.append(tr); active_trade = None
                continue

        if ts.weekday() >= 5:
            # Weekend : sécurité (le EOD_CLOSE ci-dessus devrait déjà avoir fermé)
            if active_trade:
                tr = active_trade
                if i > tr.get("entry_i15", -1):
                    exit_px = c15[i]
                    if tr["direction"] == "short":
                        pnl_pts = tr["entry"] - exit_px
                    else:
                        pnl_pts = exit_px - tr["entry"]
                    risk_pts = abs(tr["entry"] - tr["sl"])
                    r_mult = pnl_pts / risk_pts if risk_pts > 0 else 0
                    result = "win" if r_mult > 0 else "loss"
                    tr.update({"exit": exit_px, "exit_time": ts, "result": result,
                               "exit_note": "WEEKEND_CLOSE", "rr": abs(r_mult)})
                    pnl = r_mult * RISK_DOLLARS
                    daily_pnl[ts.date()] = daily_pnl.get(ts.date(), 0) + pnl
                    trades.append(tr); active_trade = None
            continue
        t  = ts.time()
        if not (TRADE_START <= t < TRADE_END):
            continue
        # Confirmation #2 : exclut Asia si flag activé
        if EXCLUDE_ASIA and t < ASIA_END:
            continue
        diag["kz"] += 1

        # ── Gestion trade ouvert ──────────────────────────
        if active_trade:
            tr = active_trade
            if i <= tr.get("entry_i15", -1):
                continue

            # ── Confirmation #1 : valider à la clôture M15 que le sweep tient ──
            # Vérifié ici, AVANT la résolution SL/TP, uniquement si le trade
            # n'a pas encore d'exit (SL/TP pas encore touché).
            if USE_M15_CONFIRMATION and tr.get("exit") is None:
                confirm_idx = tr.get("_confirm_check_i15")
                if confirm_idx is not None and i > confirm_idx:
                    c_final = c15[confirm_idx]
                    level = tr["_confirm_level"]
                    invalidate = False
                    if tr["direction"] == "short":
                        if c_final >= level:
                            invalidate = True
                    else:
                        if c_final <= level:
                            invalidate = True
                    if invalidate:
                        exit_px = c_final
                        if tr["direction"] == "short":
                            pnl_pts = tr["entry"] - exit_px
                        else:
                            pnl_pts = exit_px - tr["entry"]
                        risk_pts = abs(tr["entry"] - tr["sl"])
                        r_mult = pnl_pts / risk_pts if risk_pts > 0 else 0
                        result = "win" if r_mult > 0 else "loss"
                        tr.update({"exit": exit_px, "exit_time": ts,
                                   "result": result,
                                   "exit_note": "M15_INVALIDATED",
                                   "rr": abs(r_mult)})
                        pnl = r_mult * RISK_DOLLARS
                        daily_pnl[ts.date()] = daily_pnl.get(ts.date(), 0) + pnl
                        if daily_pnl[ts.date()] <= DAILY_STOP_LOSS:
                            blocked_dates.add(ts.date())
                        entry_date = tr.get("entry_time").date()
                        daily_losses[entry_date] = max(0, daily_losses.get(entry_date, 0) - 1)
                        trades.append(tr); active_trade = None; continue
                    else:
                        # Sweep confirmé : on réinitialise pour ne pas re-checker
                        tr["_confirm_check_i15"] = None

            slip = 0.0 if LIMIT_ORDER else SLIPPAGE_PTS
            if tr["direction"] == "short":
                tp_hit = l15[i] <= tr["tp"]
                sl_hit = h15[i] >= tr["sl"]
                if sl_hit and tp_hit:
                    if df1m is not None:
                        result, exit_px, note = resolve_same_candle_1m(
                            df1m, ts, tr["sl"], tr["tp"], "short", slip)
                    else:
                        result, exit_px, note = "loss", tr["sl"] + slip, "same_candle_fallback_SL"
                    tr.update({"exit":exit_px,"exit_time":ts,"result":result,"exit_note":note})
                    pnl = tr["rr"] * RISK_DOLLARS if result == "win" else -RISK_DOLLARS
                    daily_pnl[ts.date()] = daily_pnl.get(ts.date(), 0) + pnl
                    if daily_pnl[ts.date()] <= DAILY_STOP_LOSS:
                        blocked_dates.add(ts.date())
                    # Libère le "slot de perte engagée" du trade qui vient de se fermer
                    entry_date = tr.get("entry_time").date()
                    daily_losses[entry_date] = max(0, daily_losses.get(entry_date, 0) - 1)
                    trades.append(tr); active_trade=None; continue
                if tp_hit:
                    exit_px = tr["tp"] + slip
                    tr.update({"exit":exit_px,"exit_time":ts,"result":"win","exit_note":"TP"})
                    pnl = tr["rr"] * RISK_DOLLARS
                    daily_pnl[ts.date()] = daily_pnl.get(ts.date(), 0) + pnl
                    if daily_pnl[ts.date()] <= DAILY_STOP_LOSS:
                        blocked_dates.add(ts.date())
                    entry_date = tr.get("entry_time").date()
                    daily_losses[entry_date] = max(0, daily_losses.get(entry_date, 0) - 1)
                    trades.append(tr); active_trade=None; continue
                if sl_hit:
                    exit_px = tr["sl"] + slip
                    tr.update({"exit":exit_px,"exit_time":ts,"result":"loss","exit_note":"SL"})
                    pnl = -RISK_DOLLARS
                    daily_pnl[ts.date()] = daily_pnl.get(ts.date(), 0) + pnl
                    if daily_pnl[ts.date()] <= DAILY_STOP_LOSS:
                        blocked_dates.add(ts.date())
                    entry_date = tr.get("entry_time").date()
                    daily_losses[entry_date] = max(0, daily_losses.get(entry_date, 0) - 1)
                    trades.append(tr); active_trade=None; continue
            else:
                tp_hit = h15[i] >= tr["tp"]
                sl_hit = l15[i] <= tr["sl"]
                if sl_hit and tp_hit:
                    if df1m is not None:
                        result, exit_px, note = resolve_same_candle_1m(
                            df1m, ts, tr["sl"], tr["tp"], "long", slip)
                    else:
                        result, exit_px, note = "loss", tr["sl"] - slip, "same_candle_fallback_SL"
                    tr.update({"exit":exit_px,"exit_time":ts,"result":result,"exit_note":note})
                    pnl = tr["rr"] * RISK_DOLLARS if result == "win" else -RISK_DOLLARS
                    daily_pnl[ts.date()] = daily_pnl.get(ts.date(), 0) + pnl
                    if daily_pnl[ts.date()] <= DAILY_STOP_LOSS:
                        blocked_dates.add(ts.date())
                    entry_date = tr.get("entry_time").date()
                    daily_losses[entry_date] = max(0, daily_losses.get(entry_date, 0) - 1)
                    trades.append(tr); active_trade=None; continue
                if tp_hit:
                    exit_px = tr["tp"] - slip
                    tr.update({"exit":exit_px,"exit_time":ts,"result":"win","exit_note":"TP"})
                    pnl = tr["rr"] * RISK_DOLLARS
                    daily_pnl[ts.date()] = daily_pnl.get(ts.date(), 0) + pnl
                    if daily_pnl[ts.date()] <= DAILY_STOP_LOSS:
                        blocked_dates.add(ts.date())
                    entry_date = tr.get("entry_time").date()
                    daily_losses[entry_date] = max(0, daily_losses.get(entry_date, 0) - 1)
                    trades.append(tr); active_trade=None; continue
                if sl_hit:
                    exit_px = tr["sl"] - slip
                    tr.update({"exit":exit_px,"exit_time":ts,"result":"loss","exit_note":"SL"})
                    pnl = -RISK_DOLLARS
                    daily_pnl[ts.date()] = daily_pnl.get(ts.date(), 0) + pnl
                    if daily_pnl[ts.date()] <= DAILY_STOP_LOSS:
                        blocked_dates.add(ts.date())
                    entry_date = tr.get("entry_time").date()
                    daily_losses[entry_date] = max(0, daily_losses.get(entry_date, 0) - 1)
                    trades.append(tr); active_trade=None; continue
            continue

        # ── Stop journalier (pré-check basé sur bougie 15m courante) ───────
        # Note : un deuxième check plus strict est fait juste avant la création
        # du trade, basé sur la DATE D'ENTRÉE RÉELLE (qui peut être sur une autre
        # bougie 3m plus tard, voire un autre jour).
        if ts.date() in blocked_dates:
            continue
        if daily_pnl.get(ts.date(), 0) <= DAILY_STOP_LOSS:
            blocked_dates.add(ts.date())
            continue

        bias = get_bias(bmap, ts)
        if bias == "neutral": diag["neutral"] += 1; continue

        bsl_lvls, ssl_lvls = get_h1_levels_fast(bsl_precomp, ssl_precomp, h1_index, ts)

        # ── Purger sweeps anciens (1 <= age <= SWEEP_WIN barres 15m) ──
        recent_sweeps = [s for s in recent_sweeps if 1 <= (i-s["idx"]) <= SWEEP_WIN]
        # Garder 1 sweep actif par type (le plus récent)
        bsl_active = [s for s in recent_sweeps if s["type"]=="bsl"]
        ssl_active = [s for s in recent_sweeps if s["type"]=="ssl"]
        recent_sweeps = []
        if bsl_active: recent_sweeps.append(max(bsl_active, key=lambda x: x["idx"]))
        if ssl_active: recent_sweeps.append(max(ssl_active, key=lambda x: x["idx"]))

        # ── Détecter sweeps avec M15 PARTIELLE (réaliste mode live) ──
        # On itère sur les M3 de la M15 courante (i). Pour chaque M3, on
        # construit h15/l15/c15 partiel et on teste les conditions sweep.
        # Dès qu'un sweep partiel est détecté, on mémorise le timestamp M3
        # de détection (pour la recherche IFVG ultérieure).

        # Trouver les indices M3 qui appartiennent à cette M15
        m3_mask = (m15_idx_per_3m == i)
        m3_indices = np.where(m3_mask)[0]
        if len(m3_indices) == 0:
            continue  # pas de M3 pour cette M15, skip

        # Sweep BSL : on cherche la PREMIÈRE M3 où conditions remplies
        bsl_sweep_found = None
        for k_pos, k in enumerate(m3_indices):
            # h15_partial = max High des M3 0..k_pos
            h15_p = float(np.max(h3m[m3_indices[:k_pos+1]]))
            l15_p = float(np.min(l3m[m3_indices[:k_pos+1]]))
            c15_p = float(c3m[k])  # close de la M3 courante = close partielle de la M15
            for lvl in bsl_lvls:
                wick = h15_p - lvl
                if h15_p > lvl + SWEEP_BUF and c15_p < lvl and wick >= SWEEP_MIN_PTS:
                    bsl_sweep_found = {"type":"bsl", "level":lvl, "idx":i,
                                       "ts_detect":ts3m[k], "k3m":k}
                    break
            if bsl_sweep_found is not None:
                break
        if bsl_sweep_found is not None:
            recent_sweeps.append(bsl_sweep_found)

        # Sweep SSL : idem
        ssl_sweep_found = None
        for k_pos, k in enumerate(m3_indices):
            h15_p = float(np.max(h3m[m3_indices[:k_pos+1]]))
            l15_p = float(np.min(l3m[m3_indices[:k_pos+1]]))
            c15_p = float(c3m[k])
            for lvl in ssl_lvls:
                wick = lvl - l15_p
                if l15_p < lvl - SWEEP_BUF and c15_p > lvl and wick >= SWEEP_MIN_PTS:
                    ssl_sweep_found = {"type":"ssl", "level":lvl, "idx":i,
                                       "ts_detect":ts3m[k], "k3m":k}
                    break
            if ssl_sweep_found is not None:
                break
        if ssl_sweep_found is not None:
            recent_sweeps.append(ssl_sweep_found)

        def search_ifvg(direction, tp_fn, levels, setup_num, sweep_level, bias_val, ts_detect):
            # ── Recherche IFVG démarre sur la M3 de détection ──
            # ts_detect = timestamp de la M3 où le sweep partiel a été détecté.
            # NT8 utilise (CurrentBar - lastSweepBar) >= 0 — donc la recherche
            # démarre sur la M3 de détection elle-même (pas la suivante).
            te = ts_detect.normalize()+pd.Timedelta(hours=TRADE_END.hour)
            se = min(ts_detect+pd.Timedelta(IFVG_SEARCH), te)
            ep, et, sl_ltf = find_ifvg(df1m, ts_detect, se, direction)
            tf = "1m"
            if ep == "MISSED" or ep is None:
                return None
            if abs(ep-sweep_level) > MAX_ENTRY_DIST: return None
            # Confirmation #3 : distance minimum entry-sweep_level
            if abs(ep-sweep_level) < MIN_ENTRY_DIST_PTS: return None
            if direction == "bear":
                sl = max(sl_ltf, ep+MIN_RISK_PTS)
                if sl-ep > MAX_SL_PTS: return None
                tp, rr = tp_fn(ep, sl, levels, "bear")
                if sl <= ep: return None
            else:
                sl = min(sl_ltf, ep-MIN_RISK_PTS)
                if ep-sl > MAX_SL_PTS: return None
                tp, rr = tp_fn(ep, sl, levels, "bull")
                if sl >= ep: return None
            # Filtre : si l'entrée réelle tombe hors de la fenêtre 02h-20h, on refuse.
            # search_ifvg peut retourner un fill sur une bougie 3m postérieure
            # au sweep, qui peut être hors session.
            et_time = et.time()
            if not (TRADE_START <= et_time < TRADE_END):
                return None
            if EXCLUDE_ASIA and et_time < ASIA_END:
                return None
            if et.weekday() >= 5:
                return None

            # entry_i15 = index de la bougie 15m qui CONTIENT l'entry réelle
            # (pas la bougie du sweep). Bug fix : sinon l'exit peut être évalué
            # sur des bougies 15m antérieures à l'entry réelle.
            entry_i15_real = df15.index.searchsorted(et, side="right") - 1
            if entry_i15_real < 0:
                entry_i15_real = i
            return {"setup":setup_num, "direction":"short" if direction=="bear" else "long",
                    "bias":bias_val,
                    "session":get_session(ts), "entry":ep, "sl":sl, "tp":tp, "rr":rr,
                    "qty":qty(ep,sl), "entry_time":et, "entry_tf":tf,
                    "exit":None, "exit_time":None, "result":None, "exit_note":"",
                    "entry_i15":entry_i15_real}

        rsi_val = float(rsi_15m.iloc[i]) if i < len(rsi_15m) else 50

        # ── S1 Bear ───────────────────────────────────────
        bsl_sw = [s for s in recent_sweeps if s["type"]=="bsl"]
        if bias=="bearish" and bsl_sw and rsi_val < RSI_S1_MAX:
            diag["s1_sw"] += 1
            sw = bsl_sw[-1]
            tr = search_ifvg("bear", calc_tp, ssl_lvls, 1, sw["level"], bias, sw.get("ts_detect", ts))
            if tr:
                entry_d = tr["entry_time"].date()
                # ── Vérif ROBUSTE du stop journalier (3 méthodes combinées) ──
                # 1. blocked_dates : set de dates déjà blockées (mis à jour aux exits)
                if entry_d in blocked_dates:
                    continue
                # 2. daily_pnl direct
                if daily_pnl.get(entry_d, 0) <= DAILY_STOP_LOSS:
                    blocked_dates.add(entry_d)
                    continue
                # 3. Recalcul depuis trades[] au cas où daily_pnl est pas en phase
                realized_entry_day = sum(
                    (t["rr"]*RISK_DOLLARS if t["result"]=="win" else -RISK_DOLLARS)
                    for t in trades
                    if t.get("exit_time") is not None
                    and t["exit_time"] <= tr["entry_time"]
                    and t["exit_time"].date() == entry_d
                )
                if realized_entry_day <= DAILY_STOP_LOSS:
                    blocked_dates.add(entry_d)
                    continue
                diag["s1_tr"] += 1
                # Stocker pour confirmation M15 au prochain bar
                if USE_M15_CONFIRMATION:
                    tr["_confirm_check_i15"] = i
                    tr["_confirm_level"] = sw["level"]
                active_trade=tr
                recent_sweeps=[]; continue

        # ── S2 Bull ───────────────────────────────────────
        ssl_sw = [s for s in recent_sweeps if s["type"]=="ssl"]
        if bias=="bullish" and ssl_sw and rsi_val > RSI_S2_MIN:
            diag["s2_sw"] += 1
            sw = ssl_sw[-1]
            tr = search_ifvg("bull", calc_tp, bsl_lvls, 2, sw["level"], bias, sw.get("ts_detect", ts))
            if tr:
                entry_d = tr["entry_time"].date()
                if entry_d in blocked_dates:
                    continue
                if daily_pnl.get(entry_d, 0) <= DAILY_STOP_LOSS:
                    blocked_dates.add(entry_d)
                    continue
                realized_entry_day = sum(
                    (t["rr"]*RISK_DOLLARS if t["result"]=="win" else -RISK_DOLLARS)
                    for t in trades
                    if t.get("exit_time") is not None
                    and t["exit_time"] <= tr["entry_time"]
                    and t["exit_time"].date() == entry_d
                )
                if realized_entry_day <= DAILY_STOP_LOSS:
                    blocked_dates.add(entry_d)
                    continue
                diag["s2_tr"] += 1
                if USE_M15_CONFIRMATION:
                    tr["_confirm_check_i15"] = i
                    tr["_confirm_level"] = sw["level"]
                active_trade=tr
                recent_sweeps=[]; continue

    print(f"\n  ✓ {len(trades)} trades\n")
    print(f"  DIAGNOSTIC :")
    print(f"  Bougies en fenêtre   : {diag['kz']}")
    print(f"  Biais neutral ignoré : {diag['neutral']}")
    print(f"  S1 | Sweeps:{diag['s1_sw']:>4} | Trades:{diag['s1_tr']:>4}")
    print(f"  S2 | Sweeps:{diag['s2_sw']:>4} | Trades:{diag['s2_tr']:>4}")

    return pd.DataFrame(trades), bmap


# ─────────────────────────────────────────────────────────
# STATISTIQUES
# ─────────────────────────────────────────────────────────
def max_streak(results, target):
    mx = cur = 0
    streaks = []
    for r in results:
        if r == target: cur += 1; mx = max(mx, cur)
        else:
            if cur > 0: streaks.append(cur)
            cur = 0
    if cur > 0: streaks.append(cur)
    return mx, (sum(streaks)/len(streaks) if streaks else 0)


def print_stats(df):
    if df.empty or "result" not in df.columns:
        print("❌ Aucun trade."); return None

    df = df.dropna(subset=["result"]).copy()
    if df.empty:
        print("❌ Aucun trade clôturé."); return None

    total  = len(df)
    wins   = (df["result"]=="win").sum()
    losses = (df["result"]=="loss").sum()
    wr     = wins/total*100
    win_r  = df[df["result"]=="win"]["rr"].sum()
    pf     = win_r/losses if losses>0 else float("inf")
    avg_r  = df["rr"].mean()

    risk_pts = (df["sl"]-df["entry"]).abs().clip(lower=1.0)
    risk_dol = risk_pts * POINT_VALUE
    if APPLY_COSTS:
        cost_dol  = COMMISSION_RT
        cost_r    = cost_dol / risk_dol
        df["r_pnl"] = df.apply(
            lambda r: r["rr"]-cost_r[r.name] if r["result"]=="win"
                      else -1.0-cost_r[r.name], axis=1)
        slip_info = f"slip {SLIPPAGE_PTS}pts dans prix exit" if not LIMIT_ORDER else "slip:0 (limite)"
        print(f"  Frais appliqués   : comm:{COMMISSION_RT}$ + {slip_info}")
        print(f"  Coût moyen en R   : {cost_r.mean():.3f}R/trade")
    else:
        df["r_pnl"] = df.apply(lambda r: r["rr"] if r["result"]=="win" else -1.0, axis=1)

    df["equity_r"] = df["r_pnl"].cumsum()
    eq = df["equity_r"].iloc[-1]
    dd = (df["equity_r"]-df["equity_r"].cummax()).min()

    max_l, avg_l = max_streak(df["result"].tolist(), "loss")
    max_w, avg_w = max_streak(df["result"].tolist(), "win")

    if "exit_note" in df.columns:
        n_1m_sl  = df["exit_note"].str.startswith("1m_SL").sum()
        n_1m_tp  = df["exit_note"].str.startswith("1m_TP").sum()
        n_1m_fb  = df["exit_note"].str.contains("fallback").sum()
        same_candle = n_1m_sl + n_1m_tp + n_1m_fb
    else:
        n_1m_sl = n_1m_tp = n_1m_fb = same_candle = 0

    sep = "="*54
    print(f"\n{sep}")
    print(f"  RÉSULTATS — NQ ICT (S1 Bear + S2 Bull)")
    print(sep)
    print(f"  Période           : {df['entry_time'].min().date()} → {df['entry_time'].max().date()}")
    print(f"  Trades            : {total} (wins:{wins} | losses:{losses})")
    if same_candle:
        print(f"  ⚠ SL/TP même bougie (désambiguïsés via 1m) : {same_candle} trades")
        print(f"    → SL d'abord:{n_1m_sl}  TP d'abord:{n_1m_tp}  fallback SL:{n_1m_fb}")
    print(f"  Win Rate          : {wr:.1f}%")
    print(f"  RR moyen          : {avg_r:.2f}R")
    print(f"  Profit Factor     : {pf:.2f}")
    print(sep)
    print(f"  Pertes consécutives max   : {max_l}")
    print(f"  Pertes consécutives moy   : {avg_l:.1f}")
    print(f"  Gains consécutifs max     : {max_w}")
    print(f"  Gains consécutifs moy     : {avg_w:.1f}")
    print(sep)

    names = {1:"S1 Bear", 2:"S2 Bull"}
    for sid in sorted(df["setup"].unique()):
        s  = df[df["setup"]==sid]
        sw = (s["result"]=="win").sum()
        sl = (s["result"]=="loss").sum()
        swr = sw/len(s)*100
        spf = s[s["result"]=="win"]["rr"].sum()/sl if sl>0 else float("inf")
        ml, _ = max_streak(s["result"].tolist(), "loss")
        sess = " | ".join(
            f"{se}:{(s[s['session']==se]['result']=='win').sum()}/{len(s[s['session']==se])}"
            for se in ["london","ny"] if len(s[s["session"]==se])>0
        )
        print(f"  {names.get(sid,'?'):8s} | {sess} | WR:{swr:.0f}% RR:{s['rr'].mean():.2f} PF:{spf:.2f} | Max pertes:{ml}")

    print(f"\n  Equity   : {eq:+.1f}R  ({eq*RISK_DOLLARS:+,.0f}$)")
    print(f"  Max DD   : {dd:.1f}R  ({dd*RISK_DOLLARS:,.0f}$)")
    print(sep)

    return df


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    sep = "="*54
    print(f"\n{sep}")
    print(f"  NQ ICT BACKTESTER — S1 Bear + S2 Bull")
    print(f"  Sweep BSL/SSL H1 + RSI({RSI_PERIOD}) → IFVG 1m")
    print(sep+"\n")

    live_mode = "--live" in sys.argv

    if "--csv" in sys.argv:
        idx = sys.argv.index("--csv")
        df1h, df15, df5, df3m, df1m = load_csv(sys.argv[idx+1])
        live_cutoff = None
    elif live_mode:
        df1h, df15, df5, df3m, df1m, live_cutoff = download_live()
    else:
        print("Usage : python nq_ict_backtester.py --csv fichier.csv")
        print("        python nq_ict_backtester.py --live")
        sys.exit(0)

    df_trades, bmap = run_backtest(df1h, df15, df5, df3m, df1m)
    results = print_stats(df_trades)

    if results is not None:
        if live_mode and os.path.exists(CSV_RESULTS):
            existing = pd.read_csv(CSV_RESULTS)
            existing["entry_time"] = pd.to_datetime(existing["entry_time"]).dt.tz_localize(PARIS_TZ, ambiguous="infer", nonexistent="shift_forward")
            results["entry_time"]  = pd.to_datetime(results["entry_time"],  utc=True).dt.tz_convert(PARIS_TZ)
            # Ne garder que les trades strictement après le dernier trade connu
            if live_cutoff is not None:
                results = results[results["entry_time"] > live_cutoff].copy()
            if len(results) > 0 and "entry_time" in results.columns:
                combined = pd.concat([existing, results], ignore_index=True)
                combined = combined.drop_duplicates(subset=["entry_time"], keep="first")
                combined = combined.sort_values("entry_time").reset_index(drop=True)
                out = combined.drop(columns=["entry_i15"], errors="ignore").copy()
                for col in ["entry_time", "exit_time"]:
                    if col in out.columns:
                        out[col] = pd.to_datetime(out[col], utc=True).dt.tz_convert(PARIS_TZ).dt.strftime("%Y-%m-%d %H:%M:%S")
                out.to_csv(CSV_RESULTS, index=False)
                n_new = len(results)
                print(f"\n💾 {CSV_RESULTS} — {n_new} nouveau(x) trade(s) | Total : {len(combined)}")
            else:
                print(f"\n💾 Aucun nouveau trade depuis le dernier lancement")
        else:
            out = results.drop(columns=["entry_i15"], errors="ignore").copy()
            for col in ["entry_time", "exit_time"]:
                if col in out.columns:
                    out[col] = pd.to_datetime(out[col], utc=True).dt.tz_convert(PARIS_TZ).dt.strftime("%Y-%m-%d %H:%M:%S")
            out.to_csv(CSV_RESULTS, index=False)
            print(f"\n💾 {CSV_RESULTS}")

        # ── 10 derniers trades avec colonne bias ──────────
        print("\n  10 DERNIERS TRADES :")
        cols = ["setup","session","direction","bias","entry_tf","entry_time","entry","sl","tp","rr","result"]
        # En mode live, afficher les 10 derniers du CSV complet (pas seulement les nouveaux)
        if live_mode and os.path.exists(CSV_RESULTS):
            disp_df = pd.read_csv(CSV_RESULTS)
        else:
            disp_df = results
        disp_cols = [c for c in cols if c in disp_df.columns]
        print(disp_df[disp_cols].tail(10).to_string(index=False))

    # ── État actuel du marché (mode --live uniquement) ──
    if live_mode:
        sep = "="*54
        print(f"\n{sep}")
        print(f"  ÉTAT ACTUEL DU MARCHÉ")
        print(sep)

        ts_now    = df1h.index[-1]
        bias_now  = get_bias(bmap, ts_now) if bmap is not None else "neutral"
        bsl_lvls, ssl_lvls = get_h1_levels(df1h, ts_now)
        price_now = df15["Close"].iloc[-1]
        ts_15m    = df15.index[-1]
        rsi_now   = calc_rsi(df15["Close"], period=RSI_PERIOD).iloc[-1]

        print(f"  Heure         : {ts_15m.strftime('%Y-%m-%d %H:%M')} (Paris)")
        print(f"  Prix actuel   : {price_now:.2f}")
        print(f"  Biais H1      : {bias_now.upper()}")
        print(f"  RSI(7) 15m    : {rsi_now:.1f}")
        print(sep)

        bsl_above = sorted([b for b in bsl_lvls if b > price_now])
        print(f"  BSL au-dessus du prix :")
        if bsl_above:
            for i, lvl in enumerate(bsl_above[:5]):
                print(f"    #{i+1}  {lvl:.2f}  (+{lvl-price_now:.1f} pts)")
        else:
            print("    Aucun — prix au-dessus de tous les swing highs H1")

        print()
        ssl_below = sorted([s for s in ssl_lvls if s < price_now], reverse=True)
        print(f"  SSL en-dessous du prix :")
        if ssl_below:
            for i, lvl in enumerate(ssl_below[:5]):
                print(f"    #{i+1}  {lvl:.2f}  (-{price_now-lvl:.1f} pts)")
        else:
            print("    Aucun — prix en-dessous de tous les swing lows H1")

        print(sep)

        h15_last = df15["High"].iloc[-1]
        l15_last = df15["Low"].iloc[-1]
        c15_last = df15["Close"].iloc[-1]
        sweep_found = False
        for lvl in bsl_lvls:
            wick = h15_last - lvl
            if h15_last > lvl + SWEEP_BUF and c15_last < lvl and wick >= SWEEP_MIN_PTS:
                print(f"  ⚡ Sweep BSL actif @ {lvl:.2f} → attente IFVG bear")
                sweep_found = True; break
        for lvl in ssl_lvls:
            wick = lvl - l15_last
            if l15_last < lvl - SWEEP_BUF and c15_last > lvl and wick >= SWEEP_MIN_PTS:
                print(f"  ⚡ Sweep SSL actif @ {lvl:.2f} → attente IFVG bull")
                sweep_found = True; break
        if not sweep_found:
            print(f"  Pas de sweep actif sur la dernière bougie 15m")
        print(sep)

        # ── Génération bias_log.csv pour comparaison avec NT8 ──────────────
        # Ce fichier contient le biais H1 heure par heure.
        # NT8 génère bias_comparison.csv dans le même dossier que ict_bot.log.
        # Comparer les deux fichiers pour détecter les divergences de biais.
        BIAS_LOG = "bias_log_v2.csv"
        bias_rows = []
        prev_bias = None
        for ts_h1 in bmap.index:
            b = bmap[ts_h1]
            # N'écrire que les bougies H1 de la période récente (90 derniers jours)
            if ts_h1 < ts_now - pd.Timedelta(days=90):
                continue
            bias_rows.append({
                "datetime": ts_h1.strftime("%Y-%m-%d %H:00"),
                "bias_python": b,
                "changed": "yes" if b != prev_bias else "no",
            })
            prev_bias = b

        bias_df = pd.DataFrame(bias_rows)
        bias_df.to_csv(BIAS_LOG, index=False)
        n_changes = (bias_df["changed"] == "yes").sum()
        print(f"\n  Biais loggé → {BIAS_LOG} ({len(bias_df)} heures, {n_changes} changements)")
        print(f"  Comparer avec bias_comparison.csv généré par NT8 pour valider l'alignement.")