from datetime import datetime


class DataProvider:
    def get_server_time(self):
        return datetime.now()