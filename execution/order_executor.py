from execution.order import Order


class OrderExecutor:
    def execute(self, order: Order):
        print(f"Ordine eseguito: {order.signal} {order.symbol}")
        return True