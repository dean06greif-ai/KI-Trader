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

1. Render Dashboard → **New** → **Web Service** → *Deploy an existing image*.
2. Image: `voyz/ibeam:latest`
3. Environment-Variablen des IBeam-Service (NICHT der Website!):
   ```
   IBEAM_ACCOUNT=DU1234567          # dein IBKR-(Paper-)Benutzername
   IBEAM_PASSWORD=deinPasswort
   IBEAM_PORT=10000                 # Render erwartet den Port aus PORT/10000
   ```
   (Render setzt `PORT` automatisch; falls der Service nicht startet,
   `IBEAM_PORT` auf den Wert von Renders `PORT` stellen.)
4. Instance Type: der kleinste (512 MB) reicht.
5. Nach dem Deploy: `https://<service>.onrender.com/v1/api/iserver/auth/status`
   aufrufen → sollte `"authenticated": true` liefern.

**Alternative ohne Render:** IBeam läuft genauso auf einem Mini-VPS oder
deinem Heim-PC per Docker:
`docker run -d --env IBEAM_ACCOUNT=DU1234567 --env IBEAM_PASSWORD=... -p 5000:5000 voyz/ibeam`
→ dann `IBKR_GATEWAY_URL=https://<deine-ip>:5000`.

## Schritt 3: Website verbinden

1. In den Render-Environment-Variablen des **Backends** eintragen:
   ```
   IBKR_GATEWAY_URL=https://<dein-ibeam-service>.onrender.com
   ```
2. Backend neu deployen.
3. Prüfen: **Master-Einstellungen → Forex-Gebühren (Interactive Brokers)** –
   dort zeigt die Statuszeile "IBKR-Gateway: verbunden & eingeloggt (Konto ...)".
   Alternativ direkt: `GET /api/ibkr/status`.

## Schritt 4: Forex-Gebühren einstellen

Master-Einstellungen → **Forex-Gebühren (Interactive Brokers)**:
- **Kommission (%/Seite):** Standard `0.002` % (= 0.2 Basispunkte, IBKR IdealPro)
- **Mindestkommission (USD/Seite):** Standard `2.00` USD

Diese Gebühren sind komplett **getrennt von den Bitunix-Krypto-Gebühren** und
werden automatisch für **Live-, Paper- UND Backtest-Berechnungen** auf allen
FOREX-Paaren verwendet (kleine Positionen zahlen durch die Mindestkommission
effektiv mehr % – das wird korrekt eingerechnet).

## So funktioniert das Live-Trading (technisch)

- Forex-Signale mit Modus **Live** platzieren eine **Market-Order + Bracket**
  (Stop-Loss als STP-Order, Take-Profit als LMT-Order, beides GTC) direkt bei
  IBKR – SL/TP liegen also **an der Börse**, nicht nur lokal.
- Live-Forex nutzt den **vollen TP** (IBKR-Brackets tragen genau einen TP);
  Partial-TP1/Break-Even gibt es weiterhin im Paper-Modus.
- Ein Hintergrund-Monitor hält die Gateway-Session am Leben und verbucht
  geschlossene Positionen (SL/TP-Fill oder manueller Close) mit echtem
  Fill-Preis und IBKR-Kommission.
- Ist das Gateway nicht erreichbar/eingeloggt, fällt ein Live-Forex-Signal
  **automatisch auf Paper** zurück (kein verlorenes Signal, kein Blindflug).
- Ordergröße: IBKR-FX läuft in Einheiten der Basiswährung
  (Kapital × Hebel = Notional). Beachte: IBKR empfiehlt Orders ab ~25.000
  Einheiten (kleinere gehen als "Odd Lot" mit etwas schlechterem Spread durch).

## Troubleshooting

| Problem | Lösung |
|---|---|
| `authenticated: false` | IBeam-Logs prüfen (Render → Logs). Meist: 2FA-Abfrage oder falsches Passwort. |
| Status "Gateway erreichbar, aber NICHT eingeloggt" | IBeam re-authentifiziert sich normalerweise selbst binnen 1–2 Minuten. Bleibt es hängen: IBeam-Service neu starten. |
| Bot fliegt raus, wenn du dich manuell einloggst | Eigenen Zweit-Benutzer für den Bot anlegen (Schritt 1.3). |
| Order abgelehnt: Konto nicht ermittelbar | `IBKR_ACCOUNT_ID` explizit in die Backend-Env eintragen (z.B. `DU1234567`). |
| Sonntags/nachts keine Fills | Forex-Handelszeiten: So 23:15 – Fr 23:00 (MEZ) – außerhalb ruht der Markt. |
