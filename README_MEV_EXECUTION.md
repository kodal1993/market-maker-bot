# MEV-Vedett Execution Reteg

A bot most minden vegrehajthato signalt az `execute_trade(signal, context)` hivasra route-ol a [`src/execution_router.py`](src/execution_router.py) modulon keresztul, mielott barmilyen szimulalt fill megtortenik.

## Execution Modok

- `private_tx`: elonyben reszesitett mod, ha a MEV risk kozepes vagy magas, es van beallitott private RPC
- `cow_intent`: tamogatott paroknal hasznalt mod a beallitott minimum notional felett
- `guarded_public`: csak akkor engedelyezett, ha a quote, slippage, gas es MEV ellenorzesek mind atmennek
- `skip`: akkor adodik vissza, amikor az execution minosege nem biztonsagos

A jelenleg aktiv minimal utvonal:

- alacsony MEV risk: guarded public
- kozepes MEV risk: csak private
- magas MEV risk: skip

## Fo Komponensek

- `src/private_tx_executor.py`: private RPC alapu execution dontesi reteg
- `src/cow_executor.py`: CoW/intent routing az alkalmas parokhoz
- `src/slippage_guard.py`: dinamikus slippage es price impact vedelmi reteg
- `src/mev_risk_engine.py`: `mev_risk_score`, `sandwich_risk` es `execution_window_score` szamitasa
- `src/quote_validator.py`: router, backup, on-chain referencia es TWAP quote-ok keresztellenorzese
- `src/order_slicer.py`: nagyobb swapok feldarabolasa execution elott
- `src/trade_simulator.py`: pre-trade szimulacio minden route-hoz
- `src/policy_engine.py`: `safe`, `balanced` es `aggressive` profilok, plusz a `mev_policy.yaml`
- `src/execution_analytics.py`: trade-enkenti execution logok normalizalasa

## Config

1. Masold at a szukseges ertekeket a [`.env.example`](.env.example) fajlbol.
2. Allitsd be a `PRIVATE_RPC_URL`, `WALLET_PRIVATE_KEY` es `WALLET_ADDRESS` ertekeket live private tx kuldeshez.
3. Hangold a pair/router szabalyokat a [`mev_policy.yaml`](mev_policy.yaml) fajlban.

`BOT_MODE=paper` modban a private execution logolva es szimulalva fut a routeren keresztul.
Live modban a private executor valos tranzakcios payloadot var, es alairt raw tx-kent kuldi el a private RPC-n.

## Tesztek

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
