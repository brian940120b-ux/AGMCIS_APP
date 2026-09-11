-- Phase 0.5 guardrails
--
-- 1. 同一個 symbol 同時只能有一筆 OPEN 倉位。
--    原本只靠 has_open_trade() 這個 read-then-write 檢查,兩個 process 同時檢查會開出兩張單。
--    改由資料庫的 partial unique index 保證,應用層無法繞過。
-- 2. pnl_basis 標記損益的計算基準。
--    既有歷史資料是在「已實現不乘槓桿」的錯誤基準下寫入的,一律保留原值並標記為 LEGACY_UNLEVERAGED,
--    不回頭改寫任何歷史數字。修正後的新資料標記為 LEVERAGED。

ALTER TABLE trades ADD COLUMN IF NOT EXISTS pnl_basis VARCHAR(24);

UPDATE trades
   SET pnl_basis = 'LEGACY_UNLEVERAGED'
 WHERE pnl_basis IS NULL
   AND status = 'CLOSED';

CREATE UNIQUE INDEX IF NOT EXISTS ux_trades_open_symbol
    ON trades (symbol)
 WHERE status = 'OPEN';
