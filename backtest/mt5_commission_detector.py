from collections import defaultdict
from datetime import datetime, timedelta

import MetaTrader5 as mt5


class MT5CommissionDetector:

    def __init__(
        self,
        symbol: str = "XAUUSD",
        lookback_days: int = 365,
    ):
        self.symbol = symbol
        self.lookback_days = lookback_days

    def calculate_round_turn_per_lot(
        self,
    ) -> float | None:
        date_to = datetime.now()

        date_from = date_to - timedelta(
            days=self.lookback_days
        )

        deals = mt5.history_deals_get(
            date_from,
            date_to,
            group=f"*{self.symbol}*",
        )

        if deals is None:
            raise RuntimeError(
                "Impossibile leggere lo storico MT5: "
                f"{mt5.last_error()}"
            )

        positions = defaultdict(list)

        for deal in deals:
            deal_symbol = str(
                getattr(deal, "symbol", "")
            )

            position_id = int(
                getattr(deal, "position_id", 0)
            )

            volume = float(
                getattr(deal, "volume", 0.0)
            )

            if deal_symbol != self.symbol:
                continue

            if position_id <= 0 or volume <= 0:
                continue

            positions[position_id].append(deal)

        entry_in = getattr(
            mt5,
            "DEAL_ENTRY_IN",
            0,
        )

        exit_entries = {
            getattr(mt5, "DEAL_ENTRY_OUT", 1),
            getattr(mt5, "DEAL_ENTRY_INOUT", 2),
            getattr(mt5, "DEAL_ENTRY_OUT_BY", 3),
        }

        total_cost = 0.0
        total_open_volume = 0.0

        for position_deals in positions.values():
            opening_deals = [
                deal
                for deal in position_deals
                if int(getattr(deal, "entry", -1))
                == entry_in
            ]

            closing_deals = [
                deal
                for deal in position_deals
                if int(getattr(deal, "entry", -1))
                in exit_entries
            ]

            if not opening_deals or not closing_deals:
                continue

            opened_volume = sum(
                float(getattr(deal, "volume", 0.0))
                for deal in opening_deals
            )

            if opened_volume <= 0:
                continue

            position_cost = sum(
                abs(
                    float(
                        getattr(
                            deal,
                            "commission",
                            0.0,
                        )
                    )
                )
                + abs(
                    float(
                        getattr(
                            deal,
                            "fee",
                            0.0,
                        )
                    )
                )
                for deal in position_deals
            )

            total_cost += position_cost
            total_open_volume += opened_volume

        if total_open_volume <= 0:
            return None

        commission_per_lot = (
            total_cost / total_open_volume
        )

        return round(
            commission_per_lot,
            4,
        )