from data.data_engine import DataEngine


def test_data_engine_update():
    engine = DataEngine()

    candle = engine.update()

    assert candle is not None
    assert engine.repository.last() == candle