from paper.paper_account import PaperAccount


class AccountManager:

    def update_balance(self, account: PaperAccount, profit_loss: float):
        account.balance += profit_loss
        account.equity = account.balance
        account.free_margin = account.balance - account.margin

        return account