"""
Tests unitaires — NQ ICT Backtester
Couvre les 5 fonctions critiques :
  1. calc_rsi
  2. get_session
  3. calc_tp
  4. get_bias
  5. resolve_same_candle_1m

Usage :
  pip install pytest pandas numpy pytz
  pytest test_backtester.py -v
"""

import pytest
import pandas as pd
import numpy as np
import pytz
from datetime import time, datetime

# ─── Import des fonctions à tester ────────────────────────────────────────────
# On importe directement depuis le backtester. Les paramètres globaux sont
# utilisés tels quels (MIN_RR=2.5, MIN_RISK_PTS=10.0, etc.)
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from nq_ict_backtester_BACKUP import (
    calc_rsi,
    get_session,
    calc_tp,
    get_bias,
    resolve_same_candle_1m,
    PARIS_TZ,
    MIN_RR,
    MIN_RISK_PTS,
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. calc_rsi
# Implémentation Wilder (EMA alpha=1/period) — doit correspondre à NT8
# ══════════════════════════════════════════════════════════════════════════════
class TestCalcRsi:

    def _make_series(self, values):
        return pd.Series(values, dtype=float)

    def test_rsi_output_range(self):
        """RSI doit toujours être entre 0 et 100."""
        prices = self._make_series([100, 102, 101, 105, 103, 107, 110, 108, 112, 115,
                                    113, 116, 114, 118, 120, 117, 121, 119, 123, 125])
        rsi = calc_rsi(prices, period=7)
        valid = rsi.dropna()
        assert (valid >= 0).all(), "RSI < 0 détecté"
        assert (valid <= 100).all(), "RSI > 100 détecté"

    def test_rsi_strong_uptrend_above_50(self):
        """Une série en forte hausse doit produire un RSI > 50."""
        prices = self._make_series([100 + i * 2 for i in range(20)])
        rsi = calc_rsi(prices, period=7)
        assert rsi.iloc[-1] > 50, f"RSI attendu > 50 en uptrend, obtenu {rsi.iloc[-1]:.1f}"

    def test_rsi_strong_downtrend_below_50(self):
        """Une série en forte baisse doit produire un RSI < 50."""
        prices = self._make_series([200 - i * 2 for i in range(20)])
        rsi = calc_rsi(prices, period=7)
        assert rsi.iloc[-1] < 50, f"RSI attendu < 50 en downtrend, obtenu {rsi.iloc[-1]:.1f}"

    def test_rsi_flat_series_near_50(self):
        """Une série plate (gains=pertes) doit produire un RSI proche de 50."""
        # Alternance +1/-1 : gains et pertes égaux → RSI ≈ 50
        prices = self._make_series([100 + (1 if i % 2 == 0 else -1) for i in range(30)])
        rsi = calc_rsi(prices, period=7)
        last_val = rsi.iloc[-1]
        assert 40 < last_val < 60, f"RSI attendu ≈ 50 sur série plate, obtenu {last_val:.1f}"

    def test_rsi_filter_s1_bear(self):
        """
        S1 Bear exige RSI(7) < 60.
        Une série haussière modérée doit laisser passer le filtre (RSI < 60 possible).
        """
        # Hausse légère avec quelques corrections
        prices = self._make_series([100, 101, 100, 102, 101, 103, 102, 104, 103, 105,
                                    104, 103, 102, 101, 102, 103])
        rsi = calc_rsi(prices, period=7)
        # On vérifie juste que la fonction tourne sans erreur et que la valeur
        # peut être comparée à 60 (logic dans run_backtest)
        assert isinstance(rsi.iloc[-1], float)

    def test_rsi_no_nan_after_warmup(self):
        """Pas de NaN après la période de chauffe (period barres)."""
        prices = self._make_series([100 + np.sin(i) * 5 for i in range(50)])
        rsi = calc_rsi(prices, period=7)
        # Les valeurs après l'index 7 ne doivent pas être NaN
        assert not rsi.iloc[7:].isna().any(), "NaN détecté après la période de chauffe"

    def test_rsi_constant_gain_approaches_100(self):
        """Série avec uniquement des hausses → RSI doit tendre vers 100."""
        prices = self._make_series([100 + i for i in range(30)])
        rsi = calc_rsi(prices, period=7)
        assert rsi.iloc[-1] > 90, f"RSI attendu > 90 sur hausse constante, obtenu {rsi.iloc[-1]:.1f}"

    def test_rsi_constant_loss_approaches_0(self):
        """Série avec uniquement des baisses → RSI doit tendre vers 0."""
        prices = self._make_series([200 - i for i in range(30)])
        rsi = calc_rsi(prices, period=7)
        assert rsi.iloc[-1] < 10, f"RSI attendu < 10 sur baisse constante, obtenu {rsi.iloc[-1]:.1f}"


# ══════════════════════════════════════════════════════════════════════════════
# 2. get_session
# London : 09h-15h29 Paris | NY : 15h30-21h59 | Asia : reste
# ══════════════════════════════════════════════════════════════════════════════
class TestGetSession:

    def _ts(self, h, m):
        """Crée un Timestamp Paris naive pour tester."""
        return pd.Timestamp(f"2024-01-15 {h:02d}:{m:02d}:00", tz=PARIS_TZ)

    def test_london_start(self):
        assert get_session(self._ts(9, 0)) == "london"

    def test_london_mid(self):
        assert get_session(self._ts(12, 30)) == "london"

    def test_london_end_boundary(self):
        """15h29 = encore london."""
        assert get_session(self._ts(15, 29)) == "london"

    def test_ny_start(self):
        """15h30 = NY commence."""
        assert get_session(self._ts(15, 30)) == "ny"

    def test_ny_mid(self):
        assert get_session(self._ts(18, 0)) == "ny"

    def test_ny_end_boundary(self):
        """21h59 = encore NY."""
        assert get_session(self._ts(21, 59)) == "ny"

    def test_asia_early_morning(self):
        """02h00 = Asia (début session de trading)."""
        assert get_session(self._ts(2, 0)) == "asia"

    def test_asia_late_night(self):
        """22h00 = Asia (après clôture NY)."""
        assert get_session(self._ts(22, 0)) == "asia"

    def test_asia_midnight(self):
        assert get_session(self._ts(0, 0)) == "asia"

    def test_exactly_15h30_is_ny_not_london(self):
        """Frontière critique : 15h30 doit être NY."""
        assert get_session(self._ts(15, 30)) == "ny"
        assert get_session(self._ts(15, 29)) == "london"


# ══════════════════════════════════════════════════════════════════════════════
# 3. calc_tp
# TP = entry ± risk * MIN_RR | RR retourné = MIN_RR
# ══════════════════════════════════════════════════════════════════════════════
class TestCalcTp:

    def test_bear_tp_below_entry(self):
        """Short : TP doit être en dessous de l'entry."""
        entry, sl = 17000.0, 17015.0  # risk = 15 pts
        tp, rr = calc_tp(entry, sl, [], "bear")
        assert tp < entry, f"TP bear doit être < entry, obtenu {tp}"

    def test_bull_tp_above_entry(self):
        """Long : TP doit être au-dessus de l'entry."""
        entry, sl = 17000.0, 16985.0  # risk = 15 pts
        tp, rr = calc_tp(entry, sl, [], "bull")
        assert tp > entry, f"TP bull doit être > entry, obtenu {tp}"

    def test_rr_equals_min_rr(self):
        """Le RR retourné doit toujours être MIN_RR."""
        entry, sl = 17000.0, 17015.0
        _, rr = calc_tp(entry, sl, [], "bear")
        assert rr == MIN_RR, f"RR attendu {MIN_RR}, obtenu {rr}"

    def test_bear_tp_correct_distance(self):
        """TP bear = entry - risk * MIN_RR."""
        entry, sl = 17000.0, 17015.0
        risk = abs(sl - entry)  # 15 pts
        tp, _ = calc_tp(entry, sl, [], "bear")
        expected = entry - risk * MIN_RR
        assert abs(tp - expected) < 0.01, f"TP bear attendu {expected}, obtenu {tp}"

    def test_bull_tp_correct_distance(self):
        """TP bull = entry + risk * MIN_RR."""
        entry, sl = 17000.0, 16985.0
        risk = abs(sl - entry)  # 15 pts
        tp, _ = calc_tp(entry, sl, [], "bull")
        expected = entry + risk * MIN_RR
        assert abs(tp - expected) < 0.01, f"TP bull attendu {expected}, obtenu {tp}"

    def test_min_risk_pts_enforced(self):
        """
        Si risk < MIN_RISK_PTS, calc_tp doit utiliser MIN_RISK_PTS.
        Ex : entry=17000, sl=17002 → risk=2 < 10 → doit utiliser 10.
        """
        entry, sl = 17000.0, 17002.0  # risk réel = 2 pts < MIN_RISK_PTS=10
        tp, _ = calc_tp(entry, sl, [], "bear")
        expected = entry - MIN_RISK_PTS * MIN_RR
        assert abs(tp - expected) < 0.01, (
            f"MIN_RISK_PTS non appliqué. Attendu {expected}, obtenu {tp}"
        )

    def test_bear_tp_rr_2_5(self):
        """Test concret : SL 10 pts → TP à 25 pts sous l'entry (RR=2.5)."""
        entry, sl = 17000.0, 17010.0
        tp, rr = calc_tp(entry, sl, [], "bear")
        assert abs(tp - 16975.0) < 0.01
        assert rr == 2.5


# ══════════════════════════════════════════════════════════════════════════════
# 4. get_bias
# Lookup "le dernier biais connu avant ts" dans une pd.Series indexée en temps
# ══════════════════════════════════════════════════════════════════════════════
class TestGetBias:

    def _make_bias_series(self):
        idx = pd.date_range("2024-01-01 00:00", periods=10, freq="1h", tz=PARIS_TZ)
        values = ["neutral", "neutral", "bullish", "bullish", "bullish",
                  "bearish", "bearish", "neutral", "bullish", "bullish"]
        return pd.Series(values, index=idx)

    def test_exact_timestamp(self):
        """Timestamp exact dans l'index → doit retourner la valeur correspondante."""
        bs = self._make_bias_series()
        ts = bs.index[2]  # "bullish"
        assert get_bias(bs, ts) == "bullish"

    def test_between_timestamps_returns_previous(self):
        """Timestamp entre deux entrées → doit retourner la valeur PRÉCÉDENTE."""
        bs = self._make_bias_series()
        # Entre index[2] (bullish) et index[3] (bullish) + 30 min
        ts = bs.index[2] + pd.Timedelta(minutes=30)
        assert get_bias(bs, ts) == "bullish"

    def test_before_all_data_returns_neutral(self):
        """Timestamp avant le début de la série → doit retourner 'neutral'."""
        bs = self._make_bias_series()
        ts = bs.index[0] - pd.Timedelta(hours=1)
        assert get_bias(bs, ts) == "neutral"

    def test_bearish_lookup(self):
        bs = self._make_bias_series()
        ts = bs.index[5]  # "bearish"
        assert get_bias(bs, ts) == "bearish"

    def test_last_timestamp(self):
        bs = self._make_bias_series()
        ts = bs.index[-1]
        assert get_bias(bs, ts) == "bullish"

    def test_well_after_series_end(self):
        """Timestamp après la fin → doit retourner le dernier biais connu."""
        bs = self._make_bias_series()
        ts = bs.index[-1] + pd.Timedelta(hours=5)
        assert get_bias(bs, ts) == "bullish"


# ══════════════════════════════════════════════════════════════════════════════
# 5. resolve_same_candle_1m
# Désambiguïse SL/TP touchés sur la même bougie 15m via les bougies 1m
# ══════════════════════════════════════════════════════════════════════════════
class TestResolveSameCandle1m:

    def _make_df1m(self, candle_ts, bars):
        """
        bars : liste de dicts {h, l, o, c}
        Crée un DataFrame 1m avec des timestamps consécutifs à partir de candle_ts.
        """
        timestamps = [candle_ts + pd.Timedelta(minutes=i) for i in range(len(bars))]
        return pd.DataFrame([{
            "High": b["h"], "Low": b["l"], "Open": b["o"], "Close": b["c"]
        } for b in bars], index=pd.DatetimeIndex(timestamps, tz=PARIS_TZ))

    def _candle_ts(self):
        return pd.Timestamp("2024-01-15 10:00:00", tz=PARIS_TZ)

    # ── SHORT ──────────────────────────────────────────────

    def test_short_tp_hit_first(self):
        """Short : TP touché sur 1ère bougie 1m avant SL → WIN."""
        ts = self._candle_ts()
        sl, tp = 17020.0, 16980.0
        # 1ère bougie : low ≤ tp (16978) sans toucher SL (high < sl)
        bars = [{"h": 17005.0, "l": 16978.0, "o": 17000.0, "c": 16985.0}]
        df1m = self._make_df1m(ts, bars)
        result, exit_px, note = resolve_same_candle_1m(df1m, ts, sl, tp, "short", 0.0)
        assert result == "win", f"Attendu win, obtenu {result}"
        assert "TP" in note

    def test_short_sl_hit_first(self):
        """Short : SL touché sur 1ère bougie 1m avant TP → LOSS."""
        ts = self._candle_ts()
        sl, tp = 17020.0, 16980.0
        # 1ère bougie : high ≥ sl sans toucher TP (low > tp)
        bars = [{"h": 17022.0, "l": 16990.0, "o": 17000.0, "c": 17010.0}]
        df1m = self._make_df1m(ts, bars)
        result, _, note = resolve_same_candle_1m(df1m, ts, sl, tp, "short", 0.0)
        assert result == "loss", f"Attendu loss, obtenu {result}"
        assert "SL" in note

    def test_short_both_hit_open_below_tp_wins(self):
        """Short : SL et TP touchés sur même bougie 1m, open < tp → TP atteint en premier → WIN."""
        ts = self._candle_ts()
        sl, tp = 17020.0, 16980.0
        # Open en dessous du TP → le prix était déjà au TP en ouverture
        bars = [{"h": 17025.0, "l": 16970.0, "o": 16975.0, "c": 17000.0}]
        df1m = self._make_df1m(ts, bars)
        result, _, note = resolve_same_candle_1m(df1m, ts, sl, tp, "short", 0.0)
        assert result == "win", f"Open < TP → WIN attendu, obtenu {result}"

    def test_short_both_hit_open_above_tp_loses(self):
        """Short : SL et TP touchés, open > tp → SL atteint en premier → LOSS."""
        ts = self._candle_ts()
        sl, tp = 17020.0, 16980.0
        # Open au-dessus du TP → le prix part à la hausse d'abord (SL first)
        bars = [{"h": 17025.0, "l": 16970.0, "o": 17000.0, "c": 16990.0}]
        df1m = self._make_df1m(ts, bars)
        result, _, note = resolve_same_candle_1m(df1m, ts, sl, tp, "short", 0.0)
        assert result == "loss", f"Open > TP → LOSS attendu, obtenu {result}"

    # ── LONG ───────────────────────────────────────────────

    def test_long_tp_hit_first(self):
        """Long : TP touché sur 1ère bougie 1m → WIN."""
        ts = self._candle_ts()
        sl, tp = 16980.0, 17020.0
        bars = [{"h": 17022.0, "l": 16990.0, "o": 17000.0, "c": 17015.0}]
        df1m = self._make_df1m(ts, bars)
        result, _, note = resolve_same_candle_1m(df1m, ts, sl, tp, "long", 0.0)
        assert result == "win"
        assert "TP" in note

    def test_long_sl_hit_first(self):
        """Long : SL touché sur 1ère bougie 1m → LOSS."""
        ts = self._candle_ts()
        sl, tp = 16980.0, 17020.0
        bars = [{"h": 17005.0, "l": 16978.0, "o": 17000.0, "c": 16990.0}]
        df1m = self._make_df1m(ts, bars)
        result, _, note = resolve_same_candle_1m(df1m, ts, sl, tp, "long", 0.0)
        assert result == "loss"
        assert "SL" in note

    def test_long_both_hit_open_above_tp_wins(self):
        """Long : SL et TP touchés, open ≥ tp → TP premier → WIN."""
        ts = self._candle_ts()
        sl, tp = 16980.0, 17020.0
        bars = [{"h": 17030.0, "l": 16970.0, "o": 17025.0, "c": 17000.0}]
        df1m = self._make_df1m(ts, bars)
        result, _, _ = resolve_same_candle_1m(df1m, ts, sl, tp, "long", 0.0)
        assert result == "win"

    def test_long_both_hit_open_below_tp_loses(self):
        """Long : SL et TP touchés, open < tp → SL premier → LOSS."""
        ts = self._candle_ts()
        sl, tp = 16980.0, 17020.0
        bars = [{"h": 17030.0, "l": 16970.0, "o": 17000.0, "c": 17010.0}]
        df1m = self._make_df1m(ts, bars)
        result, _, _ = resolve_same_candle_1m(df1m, ts, sl, tp, "long", 0.0)
        assert result == "loss"

    # ── Cas limites ─────────────────────────────────────────

    def test_empty_1m_data_fallback_short(self):
        """Pas de données 1m → fallback loss pour short."""
        ts = self._candle_ts()
        df1m_empty = pd.DataFrame(columns=["High","Low","Open","Close"],
                                  index=pd.DatetimeIndex([], tz=PARIS_TZ))
        result, _, note = resolve_same_candle_1m(df1m_empty, ts, 17020.0, 16980.0, "short", 0.0)
        assert result == "loss"
        assert "fallback" in note

    def test_empty_1m_data_fallback_long(self):
        """Pas de données 1m → fallback loss pour long."""
        ts = self._candle_ts()
        df1m_empty = pd.DataFrame(columns=["High","Low","Open","Close"],
                                  index=pd.DatetimeIndex([], tz=PARIS_TZ))
        result, _, note = resolve_same_candle_1m(df1m_empty, ts, 16980.0, 17020.0, "long", 0.0)
        assert result == "loss"
        assert "fallback" in note

    def test_slip_applied_to_exit_price_short_sl(self):
        """Le slippage est ajouté au SL pour short LOSS."""
        ts = self._candle_ts()
        sl, tp = 17020.0, 16980.0
        slip = 1.0
        bars = [{"h": 17022.0, "l": 16990.0, "o": 17000.0, "c": 17010.0}]
        df1m = self._make_df1m(ts, bars)
        result, exit_px, _ = resolve_same_candle_1m(df1m, ts, sl, tp, "short", slip)
        assert result == "loss"
        assert abs(exit_px - (sl + slip)) < 0.01, f"Exit attendu {sl+slip}, obtenu {exit_px}"

    def test_no_hit_within_candle_returns_fallback(self):
        """Aucun SL ni TP touché sur les 15 bougies 1m → fallback loss."""
        ts = self._candle_ts()
        sl, tp = 17050.0, 16950.0  # niveaux très éloignés
        bars = [{"h": 17010.0, "l": 16990.0, "o": 17000.0, "c": 17000.0}
                for _ in range(15)]
        df1m = self._make_df1m(ts, bars)
        result, _, note = resolve_same_candle_1m(df1m, ts, sl, tp, "short", 0.0)
        assert result == "loss"
        assert "fallback" in note

    def test_resolution_uses_second_candle_if_first_no_hit(self):
        """SL touché seulement sur 2ème bougie 1m → LOSS via 2ème bougie."""
        ts = self._candle_ts()
        sl, tp = 17020.0, 16980.0
        bars = [
            {"h": 17010.0, "l": 16990.0, "o": 17000.0, "c": 17000.0},  # rien
            {"h": 17025.0, "l": 16990.0, "o": 17000.0, "c": 17010.0},  # SL touché
        ]
        df1m = self._make_df1m(ts, bars)
        result, _, note = resolve_same_candle_1m(df1m, ts, sl, tp, "short", 0.0)
        assert result == "loss"
        assert "SL" in note
