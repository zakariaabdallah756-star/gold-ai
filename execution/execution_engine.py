from execution.order_executor import OrderExecutor


class ExecutionEngine:

    def __init__(self):
        self.executor = OrderExecutor()

    def execute_order(self, order):
        return self.executor.execute(order)