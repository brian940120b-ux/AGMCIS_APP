"""
Strategy Lab。

回測引擎(Phase 7)只回答「這組參數在這段歷史上表現如何」。
那個問題本身沒有用 —— 任何策略只要參數夠多,都能在一段固定歷史上做出漂亮的曲線。

Strategy Lab 回答的是另一個問題:**這個表現是真的優勢,還是過擬合?**

四種檢驗:

  1. In-Sample / Out-of-Sample 切分 —— 參數沒看過的那段資料上還行不行
  2. Walk Forward —— 滾動地「用過去調參、在未來驗證」,模擬真實使用方式
  3. Monte Carlo —— 交易順序重抽樣,看這條權益曲線有多少是運氣
  4. 過擬合偵測 + Strategy Health Score —— 把上面的結果變成一個能拒絕的判準

鐵律(Master Prompt 第二節):
  * 不得只報告最好的那組參數(cherry-picking)
  * 不得用 OOS 資料挑參數 —— 那等於沒有 OOS
  * 失敗的組合要保留在報告裡
"""
