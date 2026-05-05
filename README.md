# ICT Trading Bot — NQ Futures

Système de trading algorithmique complet sur le NQ (Nasdaq Futures), basé sur la méthodologie ICT (*Inner Circle Trader*). Conçu, backtesté et déployé en production de façon autonome.

**Statut : bot actif en simulation live depuis avril 2026 (NinjaTrader 8 / Tradovate)**

---

## Résultats du backtest (8 ans, 2017–2025)

| Métrique | Valeur |
|---|---|
| Trades simulés | **1 607** |
| Win rate | **59,3%** |
| Rendement net | **+1 417R** |
| RR moyen | **2,36** |
| Drawdown max | **11,5R** |
| Sessions | Asia · London · New York |

> Données : 2,9M de bougies 1m NQ Futures. Frais et slippage inclus.

---

## Architecture

```
┌────────────────────────────────────────────────────────┐
│                    BACKTESTER                          │
│  nq_ict_backtester_BACKUP.py  (Python · pandas/numpy) │
│  → 8 ans de données 1m                                │
│  → Exporte nq_ict_backtest_results_3m.csv             │
└──────────────────────┬─────────────────────────────────┘
                       │ valide les règles métier
                       ▼
┌────────────────────────────────────────────────────────┐
│                  BOT TEMPS RÉEL                        │
│  ICTSignalReader.cs  (C# · NinjaTrader 8)             │
│  → Données live Tradovate                             │
│  → Même algorithme que le backtester                  │
│  → Écrit tous les événements dans ict_bot.log         │
└──────────────────────┬─────────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
┌─────────────────┐       ┌──────────────────────┐
│  DASHBOARD LIVE │       │  DASHBOARD ANALYTICS │
│  launcher.py    │       │  stats_server.py     │
│  dashboard.html │       │  stats_dashboard.html│
│  → parse le log │       │  → equity curve      │
│  → API JSON     │       │  → stats par mois    │
│  → polling 2s   │       │  → breakdown setup   │
└─────────────────┘       └──────────────────────┘
```

---

## Stratégies implémentées

**S1 Bear** — Short après sweep de BSL (Buy-Side Liquidity)
1. Biais H1 baissier (Dow Theory : LH + LL sur fenêtre glissante 50 bougies)
2. Wick de bougie 15m au-dessus d'un swing high H1 (buf=3pts, min=5pts)
3. Clôture 15m en-dessous du niveau — confirmation anti-faux-sweep
4. RSI(7) Wilder < 60 sur la 15m
5. IFVG bearish 1m dans les 3h suivant le sweep
6. Entrée ordre limit · SL au-dessus de l'open de la bougie signal · TP à 2.5R

**S2 Bull** — Long après sweep de SSL (logique miroir)

**Gestion du risque**
- Risque fixe : 500$/trade (Topstep 50k)
- Stop journalier : −1 000$ (2 pertes max/jour)
- Expiration limite : 9 bougies 1m
- Détection M3 partielle : réplique exacte du comportement NT8 live

---

## Lancer la démo (aucune dépendance externe)

```bash
git clone https://github.com/Miky78450/ict-trading-bot
cd ict-trading-bot
python demo.py
# → http://localhost:5050
```

Rejoue les 1 607 trades du backtest en temps réel simulé.

## Lancer le dashboard analytics

```bash
pip install pandas numpy
python stats_server.py nq_ict_backtest_results_3m.csv
# → http://localhost:5051
```

Equity curve, breakdown par mois, par setup, par session.

## Lancer le backtester complet

```bash
pip install pandas numpy pytz
python nq_ict_backtester_BACKUP.py --csv NQ_full_clean.csv
```

---

## Stack technique

| Composant | Technologie |
|---|---|
| Backtester | Python · pandas · numpy · pytz |
| Bot temps réel | C# · .NET · NinjaTrader 8 API |
| Données live | Tradovate (via NT8) |
| Dashboard live | Python stdlib · HTTP server · HTML/CSS/JS |
| Dashboard stats | Python · pandas · numpy · Chart.js |
| Démo déployable | Python stdlib — zéro dépendance |

---

## Structure du repo

```
ict-trading-bot/
├── demo.py                            # Démo — replay backtest
├── launcher.py                        # Serveur live (NT8 requis)
├── stats_server.py                    # Serveur analytics
├── dashboard.html                     # Interface live
├── stats_dashboard.html               # Interface analytics
├── ICTSignalReader.cs                 # Stratégie C# NinjaTrader 8
├── nq_ict_backtester_BACKUP.py        # Moteur de backtest Python
├── nq_ict_backtest_results_3m.csv     # Résultats (1 607 trades)
├── requirements.txt
└── railway.toml                       # Déploiement Railway (démo)
```

---

## Auteur

Nathan Lecourieux · [github.com/Miky78450](https://github.com/Miky78450)
