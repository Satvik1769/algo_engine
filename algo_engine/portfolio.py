"""Portfolio and position management."""

from dataclasses import dataclass, field

from .orders import OrderSide, Trade


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    average_price: float = 0.0

    def update(self, side: OrderSide | str, quantity: int, price: float) -> None:
        side = side if isinstance(side, OrderSide) else OrderSide(str(side).upper())
        if side == OrderSide.BUY:
            new_quantity = self.quantity + quantity
            if new_quantity:
                self.average_price = (
                    self.average_price * self.quantity + price * quantity
                ) / new_quantity
            self.quantity = new_quantity
        else:
            if quantity > self.quantity:
                raise ValueError(f"Cannot sell {quantity} units of {self.symbol}")
            self.quantity -= quantity
            if self.quantity == 0:
                self.average_price = 0.0

    def market_value(self, price: float) -> float:
        return self.quantity * price

    def unrealized_pnl(self, price: float) -> float:
        return self.quantity * (price - self.average_price)


@dataclass
class Portfolio:
    initial_capital: float
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.initial_capital < 0:
            raise ValueError("initial_capital cannot be negative")
        self.cash = float(self.initial_capital)

    def execute_trade(self, trade: Trade) -> None:
        position = self.positions.setdefault(trade.symbol, Position(trade.symbol))
        position.update(trade.side, trade.quantity, trade.price)
        cost = trade.quantity * trade.price + trade.commission
        if trade.side == OrderSide.BUY or str(trade.side).upper() == "BUY":
            self.cash -= cost
        else:
            self.cash += trade.quantity * trade.price - trade.commission
        self.trades.append(trade)

    def value(self, prices: dict[str, float]) -> float:
        return self.cash + sum(
            position.market_value(prices.get(symbol, position.average_price))
            for symbol, position in self.positions.items()
        )
