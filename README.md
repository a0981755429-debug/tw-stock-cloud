# 我的台股（雲端免安裝版）

不用在電腦上安裝任何程式，也不用開機。GitHub 的免費雲端主機會自動：

- **盤中**（週一到週五 09:00–13:30）每 10 分鐘檢查一次即時股價，到價就傳 **Telegram** 通知你
- **盤後**（每天 17:30 左右）抓日 K 線、算買賣訊號、抓財報，更新網頁，並推播今日訊號摘要
- 產生一個**手機網頁**，隨時打開看自選股、訊號分析、走勢圖、財報

> ⚠️ 訊號是規則式的參考（均線、MACD、RSI、KD、成交量），**不是投資建議**。

---

## 📱 手機設定（只要做一次，約 10 分鐘）

### 1. 打開網頁

網址：`https://<你的 GitHub 帳號>.github.io/tw-stock-cloud/`

用手機瀏覽器打開後，選「分享 › 加入主畫面」（iPhone Safari）或「⋮ › 加到主畫面」（Android Chrome），就會像 App 一樣出現在桌面。

### 2. 設定 Telegram 通知

1. 手機安裝 Telegram，搜尋 **@BotFather**，傳 `/newbot`，照指示幫機器人取名字。
2. BotFather 會回你一串像 `123456789:ABCdef...` 的文字，這就是 **bot token**，先複製起來。
3. 搜尋你剛建立的機器人，按「開始」，隨便傳一句話給它。
4. 用瀏覽器打開下面這個網址（把 `<bot token>` 換成你的），找 `"chat":{"id":` 後面的數字，那就是 **chat id**：
   `https://api.telegram.org/bot<bot token>/getUpdates`
5. 到這個專案的 GitHub 頁面 › **Settings › Secrets and variables › Actions › New repository secret**，新增兩個：
   - 名稱 `TELEGRAM_BOT_TOKEN`，值填 bot token
   - 名稱 `TELEGRAM_CHAT_ID`，值填 chat id

   Secrets 是加密的，別人看不到，連你自己存了以後也看不到內容。

### 3. 設定持股（加密，不公開）

持股的股數和成本放在 GitHub Secrets，**別人看不到**。網頁上只顯示報酬率和占比的百分比，金額只會用 Telegram 私訊給你。

到 **Settings › Secrets and variables › Actions › New repository secret**，名稱填 `HOLDINGS`，內容照這個格式：

```yaml
holdings:
  - id: "2317"
    shares: 1000         # 股數
    cost: 200000         # 總成本（含手續費，券商 App 的「付出成本」）
    stop_loss: 180       # 選填：跌到這個價格用 Telegram 通知
    take_profit: 300     # 選填：漲到這個價格通知
  - id: "00878"
    shares: 2000
    cost: 60000          # 高股息 ETF 建議不設停損，改在 watchlist.yml 設 yield_above
```

買賣後到同一個地方按「Update」改內容（Secrets 存了以後看不到舊內容，建議自己另外留一份）。

### 4.（選用）FinMind 金鑰

財報資料來自 [FinMind](https://finmindtrade.com/)。不設也能用，但如果自選股很多（超過 30 檔），建議免費註冊拿 Token，加一個 secret：`FINMIND_TOKEN`。

---

## ✏️ 修改自選股和價格提醒

直接在 GitHub 編輯 [`watchlist.yml`](watchlist.yml)（手機也可以：打開檔案 › 右上角鉛筆 ✏️ › 修改 › Commit changes）。網頁底部也有「編輯自選股與提醒」的連結。

```yaml
stocks:
  - id: "2330"          # 股票代號，記得加引號
    note: 長期持有       # 備註（可省略）
    alert:
      above: 1200       # 漲到 1200 以上通知
      below: 1000       # 跌到 1000 以下通知
      change_pct: 5     # 單日漲跌超過 5% 通知
  - id: "0050"          # 只追蹤、不設提醒
```

存檔後大約 2 分鐘，網頁會自動重算。同一檔、同一種提醒，每天最多通知一次。

**ETF（代號 00 開頭）** 不打技術分數，改顯示殖利率、配息、除息日和折溢價，可以設 `yield_above`（殖利率高於幾 % 通知加碼），下次除息日 `next_ex`、每股配息 `next_dps` 在投信公告後手動填。

**均線和訊號用還原權值價格**，除息當天不會被誤判成大跌。

---

## ❓ 常見問題

**通知好像晚了幾分鐘？**
GitHub 的免費排程有時會延遲 5–30 分鐘，尖峰時間偶爾會跳過一次。這是免費方案的限制，適合「到價提醒」，不適合當沖搶秒。

**想手動更新一次？**
GitHub 專案頁 › **Actions** › 左邊選「盤後更新」或「盤中提醒」› **Run workflow**。手動跑盤中提醒會不管時段立刻檢查一次（可用來測 Telegram 有沒有設好）。

**網頁顯示「還沒有資料」？**
到 Actions 手動跑一次「盤後更新」，等綠色勾勾出現後重新整理網頁。

**連續 60 天沒動，排程停了？**
GitHub 會暫停 60 天沒有任何更新的專案排程。這個專案每天都會自動存檔資料，正常不會發生；如果真的停了，到 Actions 頁面按「Enable workflow」即可。

## 資料來源

| 資料 | 來源 |
|------|------|
| 盤中即時價 | 臺灣證券交易所 基本市況報導網站（mis.twse.com.tw） |
| 日 K 線 | Yahoo Finance（備援：FinMind） |
| 股票名稱、月營收、EPS、本益比 | FinMind 開放資料 |

## 開發者

```bash
pip install -r requirements.txt
python -m pytest -q
python -m stockapp.daily --no-push          # 盤後更新（不推播）
python -m stockapp.intraday --force --no-push
```
