// ICTStrategy.cs — NinjaTrader 8
// Stratégie ICT complète : S1 Bear + S2 Bull
// Données temps réel Tradovate — plus de dépendance yfinance
//
// Installation :
//   1. Copier dans Documents\NinjaTrader 8\bin\Custom\Strategies\
//   2. Tools → Edit NinjaScript → Compile
//   3. Ajouter sur graphique MNQ 1min, compte SIM
//   4. La stratégie tourne seule — plus besoin de nq_bot.py

using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Strategies;

namespace NinjaTrader.NinjaScript.Strategies
{
    public class ICTStrategy : Strategy
    {
        // ── Paramètres (modifiables dans NT8) ─────────────────
        private string LogFile = @"C:\Users\natha\Documents\Backtest\Trading MGB\ict_bot.log";
        private string CsvFile = @"C:\Users\natha\Documents\Backtest\Trading MGB\ict_bot_trades.csv";

        // Horaires (heure Paris = UTC+2 en été)
        private int TradeStartHour = 2;
        private int TradeStartMin = 0;
        private int TradeEndHour = 20;
        private int TradeEndMin = 0;

        // ICT params
        private int SwingLB = 3;
        private double SweepBuf = 3.0;
        private int SweepWin = 120;   // 8 barres 15m × 15 = 120 barres 1m (= ~2h comme Python)
        private double SweepMinPts = 5.0;
        private double MaxEntryDist = 200.0;
        private double FvgMinPts = 1.0;
        private int FvgMaxAge = 200;   // bougies 1m
        private int IfvgSearchBars = 180;   // bougies 1m max pour chercher IFVG (3h)
        private double MinRR = 2.5;
        private double MinRiskPts = 10.0;
        private double MaxSlPts = 20.0;
        private int RsiPeriod = 7;
        private double RsiS1Max = 60.0;
        private double RsiS2Min = 40.0;
        private double RiskDollars = 500.0;  // Topstep 50k : 300$/trade
        private double DailyStopLoss = -1000.0;
        private int LimitExpiry = 9;     // bougies 1m (= 9 minutes, identique au Python)

        // ── Confirmations (reproduit les flags Python USE_M15_CONFIRMATION / MIN_ENTRY_DIST) ──
        private bool UseM15Confirmation = true;   // Annule trade si M15 close invalide le sweep
        private double MinEntryDistPts = 5.0;     // Distance min entry vs sweep level (pts)

        // ── Topstep : protection journalière ──
        private int MaxLossesPerDay = 2;   // stop trading après 2 pertes

        // ── Variables internes ────────────────────────────────
        private Order entryOrder = null;
        private Order slOrder = null;
        private Order tpOrder = null;
        private bool inTrade = false;
        private double savedSL = 0;
        private double savedTP = 0;
        private string savedDirection = "";
        private double entryFillPrice = 0;
        private double dailyPnl = 0;
        private DateTime lastDayReset = DateTime.MinValue;
        private int entryOrderBar = -999;  // Barre 1m où l'ordre limit a été placé (pour expiration)

        // Sweep tracking
        private double lastBslSweepPrice = 0;
        private int lastBslSweepBar = -999;
        private double lastSslSweepPrice = 0;
        private int lastSslSweepBar = -999;

        // M15 confirmation tracking
        private bool m15ConfirmPending = false;   // Un trade attend confirmation M15
        private double m15ConfirmLevel = 0;       // Niveau sweepé à confirmer
        private string m15ConfirmDir = "";        // "short" ou "long"
        private DateTime m15ConfirmCloseTime = DateTime.MinValue;  // Heure de clôture M15 à attendre

        // ── Look-ahead simulé : tracker la M15 où le sweep a été détecté pour
        // éviter les doublons (si conditions partial->valid->invalid->valid sur même M15).
        private DateTime lastBslSweptM15Open = DateTime.MinValue;
        private DateTime lastSslSweptM15Open = DateTime.MinValue;
        // Nombre de M3 dans la M15 partielle au moment du déclenchement (1..5)
        private int sweepN3InM15 = 0;

        // ── Contexte du trade en cours pour l'export CSV ──
        private string tradeSetup = "";       // "S1_Bear" ou "S2_Bull"
        private string tradeDirection = "";   // "short" ou "long"
        private string tradeBias = "";        // "bearish" ou "bullish"
        private string tradeSession = "";     // "asia" / "london" / "ny"
        private double tradeEntryPlanned = 0; // prix limit demandé (peut différer du fill)
        private double tradeRR = 0;
        private int tradeQty = 0;
        private DateTime tradeEntryTime = DateTime.MinValue;
        private double tradeEquityR = 0;      // cumul R-multiples

        // Topstep : compteur pertes journalières
        private int dailyLossCount = 0;

        // Note : les listes historicSweptBsl/Ssl ont été supprimées.
        // Elles bloquaient tout trade en Realtime parce qu'un même swing H1 reste valide
        // plusieurs jours — une fois marqué "déjà sweepé" il ne se retradait jamais.
        // Le backtester Python ne filtre pas par "déjà sweepé", donc on fait pareil.

        // IFVG search
        private bool searchingIfvg = false;
        private int searchStartBar = 0;
        private string searchDirection = "";
        private double searchSweepLevel = 0;

        // RSI 15m — liste des vraies clôtures 15m détectées
        private List<double> closes15m = new List<double>();
        private double cachedRsi = 50.0;
        private int last15mMinute = -1;
        private bool rsiInitialized = false;

        // Series H1
        private int idx1H = 1;  // index de la série H1
        private int idx15m = 2; // index de la série 15m

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "ICT Strategy — S1 Bear + S2 Bull — Temps réel NT8";
                Name = "ICTStrategy";
                Calculate = Calculate.OnBarClose;
                IsUnmanaged = true;
                BarsRequiredToTrade = 20;
                RealtimeErrorHandling = RealtimeErrorHandling.IgnoreAllErrors;
                IsInstantiatedOnEachOptimizationIteration = false;
            }
            else if (State == State.Configure)
            {
                // Ajouter série H1 pour le biais
                AddDataSeries(Data.BarsPeriodType.Minute, 60);
                // Ajouter série 15m pour la détection des sweeps (comme le backtester)
                AddDataSeries(Data.BarsPeriodType.Minute, 15);
            }
            else if (State == State.Transition)
            {
                closes15m.Clear();
                last15mMinute = -1;
                cachedRsi = 50.0;
                rsiInitialized = false;
                Log("Transition Historical→Realtime : RSI réinitialisé");
            }
            else if (State == State.DataLoaded)
            {
                // Log toutes les series pour verifier les indices
                for (int i = 0; i < BarsArray.Length; i++)
                    Log($"[SERIES] idx={i} | Type={BarsArray[i].BarsPeriod.BarsPeriodType} | Value={BarsArray[i].BarsPeriod.Value}");
            }
        }

        protected override void OnBarUpdate()
        {
            // Reset PnL journalier
            if (Time[0].Date != lastDayReset.Date)
            {
                dailyPnl = 0;
                dailyLossCount = 0;
                lastDayReset = Time[0];
                searchingIfvg = false;
                Log($"── Nouveau jour : {Time[0].Date} ──");
            }

            if (State != State.Realtime && State != State.Historical) return;

            // Log series au 1er appel pour verifier les indices
            if (CurrentBar == 1)
                for (int i = 0; i < BarsArray.Length; i++)
                    Log($"[SERIES] idx={i} Type={BarsArray[i].BarsPeriod.BarsPeriodType} Value={BarsArray[i].BarsPeriod.Value}");

            // Ignorer toutes les séries secondaires — le RSI est géré dans le bloc primaire
            if (BarsInProgress != 0) return;

            // ── Expiration ordre limit d'entrée ──────────────
            // Python : si l'ordre limit n'est pas rempli en LimitExpiry bougies 1m → annulation.
            // NT8 : par défaut les ordres limit restent pending. On cancel manuellement pour aligner.
            if (entryOrder != null && !inTrade
                && (CurrentBar - entryOrderBar) > LimitExpiry
                && entryOrder.OrderState == OrderState.Working)
            {
                Log($"Ordre limit expiré après {LimitExpiry} bougies 1m — annulation");
                try { CancelOrder(entryOrder); } catch { }
                entryOrder = null;
                entryOrderBar = -999;
                searchingIfvg = false;
                m15ConfirmPending = false;
            }

            // ── Confirmation USE_M15_CONFIRMATION : vérifier la clôture M15 ──
            // Si un trade a été pris sur un sweep partiel, on attend la clôture M15 pour valider.
            // Si la close finale invalide (S1: c15>=BSL, S2: c15<=SSL) → annuler l'ordre ou fermer.
            if (UseM15Confirmation && m15ConfirmPending)
            {
                // La barre courante a-t-elle atteint ou dépassé la clôture M15 attendue ?
                DateTime barTime = new DateTime(Time[0].Year, Time[0].Month, Time[0].Day,
                    Time[0].Hour, Time[0].Minute, 0);
                if (barTime >= m15ConfirmCloseTime)
                {
                    double c15Final = Close[0];
                    bool invalidate = false;
                    if (m15ConfirmDir == "short" && c15Final >= m15ConfirmLevel) invalidate = true;
                    if (m15ConfirmDir == "long"  && c15Final <= m15ConfirmLevel) invalidate = true;

                    if (invalidate)
                    {
                        Log($"M15_INVALIDATED : close={c15Final:F2} vs level={m15ConfirmLevel:F2} dir={m15ConfirmDir}");
                        // Annuler ordre limit si pas encore rempli
                        if (entryOrder != null && entryOrder.OrderState == OrderState.Working)
                        {
                            try { CancelOrder(entryOrder); } catch { }
                            entryOrder = null;
                            entryOrderBar = -999;
                        }
                        // Si déjà en trade ET position toujours ouverte, fermer au marché.
                        // CRITIQUE : annuler SL/TP AVANT de submit le Market exit pour éviter
                        // une race condition où les deux remplissent (= position inverse ouverte).
                        if (inTrade && Position.MarketPosition != MarketPosition.Flat)
                        {
                            try { if (slOrder != null && slOrder.OrderState == OrderState.Working) CancelOrder(slOrder); } catch { }
                            try { if (tpOrder != null && tpOrder.OrderState == OrderState.Working) CancelOrder(tpOrder); } catch { }
                            if (m15ConfirmDir == "short")
                                SubmitOrderUnmanaged(0, OrderAction.BuyToCover, OrderType.Market, tradeQty, 0, 0, "", "ICT_M15_Exit");
                            else
                                SubmitOrderUnmanaged(0, OrderAction.Sell, OrderType.Market, tradeQty, 0, 0, "", "ICT_M15_Exit");
                        }
                        searchingIfvg = false;
                    }
                    m15ConfirmPending = false;
                }
            }

            // Vérifier si on est en session
            if (!InSession()) return;

            // Stop journalier
            if (dailyPnl <= DailyStopLoss)
            {
                Log($"Stop journalier atteint ({dailyPnl:F0}$)");
                return;
            }

            // Topstep : stop après N pertes dans la journée
            if (dailyLossCount >= MaxLossesPerDay)
            {
                return;
            }

            // Trade actif — gérer la sortie
            if (inTrade) return;

            // Mettre à jour le RSI 15m — doit tourner à chaque barre 1m, même si biais neutral
            // ou si on est en mode recherche IFVG.
            UpdateRsi15m();

            // ── Mode recherche IFVG ─────────────────────────
            if (searchingIfvg)
            {
                if (CurrentBar - searchStartBar > IfvgSearchBars)
                {
                    Log($"IFVG timeout après {IfvgSearchBars} bougies");
                    searchingIfvg = false;
                    return;
                }
                SearchIfvg();
                return;
            }

            // ── DEBUG temps réel ─────────────────────────────
            string bias = GetBias();
            double bslDbg = GetBSL();
            double sslDbg = GetSSL();
            double rsiDbg = GetRsi();
            Log($"[ICT DEBUG] {Time[0]:HH:mm} | Biais:{bias} | BSL:{bslDbg:F2} | SSL:{sslDbg:F2} | RSI:{rsiDbg:F1} | Close:{Close[0]:F2} | H1bars:{BarsArray[idx1H].Count}");
            if (bias == "neutral") return;

            // ── Niveaux BSL/SSL ──────────────────────────────
            double bsl = GetBSL();
            double ssl = GetSSL();

            double h = High[0];
            double l = Low[0];
            double c = Close[0];
            double o = Open[0];

            double rsi = GetRsi();

            // ── Purger sweeps anciens ────────────────────────
            if (CurrentBar - lastBslSweepBar > SweepWin) lastBslSweepPrice = 0;
            if (CurrentBar - lastSslSweepBar > SweepWin) lastSslSweepPrice = 0;

            // ── Détecter sweeps sur M15 partielle (look-ahead simulé) ─────
            // OBJECTIF : aligner NT8 sur le backtester Python qui détecte le sweep
            // dès que la M15 (vue dans son intégralité par look-ahead) valide les
            // conditions, et place une entrée sur les M3 dans cette même M15.
            //
            // En live, on simule ça en vérifiant à CHAQUE close M3 si la M15 wall-clock
            // EN COURS valide déjà les conditions de sweep avec les valeurs partielles
            // (h15_partial = max High depuis l'ouverture M15, c15_partial = Close[0]).
            //
            // Anti-doublon : on ne re-déclenche pas pour la même M15 (par open time).
            //
            // Attention : si c15_partial repasse au-dessus du BSL plus tard dans la M15,
            // Python aurait considéré le sweep invalide. Mais si on a déjà placé l'ordre,
            // on accepte ce risque (faible en pratique).

            // Combien de M1 sont DÉJÀ fermées dans la M15 wall-clock courante.
            // Time[0] = close time de la M1 courante. Chaque M15 contient 15 M1.
            // Si Time[0].Minute % 15 == 0 → dernière M1 de la M15 précédente (n=15).
            int closeMin = Time[0].Minute;
            int n1InM15 = (closeMin % 15 == 0) ? 15 : (closeMin % 15);

            if (n1InM15 >= 1 && CurrentBar >= n1InM15)
            {
                // Open time de la M15 wall-clock en cours (pour anti-doublon).
                DateTime m15Open = Time[0].AddMinutes(-n1InM15);

                // M15 partielle : agréger les n1InM15 dernières M1 (incluant la courante).
                double h15 = High[0], l15 = Low[0];
                for (int k = 1; k < n1InM15; k++)
                {
                    if (High[k] > h15) h15 = High[k];
                    if (Low[k]  < l15) l15 = Low[k];
                }
                double c15 = Close[0]; // close partielle = close de la M1 courante

                // ── Détection BSL (1 fois par M15 max) ──
                if (m15Open != lastBslSweptM15Open)
                {
                    foreach (double lvl in GetAllBSL())
                    {
                        double wick = h15 - lvl;
                        if (h15 > lvl + SweepBuf && c15 < lvl && wick >= SweepMinPts)
                        {
                            lastBslSweepPrice = lvl;
                            lastBslSweepBar = CurrentBar;
                            lastBslSweptM15Open = m15Open;
                            sweepN3InM15 = n1InM15;
                            if (State == State.Realtime)
                                Log($"Sweep BSL @ {lvl:F2} | h15p={h15:F2} c15p={c15:F2} wick={wick:F2} | M15open={m15Open:HH:mm} (n1={n1InM15}/15)");
                            break;  // Python fait break sur le premier sweep trouvé
                        }
                    }
                }

                // ── Détection SSL (1 fois par M15 max) ──
                if (m15Open != lastSslSweptM15Open)
                {
                    foreach (double lvl in GetAllSSL())
                    {
                        double wick = lvl - l15;
                        if (l15 < lvl - SweepBuf && c15 > lvl && wick >= SweepMinPts)
                        {
                            lastSslSweepPrice = lvl;
                            lastSslSweepBar = CurrentBar;
                            lastSslSweptM15Open = m15Open;
                            sweepN3InM15 = n1InM15;
                            if (State == State.Realtime)
                                Log($"Sweep SSL @ {lvl:F2} | l15p={l15:F2} c15p={c15:F2} wick={wick:F2} | M15open={m15Open:HH:mm} (n1={n1InM15}/15)");
                            break;  // Python fait break sur le premier sweep trouvé
                        }
                    }
                }
            }

            // ── S1 Bear ─────────────────────────────────────
            // Python: le sweep est détecté sur la M3 où conditions remplies (ts_detect).
            // La recherche IFVG démarre à partir de cette M3, pas avant.
            // En NT8 : searchStartBar = lastBslSweepBar (la M1 où le sweep a été détecté).
            // Cela évite de trouver des entrées "anticipées" avant le sweep technique.
            if (bias == "bearish" && lastBslSweepPrice > 0
                && (CurrentBar - lastBslSweepBar) >= 0
                && rsi < RsiS1Max)
            {
                Log($"S1 Bear signal — démarrage recherche IFVG | RSI:{rsi:F1} | n1={sweepN3InM15}/15");
                searchingIfvg = true;
                searchStartBar = lastBslSweepBar;
                searchDirection = "bear";
                searchSweepLevel = lastBslSweepPrice;
                lastBslSweepPrice = 0;
                lastSslSweepPrice = 0;
                SearchIfvg();
                return;
            }

            // ── S2 Bull ─────────────────────────────────────
            if (bias == "bullish" && lastSslSweepPrice > 0
                && (CurrentBar - lastSslSweepBar) >= 0
                && rsi > RsiS2Min)
            {
                Log($"S2 Bull signal — démarrage recherche IFVG | RSI:{rsi:F1} | n1={sweepN3InM15}/15");
                searchingIfvg = true;
                searchStartBar = lastSslSweepBar;
                searchDirection = "bull";
                searchSweepLevel = lastSslSweepPrice;
                lastSslSweepPrice = 0;
                lastBslSweepPrice = 0;
                SearchIfvg();
                return;
            }
        }

        private void SearchIfvg()
        {
            int lookback = Math.Min(CurrentBar - searchStartBar + 1, IfvgSearchBars);
            if (lookback < 1) return;

            // ── Étape 1 : construire la liste de tous les FVGs ────────────────
            // Python: fvgs = [f for f in fvgs if s-f['idx'] <= FVG_MAX_AGE]
            // En NT8 : searchStartBar = barre du sweep
            // Un FVG à index j est valide si (searchStartBar - j) <= FvgMaxAge
            // ET le FVG doit être plus ancien que le sweep : j > lookback-1 (relatif)
            var fvgs = new List<(double top, double bot, int idx)>();
            int fvgScanLimit = lookback + FvgMaxAge + 2;

            for (int j = 2; j < fvgScanLimit && j + 2 < CurrentBar; j++)
            {
                // Filtre FVG_MAX_AGE : le FVG doit être dans les FvgMaxAge barres avant le sweep
                // En NT8 : sweep = barre searchStartBar, FVG à index j (relatif à CurrentBar)
                // La barre du sweep correspond à index (CurrentBar - searchStartBar) en relatif
                int sweepRelIdx = CurrentBar - searchStartBar;
                if (j > sweepRelIdx + FvgMaxAge) continue; // FVG trop ancien

                if (searchDirection == "bear")
                {
                    double sz = Low[j] - High[j + 2];
                    if (sz >= FvgMinPts)
                        fvgs.Add((Low[j], High[j + 2], j));
                }
                else
                {
                    double sz = Low[j + 2] - High[j];
                    if (sz >= FvgMinPts)
                        fvgs.Add((Low[j + 2], High[j], j));
                }
            }

            // ── Étape 2 : scanner les barres d'entrée du plus ancien au plus récent ─
            // Python scanne de ts_start (post-sweep, oldest) vers ts_end (newest)
            // En NT8 : i=lookback-1 = plus ancienne, i=0 = plus récente
            for (int i = lookback - 1; i >= 0; i--)
            {
                double c0 = Close[i];
                double o0 = Open[i];
                bool isBear = c0 < o0;
                bool isBull = c0 > o0;

                foreach (var fvg in fvgs)
                {
                    // FVG doit être PLUS ANCIEN que la barre d'entrée
                    // En NT8 : index plus grand = plus ancien → fvg.idx > i
                    if (fvg.idx <= i) continue;

                    if (searchDirection == "bear" && isBear)
                    {
                        // Entrée short : clôture sous le bot du FVG bullish
                        if (c0 < fvg.bot - 0.25 && (fvg.bot - c0) <= 50)
                        {
                            double entryPrice = c0;
                            double slPrice = Math.Max(o0, c0) + 0.25;
                            // Python: sl = max(sl_naturel, entry + MIN_RISK_PTS)
                            slPrice = Math.Max(slPrice, entryPrice + MinRiskPts);
                            double risk = slPrice - entryPrice;
                            if (risk > MaxSlPts) continue;
                            if (Math.Abs(entryPrice - searchSweepLevel) > MaxEntryDist) continue;
                            // Confirmation MinEntryDist : entry doit être à au moins MinEntryDistPts du sweep level
                            if (MinEntryDistPts > 0 && Math.Abs(entryPrice - searchSweepLevel) < MinEntryDistPts) continue;

                            bool filled = (i == 0);
                            for (int k = 1; k <= LimitExpiry; k++)
                            {
                                if (i - k < 0) break; // pas assez de barres futures disponibles
                                if (Low[i - k] <= entryPrice) { filled = true; break; }
                            }

                            if (filled)
                            {
                                double tp = entryPrice - risk * MinRR;
                                int qty = Math.Max(1, (int)(RiskDollars / (risk * 2)));
                                PlaceOrder("Sell", entryPrice, slPrice, tp, qty, "S1_Bear");
                                searchingIfvg = false;
                                return;
                            }
                            else
                            {
                                Log($"IFVG bear @ {entryPrice:F2} — limite non rempli");
                                searchingIfvg = false;
                                return;
                            }
                        }
                    }
                    else if (searchDirection == "bull" && isBull)
                    {
                        // Entrée long : clôture au-dessus du top du FVG bearish
                        if (c0 > fvg.top + 0.25 && (c0 - fvg.top) <= 50)
                        {
                            double entryPrice = c0;
                            double slPrice = Math.Min(o0, c0) - 0.25;
                            // Python: sl = min(sl_naturel, entry - MIN_RISK_PTS)
                            slPrice = Math.Min(slPrice, entryPrice - MinRiskPts);
                            double risk = entryPrice - slPrice;

                            if (risk > MaxSlPts) continue;
                            if (Math.Abs(entryPrice - searchSweepLevel) > MaxEntryDist) continue;
                            // Confirmation MinEntryDist : entry doit être à au moins MinEntryDistPts du sweep level
                            if (MinEntryDistPts > 0 && Math.Abs(entryPrice - searchSweepLevel) < MinEntryDistPts) continue;

                            bool filled = (i == 0);
                            for (int k = 1; k <= LimitExpiry; k++)
                            {
                                if (i - k < 0) break; // pas assez de barres futures disponibles
                                if (High[i - k] >= entryPrice) { filled = true; break; }
                            }

                            if (filled)
                            {
                                double tp = entryPrice + risk * MinRR;
                                int qty = Math.Max(1, (int)(RiskDollars / (risk * 2)));
                                PlaceOrder("Buy", entryPrice, slPrice, tp, qty, "S2_Bull");
                                searchingIfvg = false;
                                return;
                            }
                            else
                            {
                                Log($"IFVG bull @ {entryPrice:F2} — limite non rempli");
                                searchingIfvg = false;
                                return;
                            }
                        }
                    }
                }
            }
        }

        private void PlaceOrder(string action, double entry, double sl, double tp, int qty, string setup)
        {
            // Ne jamais placer d'ordre en phase Historical (rejeu d'historique au démarrage).
            // Les ordres live ne doivent être envoyés qu'en Realtime.
            if (State != State.Realtime) return;

            savedSL = sl;
            savedTP = tp;
            savedDirection = action;
            entryOrderBar = CurrentBar;  // Pour expiration après LimitExpiry barres 1m

            // ── Confirmation USE_M15_CONFIRMATION : stocker le contexte pour check clôture M15 ──
            if (UseM15Confirmation)
            {
                int minNow = Time[0].Minute;
                if (minNow % 15 == 0)
                {
                    // Cas spécial : l'ordre est placé EXACTEMENT à la clôture M15.
                    // La M15 sweepée vient de finaliser ses H/L/C. Pas besoin de confirmation
                    // car c'est déjà la close finale (Close[0] = close M15).
                    m15ConfirmPending = false;
                }
                else
                {
                    m15ConfirmPending = true;
                    m15ConfirmLevel = searchSweepLevel;
                    m15ConfirmDir = (action == "Sell") ? "short" : "long";
                    // Heure de clôture de la M15 en cours = prochain multiple de 15 minutes.
                    int minsToClose = 15 - (minNow % 15);
                    m15ConfirmCloseTime = Time[0].AddMinutes(minsToClose);
                    m15ConfirmCloseTime = new DateTime(m15ConfirmCloseTime.Year, m15ConfirmCloseTime.Month,
                        m15ConfirmCloseTime.Day, m15ConfirmCloseTime.Hour, m15ConfirmCloseTime.Minute, 0);
                }
            }

            // ── Capture du contexte pour l'export CSV ──
            tradeSetup = setup;
            tradeDirection = (action == "Sell") ? "short" : "long";
            tradeBias = GetBias();
            tradeSession = GetSession(Time[0]);
            tradeEntryPlanned = entry;
            tradeRR = MinRR;
            tradeQty = qty;
            // tradeEntryTime sera renseigné au fill effectif (OnOrderUpdate)

            Log($"[{setup}] Entry:{entry:F2} SL:{sl:F2} TP:{tp:F2} Qty:{qty}");

            if (action == "Sell")
                entryOrder = SubmitOrderUnmanaged(0, OrderAction.SellShort, OrderType.Limit, qty, entry, 0, "", "ICT_Entry");
            else
                entryOrder = SubmitOrderUnmanaged(0, OrderAction.Buy, OrderType.Limit, qty, entry, 0, "", "ICT_Entry");
        }

        private string GetSession(DateTime t)
        {
            int h = t.Hour;
            int m = t.Minute;
            int totalMin = h * 60 + m;
            if (totalMin >= 9 * 60 && totalMin < 15 * 60 + 30) return "london";
            if (totalMin >= 15 * 60 + 30 && totalMin < 22 * 60) return "ny";
            return "asia";
        }

        protected override void OnOrderUpdate(Order order, double limitPrice, double stopPrice,
            int quantity, int filled, double averageFillPrice,
            OrderState orderState, DateTime time, ErrorCode error, string nativeError)
        {
            try
            {
                if (order.Name == "ICT_Entry" && orderState == OrderState.Filled)
                {
                    inTrade = true;
                    entryFillPrice = averageFillPrice;
                    tradeEntryTime = time;  // timestamp du fill réel pour le CSV
                    Log($"Entree @ {averageFillPrice:F2}");

                    if (savedDirection == "Sell")
                    {
                        slOrder = SubmitOrderUnmanaged(0, OrderAction.BuyToCover, OrderType.StopMarket, filled, 0, savedSL, "", "ICT_SL");
                        tpOrder = SubmitOrderUnmanaged(0, OrderAction.BuyToCover, OrderType.Limit, filled, savedTP, 0, "", "ICT_TP");
                    }
                    else
                    {
                        slOrder = SubmitOrderUnmanaged(0, OrderAction.Sell, OrderType.StopMarket, filled, 0, savedSL, "", "ICT_SL");
                        tpOrder = SubmitOrderUnmanaged(0, OrderAction.Sell, OrderType.Limit, filled, savedTP, 0, "", "ICT_TP");
                    }
                    Log($"SL @ {savedSL:F2} | TP @ {savedTP:F2}");
                }

                if (order.Name == "ICT_SL" && orderState == OrderState.Filled)
                {
                    double pnl = -Math.Abs(entryFillPrice - averageFillPrice) * filled * 2;
                    dailyPnl += pnl;
                    dailyLossCount++;
                    Log($"SL @ {averageFillPrice:F2} | PnL: {pnl:F0}$ | Jour: {dailyPnl:F0}$ | Pertes: {dailyLossCount}/{MaxLossesPerDay}");
                    WriteTradeToCsv(averageFillPrice, time, "loss", "SL", pnl);
                    try { if (tpOrder != null && tpOrder.OrderState == OrderState.Working) CancelOrder(tpOrder); } catch { }
                    ResetTrade();

                    if (dailyLossCount >= MaxLossesPerDay)
                        Log($"⚠️ Max pertes journalières atteint — arrêt pour aujourd'hui");
                }

                if (order.Name == "ICT_TP" && orderState == OrderState.Filled)
                {
                    double pnl = Math.Abs(entryFillPrice - averageFillPrice) * filled * 2;
                    dailyPnl += pnl;
                    Log($"TP @ {averageFillPrice:F2} | PnL: +{pnl:F0}$ | Jour: {dailyPnl:F0}$");
                    WriteTradeToCsv(averageFillPrice, time, "win", "TP", pnl);
                    try { if (slOrder != null && slOrder.OrderState == OrderState.Working) CancelOrder(slOrder); } catch { }
                    ResetTrade();
                }

                // ── Sortie forcée par confirmation M15 (ordre Market) ──
                // Ce handler est crucial : sans lui, après un M15_INVALIDATED, la position
                // serait fermée mais inTrade resterait true → deadlock.
                if (order.Name == "ICT_M15_Exit" && orderState == OrderState.Filled)
                {
                    // Calcul du PnL réel : entry → exit (pas forcément perte)
                    double pnl;
                    if (savedDirection == "Sell")
                        pnl = (entryFillPrice - averageFillPrice) * filled * 2;
                    else
                        pnl = (averageFillPrice - entryFillPrice) * filled * 2;
                    dailyPnl += pnl;
                    string result = pnl >= 0 ? "win" : "loss";
                    if (pnl < 0) dailyLossCount++;
                    Log($"M15_EXIT @ {averageFillPrice:F2} | PnL: {pnl:+0;-0}$ | Jour: {dailyPnl:F0}$");
                    WriteTradeToCsv(averageFillPrice, time, result, "M15_INVALIDATED", pnl);
                    // Cancel SL et TP encore actifs
                    try { if (slOrder != null && slOrder.OrderState == OrderState.Working) CancelOrder(slOrder); } catch { }
                    try { if (tpOrder != null && tpOrder.OrderState == OrderState.Working) CancelOrder(tpOrder); } catch { }
                    ResetTrade();
                }

                if (orderState == OrderState.Rejected)
                {
                    Log($"Rejet : {order.Name} — {nativeError}");
                    try { if (slOrder != null && slOrder.OrderState == OrderState.Working) CancelOrder(slOrder); } catch { }
                    try { if (tpOrder != null && tpOrder.OrderState == OrderState.Working) CancelOrder(tpOrder); } catch { }
                    try { if (entryOrder != null && entryOrder.OrderState == OrderState.Working) CancelOrder(entryOrder); } catch { }
                    ResetTrade();
                }
            }
            catch (Exception ex)
            {
                Log($"Erreur OnOrderUpdate : {ex.Message}");
            }
        }

        private void ResetTrade()
        {
            inTrade = false;
            slOrder = null;
            tpOrder = null;
            entryOrder = null;
            entryOrderBar = -999;
            savedSL = 0;
            savedTP = 0;
            savedDirection = "";
            searchingIfvg = false;
            m15ConfirmPending = false;
            Log("Pret pour le prochain signal");
        }

        // ── Biais H1 (Dow Theory) — aligné sur Python precompute_bias ──────
        // Python : sub_h = h[max(0,i-window):i]  → EXCLUT la barre courante
        // On doit faire pareil : end = total - 1 (pour exclure la dernière barre H1)
        private string GetBias()
        {
            if (BarsArray[idx1H].Count < 10) return "neutral";
            var h1 = BarsArray[idx1H];
            int lb = SwingLB;
            int total = h1.Count;
            int win = 50;
            // Python : sub_h = h[max(0,i-window):i] — EXCLUT la barre H1 en cours de formation.
            // → end = total - 1 (exclut la dernière barre, qui est la H1 partielle)
            int end = total - 1;
            int start = Math.Max(0, end - win);

            var sh = new List<double>();
            var sl = new List<double>();

            for (int j = start + lb; j < end - lb; j++)
            {
                bool isHigh = true, isLow = true;
                for (int k = j - lb; k <= j + lb; k++)
                {
                    if (k == j) continue;
                    if (k < start || k >= end) continue;
                    if (h1.GetHigh(k) > h1.GetHigh(j)) isHigh = false;
                    if (h1.GetLow(k) < h1.GetLow(j)) isLow = false;
                }
                if (isHigh) sh.Add(h1.GetHigh(j));
                if (isLow) sl.Add(h1.GetLow(j));
            }

            if (sh.Count >= 2 && sl.Count >= 2)
            {
                int si = sh.Count - 1;
                int li = sl.Count - 1;
                bool hh = sh[si] > sh[si - 1];
                bool hl = sl[li] > sl[li - 1];
                bool lh = sh[si] < sh[si - 1];
                bool ll = sl[li] < sl[li - 1];
                if (hh && hl) return "bullish";
                if (lh && ll) return "bearish";
            }

            return "neutral";
        }

        // ── BSL = swing high H1 le plus proche AU-DESSUS du prix ──
        // ── BSL pour affichage dashboard : le plus proche AU-DESSUS du prix ──
        private double GetBSL()
        {
            var levels = GetAllBSL();
            double price = Close[0];
            foreach (var lvl in levels)  // déjà triés croissant
                if (lvl > price) return lvl;
            return 0;
        }

        // ── SSL pour affichage dashboard : le plus proche EN-DESSOUS du prix ──
        private double GetSSL()
        {
            var levels = GetAllSSL();
            double price = Close[0];
            foreach (var lvl in levels)  // déjà triés décroissant
                if (lvl < price) return lvl;
            return 0;
        }

        // ── Tous les BSL (swing highs H1) — aligné sur Python precompute_h1_levels ──
        // Python : scanne les 100 dernières barres H1 AVANT la barre courante,
        //         et retourne TOUS les swing highs sans filtre par prix.
        // Trié croissant (comme Python sorted(set(...)))
        private List<double> GetAllBSL()
        {
            var h1 = BarsArray[idx1H];
            int lb = SwingLB;
            int total = h1.Count;
            int end = total - 1;  // EXCLUT la barre H1 courante (alignement avec Python precompute_h1_levels)
            int start = Math.Max(0, end - 100);
            var levels = new HashSet<double>();

            for (int j = start + lb; j < end - lb; j++)
            {
                bool isHigh = true;
                for (int k = j - lb; k <= j + lb; k++)
                {
                    if (k == j || k < start || k >= end) continue;
                    if (h1.GetHigh(k) > h1.GetHigh(j)) { isHigh = false; break; }
                }
                if (isHigh) levels.Add(h1.GetHigh(j));
            }
            var result = levels.ToList();
            result.Sort();  // ordre croissant comme Python sorted(...)
            return result;
        }

        // ── Tous les SSL (swing lows H1) — aligné sur Python precompute_h1_levels ──
        // Trié décroissant (comme Python sorted(set(...), reverse=True))
        private List<double> GetAllSSL()
        {
            var h1 = BarsArray[idx1H];
            int lb = SwingLB;
            int total = h1.Count;
            int end = total - 1;  // EXCLUT la barre H1 courante (alignement avec Python precompute_h1_levels)
            int start = Math.Max(0, end - 100);
            var levels = new HashSet<double>();

            for (int j = start + lb; j < end - lb; j++)
            {
                bool isLow = true;
                for (int k = j - lb; k <= j + lb; k++)
                {
                    if (k == j || k < start || k >= end) continue;
                    if (h1.GetLow(k) < h1.GetLow(j)) { isLow = false; break; }
                }
                if (isLow) levels.Add(h1.GetLow(j));
            }
            var result = levels.ToList();
            result.Sort((a, b) => b.CompareTo(a));  // ordre décroissant comme Python reverse=True
            return result;
        }

        // ── RSI sur vraies clôtures 15m ─────────────────────────────────
        // En NT8, Time[0] = heure d'OUVERTURE de la barre 1m.
        // La barre qui CLÔTURE à 10:15 a Time[0]=10:12.
        // On utilise donc le temps de clôture = Time[0] + 3min pour détecter
        // exactement quand une bougie 15m se ferme.
        private void UpdateRsi15m()
        {
            int needed = 200;

            // Initialisation une seule fois en Realtime.
            // IMPORTANT : BarsArray[idx15m].GetClose(j) avec j>=1 retourne des barres corrompues
            // (données de février figées dans le cache NT8) — bug NT8 documenté avec Calculate=OnBarClose.
            // Solution : reconstruire les clôtures 15m wall-clock-aligned depuis l'historique 1m.
            // En NT8, Time[i] = close time de la barre i. Une clôture M15 wall-clock = barre M3
            // dont Time.Minute ∈ {0, 15, 30, 45}. Identique au resample Python.
            // L'historique 1m (Close[j], Time[j]) est toujours fiable.
            if (!rsiInitialized && State == State.Realtime)
            {
                // Scan de l'historique 1m du plus récent au plus ancien
                // CurrentBar = index de la barre courante ; Close[j] = clôture j barres en arrière
                for (int j = 1; j < CurrentBar && closes15m.Count < needed; j++)
                {
                    DateTime t = Time[j];
                    if (t.Minute % 15 == 0)
                        closes15m.Add(Close[j]);
                }
                rsiInitialized = true;
                RecalcRsi();
                Log($"RSI initialisé depuis historique 1m sur {closes15m.Count} clôtures 15m → RSI:{cachedRsi:F1}");
                return;
            }

            // Mise à jour à chaque frontière 15m wall-clock en Realtime.
            // Time[0] = close time de la barre M3 courante. Une close M15 wall-clock-aligned
            // tombe quand Time[0].Minute ∈ {0, 15, 30, 45}.
            if (!rsiInitialized || State != State.Realtime) return;

            if (Time[0].Minute % 15 != 0) return;

            double newClose = Close[0];
            Log($"[RSI DBG] Clôture 15m @ {Time[0]:HH:mm} | Close={newClose:F2}");
            closes15m.Insert(0, newClose);
            if (closes15m.Count > needed)
                closes15m.RemoveAt(closes15m.Count - 1);
            RecalcRsi();
            Log($"[RSI DBG] Updated | NewClose={newClose:F2} | RSI={cachedRsi:F1} | Count={closes15m.Count}");
        }
        private void RecalcRsi()
        {
            if (closes15m.Count < RsiPeriod + 1)
            {
                cachedRsi = 50.0; // pas assez de données → valeur neutre, ne bloque aucun trade
                return;
            }

            // RSI Wilder — closes15m[0] = plus récent, closes15m[Count-1] = plus ancien
            // Étape 1 : première moyenne simple sur les RsiPeriod périodes les plus anciennes
            int oldest = closes15m.Count - 2; // index le plus ancien utilisable (besoin de [i+1])
            double avgGain = 0, avgLoss = 0;

            for (int i = oldest; i > oldest - RsiPeriod && i >= 0; i--)
            {
                double delta = closes15m[i] - closes15m[i + 1];
                if (delta > 0) avgGain += delta;
                else avgLoss -= delta;
            }
            avgGain /= RsiPeriod;
            avgLoss /= RsiPeriod;

            // Étape 2 : lissage Wilder sur toutes les données restantes vers le plus récent
            for (int i = oldest - RsiPeriod; i >= 0; i--)
            {
                double delta = closes15m[i] - closes15m[i + 1];
                double gain = delta > 0 ? delta : 0;
                double loss = delta < 0 ? -delta : 0;
                avgGain = (avgGain * (RsiPeriod - 1) + gain) / RsiPeriod;
                avgLoss = (avgLoss * (RsiPeriod - 1) + loss) / RsiPeriod;
            }

            double rs = (avgLoss == 0) ? 1000 : avgGain / avgLoss;
            cachedRsi = 100 - (100 / (1 + rs));
        }

        private double GetRsi()
        {
            return cachedRsi;
        }

        // ── Vérification session ─────────────────────────────
        private bool InSession()
        {
            if (Time[0].DayOfWeek == DayOfWeek.Saturday ||
                Time[0].DayOfWeek == DayOfWeek.Sunday) return false;

            // Convertir en heure Paris (UTC+2 été, UTC+1 hiver)
            // NT8 utilise l'heure locale du PC — si ton PC est en heure Paris c'est direct
            int h = Time[0].Hour;
            int m = Time[0].Minute;
            int t = h * 60 + m;
            int start = TradeStartHour * 60 + TradeStartMin;
            int end = TradeEndHour * 60 + TradeEndMin;
            return t >= start && t < end;
        }

        // ── Log ──────────────────────────────────────────────
        private void Log(string msg)
        {
            string line = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {msg}";
            Print($"[ICT] {line}");
            try { File.AppendAllText(LogFile, line + Environment.NewLine); } catch { }
        }

        // ── Export CSV des trades (mêmes colonnes que le backtester Python) ──
        // Permet la comparaison directe NT8 vs Python (diff par entry_time).
        private void WriteTradeToCsv(double exitPrice, DateTime exitTime, string result, string exitNote, double pnlDollars)
        {
            try
            {
                // En-tête à la création du fichier
                if (!File.Exists(CsvFile))
                {
                    File.WriteAllText(CsvFile,
                        "setup,direction,bias,session,entry,sl,tp,rr,qty,entry_time,entry_tf,exit,exit_time,result,exit_note,r_pnl,equity_r" + Environment.NewLine);
                }

                // setup numérique : "S1_Bear" → 1, "S2_Bull" → 2
                int setupNum = tradeSetup.StartsWith("S1") ? 1 : 2;

                // r_pnl : équivalent Python = ratio rr*RISK_DOLLARS (win) ou -RISK_DOLLARS (loss),
                // exprimé en multiples de R. RR moyen affiché = pnlDollars / RiskDollars.
                double rPnl = pnlDollars / RiskDollars;
                tradeEquityR += rPnl;

                // Format ISO pour entry_time/exit_time (compatible pandas)
                string entryStr = tradeEntryTime.ToString("yyyy-MM-dd HH:mm:ss");
                string exitStr  = exitTime.ToString("yyyy-MM-dd HH:mm:ss");

                // Format invariant culture pour les nombres décimaux (point, pas virgule)
                var ic = System.Globalization.CultureInfo.InvariantCulture;
                string line = string.Join(",", new string[] {
                    setupNum.ToString(ic),
                    tradeDirection,
                    tradeBias,
                    tradeSession,
                    entryFillPrice.ToString("F2", ic),
                    savedSL.ToString("F2", ic),
                    savedTP.ToString("F2", ic),
                    tradeRR.ToString("F2", ic),
                    tradeQty.ToString(ic),
                    entryStr,
                    "1m",
                    exitPrice.ToString("F2", ic),
                    exitStr,
                    result,
                    exitNote,
                    rPnl.ToString("F4", ic),
                    tradeEquityR.ToString("F4", ic)
                });

                File.AppendAllText(CsvFile, line + Environment.NewLine);
            }
            catch (Exception ex)
            {
                Log($"Erreur WriteTradeToCsv : {ex.Message}");
            }
        }
    }
}