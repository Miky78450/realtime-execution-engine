# Realtime Execution Engine

Application end-to-end de traitement et d'exécution de signaux sur flux de données financières temps réel. Pipeline Python pour le backtesting historique, moteur C# .NET pour l'exécution live, deux interfaces web de monitoring. Conçu, développé et déployé en autonomie complète.

**🔗 Démo live :** [realtime-execution-engine-production.up.railway.app](https://realtime-execution-engine-production.up.railway.app)

---

## Vue d'ensemble

Le système est composé de quatre briques techniques interconnectées :

1. **Pipeline de backtesting (Python)** — analyse statistique sur 8 ans de données historiques (~2,9M de bougies 1 minute, 1 600+ événements traités)
2. **Moteur d'exécution temps réel (C# .NET)** — intégré à une plateforme de marché externe via API native
3. **Dashboard de monitoring live** — polling API JSON 2s, visualisation des événements en cours
4. **Dashboard analytique** — séries temporelles, agrégats mensuels, statistiques détaillées

Le pipeline Python valide la logique métier sur l'historique. La même logique est ensuite portée en C# .NET pour l'exécution sur flux live. Les deux interfaces web exposent l'état du système en temps réel et l'historique consolidé.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    PIPELINE BACKTESTING                  │
│  Python · pandas · numpy · pytz                          │
│  → Traitement de 8 ans de données 1m                     │
│  → Validation de la logique métier                       │
│  → Export résultats CSV                                  │
└──────────────────────────┬───────────────────────────────┘
                           │ logique métier validée
                           ▼
┌──────────────────────────────────────────────────────────┐
│                  MOTEUR D'EXÉCUTION                      │
│  C# · .NET · API plateforme externe                      │
│  → Données live                                          │
│  → Algorithme identique au backtester                    │
│  → Logging structuré                                     │
└──────────────────────────┬───────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
┌─────────────────────┐       ┌──────────────────────┐
│   DASHBOARD LIVE    │       │ DASHBOARD ANALYTICS  │
│   Python · HTTP     │       │ Python · pandas      │
│   HTML/CSS/JS       │       │ Chart.js             │
│   → Parse log live  │       │ → Equity curve       │
│   → API JSON        │       │ → Stats mensuelles   │
│   → Polling 2s      │       │ → Breakdown détaillé │
└─────────────────────┘       └──────────────────────┘
```

---

## Stack technique

| Composant | Technologie |
|-----------|-------------|
| Pipeline de backtesting | Python · pandas · numpy · pytz |
| Moteur d'exécution | C# · .NET |
| Dashboard live | Python (HTTP server stdlib) · HTML/CSS/JS |
| Dashboard analytics | Python · pandas · numpy · Chart.js |
| Démo déployable | Python stdlib (zéro dépendance externe) |
| Déploiement continu | Git · Railway |

---

## Choix d'ingénierie

**Pourquoi deux langages.** Le pipeline de backtesting tourne sur des historiques de millions de lignes : Python + pandas est imbattable pour le prototypage et l'itération rapide sur la logique métier. Une fois la logique validée, le portage en C# .NET pour l'exécution live apporte la performance et l'intégration native avec la plateforme cible.

**Pourquoi deux dashboards distincts.** Les besoins ne sont pas les mêmes : le live tourne en mode minimaliste (polling 2s, parse de log, faible empreinte) ; l'analytique fait de la lecture lourde sur CSV avec calculs d'agrégats. Les séparer évite que les calculs lourds bloquent l'affichage live.

**Pourquoi une démo zéro-dépendance.** Pour permettre le test du système sans configuration externe (Railway lance simplement `python demo.py`), j'ai écrit la démo avec uniquement la stdlib Python. Le HTTP server, le parsing JSON, le routage : tout est fait à la main. La démo rejoue les événements du backtest en temps réel simulé.

**Déploiement continu.** Chaque push sur `main` redéploie automatiquement la démo via Railway. Versioning Git, itérations hebdomadaires, environnement de prod isolé.

---

## Lancer en local

### Démo (zéro dépendance)

```bash
git clone https://github.com/Miky78450/realtime-execution-engine
cd realtime-execution-engine
python demo.py
# → http://localhost:5050
```

Rejoue les 1 607 événements du backtest en temps réel simulé.

### Dashboard analytics

```bash
pip install pandas numpy
python stats_server.py backtest_results.csv
# → http://localhost:5051
```

### Pipeline de backtesting complet

```bash
pip install pandas numpy pytz
python backtester.py --csv data.csv
```

---

## Structure du repo

```
realtime-execution-engine/
├── demo.py                    # Démo (replay backtest, zéro dépendance)
├── launcher.py                # Serveur live
├── stats_server.py            # Serveur analytics
├── dashboard.html             # Interface live
├── stats_dashboard.html       # Interface analytics
├── SignalReader.cs            # Moteur d'exécution C#
├── backtester.py              # Pipeline Python
├── backtest_results.csv       # Résultats du backtest
├── requirements.txt
└── railway.toml               # Config déploiement
```

---

## Auteur

**Nathan Lecourieux** · Développeur · [github.com/Miky78450](https://github.com/Miky78450)

Projet conçu, développé, testé et déployé en autonomie complète dans le cadre d'une activité indépendante.