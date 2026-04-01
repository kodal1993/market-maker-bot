# Market Maker Bot

## Gyors Inditas

Windows alatt van egy egygombos indito fajl a projekt gyokerben:

```powershell
.\start_trading.bat
```

Ez a jelenlegi `.env` beallitasokkal inditja a botot. Ha csak ellenorizni akarod, hogy minden keszen all-e, futtasd ezt:

```powershell
.\start_trading.bat --check
```

A `--check` ugyanazt a startup validaciot futtatja, mint a normal inditas, es hiba eseten nem engedi elindulni a botot.

Ha azt akarod, hogy a bot folyamatosan fusson, a `.env`-ben hagyd a `MAX_LOOPS=0` beallitast. Ez a projektben azt jelenti, hogy a bot addig megy, amig kezzel le nem allitod, peldaul `Ctrl+C`-vel.

Rovid architektura leiras: [ARCHITECTURE.md](ARCHITECTURE.md).

## Tobbjelu Intelligence Reteg

A bot most mar egy tobbretegu decision engine-t hasznal a price loop tetejen. Egy ciklusban egyszerre tudja figyelembe venni:

- market regime felismeres: `TREND`, `RANGE`, `RISK_OFF`
- volatilitas allapot felismeres: `LOW`, `NORMAL`, `HIGH`, `EXTREME`
- drawdown-erzekeny risk throttling es capital-preservation logika
- RSS/hir alapu sentiment scoring
- makro jelzesek figyelese hivatalos RSS feedekbol, peldaul Fed, BLS, ECB vagy BIS forrasokbol
- opcionis on-chain es security alert scoring RSS vagy lokalis JSON feedekbol
- adaptiv spread, inventory es meretezes optimalizalas a kozelmultbeli equity viselkedes alapjan

Maga a trading mod tovabbra is arvezerelet. A kulso hir, makro es on-chain feedek elsosorban risk filterkent mukodnek, nem primer trade triggerkent.

Az aktualis execution modok:

- `TREND_UP`: bullish reszvetel agresszivebb buy logikaval
- `RANGE_MAKER`: alacsonyabb kockazatu ketoldalu market making
- `OVERWEIGHT_EXIT`: vedelmi inventory csokkentes
- `NO_TRADE`: warmup vagy capital-preservation allapot

### Signal Beallitasok

Minden kulso signal opcionis. Ha nincs beallitva, a bot nem all le, hanem semleges ertekekre esik vissza.

Hasznos environment valtozok:

- `NEWS_RSS_URLS`: vesszovel elvalasztott RSS vagy Atom URL-ek, vagy lokalis XML fajlok
- `MACRO_RSS_URLS`: vesszovel elvalasztott hivatalos makro RSS feedek
- `ONCHAIN_RSS_URLS`: opcionis exploit, whale, bridge vagy liquidation alert feedek RSS/Atom/JSON formatumban
- `START_USDC`, `START_ETH`, `START_ETH_USD`: paper/live paper indulobalance, ahol a `START_ETH_USD` az elso elerheto referencia aron vesz extra ETH-t
- `REENTRY_ENGINE_ENABLED`, `REENTRY_ZONE_1_MULTIPLIER`, `REENTRY_ZONE_2_MULTIPLIER`, `REENTRY_ZONE_3_MULTIPLIER`: SELL utani re-entry anchor es buy zonak
- `REENTRY_TIMEOUT_MINUTES`, `REENTRY_TIMEOUT_BUY_FRACTION`, `REENTRY_MAX_MISS_PCT`: timeout es runaway/max-miss vedelmek
- `PROFIT_LOCK_LEVEL_1_BPS`, `PROFIT_LOCK_LEVEL_2_BPS`, `MICRO_TRAILING_PULLBACK_BPS`: profit-lock reszleges SELL es micro trailing viselkedes
- `PARTIAL_RESET_USDC_THRESHOLD_PCT`, `PARTIAL_RESET_BUY_FRACTION`, `ETH_ACCUMULATION_REINVEST_PCT`: ETH accumulation es partial reset hangolas
- `EXECUTION_ENGINE_ENABLED`, `EXECUTION_MIN_EXPECTED_PROFIT_PCT`, `EXECUTION_TAKER_SLIPPAGE_BPS`: execution reteg, maker/taker es minimum vart profit kuszob
- `TRADE_FILTER_ENABLED`, `MIN_TRADE_DISTANCE_PCT`, `TRADE_COOLDOWN_MINUTES`: overtrading csokkentese, trade gate es cooldown
- `INVENTORY_MANAGER_ENABLED`, `INVENTORY_NORMAL_MIN`, `INVENTORY_UPTREND_MAX`, `INVENTORY_DOWNTREND_MIN`: ETH/USDC arany kontroll
- `SIDE_FLIP_COOLDOWN_CYCLES`, `SIDE_FLIP_MIN_BPS`: churn csokkentese ugy, hogy side-valtas elott ido- es ar-elvalasztast kovetel
- `TREND_BUY_MIN_MARKET_SCORE`, `TREND_BUY_MIN_SIGNAL_SCORE`, `TREND_BUY_MIN_LONG_BUFFER_BPS`, `MAX_TREND_PULLBACK_BPS`: trend-buy minoseg szigoritasa, hogy a bot erosebb pullbackeket vegyen a zajos fordulok helyett
- `SIGNAL_CACHE_SECONDS`: kulso feedek cache ideje
- `INTELLIGENCE_WARMUP_ROWS`: minimum sorszam, mielott az intelligence layer tradelhet
- `CAPITAL_PRESERVATION_DRAWDOWN_PCT`: drawdown kuszob, amely vedelmibb viselkedest kenyszerit ki

Peldakent hasznalhato lokalis tesztbemenetek a `data\signals\` mappaban vannak.

## Tortenelmi Adatok

Valos Coinbase spot candle adatok letolthetok olyan CSV-be, amely mar illeszkedik a backtest folyamathoz:

```powershell
.\.venv\Scripts\python.exe src\download_coinbase_history.py --product ETH-USD --granularity 300 --days 30
```

Ez letrehoz egy fajlt a `data\historical\` mappaban olyan oszlopokkal, mint a `timestamp`, `open`, `high`, `low`, `close`, `volume` es `price`.

Tobb timeframe osszehasonlitasa ugyanazon a tortenelmi idoszakon:

```powershell
.\.venv\Scripts\python.exe src\timeframe_benchmark.py --product ETH-USD --days 30 --granularities 60,300,900,3600 --seeds 41,42,43,44,45
```

## Visszateszteles

Barmely CSV fajl tortenelmi arai alapjan visszajatszhatod az aktualis strategiat:

```powershell
.\.venv\Scripts\python.exe src\backtest.py --input logs\equity.csv --price-column price
```

Pelda letoltott Coinbase adatokkal:

```powershell
.\.venv\Scripts\python.exe src\backtest.py --input data\historical\eth_usd_300s_20260220_20260322.csv --price-column close --source-column source
```

Hasznos flag-ek:

- `--price-column close` ha a CSV a candle close erteket a `close` oszlopban tarolja
- `--source-column source` ha szeretned, hogy a source mezot is atvigye a backtest logokba
- `--limit 500` csak az elso 500 sor visszajatszasahoz
- `--cycle-seconds 300` ha a bemeneti fajlnev nem kodolja a candle spacinget
- `--disable-reentry` a regi adaptive baseline visszajatszasahoz az uj re-entry engine nelkul
- `--disable-execution`, `--disable-trade-filter`, `--disable-inventory-manager` az uj retegek izolalt tesztelesehez
- `--seed 42` determinisztikus fillekhez
- `--verbose` minden visszajatszott ciklus kiirasahoz

Minden backtest a sajat output fajljait a `logs\backtests\` mappaba irja.

Az alap valtozat es az uj adaptive + re-entry engine osszehasonlitasa ugyanazon az adathalmazon:

```powershell
$env:SIGNAL_FETCH_ENABLED='false'
.\.venv\Scripts\python.exe src\variant_benchmark.py --input data\historical\eth_usd_300s_20260220_20260322.csv --price-column close --source-column source --seeds 41,42,43
```

Az osszehasonlitas most negy varianshoz ir kimenetet:

- `current_bot`
- `adaptive`
- `adaptive_reentry`
- `adaptive_reentry_execution`

### Visszateszteles Lokalis Signal Mintakkal

Az intelligence layer live feedek nelkul is smoke-tesztelheto:

```powershell
$env:NEWS_RSS_URLS='data\signals\sample_news.xml'
$env:MACRO_RSS_URLS='data\signals\sample_macro.json'
$env:ONCHAIN_RSS_URLS='data\signals\sample_onchain.json'
.\.venv\Scripts\python.exe src\backtest.py --input data\historical\eth_usd_300s_20260220_20260322.csv --price-column close --limit 150 --label multi_signal_smoke
```
