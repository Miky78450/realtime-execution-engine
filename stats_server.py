#!/usr/bin/env python3
"""
Realtime Execution Engine — Serveur unifié
- /          → index.html (page avec onglets)
- /demo      → dashboard.html (démo live, replay du backtest)
- /stats     → stats_dashboard.html (analytics Supabase)
- /api       → état live de la démo (rafraîchi par le replay loop)
- /api/stats → stats agrégées depuis Supabase
- /api/trades → trades paginés depuis Supabase
- /health    → healthcheck
"""
import os, csv, time, json, random, threading, webbrowser
from pathlib import Path
from datetime import datetime

import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ──────────────────────────────────────────────────────────────────
DATABASE_URL   = os.getenv("DATABASE_URL")
RISK_DOLLARS   = 500.0
COMMISSION_RT  = 1.08
SLIPPAGE_PTS   = 0.0
POINT_VALUE    = 2.0
APPLY_COSTS    = True
PORT           = int(os.getenv("PORT", 5051))
TRADE_INTERVAL = int(os.getenv("TRADE_INTERVAL", 8))   # secondes entre trades replay
MOIS = ["Jan","Fév","Mar","Avr","Mai","Jun","Jul","Aoû","Sep","Oct","Nov","Déc"]

BASE_DIR        = Path(__file__).parent
INDEX_FILE      = BASE_DIR / "index.html"
DASHBOARD_FILE  = BASE_DIR / "dashboard.html"
STATS_HTML_FILE = BASE_DIR / "stats_dashboard.html"
CSV_FILE        = BASE_DIR / "nq_ict_backtest_results.csv"

# ── APP ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="Realtime Execution Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ════════════════════════════════════════════════════════════════════════════
#  PARTIE 1 — DEMO LIVE (ex demo.py)
# ════════════════════════════════════════════════════════════════════════════

state = {
    "bias":        "neutral",
    "bsl":         "—",
    "ssl":         "—",
    "rsi":         "50.0",
    "close":       "—",
    "h1bars":      "—",
    "pnl_day":     0.0,
    "pnl_total":   0.0,
    "wins":        0,
    "losses":      0,
    "last_update": "—",
    "searching":   False,
    "in_trade":    False,
    "logs":        [],
    "trades":      [],
    "demo_index":  0,
}
state_lock = threading.Lock()
all_trades: list = []


def load_replay_trades():
    """Charge le CSV de backtest pour le replay. Retourne [] si fichier absent."""
    if not CSV_FILE.exists():
        print(f"⚠️  CSV de replay introuvable ({CSV_FILE.name}) — démo désactivée")
        return []
    trades = []
    with open(CSV_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append({
                "setup":     "S1_Bear" if row["setup"] == "1" else "S2_Bull",
                "direction": row["direction"],
                "bias":      row["bias"],
                "session":   row["session"],
                "entry":     float(row["entry"]),
                "sl":        float(row["sl"]),
                "tp":        float(row["tp"]),
                "rr":        float(row["rr"]),
                "result":    row["result"],
                "r_pnl":     float(row["r_pnl"]),
                "equity_r":  float(row["equity_r"]),
                "entry_time": row["entry_time"],
                "exit_time":  row["exit_time"],
                "exit_note":  row["exit_note"],
                "_confirm_level": float(row["_confirm_level"]) if row["_confirm_level"] else 0.0,
            })
    return trades


def fake_price(base, bias):
    drift = 0.3 if bias == "bullish" else -0.3 if bias == "bearish" else 0
    return round(base + drift + random.uniform(-2, 2), 2)


def fake_rsi(bias):
    if bias == "bearish":
        return round(random.uniform(35, 58), 1)
    elif bias == "bullish":
        return round(random.uniform(42, 65), 1)
    return round(random.uniform(40, 60), 1)


def add_log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["logs"].append({"ts": ts, "msg": msg})
    if len(state["logs"]) > 200:
        state["logs"] = state["logs"][-200:]


def replay_loop():
    """Rejoue les trades en boucle. Tourne en thread de fond."""
    time.sleep(2)

    if not all_trades:
        with state_lock:
            add_log("⚠️  Aucun CSV de replay trouvé — démo en pause")
        return

    total = len(all_trades)
    with state_lock:
        add_log("=== ICT Bot Demo — Replay mode ===")
        add_log(f"Chargement de {total} trades historiques NQ...")
        add_log("Sessions : Asia / London / New York")

    time.sleep(3)
    idx = 0

    while True:
        tr = all_trades[idx % total]
        bias    = tr["bias"]
        session = tr["session"]
        entry   = tr["entry"]
        sl      = tr["sl"]
        tp      = tr["tp"]
        result  = tr["result"]
        r_pnl   = tr["r_pnl"]

        bsl = round(entry + random.uniform(15, 40), 2)
        ssl = round(entry - random.uniform(15, 40), 2)
        sweep_level = tr["_confirm_level"] if tr["_confirm_level"] \
                      else (bsl if tr["direction"] == "short" else ssl)

        with state_lock:
            state["bias"]        = bias
            state["bsl"]         = str(bsl)
            state["ssl"]         = str(ssl)
            state["close"]       = str(fake_price(entry, bias))
            state["rsi"]         = str(fake_rsi(bias))
            state["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            state["in_trade"]    = False
            state["searching"]   = True
            state["demo_index"]  = idx
            add_log(f"Nouveau jour: session {session.upper()} | Biais {bias.upper()}")

        time.sleep(1.5)

        sweep_type = "BSL" if tr["direction"] == "short" else "SSL"
        with state_lock:
            add_log(f"Sweep {sweep_type} @ {sweep_level:.2f}")
            state["close"] = str(fake_price(entry, bias))

        time.sleep(1.5)

        signal = "S1 Bear signal" if tr["setup"] == "S1_Bear" else "S2 Bull signal"
        with state_lock:
            add_log(f"{signal} | RSI={state['rsi']}")

        time.sleep(1.0)

        ifvg_type = "bear" if tr["direction"] == "short" else "bull"
        with state_lock:
            add_log(f"IFVG {ifvg_type} @ {entry:.2f} — limite placée")
            add_log(f"[{tr['setup']}] Entry:{entry:.2f} SL:{sl:.2f} TP:{tp:.2f}")
            state["in_trade"]  = True
            state["searching"] = False
            state["close"]     = str(entry)
            open_trade = {
                "setup":     tr["setup"],
                "entry":     entry,
                "sl":        sl,
                "tp":        tp,
                "open_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "result":    "open",
                "pnl":       0,
            }
            state["trades"] = (state["trades"] + [open_trade])[-20:]

        time.sleep(1.0)
        with state_lock:
            add_log(f"Entree @ {entry:.2f}")

        time.sleep(TRADE_INTERVAL)

        exit_price  = tp if result == "win" else sl
        pnl_dollars = round(r_pnl * 500, 0)

        with state_lock:
            if result == "win":
                add_log(f"TP @ {tp:.2f} | PnL: +{abs(pnl_dollars):.0f}$")
                state["wins"] += 1
            else:
                add_log(f"SL @ {sl:.2f} | PnL: -{abs(pnl_dollars):.0f}$")
                state["losses"] += 1

            state["pnl_total"] = round(state["pnl_total"] + pnl_dollars, 2)
            state["pnl_day"]   = round(state["pnl_day"] + pnl_dollars, 2)
            state["in_trade"]  = False
            state["close"]     = str(fake_price(exit_price, bias))

            closed = {
                "setup":     tr["setup"],
                "entry":     entry,
                "sl":        sl,
                "tp":        tp,
                "open_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "result":    result,
                "pnl":       pnl_dollars,
            }
            if state["trades"]:
                state["trades"][-1] = closed
            else:
                state["trades"].append(closed)

        if (idx + 1) % 20 == 0:
            with state_lock:
                state["pnl_day"] = 0.0
                add_log("─── Nouveau jour de trading ───")

        idx += 1
        time.sleep(2)


# ════════════════════════════════════════════════════════════════════════════
#  PARTIE 2 — STATS (Supabase)
# ════════════════════════════════════════════════════════════════════════════

def load_df() -> pd.DataFrame:
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        df = pd.read_sql(text("SELECT * FROM trades ORDER BY entry_time"), conn)
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    return df


def compute_stats():
    df = load_df()

    df["date"]  = df["entry_time"].dt.date
    df["month"] = df["entry_time"].dt.tz_convert(None).dt.to_period("M")

    if APPLY_COSTS:
        risk_pts = (df["sl"] - df["entry"]).abs().clip(lower=1.0)
        risk_dol = risk_pts * POINT_VALUE
        cost_dol = SLIPPAGE_PTS * 2 * POINT_VALUE + COMMISSION_RT
        cost_r   = cost_dol / risk_dol
        df["r_pnl"] = df.apply(
            lambda r: r["rr"] - cost_r[r.name] if r["result"] == "win"
                      else -1.0 - cost_r[r.name], axis=1
        )
    else:
        df["r_pnl"] = df.apply(
            lambda r: r["rr"] if r["result"] == "win" else -1.0, axis=1
        )

    df["pnl_$"] = df["r_pnl"] * RISK_DOLLARS
    df = df.sort_values("entry_time").reset_index(drop=True)
    df["cumul"] = df["r_pnl"].cumsum()

    total  = len(df)
    wins   = int((df["result"] == "win").sum())
    losses = int((df["result"] == "loss").sum())
    wr     = wins / total * 100
    pf     = float(df[df["result"] == "win"]["rr"].sum() / losses) if losses > 0 else 0
    avg_rr = float(df["rr"].mean())
    eq_r   = float(df["r_pnl"].sum())
    eq_dol = float(df["pnl_$"].sum())
    dd_r   = float((df["cumul"] - df["cumul"].cummax()).min())

    def max_streak(results, t):
        mx = cur = 0; streaks = []
        for r in results:
            if r == t:
                cur += 1; mx = max(mx, cur)
            else:
                if cur > 0: streaks.append(cur)
                cur = 0
        if cur > 0: streaks.append(cur)
        return mx, float(sum(streaks) / len(streaks) if streaks else 0)

    ml, al = max_streak(df["result"].tolist(), "loss")
    mw, aw = max_streak(df["result"].tolist(), "win")

    equity_curve = [
        {"x": str(row["entry_time"].date()), "y": round(float(row["cumul"]) * RISK_DOLLARS, 0)}
        for _, row in df.iterrows()
    ]

    setups = []
    for sid in sorted(df["setup"].unique()):
        s  = df[df["setup"] == sid]
        sw = int((s["result"] == "win").sum())
        sl = int((s["result"] == "loss").sum())
        spf = float(s[s["result"] == "win"]["rr"].sum() / sl) if sl > 0 else 0
        sml, _ = max_streak(s["result"].tolist(), "loss")
        lon = s[s["session"] == "london"]
        ny  = s[s["session"] == "ny"]
        setups.append({
            "name": "S1 Bear" if sid == 1 else "S2 Bull",
            "trades": int(len(s)),
            "wins": sw, "losses": sl,
            "wr": round(sw / len(s) * 100, 1),
            "pf": round(spf, 2),
            "rr": round(float(s["rr"].mean()), 2),
            "eq_r": round(float(s["r_pnl"].sum()), 1),
            "eq_dol": round(float(s["pnl_$"].sum()), 0),
            "max_loss_streak": sml,
            "london": f"{int((lon['result']=='win').sum())}/{len(lon)}",
            "ny":     f"{int((ny['result']=='win').sum())}/{len(ny)}",
        })

    months = []
    for period in sorted(df["month"].unique()):
        sub = df[df["month"] == period]
        months.append({
            "label":  f"{MOIS[period.month-1]} {str(period.year)[2:]}",
            "trades": int(len(sub)),
            "wr":     round(float((sub["result"] == "win").mean() * 100), 1),
            "r":      round(float(sub["r_pnl"].sum()), 1),
            "dol":    round(float(sub["pnl_$"].sum()), 0),
        })

    recent = [
        {
            "setup":  int(row["setup"]),
            "dir":    row["direction"],
            "time":   str(row["entry_time"])[:16],
            "entry":  float(row["entry"]),
            "sl":     float(row["sl"]),
            "tp":     float(row["tp"]),
            "rr":     float(row["rr"]),
            "result": row["result"],
            "pnl":    round(float(row["r_pnl"]) * RISK_DOLLARS, 0),
        }
        for _, row in df.tail(20).iloc[::-1].iterrows()
    ]

    return {
        "period":       f"{df['entry_time'].min().date()} → {df['entry_time'].max().date()}",
        "total": total, "wins": wins, "losses": losses,
        "wr": round(wr, 1), "pf": round(pf, 2), "avg_rr": round(avg_rr, 2),
        "eq_r": round(eq_r, 1), "eq_dol": round(eq_dol, 0),
        "dd_r": round(dd_r, 1), "dd_dol": round(dd_r * RISK_DOLLARS, 0),
        "ml": ml, "al": round(al, 1), "mw": mw, "aw": round(aw, 1),
        "equity_curve": equity_curve,
        "setups":  setups,
        "months":  months,
        "recent":  recent,
        "source":  "Supabase PostgreSQL",
        "csv":     "Supabase PostgreSQL",   # alias pour compat front-end
    }


# ════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ════════════════════════════════════════════════════════════════════════════

def serve_html(file: Path) -> HTMLResponse:
    if not file.exists():
        raise HTTPException(status_code=404, detail=f"{file.name} introuvable")
    return HTMLResponse(content=file.read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
def index():
    """Page d'accueil avec les onglets Démo / Stats."""
    return serve_html(INDEX_FILE)


@app.get("/demo", response_class=HTMLResponse)
def demo_page():
    """Dashboard live (replay du backtest)."""
    return serve_html(DASHBOARD_FILE)


@app.get("/stats", response_class=HTMLResponse)
def stats_page():
    """Dashboard stats (analytics Supabase)."""
    return serve_html(STATS_HTML_FILE)


@app.get("/api")
def api_live():
    """État live du replay (consommé par dashboard.html)."""
    with state_lock:
        w = state["wins"]
        l = state["losses"]
        total = len(all_trades) if all_trades else 1
        data = {
            "bias":        state["bias"],
            "bsl":         state["bsl"],
            "ssl":         state["ssl"],
            "rsi":         state["rsi"],
            "close":       state["close"],
            "h1bars":      "—",
            "pnl_day":     state["pnl_day"],
            "pnl_total":   state["pnl_total"],
            "wins":        w,
            "losses":      l,
            "last_update": state["last_update"],
            "searching":   state["searching"],
            "in_trade":    state["in_trade"],
            "logs":        state["logs"][-60:],
            "trades":      state["trades"][-20:],
            "demo_progress": f"{state['demo_index'] % total + 1}/{total}",
        }
    return JSONResponse(content=data)


@app.get("/api/stats")
def api_stats():
    try:
        return JSONResponse(content=compute_stats())
    except Exception as e:
        import traceback
        print(traceback.format_exc(), flush=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trades")
def api_trades(limit: int = 50, offset: int = 0):
    try:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM trades ORDER BY entry_time DESC LIMIT :l OFFSET :o"),
                {"l": limit, "o": offset}
            ).mappings().all()
            total = conn.execute(text("SELECT COUNT(*) FROM trades")).scalar()
        return JSONResponse(content={
            "total": total,
            "limit": limit,
            "offset": offset,
            "trades": [dict(r) for r in rows],
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {
        "status":      "ok",
        "source":      "Supabase",
        "demo_loaded": len(all_trades),
    }


# ── STARTUP ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    global all_trades
    all_trades = load_replay_trades()
    if all_trades:
        print(f"✅ Replay : {len(all_trades)} trades chargés depuis {CSV_FILE.name}")
        threading.Thread(target=replay_loop, daemon=True).start()
    else:
        print("⚠️  Replay désactivé (pas de CSV)")


# ── MAIN ────────────────────────────────────────────────────────────────────

def open_browser():
    time.sleep(1.5)
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass


if __name__ == "__main__":
    print(f"\n{'═'*52}")
    print(f"  REALTIME EXECUTION ENGINE — Serveur unifié")
    print(f"  URL    : http://localhost:{PORT}")
    print(f"  Démo   : http://localhost:{PORT}/demo")
    print(f"  Stats  : http://localhost:{PORT}/stats")
    print(f"  Health : http://localhost:{PORT}/health")
    print(f"{'═'*52}\n")

    if os.getenv("OPEN_BROWSER", "0") == "1":
        threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run("stats_server:app", host="0.0.0.0", port=PORT)
