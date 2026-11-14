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
    since: 2024-03-15    # 選填：第一次買進日，用來算「含息總報酬」
  - id: "00878"
    shares: 2000
    cost: 60000          # 高股息 ETF 建議不設停損，改在 watchlist.yml 設 yield_above
    since: 2023-06-01
    # dividends: 8500    # 選填：已領配息總額（元），有填就直接用，不用 since 推算
```

**含息總報酬**：填了 `since`，系統會用 FinMind 的除息紀錄，算出那天之後每次配息 × 你現在的股數（期間有加碼的話會稍微高估，想精準就改填 `dividends`）。網頁只顯示含息報酬 %，領了多少錢、預估一年領多少只走 Telegram。除息日 7 天內，Telegram 會提醒「哪天除息、你預計領多少」。

買賣後到同一個地方按「Update」改內容（Secrets 存了以後看不到舊內容，建議自己另外留一份）。

### 3-1.（選用）永豐帳戶自動同步庫存

永豐金證券的客戶可以讓系統每天盤後自動讀庫存，買賣後就不用手動改 `HOLDINGS` 的股數。只會「查庫存」，不會下單，也不用裝憑證。

1. 到永豐「理財網 › API 管理」（https://www.sinotrade.com.tw/newweb/PythonAPIKey/）簽署 API 使用條款，完成官方要求的模擬測試。
2. 新增 API Key：權限**只勾「帳務」和「正式環境」，不要勾「交易」**（這把鑰匙就算外洩也不能下單）。IP 限制不要設，GitHub 主機的 IP 每次都不同。Secret Key 只會出現一次，記得先抄下來。
3. 到 Settings › Secrets › Actions 新增兩個：`SJ_API_KEY`、`SJ_SECRET_KEY`。
4. 不想列入的股票（例如借名），在 `HOLDINGS` 最上面加一行：`exclude: ["代號"]`。

同步規則：股數以永豐為準；股數沒變就沿用你填的成本（含手續費較準），有變就改用永豐的均價。停損停利、`since` 一樣照 `HOLDINGS`。`HOLDINGS` 有、永豐沒有的會當成已賣出，Telegram 會告訴你哪幾檔變了。讀取失敗時自動改用 `HOLDINGS`。

### 4.（選用）FinMind 金鑰

財報、配息、籌碼資料來自 [FinMind](https://finmindtrade.com/)。每檔個股每天大約用 10 次（ETF 約 2 次），免費版不註冊每小時 300 次、註冊後 600 次。自選股超過 20 檔建議免費註冊拿 Token，加一個 secret：`FINMIND_TOKEN`。

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

**ETF（代號 00 開頭）** 不打技術分數，改顯示殖利率、配息、除息日和折溢價，可以設 `yield_above`（殖利率高於幾 % 通知加碼），下次除息日和每股配息會從 FinMind 股利政策表**自動抓**（投信公告後隔天就有）；還沒公告時才會顯示你在 `next_ex` / `next_dps` 手填的。

**個股另外會看籌碼和財務體質**（都是 FinMind 免費資料）：
- 三大法人：外資、投信每天買賣超幾張，連買或連賣剛好滿 3、5、10 天時推播
- 融資融券：融資餘額 20 日增加超過 15% 視為過熱（剛變過熱那天推播一次），也顯示融資使用率、券資比
- 外資持股比例和近一、三個月的變化
- 現金流量表算自由現金流（營業現金流 − 資本支出），資產負債表算負債比、流動比

**均線和訊號用還原權值價格**，除息當天不會被誤判成大跌。

---

## 📓 交易紀錄（`docs/journal/`）

網址：`https://<你的 GitHub 帳號>.github.io/tw-stock-cloud/journal/`（首頁底部也有連結）。重點不是選股，是**下單前擋住自己、交易後回頭檢討**：

- **記一筆**：每筆都要寫「為什麼買」和「什麼情況代表我錯了」。可以選「現在要買」「先記想法」「補記既有持股」。
- **該買多少**：個股用「每筆最多賠總資產 1%」算股數：(總資產 × 1%) ÷ (買價 − 停損價)，再套單一個股上限，取比較小的那個。存股 ETF 改用目標配置 %，算出還差幾股。
- **下單前檢查**：沒寫理由、沒寫出場條件、個股沒設停損、用質押的錢但維持率低於安全線、個股停損出場後還在冷卻期，以上任一項會判「不要下」。臨時起意、這筆風險超過上限、單一個股太集中、所有持股總風險超過上限、個股連虧，會判「再想想」。沒過也可以記，但會被標記。
- **檢討**：每筆紀錄到了檢討日會出現在總覽。結案後寫心得。「檢討」分頁依類型、有沒有過關卡、事先計畫還是臨時起意，分開統計勝率、平均報酬和賺賠比。
- **規則**：都在「設定」分頁，可以自己改（每筆風險 %、單一個股上限、總風險上限、冷卻天數、質押維持率安全線、手續費折數）。

資料用你設的密碼加密後存在 `docs/journal/vault.json`，網路上只看得到亂碼。同步用的 GitHub 金鑰跟財務規劃台共用。
**Telegram 檢討提醒**（選用）：到 Settings › Secrets › Actions 新增 `JOURNAL_PASS`，值填交易紀錄的密碼。之後每天盤後的推播會附上「今天該檢討的」和「跌破你寫的停損的」。

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
| 除息日 / 配息金額、三大法人、融資融券、外資持股、現金流量表、資產負債表 | FinMind 開放資料（免費 dataset） |

## 開發者

```bash
pip install -r requirements.txt
python -m pytest -q
python -m stockapp.daily --no-push          # 盤後更新（不推播）
python -m stockapp.intraday --force --no-push
```

## 💰 財務規劃台（`docs/finance/`）

網址：`https://<你的 GitHub 帳號>.github.io/tw-stock-cloud/finance/`，股票頁底部也有連結。

- 資金分配（每月繳多少、存多少、買多少股票）、目標進度、貸款、保單、收支、資產、實行步驟都在這裡。
- 資料放在 `docs/finance/vault.json`，**整份加密**（PBKDF2＋AES-GCM），要輸入密碼才會在瀏覽器裡解開；repo 裡沒有明文。
- 股票現價直接讀股票頁的 `docs/data.json`，每天盤後自動更新。
- 在網頁上改完按「儲存」：設定分頁填一把只能寫這個 repo 的 Fine-grained token（Contents: Read and write），就會直接寫回 GitHub；沒有設定的話會下載 `vault.json`，自己上傳也可以。
- 本機加解密：`FIN_PASS=密碼 node tools/finance_vault.mjs encrypt|decrypt 來源 目的`
