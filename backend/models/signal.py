from pydantic import BaseModel
from typing import Dict, Optional

class Signal(BaseModel):
    """Trading signal model"""
    symbol: str
    type: str  # LONG or SHORT
    timestamp: str
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_full: float
    crv: float
    rsi: float
    ema_9: float
    ema_50: float
    rules_met: Dict[str, bool]
    status: str = "active"  # active, closed, expired
    result: Optional[str] = None  # win, loss, breakeven
    
    class Config:
        json_schema_extra = {
            "example": {
                "symbol": "BTCUSDT",
                "type": "LONG",
                "timestamp": "2026-01-08T10:30:00Z",
                "entry_price": 45000.50,
                "stop_loss": 44950.00,
                "take_profit_1": 45050.50,
                "take_profit_full": 45100.50,
                "crv": 2.0,
                "rsi": 30.5,
                "ema_9": 44980.00,
                "ema_50": 44800.00,
                "rules_met": {
                    "rule1_ema50": True,
                    "rule2_rsi": True,
                    "rule3_ema9_trigger": True,
                    "rule4_time_window": True
                },
                "status": "active"
            }
        }

