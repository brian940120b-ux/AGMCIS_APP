"""
向下相容 shim。

實作已移到 agmcis/execution/exit_plan.py。

原本這裡是一套依 ROI 分級的百分比移動停損(ROI 20% 跟 2%、10% 跟 3%、
5% 跟 4%)。它有兩個問題:

  1. **只有百分比。** 第五十八節要求 ATR、百分比、結構型三種。
     百分比停損在低波動標的上跟得太鬆、在高波動標的上跟得太緊,
     因為它完全不知道這檔平常動多少。

  2. **它看 ROI,而 ROI 含槓桿。** 同樣的價格變動,5 倍槓桿的 ROI 是
     2 倍槓桿的 2.5 倍,所以同一檔標的會因為開倉時的槓桿不同而套用
     不同的跟蹤距離 —— 那跟市場沒有關係。

新的實作用 R 倍數(停損距離的倍數)當基準,而且分批停利、移到成本價、
移動停損、時間出場走同一條判斷鏈,一次只做一個動作。

新程式碼請直接用:
    from agmcis.execution.exit_plan import run_exit_plans
"""
from agmcis.execution.exit_plan import run_exit_plans


def apply_trailing_stop(*args, **kwargs):
    """舊名稱。行為已經不同了 —— 見模組說明。"""
    return run_exit_plans(*args, **kwargs)


__all__ = ["apply_trailing_stop", "run_exit_plans"]
