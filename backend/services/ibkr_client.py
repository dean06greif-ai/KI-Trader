"""Interactive Brokers Client-Portal-API Client (Forex-Live-Trading).

Architektur (autonom auf Render, analog zum BitunixTradeClient):
  * Die App spricht per REST mit einem CLIENT PORTAL GATEWAY. Empfohlen wird
    IBeam (https://github.com/Voyz/ibeam) hinter dem kleinen Token-Proxy aus
    ibeam_gateway/ (Repo-Root) – siehe IBKR_SETUP_ANLEITUNG.md.
  * Env-Variablen:
      IBKR_GATEWAY_URL     z.B. https://mein-ibeam.onrender.com  (Pflicht)
      IBKR_GATEWAY_TOKEN   Shared-Secret des Proxys (Header X-Gateway-Token)
      IBKR_ACCOUNT_ID      z.B. DU1234567 / U1234567 (optional, sonst Auto-Detect)
      IBKR_VERIFY_SSL      "false" (Default) – IBeam nutzt ein Self-Signed-Zertifikat

Der Client ist bewusst dünn und stateless: Session-Keepalive (tickle) und
Auth-Status werden vom Monitor-Loop in services/ibkr_trade.py getrieben.
"""
import asyncio
import json
import logging
import os
import time
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

# Bekannte IDEALPRO-Conids der unterstützten Majors (stabil seit Jahren).
# Fallback: dynamische Suche über /iserver/secdef/search.
FOREX_CONIDS: Dict[str, int] = {
    "EURUSD": 12087792,
    "GBPUSD": 12087797,
    "USDJPY": 15016059,
    "AUDUSD": 14433401,
    "USDCAD": 15016062,
    "USDCHF": 12087820,
    "NZDUSD": 39453441,
}

HEALTH_SERVER_HINT = (
    "Die Gateway-URL antwortet mit dem IBeam-HEALTH-Server (Python BaseHTTP, "
    "Port 5001) statt mit dem Client-Portal-Gateway (Port 5000). Render leitet "
    "nur EINEN Port weiter – bitte den Token-Proxy aus ibeam_gateway/ deployen "
    "(IBKR_SETUP_ANLEITUNG.md, Schritt 2)."
)


def base_qty_for_notional(symbol: str, notional_usd: float, price: float) -> int:
    """IBKR-FX-Orders laufen in Einheiten der BASISWÄHRUNG (EURUSD -> EUR).
    USD-Quote-Paare (EURUSD): base = notional_usd / price.
    USD-Basis-Paare (USDJPY): base = notional_usd. Ganzzahlig gerundet."""
    s = str(symbol or "").upper()
    if price and not s.startswith("USD"):
        return max(1, int(round(float(notional_usd) / float(price))))
    return max(1, int(round(float(notional_usd))))


def looks_like_health_server(status: int, text: str) -> bool:
    """IBeam-Health-Server (http.server) statt Gateway erkannt? (rein, testbar)"""
    t = (text or "")[:400]
    return status in (404, 501) and "<title>Error response</title>" in t


def extract_order_ids(res) -> List[str]:
    """order_ids aus einer Order-Antwort (Liste von {order_id,...}) ziehen."""
    rows = res if isinstance(res, list) else []
    return [str(r.get("order_id")) for r in rows
            if isinstance(r, dict) and r.get("order_id")]


def map_orders_by_ref(live_orders: List[Dict], refs: List[str]) -> Dict[str, str]:
    """cOID (order_ref) -> orderId aus /iserver/account/orders (rein, testbar)."""
    want = set(refs)
    out: Dict[str, str] = {}
    for o in live_orders or []:
        if not isinstance(o, dict):
            continue
        ref = str(o.get("order_ref") or o.get("cOID") or "")
        oid = o.get("orderId") or o.get("order_id")
        if ref in want and oid:
            out[ref] = str(oid)
    return out


class IBKRClient:
    def __init__(self):
        self.gateway = (os.getenv("IBKR_GATEWAY_URL") or "").strip().rstrip("/")
        # Fallback GATEWAY_TOKEN: so heißt die Variable im Render-Backend-Env
        # (ohne IBKR_-Präfix) – vorher wurde dadurch KEIN Token mitgesendet.
        self.token = (os.getenv("IBKR_GATEWAY_TOKEN")
                      or os.getenv("GATEWAY_TOKEN") or "").strip()
        self.account_id = (os.getenv("IBKR_ACCOUNT_ID") or "").strip()
        self.verify_ssl = (os.getenv("IBKR_VERIFY_SSL", "false").strip().lower()
                           in ("1", "true", "yes"))
        self._session: Optional[aiohttp.ClientSession] = None
        self._conids: Dict[str, int] = dict(FOREX_CONIDS)
        self._portfolio_primed = False
        # Vom Monitor-Loop gepflegt: (timestamp, authenticated)
        self.last_auth = (0.0, None)
        # Diagnose der letzten Antwort (fürs Master-Panel)
        self.last_diag: Optional[str] = None

    def configured(self) -> bool:
        return bool(self.gateway)

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                connector=aiohttp.TCPConnector(limit=10, ssl=self.verify_ssl or False))
        return self._session

    def _headers(self) -> Dict[str, str]:
        h = {"User-Agent": "KI-Trader", "Accept": "application/json"}
        if self.token:
            h["X-Gateway-Token"] = self.token
        return h

    async def _raw(self, method: str, url: str, body=None, params=None):
        """(status, text) – Transportfehler als (0, fehlertext)."""
        try:
            async with self._http().request(method, url, json=body, params=params,
                                            headers=self._headers()) as r:
                return r.status, await r.text()
        except Exception as e:
            return 0, f"{type(e).__name__}: {str(e)[:160]}"

    async def _req(self, method: str, path: str, body: Optional[Dict] = None,
                   params: Optional[Dict] = None):
        status, txt = await self._raw(method, f"{self.gateway}/v1/api{path}", body, params)
        if status == 0:
            return {"_error": txt}
        if looks_like_health_server(status, txt):
            self.last_diag = HEALTH_SERVER_HINT
            return {"_error": f"HTTP {status}: IBeam-Health-Server statt Gateway", "_diag": "health_server"}
        try:
            data = json.loads(txt) if txt else {}
        except ValueError:
            data = {"_raw": txt[:300]}
        if status == 401:
            # Nur der Token-Proxy antwortet mit diesem JSON-Body; ein LEERES
            # 401 kommt vom Client-Portal-Gateway selbst = Session (noch)
            # nicht eingeloggt (z.B. IBKR-Freischaltung ausstehend).
            if "invalid gateway token" in (txt or "").lower():
                self.last_diag = ("Proxy lehnt ab (401): IBKR_GATEWAY_TOKEN stimmt "
                                  "nicht mit dem Token des IBeam-Proxys überein.")
                return {"_error": "HTTP 401: Gateway-Token ungültig", "_diag": "token"}
            self.last_diag = ("Gateway erreichbar, aber Session nicht eingeloggt "
                              "(401 vom Client-Portal) – IBeam-Login ausstehend.")
            return {"_error": "HTTP 401: Session nicht eingeloggt",
                    "_diag": "not_authenticated"}
        if status >= 400:
            return {"_error": f"HTTP {status}: {str(data)[:200]}"}
        self.last_diag = None
        return data

    # ------------------- Session / Konto -------------------
    async def probe(self) -> Dict:
        """Health-Probes des IBeam-Proxys (ohne Token): livez/readyz."""
        out = {"livez": None, "readyz": None}
        for key in out:
            status, _ = await self._raw("GET", f"{self.gateway}/{key}")
            out[key] = status if status else None
        return out

    async def auth_status(self) -> Dict:
        """{'authenticated': bool, 'connected': bool, 'error': str|None}"""
        res = await self._req("POST", "/iserver/auth/status", body={})
        if isinstance(res, dict) and "_error" not in res:
            out = {"authenticated": bool(res.get("authenticated")),
                   "connected": bool(res.get("connected")), "error": None}
        else:
            out = {"authenticated": False, "connected": False,
                   "error": (res or {}).get("_error", "keine Antwort")}
        self.last_auth = (time.time(), out["authenticated"])
        return out

    async def tickle(self) -> None:
        await self._req("POST", "/tickle", body={})

    async def ensure_account(self) -> Optional[str]:
        if self.account_id:
            return self.account_id
        res = await self._req("GET", "/iserver/accounts")
        accts = (res or {}).get("accounts") if isinstance(res, dict) else None
        if isinstance(accts, list) and accts:
            self.account_id = str(accts[0])
            logger.info(f"IBKR: Konto automatisch erkannt: {self.account_id}")
            return self.account_id
        return None

    async def _prime_portfolio(self) -> None:
        """IBKR verlangt einen /portfolio/accounts-Call vor Portfolio-Abfragen."""
        if not self._portfolio_primed:
            await self._req("GET", "/portfolio/accounts")
            self._portfolio_primed = True

    async def account_summary(self) -> Dict:
        """Kontostand: {net_liquidation, available_funds, currency}."""
        acct = await self.ensure_account()
        if not acct:
            return {"error": "Konto nicht ermittelbar"}
        await self._prime_portfolio()
        res = await self._req("GET", f"/portfolio/{acct}/summary")
        if not isinstance(res, dict) or res.get("_error"):
            return {"error": (res or {}).get("_error", "keine Antwort")}

        def _amt(key):
            v = res.get(key)
            if isinstance(v, dict):
                try:
                    return float(v.get("amount") or 0), v.get("currency")
                except (TypeError, ValueError):
                    return None, None
            return None, None
        nl, cur = _amt("netliquidation")
        af, _ = _amt("availablefunds")
        return {"net_liquidation": nl, "available_funds": af,
                "currency": cur or "USD", "account_id": acct}

    # ------------------- Kontrakte -------------------
    async def forex_conid(self, symbol: str) -> Optional[int]:
        s = str(symbol or "").upper()
        if s in self._conids:
            return self._conids[s]
        pair = f"{s[:3]}.{s[3:]}"
        res = await self._req("GET", "/iserver/secdef/search",
                              params={"symbol": pair, "secType": "CASH"})
        rows = res if isinstance(res, list) else []
        for row in rows:
            if isinstance(row, dict) and row.get("conid"):
                try:
                    self._conids[s] = int(row["conid"])
                    return self._conids[s]
                except (TypeError, ValueError):
                    continue
        logger.error(f"IBKR: conid für {s} nicht gefunden: {str(res)[:160]}")
        return None

    # ------------------- Orders -------------------
    async def _confirm_replies(self, res, max_rounds: int = 5):
        """IBKR stellt Rückfragen (Precautionary Messages) – automatisch mit
        'confirmed' beantworten, bis eine Order-Antwort kommt."""
        for _ in range(max_rounds):
            if isinstance(res, list) and res and isinstance(res[0], dict) \
                    and res[0].get("id") and "order_id" not in res[0]:
                res = await self._req("POST", f"/iserver/reply/{res[0]['id']}",
                                      body={"confirmed": True})
                continue
            break
        return res

    def fx_order(self, acct: str, conid: int, side: str, qty: int, order_type: str,
                 coid: str, price: Optional[float] = None,
                 parent: Optional[str] = None) -> Dict:
        """Order-Ticket der Client-Portal-API für ein FX-Paar (rein)."""
        o = {"acctId": acct, "conid": conid, "secType": f"{conid}:CASH",
             "cOID": coid, "orderType": order_type, "side": side,
             "quantity": int(qty), "tif": "GTC", "isCcyPair": True}
        if price is not None:
            o["price"] = round(float(price), 6)
        if parent:
            o["parentId"] = parent
        return o

    async def place_orders(self, orders: List[Dict]) -> Dict:
        """Mehrere Tickets in EINEM Request (Brackets über parentId).
        Rückgabe: {ok, order_ids (Reihenfolge der Antwort), by_ref (cOID->id), error}."""
        acct = await self.ensure_account()
        if not acct:
            return {"ok": False, "error": "IBKR-Konto nicht ermittelbar (Gateway eingeloggt?)"}
        res = await self._req("POST", f"/iserver/account/{acct}/orders",
                              body={"orders": orders})
        res = await self._confirm_replies(res)
        if isinstance(res, dict) and res.get("_error"):
            return {"ok": False, "error": res["_error"]}
        ids = extract_order_ids(res)
        if not ids:
            return {"ok": False, "error": f"Keine order_id in Antwort: {str(res)[:180]}"}
        refs = [o.get("cOID") for o in orders if o.get("cOID")]
        by_ref: Dict[str, str] = {}
        try:
            await asyncio.sleep(1.0)
            by_ref = map_orders_by_ref(await self.get_live_orders(), refs)
        except Exception as e:
            logger.debug(f"IBKR: Order-Abgleich per cOID übersprungen: {e}")
        if len(by_ref) < len(refs) and len(ids) == len(orders):
            # Fallback: Antwort-Reihenfolge = Ticket-Reihenfolge
            for ref, oid in zip(refs, ids):
                by_ref.setdefault(ref, oid)
        return {"ok": True, "order_ids": ids, "by_ref": by_ref, "raw": res}

    async def modify_order(self, order_id: str, order: Dict) -> Dict:
        """Bestehende Order ändern (z.B. STP-Preis -> Break-Even). IBKR verlangt
        den vollständigen Order-Zustand, nur der geänderte Wert weicht ab."""
        acct = await self.ensure_account()
        if not acct or not order_id:
            return {"ok": False, "error": "kein Konto/order_id"}
        body = {k: v for k, v in order.items() if k not in ("cOID", "parentId")}
        res = await self._req("POST", f"/iserver/account/{acct}/order/{order_id}", body=body)
        res = await self._confirm_replies(res)
        if isinstance(res, dict) and res.get("_error"):
            return {"ok": False, "error": res["_error"]}
        ids = extract_order_ids(res)
        return {"ok": bool(ids), "order_id": ids[0] if ids else order_id,
                "error": None if ids else f"Unerwartete Antwort: {str(res)[:160]}"}

    async def close_forex_position(self, symbol: str, side: str, qty: int) -> Dict:
        """Position per Gegen-Market-Order schließen (reduziert die Position)."""
        acct = await self.ensure_account()
        conid = await self.forex_conid(symbol)
        if not acct or not conid:
            return {"ok": False, "error": "Konto/Kontrakt nicht verfügbar"}
        coid = f"KIT-X-{symbol}-{int(time.time() * 1000) % 10_000_000_000}"
        order = self.fx_order(acct, conid, "SELL" if str(side).upper() == "LONG" else "BUY",
                              int(qty), "MKT", coid)
        res = await self.place_orders([order])
        if res.get("ok"):
            return {"ok": True, "order_id": res["order_ids"][0]}
        return res

    async def cancel_order(self, order_id: str) -> Dict:
        acct = await self.ensure_account()
        if not acct or not order_id:
            return {"_error": "kein Konto/order_id"}
        return await self._req("DELETE", f"/iserver/account/{acct}/order/{order_id}")

    # ------------------- Abfragen -------------------
    async def get_positions(self) -> List[Dict]:
        acct = await self.ensure_account()
        if not acct:
            return []
        await self._prime_portfolio()
        res = await self._req("GET", f"/portfolio/{acct}/positions/0")
        return res if isinstance(res, list) else []

    async def position_qty(self, conid) -> Optional[float]:
        """Signierte Positionsgröße für einen Kontrakt (None = API unsicher)."""
        rows = await self.get_positions()
        if not isinstance(rows, list):
            return None
        for p in rows:
            if isinstance(p, dict) and str(p.get("conid")) == str(conid):
                try:
                    return float(p.get("position") or 0)
                except (TypeError, ValueError):
                    return None
        return 0.0

    async def get_live_orders(self) -> List[Dict]:
        """Alle Orders des Tages (/iserver/account/orders – erster Call liefert
        ggf. nur einen Snapshot-Hinweis, deshalb bis zu 2 Versuche)."""
        for _ in range(2):
            res = await self._req("GET", "/iserver/account/orders")
            rows = res.get("orders") if isinstance(res, dict) else None
            if isinstance(rows, list) and (rows or res.get("snapshot")):
                return rows
            await asyncio.sleep(0.5)
        return []

    async def get_trades(self) -> List[Dict]:
        """Executions der letzten Tage (Fill-Preise für den Close-Abgleich)."""
        res = await self._req("GET", "/iserver/account/trades")
        return res if isinstance(res, list) else []

    async def get_order_status(self, order_id: str) -> Dict:
        res = await self._req("GET", f"/iserver/account/order/status/{order_id}")
        return res if isinstance(res, dict) else {}

    async def wait_fill(self, order_id: str, timeout: float = 12.0) -> Optional[float]:
        """Kurz auf den Entry-Fill warten und den Ø-Fill-Preis liefern."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = await self.get_order_status(order_id)
            status = str(st.get("order_status") or st.get("status") or "").lower()
            for key in ("avg_fill_price", "average_price", "avgPrice"):
                try:
                    v = float(st.get(key) or 0)
                    if v > 0:
                        return v
                except (TypeError, ValueError):
                    pass
            if status in ("cancelled", "inactive"):
                return None
            await asyncio.sleep(1.5)
        return None


ibkr_client = IBKRClient()
