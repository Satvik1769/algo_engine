"""Order state machine. Every order moves through the same legal transitions; terminal states are final.

    CREATED  -> ACCEPTED | REJECTED          (risk decision)
    ACCEPTED -> FILLED | REJECTED | EXPIRED  (REJECTED here = refused at fill time, e.g. cash gap at the open)
    FILLED, REJECTED, EXPIRED: terminal
"""
from __future__ import annotations

from algo_engine.engine.errors import ExecutionError
from algo_engine.engine.events import OrderStatus
from algo_engine.engine.ports import Order

_LEGAL: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.ACCEPTED, OrderStatus.REJECTED}),
    OrderStatus.ACCEPTED: frozenset({OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}


class OrderBook:
    def __init__(self) -> None:
        self._status: dict[int, OrderStatus] = {}

    def register(self, order: Order) -> None:
        if order.order_id in self._status:
            raise ExecutionError(f"Duplicate order id {order.order_id}")
        self._status[order.order_id] = OrderStatus.CREATED

    def status(self, order_id: int) -> OrderStatus:
        try:
            return self._status[order_id]
        except KeyError:
            raise ExecutionError(f"Unknown order id {order_id}") from None

    def transition(self, order_id: int, new: OrderStatus) -> None:
        current = self.status(order_id)
        if new not in _LEGAL[current]:
            raise ExecutionError(f"Order {order_id}: illegal transition {current.value} -> {new.value}")
        self._status[order_id] = new

    def statuses(self) -> dict[int, OrderStatus]:
        return dict(self._status)
