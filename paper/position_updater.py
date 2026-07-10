from paper.position_manager import PositionManager
from paper.profit_loss import ProfitLossCalculator
from paper.account_manager import AccountManager
from paper.paper_account import PaperAccount
from paper.position_closer import PositionCloser

class PositionUpdater:

    def __init__(self):
        self.pnl = ProfitLossCalculator()
        self.account_manager = AccountManager()
        self.closer = PositionCloser()
    def update(
    self,
    manager: PositionManager,
    account: PaperAccount,
    current_price: float,
        ):
        for position in manager.get_open_positions():

            profit_loss = self.pnl.calculate(position, current_price)

            self.account_manager.update_balance(account, profit_loss)

            if self.closer.should_close(position, current_price):
                manager.close_position(position)

        return account