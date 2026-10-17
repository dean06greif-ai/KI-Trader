# IBKR-Anleitung: Forex live traden über Interactive Brokers

Diese Anleitung richtet die Interactive-Brokers-Anbindung (Forex-Live-Trading)
so ein, dass sie **autonom und dauerhaft ohne dein Eingreifen** auf Render läuft –
genau wie die Bitunix-Anbindung.

## Wichtig: Bei IBKR gibt es keinen klassischen "API-Key"

Anders als Bitunix vergibt IBKR **keinen API-Key + Secret** für Privatkonten.
Stattdessen läuft die offizielle **Client Portal Web API** über ein kleines
**Gateway**, das mit deinem IBKR-Benutzernamen/Passwort eingeloggt ist.
Damit das autonom funktioniert, nutzen wir **IBeam** – ein fertiges
Docker-Image, das sich selbst einloggt, die Session am Leben hält und sich
nach Abbrüchen automatisch neu authentifiziert.

Die Website spricht dann per REST mit diesem Gateway. In die `.env` der
Website kommt **nur die URL des Gateways** (kein Passwort!):

```
IBKR_GATEWAY_URL=https://<dein-ibeam-service>.onrender.com
IBKR_ACCOUNT_ID=          # optional, wird sonst automatisch erkannt
IBKR_VERIFY_SSL=false     # IBeam nutzt ein Self-Signed-Zertifikat
```

---

## Schritt 1: IBKR-Konto vorbereiten (einmalig, ~10 Minuten)

1. Logge dich auf https://www.interactivebrokers.com ins **Client Portal** ein.
2. **Paper-Konto für den Start:** Menü → *Einstellungen* → *Kontoeinstellungen*
   → *Paper-Trading-Konto*. Notiere Benutzername (beginnt mit `DU...`) und
   setze dort ein Passwort. **Teste IMMER zuerst mit dem Paper-Konto!**
3. **Zweiten Benutzer anlegen (dringend empfohlen für Live):**
   *Einstellungen* → *Benutzer & Zugriffsrechte* → *Benutzer hinzufügen*.
   - Grund: Ein IBKR-Benutzer kann nur EINE aktive Session haben. Nutzt der
     Bot deinen Haupt-Login, wirst du beim manuellen Einloggen den Bot rauswerfen.
   - Gib dem neuen Benutzer nur Trading-Rechte (kein Banking/Auszahlungen).
4. **2FA (IB Key):** Für den Bot-Benutzer die Zwei-Faktor-Anmeldung auf
   die Option "IB Key nur bei Auszahlungen" beschränken bzw. IBeam mit 2FA
   nicht koppeln – sonst muss bei jedem Re-Login dein Handy bestätigen und
   der autonome Betrieb bricht ab. (Beim Paper-Konto gibt es keine 2FA-Pflicht.)
5. Konto muss **IBKR Pro** sein (API-Zugang), voll eröffnet und kapitalisiert.

## Schritt 2: IBeam-Gateway auf Render deployen (~10 Minuten)

> **Warum die alte Anleitung (Image `voyz/ibeam` direkt) auf Render NICHT geht:**
> Render leitet pro Web-Service genau **einen** Port weiter und nimmt automatisch
> den **ersten offenen Port** des Containers. Bei IBeam ist das der interne
> **Health-Server (Port 5001, Python `http.server`)** – nicht das eigentliche
> Client-Portal-Gateway (Port 5000, Java, nur HTTPS mit Self-Signed-Zertifikat).
> Deshalb kommt bei `https://<service>.onrender.com/v1/api/...` nur eine HTML-
> Seite **"Error response – 501 Unsupported method / 404"** (Server-Header
> `BaseHTTP/0.6 Python/3.11`). Der Website-Status meldet das jetzt eindeutig als
> "IBeam-Health-Server statt Gateway". Lösung: der kleine Token-Proxy aus
> `ibeam_gateway/` in diesem Repo.

1. Render Dashboard → **New** → **Web Service** → **Build and deploy from a Git
   repository** → dieses Repo (KI-Trader) auswählen.
2. Einstellungen des Services:
   - **Name:** z.B. `ibeam-gateway`
   - **Runtime:** `Docker`
   - **Dockerfile Path:** `ibeam_gateway/Dockerfile`
   - **Docker Build Context Directory:** `ibeam_gateway`
   - **Instance Type:** Starter (512 MB reicht; Free schläft ein → Session weg)
   - **Health Check Path:** `/livez`
3. Environment-Variablen des IBeam-Service (NICHT der Website!):
   ```
   IBEAM_ACCOUNT=DU1234567          # dein IBKR-Benutzername (Bot-Benutzer, Schritt 1.3)
   IBEAM_PASSWORD=deinPasswort
   GATEWAY_TOKEN=<langes Zufallspasswort>   # Shared-Secret des Proxys (z.B. 40 Zeichen)
   ```
   Render setzt `PORT` automatisch (10000) – der Proxy lauscht darauf, IBeam
   bleibt intern auf 5000/5001. **Kein** `IBEAM_PORT` mehr setzen.
4. Nach dem Deploy prüfen:
   - `https://<service>.onrender.com/livez` → `OK` (Proxy + IBeam laufen)
   - `https://<service>.onrender.com/readyz` → `OK` = bei IBKR eingeloggt,
     `Not Ready` (503) = IBeam ist NICHT eingeloggt → Render-Logs des IBeam-Service
     lesen (fast immer: 2FA-Abfrage oder falsches Passwort, siehe Schritt 1.4).
   - `curl -H "X-Gateway-Token: <TOKEN>" -X POST https://<service>.onrender.com/v1/api/iserver/auth/status`
     → JSON mit `"authenticated": true`. Ohne Token kommt `401 invalid gateway token`
     – so kann niemand außer deinem Backend Orders an dein Konto schicken.

**Alternative ohne Render:** IBeam läuft genauso auf einem Mini-VPS oder
deinem Heim-PC per Docker (dann kein Proxy nötig):
`docker run -d --env IBEAM_ACCOUNT=DU1234567 --env IBEAM_PASSWORD=... -p 5000:5000 voyz/ibeam`
→ dann `IBKR_GATEWAY_URL=https://<deine-ip>:5000` (und `IBKR_GATEWAY_TOKEN` leer lassen).

## Schritt 3: Website verbinden

1. In den Render-Environment-Variablen des **Backends** eintragen:
   ```
   IBKR_GATEWAY_URL=https://<dein-ibeam-service>.onrender.com
   IBKR_GATEWAY_TOKEN=<derselbe Wert wie GATEWAY_TOKEN beim IBeam-Service>
   IBKR_ACCOUNT_ID=U1234567        # optional, sonst Auto-Detect
   IBKR_VERIFY_SSL=false
   ```
2. Backend neu deployen.
3. Prüfen: **Master-Einstellungen → Forex-Gebühren (Interactive Brokers)** –
   dort zeigt die Statuszeile "IBKR-Gateway: verbunden & eingeloggt (Konto ...)".
   Bei Problemen steht darunter eine **Diagnose** (Health-Server statt Gateway,
   Token falsch, nicht eingeloggt) plus die Proxy-Probes `livez`/`readyz`.
   Alternativ direkt: `GET /api/ibkr/status`.
4. Header-Badge: Das **LIVE-Badge** zeigt standardmäßig wie bisher das
   Bitunix-Konto (Mini-Badge „Bitunix“). **Doppelklick** auf das Badge schaltet
   auf den **IBKR-Kontostand** (Net Liquidation + frei verfügbar, Mini-Badge
   „IBKR“) – erneuter Doppelklick zurück. Einfach-Klick öffnet wie gewohnt das
   Kapital-Modal.

## Schritt 4: Forex-Gebühren einstellen

Master-Einstellungen → **Forex-Gebühren (Interactive Brokers)**:
- **Kommission (%/Seite):** Standard `0.002` % (= 0.2 Basispunkte, IBKR IdealPro)
- **Mindestkommission (USD/Seite):** Standard `2.00` USD

Diese Gebühren sind komplett **getrennt von den Bitunix-Krypto-Gebühren** und
werden automatisch für **Live-, Paper- UND Backtest-Berechnungen** auf allen
FOREX-Paaren verwendet (kleine Positionen zahlen durch die Mindestkommission
effektiv mehr % – das wird korrekt eingerechnet). Auch der **Fee-Wächter** im
KI-Setup rechnet bei Forex mit der IBKR-Kommission (inkl. Mindestkommission je
Order – bei aktivem TP1 mit zwei OCA-Legs also doppelt) statt mit der
Bitunix-Gebühr.

## So funktioniert das Live-Trading (technisch)

- Forex-Signale mit Modus **Live** platzieren **OCA-Bracket-Orders** direkt bei
  IBKR (Market-Entry + Stop-Loss als STP + Take-Profit als LMT, alles GTC) –
  SL/TP liegen also **an der Börse**, nicht nur lokal.
- **Partial-TP1 + Break-Even wie bei Bitunix:** Ist `TP1 schließen %` (Coin-
  Config) gesetzt, werden **zwei Brackets** platziert:
  - Leg **„tp1“**: TP1-Anteil der Menge, TP = TP1
  - Leg **„runner“**: Restmenge, TP = voller TP
  Füllt TP1, storniert IBKR den zugehörigen SL automatisch (OCA); der Runner
  bleibt jederzeit durch seinen eigenen SL abgesichert. Der IBKR-Monitor
  verbucht den TP1-Fill (echter Fill-Preis) und zieht den Runner-SL per
  Order-Modify auf **Break-Even** (inkl. Gebühren) – exakt wie im Bitunix-Flow.
- **Trailing / Gewinnsicherung / Key-Level-SL** aus dem Trade-Manager greifen
  auch für IBKR-Trades: jede SL-Änderung wird als Order-Modify an IBKR gesendet.
- Exits (SL/TP-Fills) werden **nur an der Börse** ausgeführt und mit echtem
  Fill-Preis + IBKR-Kommission verbucht (kein lokales „Doppel-Schließen“).
  Manueller/KI-Close: offene Bracket-Kinder werden storniert, die **echte**
  Restposition gelesen und exakt diese per Gegen-Market-Order geschlossen.
- Ist das Gateway nicht erreichbar/eingeloggt, fällt ein Live-Forex-Signal
  **automatisch auf Paper** zurück (kein verlorenes Signal, kein Blindflug).
- Ordergröße: IBKR-FX läuft in Einheiten der Basiswährung
  (Kapital × Hebel = Notional). Beachte: IBKR empfiehlt Orders ab ~25.000
  Einheiten (kleinere gehen als "Odd Lot" mit etwas schlechterem Spread durch).

## Troubleshooting

| Problem | Lösung |
|---|---|
| `Error response 501 Unsupported method` / `404` als HTML-Seite | Render zeigt auf den IBeam-Health-Server statt aufs Gateway → Schritt 2 (Token-Proxy aus `ibeam_gateway/`). |
| `/readyz` liefert `Not Ready` (503) | IBeam ist nicht bei IBKR eingeloggt: IBeam-Logs (Render → Logs). Meist 2FA-Abfrage (Schritt 1.4: SLS-Opt-out / kein IB-Key für den Bot-Benutzer) oder falsches Passwort. |
| `401 invalid gateway token` | `IBKR_GATEWAY_TOKEN` (Backend) ≠ `GATEWAY_TOKEN` (IBeam-Service). |
| `authenticated: false` | IBeam-Logs prüfen (Render → Logs). Meist: 2FA-Abfrage oder falsches Passwort. |
| Status "Gateway erreichbar, aber NICHT eingeloggt" | IBeam re-authentifiziert sich normalerweise selbst binnen 1–2 Minuten. Bleibt es hängen: IBeam-Service neu starten. |
| Bot fliegt raus, wenn du dich manuell einloggst | Eigenen Zweit-Benutzer für den Bot anlegen (Schritt 1.3). |
| Order abgelehnt: Konto nicht ermittelbar | `IBKR_ACCOUNT_ID` explizit in die Backend-Env eintragen (z.B. `U1234567`). |
| Sonntags/nachts keine Fills | Forex-Handelszeiten: So 23:15 – Fr 23:00 (MEZ) – außerhalb ruht der Markt. |
