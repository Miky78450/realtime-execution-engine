#!/usr/bin/env python3
"""
ICT Bot Launcher
Lance NT8 + démarre le serveur dashboard
"""
import os, sys, time, subprocess, threading, json, re, getpass
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# ── CONFIG ──────────────────────────────────────────────────
NT8_SHORTCUT  = r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\NinjaTrader\NinjaTrader.lnk"
LOG_FILE      = r"C:\Users\natha\Documents\Backtest\Trading MGB\ict_bot.log"
DASHBOARD_PORT= 5050
MAX_LOG_LINES = 200

# Coordonnées de la checkbox "Enabled" dans NT8
# Lance find_coords.py une fois pour trouver ces valeurs
ENABLED_CHECKBOX_POS = (2007, 470)   # ← remplace avec les valeurs de find_coords.py
NT8_LOAD_WAIT        = 10       # secondes d'attente avant de cliquer
NT8_LOGIN_WAIT       = 4        # secondes d'attente pour que la fenêtre login apparaisse

# ── STATE ────────────────────────────────────────────
nt8_password = None  # stocké en mémoire uniquement────────
state = {
    "bias":    "—",
    "bsl":     "—",
    "ssl":     "—",
    "rsi":     "—",
    "close":   "—",
    "h1bars":  "—",
    "trades":  [],
    "logs":    [],
    "pnl_day": 0.0,
    "pnl_total": 0.0,
    "wins":    0,
    "losses":  0,
    "last_update": "—",
    "nt8_running": False,
    "searching": False,
    "in_trade": False,
}

# ── LOG PARSER ───────────────────────────────────────────────
RE_DEBUG  = re.compile(r"\[ICT DEBUG\] (\d+:\d+) \| Biais:(\w+) \| BSL:([\d,\.]+) \| SSL:([\d,\.]+) \| RSI:([\d,\.]+) \| Close:([\d,\.]+) \| H1bars:(\d+)")
RE_SWEEP  = re.compile(r"Sweep (BSL|SSL) @ ([\d,\.]+)")
RE_SIGNAL = re.compile(r"(S1_Bear|S2_Bull|S1 Bear signal|S2 Bull signal)")
RE_ENTRY  = re.compile(r"Entree @ ([\d,\.]+)")
RE_TP     = re.compile(r"TP @ ([\d,\.]+) \| PnL: \+?([\d,\.]+)\$")
RE_SL     = re.compile(r"SL @ ([\d,\.]+) \| PnL: ([\d,\.\-]+)\$")
RE_PLACE  = re.compile(r"\[(S1_Bear|S2_Bull)\] Entry:([\d,\.]+) SL:([\d,\.]+) TP:([\d,\.]+)")
RE_IFVG   = re.compile(r"IFVG (bear|bull) @ ([\d,\.]+) — limite non rempli")
RE_DAY    = re.compile(r"Jour: ([+-]?[\d,\.]+)\$")

def parse_num(s):
    return float(s.replace(",", ".").replace(" ", ""))

def parse_log():
    if not Path(LOG_FILE).exists():
        return
    try:
        with open(LOG_FILE, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except:
        return

    new_logs = []
    trades_by_day = {}
    current_trade = None

    # ── Variables locales — réinitialisées à chaque parse complet ──
    local_trades = []
    wins = 0
    losses = 0
    pnl_total = 0.0
    searching = False
    in_trade = False
    pnl_day = 0.0

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # Timestamp
        ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.+)", line)
        if not ts_match:
            continue
        ts, msg = ts_match.group(1), ts_match.group(2)

        # Debug line → extraire les données de marché, puis ne pas afficher
        m = RE_DEBUG.search(msg)
        if m:
            state["bias"]   = m.group(2)
            state["bsl"]    = m.group(3).replace(",", ".")
            state["ssl"]    = m.group(4).replace(",", ".")
            state["rsi"]    = m.group(5).replace(",", ".")
            state["close"]  = m.group(6).replace(",", ".")
            state["h1bars"] = m.group(7)
            state["last_update"] = ts
            continue  # ne pas afficher dans les logs

        # Dédoublonner — on ne garde qu'une occurrence par message identique
        msg_key = msg[:60]
        if any(msg_key in l['msg'] for l in new_logs[-20:]):
            continue

        new_logs.append({"ts": ts, "msg": msg})

        # Sweep
        if RE_SWEEP.search(msg):
            searching = True

        # Signal
        if RE_SIGNAL.search(msg):
            searching = True

        # IFVG non rempli
        if RE_IFVG.search(msg):
            searching = False

        # Ordre placé
        m = RE_PLACE.search(msg)
        if m:
            current_trade = {
                "setup": m.group(1),
                "entry": parse_num(m.group(2)),
                "sl":    parse_num(m.group(3)),
                "tp":    parse_num(m.group(4)),
                "open_time": ts,
                "result": "open",
                "pnl": 0,
            }
            in_trade = True

        # Fill
        m = RE_ENTRY.search(msg)
        if m and current_trade:
            current_trade["fill"] = parse_num(m.group(1))

        # TP hit
        m = RE_TP.search(msg)
        if m and current_trade:
            pnl = parse_num(m.group(2))
            current_trade["result"] = "win"
            current_trade["pnl"]    = pnl
            current_trade["close_time"] = ts
            local_trades.append(dict(current_trade))
            wins += 1
            pnl_total += pnl
            day = ts[:10]
            trades_by_day[day] = trades_by_day.get(day, 0) + pnl
            current_trade = None
            in_trade = False
            searching = False

        # SL hit
        m = RE_SL.search(msg)
        if m and current_trade:
            pnl = parse_num(m.group(2))
            current_trade["result"] = "loss"
            current_trade["pnl"]    = pnl
            current_trade["close_time"] = ts
            local_trades.append(dict(current_trade))
            losses += 1
            pnl_total += pnl
            day = ts[:10]
            trades_by_day[day] = trades_by_day.get(day, 0) + pnl
            current_trade = None
            in_trade = False
            searching = False

        # Jour PnL
        m = RE_DAY.search(msg)
        if m:
            pnl_day = parse_num(m.group(1))

    # ── Affectation finale dans state — une seule fois ──
    state["logs"]      = new_logs[-MAX_LOG_LINES:]
    state["trades"]    = local_trades
    state["wins"]      = wins
    state["losses"]    = losses
    state["pnl_total"] = pnl_total
    state["pnl_day"]   = pnl_day
    state["searching"] = searching
    state["in_trade"]  = in_trade

    # Si trade ouvert, l'ajouter temporairement pour l'affichage
    if current_trade:
        state["trades_display"] = local_trades + [dict(current_trade)]
    else:
        state["trades_display"] = local_trades

# ── HTTP SERVER ──────────────────────────────────────────────
HTML = open(Path(__file__).parent / "dashboard.html", encoding="utf-8").read

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        if self.path == "/":
            html = HTML()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())

        elif self.path == "/api":
            parse_log()
            data = {
                "bias":      state["bias"],
                "bsl":       state["bsl"],
                "ssl":       state["ssl"],
                "rsi":       state["rsi"],
                "close":     state["close"],
                "h1bars":    state["h1bars"],
                "pnl_day":   state["pnl_day"],
                "pnl_total": state["pnl_total"],
                "wins":      state["wins"],
                "losses":    state["losses"],
                "last_update": state["last_update"],
                "searching": state["searching"],
                "in_trade":  state["in_trade"],
                "logs":      state.get("logs", [])[-60:],
                "trades":    state.get("trades_display", [])[-20:],
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

# ── LAUNCHER ─────────────────────────────────────────────────
def launch_nt8():
    global nt8_password
    print("🚀 Lancement NinjaTrader 8...")

    # Demander le mot de passe avant de lancer NT8
    print()
    nt8_password = getpass.getpass("  🔑 Mot de passe NinjaTrader : ")
    print()

    try:
        os.startfile(NT8_SHORTCUT)
        state["nt8_running"] = True
        print(f"  ⏳ Attente fenêtre login ({NT8_LOGIN_WAIT}s)...")
        time.sleep(NT8_LOGIN_WAIT)
        login_nt8()
        print(f"  ⏳ Chargement workspace ({NT8_LOAD_WAIT}s)...")
        time.sleep(NT8_LOAD_WAIT)
        enable_strategy()
    except Exception as e:
        print(f"⚠️  Impossible de lancer NT8 : {e}")
        print("   Lance NT8 manuellement et charge le workspace ICT_Bot")

def login_nt8():
    """Tape le mot de passe dans la fenêtre de login NT8 et appuie sur Entrée."""
    global nt8_password
    if not nt8_password:
        print("⚠️  Pas de mot de passe — login manuel requis")
        return
    try:
        import pyautogui
        import pyperclip
        pyperclip.copy(nt8_password)
        nt8_password = None  # effacer de la mémoire immédiatement

        pyautogui.click(1374, 593)
        time.sleep(0.3)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.1)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.3)
        pyperclip.copy('')               # vider le presse-papiers
        time.sleep(0.2)
        pyautogui.press('enter')
        print("✅ Login envoyé")
        time.sleep(4)
        pyautogui.click(1492, 805)  # clic sur "Try it" (Simulation)
        print("✅ Simulation sélectionnée")
    except ImportError:
        print("⚠️  pyautogui non installé — pip install pyautogui")
    except Exception as e:
        print(f"⚠️  Erreur login : {e}")

def enable_strategy():
    """Coche automatiquement la checkbox Enabled dans NT8."""
    x, y = ENABLED_CHECKBOX_POS
    if x == 0 and y == 0:
        print("⚠️  Coordonnées non configurées — lance find_coords.py")
        print("   puis renseigne ENABLED_CHECKBOX_POS dans launcher.py")
        return
    try:
        import pyautogui
        print(f"🖱️  Clic sur Enabled @ ({x}, {y})...")
        pyautogui.click(x, y)
        time.sleep(0.5)
        pyautogui.click(1722, 689)  # clic sur OK
        time.sleep(0.5)
        pyautogui.click(2020, 411)  # minimiser NT8 dans la barre des tâches
        print("✅ Stratégie activée — NT8 minimisé")
    except ImportError:
        print("⚠️  pyautogui non installé — lance : pip install pyautogui")
    except Exception as e:
        print(f"⚠️  Erreur clic : {e}")

def open_browser():
    time.sleep(2)
    import webbrowser
    webbrowser.open(f"http://localhost:{DASHBOARD_PORT}")

if __name__ == "__main__":
    print("═" * 50)
    print("  ICT BOT LAUNCHER")
    print("═" * 50)

    # 1. Lancer NT8
    launch_nt8()

    # 2. Ouvrir le dashboard dans le navigateur
    threading.Thread(target=open_browser, daemon=True).start()

    # 3. Démarrer le serveur
    print(f"🌐 Dashboard → http://localhost:{DASHBOARD_PORT}")
    print("   Ctrl+C pour arrêter\n")
    try:
        HTTPServer(("", DASHBOARD_PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Arrêt du launcher")
