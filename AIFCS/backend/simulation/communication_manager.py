"""One inbox, dispatched by message type (PHASE 14).

Until now the datalink was the only thing that read a unit's inbox, and it
drained the whole thing while handling only position reports. Anything else —
a task order from a commander — was consumed and thrown away.

That was harmless while position reports were the only traffic. With a
commander issuing orders it is a silent message loss, and silent is the worst
kind: the order leaves, the statistics say it was delivered, and the unit never
hears it.

So there is now exactly one place that drains an inbox, and it routes by type:

    POSITION_REPORT  →  the datalink picture
    TASK_ORDER       →  the addressee's order queue
    STATUS           →  the addressee's status queue

Orders travel over the same simulated transport as everything else, so they are
subject to the same latency, jitter, loss and blackout. A commander whose orders
always arrive instantly would be a commander in a different simulation from the
units it commands.

This remains a simulation transport only. It performs no real networking and
implements nothing that scans, manipulates or interferes with anything.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from core.logging_config import get_logger
from simulation.communications import CommunicationModel, Message, MessageType
from simulation.datalink import DatalinkService

log = get_logger("communication_manager")


class CommunicationManager:
    """Owns the comms model and the datalink, and routes what arrives."""

    def __init__(self, comms: CommunicationModel, datalink: DatalinkService) -> None:
        self.comms = comms
        self.datalink = datalink
        self._orders: dict[str, list[Message]] = defaultdict(list)
        self._status: dict[str, list[Message]] = defaultdict(list)
        self._delivered: dict[str, int] = defaultdict(int)
        self._unrouted = 0

    def reset(self) -> None:
        self._orders.clear()
        self._status.clear()
        self._delivered.clear()
        self._unrouted = 0

    # ------------------------------------------------------------- delivery

    def deliver(self, entity_id: str) -> None:
        """Drain this unit's inbox once and route everything in it.

        Called exactly once per unit per decision cycle. Draining twice would
        mean whichever consumer ran second saw nothing.
        """
        for message in self.comms.receive(entity_id):
            self._delivered[message.type.value] += 1
            if message.type is MessageType.POSITION_REPORT:
                self.datalink.accept(entity_id, message)
            elif message.type is MessageType.TASK_ORDER:
                self._orders[entity_id].append(message)
            elif message.type is MessageType.STATUS:
                self._status[entity_id].append(message)
            else:
                # A new message type added without a route would otherwise be
                # dropped as quietly as task orders once were.
                self._unrouted += 1
                log.warning(
                    "message type has no route",
                    extra={"event": "COMMS_UNROUTED", "type": message.type.value},
                )

    def take_orders(self, entity_id: str) -> list[Message]:
        """Hand over the orders waiting for a unit, and clear the queue."""
        orders = self._orders.pop(entity_id, [])
        return orders

    def peek_orders(self, entity_id: str) -> list[Message]:
        return list(self._orders.get(entity_id, []))

    def take_status(self, entity_id: str) -> list[Message]:
        return self._status.pop(entity_id, [])

    # ---------------------------------------------------------------- status

    def status(self) -> dict[str, Any]:
        return {
            "delivered_by_type": dict(self._delivered),
            "orders_waiting": {k: len(v) for k, v in self._orders.items() if v},
            "unrouted": self._unrouted,
        }
