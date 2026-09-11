-- Phase 10:模擬盤成本
--
-- Phase 10 之前模擬損益完全不含成本:沒有手續費、沒有滑點、沒有點差、
-- 沒有資金費用、沒有強平。那種數字是理想化的上界,不是可達成的績效。
--
-- 既有的歷史資料**一律保留原值**並標記為 LEGACY_NO_COSTS。
-- 不回頭改寫任何歷史數字 —— 與 Phase 0.5 處理 pnl_basis 的做法一致。
-- 混在一起平均會讓「加了成本之後績效變差多少」這個問題永遠問不出答案。

ALTER TABLE trades ADD COLUMN IF NOT EXISTS requested_entry_price NUMERIC(18, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS requested_exit_price  NUMERIC(18, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_fee             NUMERIC(18, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_fee              NUMERIC(18, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS funding_usdt          NUMERIC(18, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS gross_pnl_usdt        NUMERIC(18, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS liquidation_price     NUMERIC(18, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS cost_basis            VARCHAR(24);

UPDATE trades
   SET cost_basis = 'LEGACY_NO_COSTS'
 WHERE cost_basis IS NULL;
