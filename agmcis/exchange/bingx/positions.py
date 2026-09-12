"""
BingX 部位(Master Prompt 第八節的 positions.py)。

這一層只回傳交易所說的部位,**不與本地紀錄比對** ——
那是對帳的事(見 reconciler.py 指向的模組)。

把兩件事混在一起的話,「交易所有一個我們不知道的部位」
會變成一個查詢結果的欄位,而不是一個需要人處理的差異。
"""


class PositionsMixin:
    """BingXAdapter 的一部分。需要 client 層的 _call。"""

    def get_positions(self, symbols=None):
        """
        交易所上的部位。空清單代表**確定沒有部位**,
        而查詢失敗會拋例外 —— 兩者不能混,因為
        「沒有部位」會讓對帳認為本地的紀錄都是多餘的。
        """
        return self._call("fetch_positions", symbols) or []
