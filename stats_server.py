#!/usr/bin/env python3
"""
Stats Dashboard — FastAPI + Supabase
Usage : python stats_server.py
"""
import os, time, threading, webbrowser
from pathlib import Path

import numpy as np
import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL   = os.getenv("DATABASE_URL")
RISK_DOLLARS   = 500.0
COMMISSION_RT  = 1.08
SLIPPAGE_PTS   = 0.0
POINT_VALUE    = 2.0
APPLY_COSTS    = True
PORT           = 5051
MOIS = ["Jan","Fév","Mar","Avr","Mai","Jun","Jul","Aoû","Sep","Oct","Nov","Déc"]

HTML_FILE = Path(__file__).parent / "stats_dashboard.html"

app = FastAPI(title="Stats Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_df() -> pd.DataFrame:
    """Charge les trades depuis Supabase."""
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        df = pd.read_sql(text("SELECT * FROM trades ORDER BY entry_time"), conn)
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    return df

def compute_stats():
    df = load_df()

    df["date"]  = df["entry_time"].dt.date
    df["month"] = df["entry_time"].dt.tz_localize(None).dt.to_period("M")

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
    }

# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    if not HTML_FILE.exists():
        raise HTTPException(status_code=404, detail="stats_dashboard.html introuvable")
    return HTML_FILE.read_text(encoding="utf-8")

@app.get("/api/stats")
def api_stats():
    try:
        return JSONResponse(content=compute_stats())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/trades")
def api_trades(limit: int = 50, offset: int = 0):
    """Retourne les trades paginés depuis Supabase."""
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
    return {"status": "ok", "source": "Supabase"}

# ── Main ─────────────────────────────────────────────────────────────────────

def open_browser():
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")

if __name__ == "__main__":
    print(f"\n{'═'*50}")
    print(f"  STATS DASHBOARD — FastAPI + Supabase")
    print(f"  URL : http://localhost:{PORT}")
    print(f"{'═'*50}\n")
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=PORT)