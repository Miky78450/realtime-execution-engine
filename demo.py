#!/usr/bin/env python3
"""
ICT Bot — Demo Server
Rejoue les résultats du backtest en temps réel simulé.
Sert le dashboard sur http://localhost:5050
"""
import csv, json, time, threading, os, random, math
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# ── CONFIG ──────────────────────────────────────────────────────────────────
CSV_FILE       = Path(__file__).parent / "nq_ict_backtest_results_3m.csv"
DASHBOARD_FILE = Path(__file__).parent / "dashboard.html"
PORT           = int(os.environ.get("PORT", 5050))

# Vitesse de replay : 1 trade toutes les X secondes
TRADE_INTERVAL = 8

# ── CHARGEMENT DES TRADES ────────────────────────────────────────────────────
def load_trades():
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

# ── STATE ────────────────────────────────────────────────────────────────────
all_trades  = load_trades()
total       = len(all_trades)

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

lock = threading.Lock()

# ── SIMULATION ───────────────────────────────────────────────────────────────
def fake_price(base, bias):
    """Simule un prix qui dérive légèrement selon le biais."""
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
    """Rejoue les trades un par un avec des logs réalistes."""
    # Attente initiale pour laisser le serveur démarrer
    time.sleep(2)

    with lock:
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

        # Niveaux BSL/SSL autour de l'entrée
        bsl = round(entry + random.uniform(15, 40), 2)
        ssl = round(entry - random.uniform(15, 40), 2)
        sweep_level = tr["_confirm_level"] if tr["_confirm_level"] else bsl if tr["direction"] == "short" else ssl

        with lock:
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

        # Log sweep
        sweep_type = "BSL" if tr["direction"] == "short" else "SSL"
        with lock:
            add_log(f"Sweep {sweep_type} @ {sweep_level:.2f}")
            state["close"] = str(fake_price(entry, bias))

        time.sleep(1.5)

        # Log signal
        signal = "S1 Bear signal" if tr["setup"] == "S1_Bear" else "S2 Bull signal"
        with lock:
            add_log(f"{signal} | RSI={state['rsi']}")

        time.sleep(1.0)

        # Log IFVG + entrée
        ifvg_type = "bear" if tr["direction"] == "short" else "bull"
        with lock:
            add_log(f"IFVG {ifvg_type} @ {entry:.2f} — limite placée")
            add_log(f"[{tr['setup']}] Entry:{entry:.2f} SL:{sl:.2f} TP:{tp:.2f}")
            state["in_trade"]  = True
            state["searching"] = False
            state["close"]     = str(entry)

            # Ajouter trade "open" dans la liste
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

        with lock:
            add_log(f"Entree @ {entry:.2f}")

        time.sleep(TRADE_INTERVAL)

        # Résolution du trade
        exit_price = tp if result == "win" else sl
        pnl_dollars = round(r_pnl * 500, 0)

        with lock:
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

            # Remplacer le trade open par le trade clôturé
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

        # Reset PnL du jour toutes les ~20 trades (simule changement de jour)
        if (idx + 1) % 20 == 0:
            with lock:
                state["pnl_day"] = 0.0
                add_log("─── Nouveau jour de trading ───")

        idx += 1
        time.sleep(2)

# ── HTTP SERVER ──────────────────────────────────────────────────────────────
HTML = DASHBOARD_FILE.read_text(encoding="utf-8")

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode())

        elif self.path == "/api":
            with lock:
                w = state["wins"]
                l = state["losses"]
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
                    # Infos demo
                    "demo_progress": f"{state['demo_index'] % total + 1}/{total}",
                }
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()

# ── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("═" * 52)
    print("  ICT BOT — DEMO SERVER")
    print("═" * 52)
    print(f"  Trades chargés : {total}")
    print(f"  Dashboard      : http://localhost:{PORT}")
    print(f"  API            : http://localhost:{PORT}/api")
    print("  Ctrl+C pour arrêter")
    print("═" * 52 + "\n")

    threading.Thread(target=replay_loop, daemon=True).start()

    try:
        HTTPServer(("", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Arrêt")
