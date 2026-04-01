# Architektura

## Attekintes

A bot egy vekony orchestration reteget tart meg a `src/bot_runner.py` fajlban, es az ujrafelhasznalhato logikat kulon, celzott modulokba szervezi. A cel az, hogy a fo ciklus olvashato maradjon, mikozben a config, execution, strategy, risk, logging es notification felelossegek elvalnak egymastol.

## Runtime Folyamat

1. A `src/main.py` betolti a konfiguraciot, letrehozza a notifiert, a bootstrap arhistorikat es a runtime objektumokat.
2. A `src/bot_runner.py` futtatja a ciklusonkenti orchestration logikat:
   - arfolyam frissitese
   - intelligence es strategy dontesek kiszamitasa
   - risk es trade gate szabalyok alkalmazasa
   - execution route kivalasztasa
   - logok es ertesitesek rogzitese
3. A helper modulok tartalmazzak a domain logikat, igy a runner foleg az adatatadasert es a folyamat osszefogasert felel.

## Modulterkep

- Config
  - `src/config_env.py`: `.env` betoltes es tipizalt env helper fuggvenyek
  - `src/config_models.py`: config dataclass modellek
  - `src/config.py`: konkret config objektumok es visszafele kompatibilis konstans aliasok
- Execution
  - `src/runtime_execution.py`: execution context epitese es router bridge logika
  - `src/execution_router.py`: execution mod valasztas
  - `src/private_tx_executor.py`: private RPC kuldes
  - `src/execution_engine.py`: vart profit es fill-oldali execution logika
- Strategy
  - `src/strategy.py`: quote epites, spread, RSI es ar-oldali helper logika
  - `src/decision_engine.py`: akcio kivalasztas
  - `src/intelligence.py`: market regime es score szamitas
  - `src/runtime_strategy.py`: strategy-specifikus debug reasonok es trade kategoriak
- Risk
  - `src/runtime_risk.py`: reentry, force trade, profit lock es state helper logika
  - `src/trade_filter.py`: trade gate szabalyok
  - `src/state_machine.py`: state atmenetek es time-in-state logika
  - `src/inventory_manager.py`: cel inventory kezeles
  - `src/reentry_engine.py`: a strategy altal hasznalt reentry szamitasok
- Logging
  - `src/runtime_logging.py`: ciklus logolas es CSV sorok szerializalasa
  - `src/logger.py`: konzol/file logolas
  - `src/csv_logger.py`: CSV append helper
  - `src/performance.py`: osszegzesek es riport aggregalas
- Notifications
  - `src/telegram_notifier.py`: Telegram kuldes, parancsok, retry es riportok
  - `src/notifications.py`: notification export wrapper

## Takaritas Eredmenye

A `src/bot_runner.py` fajlban korabban felhalmozodott duplikalt helper implementaciok vekony wrapperre egyszerusodtek. Az aktiv logika most a fenti dedikalt modulokban el, ami biztonsagosabba es konnyebben tesztelhetove teszi a tovabbi modositasokat.
