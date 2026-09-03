"""Interactive Brokers Client-Portal-API Client (Forex-Live-Trading).

Architektur (autonom auf Render, analog zum BitunixTradeClient):
  * Die App spricht per REST mit einem CLIENT PORTAL GATEWAY. Empfohlen wird
    IBeam (https://github.com/Voyz/ibeam): ein Docker-Dienst, der sich mit
    IBKR-Benutzername/Passwort selbst einloggt, die Session automatisch am
    Leben hält und neu authentifiziert – kein manuelles Eingreifen nötig.
  * Env-Variablen (siehe IBKR_SETUP_ANLEITUNG.md im Repo-Root):
      IBKR_GATEWAY_URL   z.B. https://mein-ibeam.onrender.com  (Pflicht)
      IBKR_ACCOUNT_ID    z.B. DU1234567 / U1234567 (optional, sonst Auto-Detect)
      IBKR_VERIFY_SSL    "false" (Default) – IBeam nutzt ein Self-Signed-Zertifikat

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


def base_qty_for_notional(symbol: str, notional_usd: float, price: float) -> int:
    """IBKR-FX-Orders laufen in Einheiten der BASISWÄHRUNG (EURUSD -> EUR).
    USD-Quote-Paare (EURUSD): base = notional_usd / price.
    USD-Basis-Paare (USDJPY): base = notional_usd. Ganzzahlig gerundet."""
    s = str(symbol or "").upper()
    if price and not s.startswith("USD"):
        return max(1, int(round(float(notional_usd) / float(price))))
    return max(1, int(round(float(notional_usd))))


class IBKRClient:
    def __init__(self):
        self.gateway = (os.getenv("IBKR_GATEWAY_URL") or "").strip().rstrip("/")
        self.account_id = (os.getenv("IBKR_ACCOUNT_ID") or "").strip()
        self.verify_ssl = (os.getenv("IBKR_VERIFY_SSL", "false").strip().lower()
                           in ("1", "true", "yes"))
        self._session: Optional[aiohttp.ClientSession] = None
        self._conids: Dict[str, int] = dict(FOREX_CONIDS)
        self._portfolio_primed = False
        # Vom Monitor-Loop gepflegt: (timestamp, authenticated)
        self.last_auth = (0.0, None)

    def configured(self) -> bool:
        return bool(self.gateway)

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                connector=aiohttp.TCPConnector(limit=10, ssl=self.verify_ssl or False))
        return self._session

    async def _req(self, method: str, path: str, body: Optional[Dict] = None,
                   params: Optional[Dict] = None):
        url = f"{self.gateway}/v1/api{path}"
        try:
            async with self._http().request(
                    method, url, json=body, params=params,
                    headers={"User-Agent": "KI-Trader"}) as r:
                txt = await r.text()
                try:
                    data = json.loads(txt) if txt else {}
                except ValueError:
                    data = {"_raw": txt[:300]}
                if r.status >= 400:
                    return {"_error": f"HTTP {r.status}: {str(data)[:200]}"}
                return data
        except Exception as e:
            return {"_error": f"{type(e).__name__}: {str(e)[:160]}"}

    # ------------------- Session / Konto -------------------
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
        """IBKR verlangt einen /portfolio/accounts-Call vor Positions-Abfragen."""
        if not self._portfolio_primed:
            await self._req("GET", "/portfolio/accounts")
            self._portfolio_primed = True

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

    async def place_forex_bracket(self, symbol: str, side: str, qty: int,
                                  sl_price: float, tp_price: float) -> Dict:
        """Market-Entry + angehängte SL-(STP)- und TP-(LMT)-Order (GTC).
        Rückgabe: {ok, order_id, sl_order_id, tp_order_id, error}."""
        acct = await self.ensure_account()
        if not acct:
            return {"ok": False, "error": "IBKR-Konto nicht ermittelbar (Gateway eingeloggt?)"}
        conid = await self.forex_conid(symbol)
        if not conid:
            return {"ok": False, "error": f"Kein IBKR-Kontrakt (conid) für {symbol}"}
        is_long = str(side).upper() == "LONG"
        coid = f"KIT-{symbol}-{int(time.time() * 1000) % 10_000_000_000}"
        parent = {"acctId": acct, "conid": conid, "secType": f"{conid}:CASH",
                  "cOID": coid, "orderType": "MKT", "side": "BUY" if is_long else "SELL",
                  "quantity": int(qty), "tif": "GTC", "isCcyPair": True}
        child_side = "SELL" if is_long else "BUY"
        children = []
        if sl_price:
            children.append({"acctId": acct, "conid": conid, "secType": f"{conid}:CASH",
                             "parentId": coid, "orderType": "STP", "side": child_side,
                             "price": round(float(sl_price), 6), "quantity": int(qty),
                             "tif": "GTC", "isCcyPair": True})
        if tp_price:
            children.append({"acctId": acct, "conid": conid, "secType": f"{conid}:CASH",
                             "parentId": coid, "orderType": "LMT", "side": child_side,
                             "price": round(float(tp_price), 6), "quantity": int(qty),
                             "tif": "GTC", "isCcyPair": True})
        res = await self._req("POST", f"/iserver/account/{acct}/orders",
                              body={"orders": [parent] + children})
        res = await self._confirm_replies(res)
        if isinstance(res, dict) and res.get("_error"):
            return {"ok": False, "error": res["_error"]}
        rows = res if isinstance(res, list) else []
        ids = [str(r.get("order_id")) for r in rows
               if isinstance(r, dict) and r.get("order_id")]
        if not ids:
            return {"ok": False, "error": f"Keine order_id in Antwort: {str(res)[:180]}"}
        return {"ok": True, "conid": conid, "order_id": ids[0],
                "sl_order_id": ids[1] if len(ids) > 1 and sl_price else None,
                "tp_order_id": (ids[2] if len(ids) > 2
                                else (ids[1] if len(ids) > 1 and not sl_price else None))}

    async def close_forex_position(self, symbol: str, side: str, qty: int) -> Dict:
        """Position per Gegen-Market-Order schließen (reduziert die Position)."""
        acct = await self.ensure_account()
        conid = await self.forex_conid(symbol)
        if not acct or not conid:
            return {"ok": False, "error": "Konto/Kontrakt nicht verfügbar"}
        order = {"acctId": acct, "conid": conid, "secType": f"{conid}:CASH",
                 "orderType": "MKT",
                 "side": "SELL" if str(side).upper() == "LONG" else "BUY",
                 "quantity": int(qty), "tif": "GTC", "isCcyPair": True}
        res = await self._req("POST", f"/iserver/account/{acct}/orders",
                              body={"orders": [order]})
        res = await self._confirm_replies(res)
        rows = res if isinstance(res, list) else []
        oid = next((str(r.get("order_id")) for r in rows
                    if isinstance(r, dict) and r.get("order_id")), None)
        if oid:
            return {"ok": True, "order_id": oid}
        return {"ok": False, "error": str(res)[:180]}

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
            for key in ("avg_fill_price", "avgPrice", "average_price"):
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
