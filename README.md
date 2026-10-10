# MarketMaker (MM)

Multi-Asset-Daytrading-Plattform (Krypto, Indizes, Rohstoffe, Forex) mit KI Trader,
Strategie-Labor, Regime-Lab und Regime-Copilot.

- `backend/` – FastAPI (Render Web Service, `uvicorn server:app`)
- `frontend/` – React/CRACO (Render Static Site, `yarn build`)
- `local_worker/` – optionaler lokaler Rechen-Worker
- `ibeam_gateway/` – IBKR-Gateway (separater Render-Service)

Hinweis: Der Datenbank-Name (`DB_NAME`) und alle Env-Keys bleiben unverändert,
nur der Anzeigename lautet MarketMaker (MM). Für den OpenRouter-Header kann
`OPENROUTER_TITLE="MarketMaker"` gesetzt werden.
