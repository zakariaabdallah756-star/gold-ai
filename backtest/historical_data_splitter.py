class HistoricalDataSplitter:

    def split(
        self,
        candles: list,
        training_ratio: float = 0.70,
    ) -> tuple[list, list]:
        if not candles:
            raise ValueError(
                "La lista delle candele è vuota."
            )

        if not 0 < training_ratio < 1:
            raise ValueError(
                "training_ratio deve essere "
                "compreso tra 0 e 1."
            )

        split_index = int(
            len(candles) * training_ratio
        )

        if split_index <= 0:
            raise ValueError(
                "Periodo di sviluppo insufficiente."
            )

        if split_index >= len(candles):
            raise ValueError(
                "Periodo di validazione insufficiente."
            )

        training_candles = candles[:split_index]
        validation_candles = candles[split_index:]

        return (
            training_candles,
            validation_candles,
        )