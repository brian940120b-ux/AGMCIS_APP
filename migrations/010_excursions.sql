-- MFE / MAE(Master Prompt 第三十節 Agent 10 Performance Analyst)。
--
-- MFE(Maximum Favourable Excursion)= 這筆交易最多曾經賺到多少
-- MAE(Maximum Adverse Excursion) = 這筆交易最多曾經虧到多少
--
-- 這兩個數字回答的問題,是總損益答不出來的:
--
--   「停損是不是設得太緊?」        看虧損單的 MAE 分布。如果那些
--                                  單的 MAE 只比停損多一點點,代表
--                                  停損再放寬一點它們就不會被掃到。
--   「停利是不是設得太早?」        看獲利單的 MFE。如果 MFE 遠大於
--                                  實際獲利,代表利潤被吐回去了。
--
-- 第五十六節要求出場參數由回測驗證,而這兩欄讓**實盤**也能回答
-- 同一個問題 —— 回測用的是歷史,這裡用的是真的發生過的事。
--
-- ⚠️ 只有在部位開著的期間、由 position_monitor 每一輪更新。
--    Migration 之前開的倉沒有這些數字,而 NULL 代表「沒有量測」
--    不是「零」—— 統計要把它們排除,不能當成 0。

ALTER TABLE trades ADD COLUMN IF NOT EXISTS max_favourable_pct NUMERIC(12, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS max_adverse_pct    NUMERIC(12, 6);

COMMENT ON COLUMN trades.max_favourable_pct IS
    '持倉期間最大有利偏移(%,已含槓桿)。NULL = 沒有量測過。';
COMMENT ON COLUMN trades.max_adverse_pct IS
    '持倉期間最大不利偏移(%,已含槓桿,負值)。NULL = 沒有量測過。';
