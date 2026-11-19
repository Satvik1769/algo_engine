"""Order types and trade records."""

from dataclasses import dataclass
from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class Order:
    symbol: str
    side: OrderSide | str
    quantity: int
    order_type: OrderType | str = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    status: OrderStatus = OrderStatus.PENDING

    def __post_init__(self) -> None:
        self.side = self.side if isinstance(self.side, OrderSide) else OrderSide(str(self.side).upper())
        self.order_type = (
            self.order_type
            if isinstance(self.order_type, OrderType)
            else OrderType(str(self.order_type).upper())
        )
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


@dataclass(frozen=True)
class Trade:
    symbol: str
    side: OrderSide | str
    quantity: int
    price: float
    timestamp: object
    commission: float = 0.0
