"""
Strategy Registry - built-in + dynamically loaded custom strategies.
"""
from typing import Dict, List, Optional
from strategies.scalping_strategy import ScalpingStrategy
from strategies.rsi_only_strategy import RSIOnlyStrategy
from strategies.bollinger_reversion_strategy import BollingerReversionStrategy
from strategies.ema_pullback_scalping_strategy import EMAPullbackScalpingStrategy
from strategies.ict_liquidity_sweep_strategy import ICTLiquiditySweepStrategy
from strategies.macd_rsi_strategy import MACDRSIStrategy
from strategies.bollinger_squeeze_strategy import BollingerSqueezeStrategy
from strategies.vwap_reversion_strategy import VWAPReversionStrategy
from strategies.horst_vwap_obv_strategy import HorstVWAPOBVStrategy
from strategies.stochastic_reversal_strategy import StochasticReversalStrategy
from strategies.pbd_model_strategy import PBDModelStrategy
from strategies.ai_trader_strategy import AITraderStrategy
from strategies.nnfx_strategies import (NNFXBreakoutStrategy, NNFXReversionStrategy,
                                        NNFXTrendStrategy)
from strategies.trend_surfer_strategy import TrendSurferStrategy
from strategies.mr_zscore_strategy import ZScoreReversionStrategy
from strategies.mr_keltner_fade_strategy import KeltnerFadeStrategy
from strategies.custom_strategy import CustomStrategy
from strategies.base_strategy import BaseStrategy

VARIANT_KIND = "variant"


class StrategyRegistry:
    def __init__(self):
        self._strategies: Dict[str, BaseStrategy] = {}
        self._custom_ids = set()
        self._variant_ids = set()
        self._register_defaults()

    def _register_defaults(self):
        self.register(ScalpingStrategy())
        self.register(EMAPullbackScalpingStrategy())
        self.register(RSIOnlyStrategy())
        self.register(BollingerReversionStrategy())
        self.register(ICTLiquiditySweepStrategy())
        self.register(MACDRSIStrategy())
        self.register(BollingerSqueezeStrategy())
        self.register(VWAPReversionStrategy())
        self.register(HorstVWAPOBVStrategy())
        self.register(StochasticReversalStrategy())
        self.register(PBDModelStrategy())
        self.register(AITraderStrategy())
        self.register(NNFXTrendStrategy())
        self.register(NNFXReversionStrategy())
        self.register(NNFXBreakoutStrategy())
        self.register(TrendSurferStrategy())
        self.register(ZScoreReversionStrategy())
        self.register(KeltnerFadeStrategy())

    def register(self, strategy: BaseStrategy):
        self._strategies[strategy.STRATEGY_ID] = strategy

    def get(self, strategy_id: str) -> Optional[BaseStrategy]:
        return self._strategies.get(strategy_id)

    def list_all(self) -> List[Dict]:
        return [s.get_metadata() for s in self._strategies.values()]

    def list_ids(self) -> List[str]:
        return list(self._strategies.keys())

    def get_default(self) -> BaseStrategy:
        return self._strategies["scalping_4_rules"]

    # ---- custom strategies ----
    def load_custom(self, definitions: List[Dict]):
        """Custom-Strategien UND Built-in-Varianten laden (Varianten tragen
        ``kind == 'variant'`` – so reicht EIN Payload-Feld für den lokalen Worker)."""
        for cid in list(self._custom_ids):
            self._strategies.pop(cid, None)
        self._custom_ids.clear()
        for vid in list(self._variant_ids):
            self._strategies.pop(vid, None)
        self._variant_ids.clear()
        for d in definitions:
            if d.get("kind") == VARIANT_KIND:
                self.upsert_variant(d)
                continue
            strat = CustomStrategy(d)
            self.register(strat)
            self._custom_ids.add(strat.STRATEGY_ID)

    def upsert_custom(self, definition: Dict):
        strat = CustomStrategy(definition)
        self.register(strat)
        self._custom_ids.add(strat.STRATEGY_ID)

    def list_custom_definitions(self) -> List[Dict]:
        """Definitionen aller Custom-Strategien + Built-in-Varianten (z.B. für
        den lokalen Worker, der daraus per load_custom dieselbe Registry baut)."""
        out = []
        for cid in self._custom_ids:
            s = self._strategies.get(cid)
            if s is not None and getattr(s, "definition", None):
                out.append(s.definition)
        out.extend(self.list_variant_definitions())
        return out

    def remove_custom(self, strategy_id: str):
        if strategy_id in self._custom_ids:
            self._strategies.pop(strategy_id, None)
            self._custom_ids.discard(strategy_id)

    # ---- Built-in-Varianten (Kopie einer Code-Strategie mit eigener ID) ----
    def is_variant(self, strategy_id: str) -> bool:
        return strategy_id in self._variant_ids

    def upsert_variant(self, definition: Dict) -> Optional[BaseStrategy]:
        """Kopie einer Built-in-Strategie unter neuer ID registrieren. Die Kopie
        teilt den Code (Klasse) mit dem Original, hat aber eigene Parameter,
        Timeframe, Trade-/Backtest-Einstellungen (alles per STRATEGY_ID gekeyt)."""
        base = self._strategies.get(definition.get("base_id"))
        if base is None or getattr(base, "IS_CUSTOM", False) or self.is_variant(base.STRATEGY_ID):
            return None
        strat = type(base)()
        strat.STRATEGY_ID = definition["id"]
        strat.STRATEGY_NAME = definition.get("name") or f"{base.STRATEGY_NAME} (Kopie)"
        strat.STRATEGY_DESCRIPTION = definition.get("description") or base.STRATEGY_DESCRIPTION
        strat.STRATEGY_TIMEFRAME = definition.get("timeframe") or base.STRATEGY_TIMEFRAME
        strat.IS_VARIANT = True
        strat.BASE_STRATEGY_ID = base.STRATEGY_ID
        strat.variant_definition = {"kind": VARIANT_KIND, "id": strat.STRATEGY_ID,
                                    "base_id": base.STRATEGY_ID, "name": strat.STRATEGY_NAME,
                                    "description": strat.STRATEGY_DESCRIPTION,
                                    "timeframe": strat.STRATEGY_TIMEFRAME}
        self.register(strat)
        self._variant_ids.add(strat.STRATEGY_ID)
        return strat

    def remove_variant(self, strategy_id: str):
        if strategy_id in self._variant_ids:
            self._strategies.pop(strategy_id, None)
            self._variant_ids.discard(strategy_id)

    def list_variant_definitions(self) -> List[Dict]:
        return [dict(self._strategies[v].variant_definition)
                for v in self._variant_ids if v in self._strategies]

    # ---- Anzeigenamen (Umbenennen, auch Built-ins) ----
    def set_name(self, strategy_id: str, name: str) -> bool:
        strat = self._strategies.get(strategy_id)
        if strat is None or not str(name or "").strip():
            return False
        strat.STRATEGY_NAME = str(name).strip()[:80]
        if getattr(strat, "IS_CUSTOM", False):
            strat.definition["name"] = strat.STRATEGY_NAME
        elif getattr(strat, "IS_VARIANT", False):
            strat.variant_definition["name"] = strat.STRATEGY_NAME
        return True

    def apply_name_overrides(self, names: Dict[str, str]):
        """Gespeicherte Umbenennungen von Built-in-Strategien anwenden (Boot)."""
        for sid, name in (names or {}).items():
            if sid in self._strategies and sid not in self._custom_ids \
                    and sid not in self._variant_ids:
                self.set_name(sid, name)


registry = StrategyRegistry()
