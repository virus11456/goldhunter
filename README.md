# GoldHunter 淘金者

> AI × 量化策略 × 交易所 的自架交易平台。**你的策略決定方向，AI 負責判斷與微調。**

GoldHunter 把「你寫好的策略（含 TradingView Pine Script）」、「AI 模型的判斷分析能力」、「新聞 / 總經 / 合約數據」與「交易所 API」串在一起。
它不只是跟單 TradingView 訊號 —— 每一個進場訊號都會先交給 AI 結合市場情報審核，持倉中 AI 會持續盯盤，並定期幫你微調策略參數。

- 目前支援：**加密貨幣 USDT 永續合約**（Binance / OKX / Bybit / Hyperliquid）
- 規劃中：美股（Alpaca）、台股（永豐 Shioaji）——架構已預留，不需重寫

> ⚠️ **風險警語**：程式交易與槓桿合約具高度風險，可能損失全部本金。本專案僅供研究與學習，不構成投資建議。請務必先用「模擬交易」與「回測」充分驗證，實盤請從小資金開始。

---

## 目錄

- [功能總覽](#功能總覽)
- [安全設計（為什麼要重寫）](#安全設計為什麼要重寫)
- [快速開始](#快速開始)
- [使用教學](#使用教學)
  - [1. 設定交易所](#1-設定交易所)
  - [2. 設定 AI 模型](#2-設定-ai-模型)
  - [3. 設定資料來源（新聞 / 總經 / 合約數據）](#3-設定資料來源新聞--總經--合約數據)
  - [4. 建立策略](#4-建立策略)
  - [5. Pine Script 轉 Python](#5-pine-script-轉-python)
  - [6. 回測](#6-回測)
  - [7. 建立 Bot 與 AI 副駕駛](#7-建立-bot-與-ai-副駕駛)
  - [8. TradingView Webhook](#8-tradingview-webhook)
  - [9. 監控與紀錄](#9-監控與紀錄)
- [AI 副駕駛詳解](#ai-副駕駛詳解)
- [風控規則](#風控規則)
- [自訂 Python 策略 API](#自訂-python-策略-api)
- [系統架構](#系統架構)
- [設定參數](#設定參數)
- [開發與測試](#開發與測試)
- [常見問題](#常見問題)
- [路線圖](#路線圖)

---

## 功能總覽

| 頁面 | 功能 |
|---|---|
| **總覽** | 總權益、運行中 Bot、今日已實現損益、今日成交數；**緊急全部停止** |
| **設定** | 交易所帳戶、AI 模型、策略（含 **Pine Script → Python**）、Bot 與風控、資料來源 |
| **回測** | 策略 × 交易對 × 週期 × 日期區間；報酬 vs 買入持有、最大回撤、Sharpe、勝率、獲利因子；權益曲線、**K 線圖買賣點**、交易明細 |
| **監控** | 哪些 Bot 在跑哪個策略、目前持倉（一鍵平倉）、**每筆成交紀錄**、**AI 決策紀錄**（含 AI 理由）、**有 AI vs 無 AI 對照組**權益曲線、AI 參數微調紀錄 |

核心能力：

- **AI 副駕駛**（三種模式可單獨開關）
  - **A. 訊號審核**：策略進場訊號 → AI 結合新聞、總經、資金費率、恐懼貪婪指數判斷 → 放行 / 否決 / 調整倉位與止損
  - **B. 持倉管理**：持倉中定期檢查，只能做「降低風險」的動作（提前平倉、減倉、上移止損）
  - **C. 參數微調**：AI 在你允許的範圍內提出新參數，**樣本外回測**較佳才套用
- **重大經濟事件避險**：CPI、FOMC、非農等高影響數據公布前後自動暫停開倉
- **對照組**：同一策略「不經 AI」的訊號同步在模擬帳本執行，直接看出 AI 到底有沒有幫上忙
- **多家 AI**：Claude、OpenAI、DeepSeek、通義千問、本地 Ollama
- **策略三種來源**：內建策略、AI 策略、自訂 Python（可由 Pine Script 自動轉換）
- **TradingView Webhook**：TradingView 警報直接觸發下單（同樣經過 AI 與風控）
- **模擬交易**：預設開啟，不需要 API Key 就能用真實行情跑

---

## 安全設計（為什麼要重寫）

本專案的架構參考了 [nofx](https://github.com/NoFxAiOS/nofx)，但**所有程式碼皆為重新撰寫，沒有複製任何 nofx 原始碼**。檢視 nofx 時發現兩個會把資料或費用送給原作者的設計（屬於公開功能而非隱藏後門，但使用者未必知情）：

1. `telemetry/experience.go`：每筆交易把交易所、幣種、金額、槓桿送到 Google Analytics
2. Hyperliquid 下單預設帶 **builder fee**（固定地址 `0x891d…af0d`），每筆交易額外付費給該地址

GoldHunter 的做法：

- ❌ **沒有任何遙測 / 追蹤 / 分析程式**
- ❌ **沒有 builder fee、推薦碼或任何第三方抽成**
- ✅ 對外連線**只有**：你設定的交易所、你設定的 AI 供應商、你開啟的資料來源（可在「設定 → 資料來源」逐一關閉）
- ✅ 交易所與 AI 的 API Key 以 **AES-256-GCM** 加密存放在本機 SQLite；介面上只顯示末 4 碼，無法讀回
- ✅ 所有 API 需要 Bearer Token；TradingView Webhook 使用每個 Bot 各自的密語並以常數時間比對
- ✅ 自訂 / AI 轉換的 Python 策略需通過 **AST 白名單檢查**（禁止 import 系統模組、檔案 / 網路存取、eval / exec、私有屬性存取），並需你**人工審核啟用**才能上線
- ✅ AI 的輸出一律經過程式硬性約束與風控，AI 無法自行改變交易方向或放寬止損
- ✅ 前端所有資源本地打包，沒有外部 CDN、字型或追蹤碼
- ✅ Docker 以非 root 使用者執行，預設只綁定 127.0.0.1

> 建議：交易所 API Key 請**關閉提領權限**，並設定 IP 白名單。

---

## 快速開始

### 方式一：Docker（推薦）

```bash
git clone https://github.com/virus11456/goldhunter.git
cd goldhunter
docker compose up -d --build
# 取得登入用的 API Token
cat data/api_token
```

打開 <http://localhost:8000>，貼上 API Token 登入。

### 方式二：本機開發

需求：Python 3.11+、Node.js 20+

```bash
# 後端
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn goldhunter.main:app --reload --port 8000
# 首次啟動會在 backend/data/ 產生 api_token 與 secret.key

# 前端（另一個終端機）
cd web
npm install
npm run dev
# 打開 http://localhost:5173
```

> `data/secret.key` 是 API Key 的加密金鑰，**遺失就無法解密已存的金鑰**，請備份；也不要把 `data/` 上傳到任何地方。

---

## 使用教學

建議順序：**交易所（模擬）→ AI 模型 → 資料來源 → 策略 → 回測 → Bot（模擬）→ 觀察 → 實盤小資金**

### 1. 設定交易所

設定 → 交易所帳戶 → 新增

| 欄位 | 說明 |
|---|---|
| 模擬交易 | 預設開啟。使用該交易所的**真實公開行情** + 本機模擬成交，不需要 API Key |
| API Key / Secret | 實盤才需要。OKX 另需 Passphrase |
| Hyperliquid | API Key 填**錢包地址**，Secret 填 **API 錢包私鑰**（建議在 Hyperliquid 建立專用 API 錢包，不要用主錢包私鑰）|
| 測試網 | 使用交易所的 testnet |

按「測試連線」確認能讀到餘額。

### 2. 設定 AI 模型

設定 → AI 模型 → 新增

| 供應商 | 預設模型 | 備註 |
|---|---|---|
| Anthropic Claude | `claude-opus-5` | 使用官方 SDK 的結構化輸出（保證回傳合法 JSON），可設定 effort（low～max）|
| OpenAI | `gpt-5` | |
| DeepSeek | `deepseek-chat` | |
| 通義千問 | `qwen-max` | |
| Ollama（本地）| `llama3.1` | 不需 Key，Base URL 預設 `http://localhost:11434/v1` |

按「測試」確認連線。

### 3. 設定資料來源（新聞 / 總經 / 合約數據）

設定 → 資料來源。這些是 AI 副駕駛判斷時會看到的市場情報：

| 來源 | 內容 | 費用 |
|---|---|---|
| 合約數據 | 資金費率、未平倉量、多空比（直接取自交易所公開 API）| 免費 |
| 恐懼貪婪指數 | alternative.me Fear & Greed Index | 免費 |
| 新聞 | CoinDesk、Cointelegraph RSS（可自訂）；可選填 [CryptoPanic](https://cryptopanic.com/developers/api/) token | 免費 |
| 總經數據 | FRED：CPI（含年增率）、聯邦基金利率、10 年期公債殖利率、美元指數、失業率 | 免費，需申請 [FRED API Key](https://fred.stlouisfed.org/docs/api/api_key.html) |
| 經濟日曆 | 本週美國高影響事件（CPI、FOMC、非農、PCE…）| 免費（社群常用的非官方 JSON 來源，失敗會自動略過）|

按「預覽 AI 看到的情報」可以看到實際送給 AI 的內容。任何來源失敗都不會中斷交易，只是 AI 少看一項資料。

### 4. 建立策略

設定 → 策略 → 新增，類型：

| 類型 | 說明 |
|---|---|
| `ma_cross` 均線交叉 | EMA 快慢線交叉進出場，ATR 止損，可反手做空 |
| `rsi_reversion` RSI 均值回歸 | RSI 超賣做多、回到中線平倉 |
| `ai` AI 決策 | 由 AI 根據行情 + 你寫的「策略指示」（中文即可）直接決策 |
| `python` 自訂 Python | 自己寫或由 Pine Script 轉換，見 [策略 API](#自訂-python-策略-api) |
| `tradingview` TradingView 訊號 | 不在本機運算，由 TradingView Webhook 觸發 |

### 5. Pine Script 轉 Python

設定 → 策略 → Pine Script 轉 Python

1. 貼上 Pine Script（`strategy()` 或 `indicator()` 皆可）
2. 選擇 AI 模型 → 轉換（約 30 秒～1 分鐘）
3. 系統會自動做安全檢查；若不通過，把錯誤回饋給 AI 重試（最多 3 次）
4. 畫面左右對照原始 Pine 與轉換後 Python，並列出「未轉換項目」（例如 `request.security` 多週期、繪圖）
5. 轉換結果狀態為 **待審核**：請**檢視程式碼、跑回測**，確認和 TradingView 上的結果相近，再按「審核啟用」

對應規則：`x[1]` → `series[-2]`、`strategy.entry(long)` → `ctx.long(...)`、`strategy.close` → `ctx.close_position()`、`strategy.exit(stop/limit)` → `stop_loss / take_profit`、`input.*()` → `default_params`。

> AI 轉換不保證 100% 等價，回測比對是必要步驟。修改過程式碼的策略會自動回到「待審核」。

### 6. 回測

回測頁：選策略、交易所、交易對、週期、日期區間、初始資金、手續費、滑價（進階設定可調參數與風控）。

- **無未來函數**：第 i 根收盤計算訊號，第 i+1 根**開盤價**成交；止損 / 止盈用 K 棒高低價判斷，跳空以開盤價成交
- 回測與實盤共用同一套策略、風控與撮合邏輯
- 歷史資料透過交易所公開 API 下載（單次最多 20,000 根）
- AI 策略也能回測，但會產生 API 費用（有 `max_ai_calls` 上限）；**新聞 / 總經類 AI 判斷無法準確回測**（歷史新聞難以重建，且模型可能已知道後來的行情），請用模擬交易 + 對照組驗證

### 7. 建立 Bot 與 AI 副駕駛

設定 → Bots → 新增

- 交易所帳戶、策略、交易對（例如 `BTC` → `crypto:BTC/USDT:perp`，可多個）、K 線週期、輪詢秒數
- **AI 副駕駛**：勾選 A / B / C 模式、事件避險分鐘數、給 AI 的交易偏好說明
- **進階設定 → 風控**：見 [風控規則](#風控規則)

建立後到「監控」頁按「啟動」。

### 8. TradingView Webhook

每個 Bot 都有自己的 Webhook URL 與密語（在 Bot 設定中可複製、可重新產生）。

1. TradingView → 建立警報 → 通知 → 勾選 Webhook URL，填入：
   `https://你的網域/api/tradingview/webhook/{bot_id}`
2. 訊息（Message）填入：

```json
{
  "passphrase": "你的 Bot 密語",
  "action": "{{strategy.order.action}}",
  "market_position": "{{strategy.market_position}}",
  "size_pct": 10
}
```

| 欄位 | 說明 |
|---|---|
| `action` | `buy` / `sell` / `long` / `short` / `close` / `exit` / `flat` |
| `market_position` | `long` / `short` / `flat`（strategy 腳本建議帶，以它為準）|
| `size_pct` | 選填，佔權益 %（仍受風控上限限制）|
| `leverage` / `stop_loss` / `take_profit` | 選填 |
| `symbol` | 選填，Bot 有多個交易對時指定（如 `ETHUSDT`）|

注意：

- 只接受**已啟動**的 Bot；訊號一樣會經過**事件避險、AI 審核（若開啟）與風控**
- TradingView 只能打 80/443 埠，請用 HTTPS 反向代理（Caddy / Nginx / Cloudflare Tunnel）對外，不要直接暴露 8000 埠

### 9. 監控與紀錄

- **Bot 卡片**：策略、交易對、狀態（運行中 / 已停止 / 錯誤 / 熔斷）、最後執行時間、錯誤訊息
- **持倉**：方向、數量、均價、標記價、未實現損益、引擎管理的止損止盈、一鍵平倉
- **權益曲線**：有 AI 副駕駛時，同時畫出「有 AI」與「無 AI 對照組」
- **成交紀錄**：每一筆成交的時間、交易對、方向、數量、價格、手續費、已實現損益、訊號來源
- **AI 決策紀錄**：每次決策的來源、動作、是否通過、理由、AI 信心、原始訊號 vs 調整後、AI 原始回覆、token 用量
- **AI 參數微調**：歷次建議、新舊參數樣本外回測對照、一鍵套用

---

## AI 副駕駛詳解

```
  你的策略（規則 / Pine 轉換 / TradingView）
            │ 進場訊號（方向由你決定）
            ▼
  ┌─────────────────────┐
  │ 經濟事件避險（規則） │── CPI/FOMC 前後 → 不開倉
  └─────────────────────┘
            ▼
  ┌─────────────────────────────────────────────┐
  │ A. AI 訊號審核                               │
  │  輸入：K 線、指標、合約數據、恐懼貪婪、       │
  │        新聞標題、總經數據、事件日曆、         │
  │        策略近 10 筆績效、你的偏好說明         │
  │  輸出：放行 / 否決 / 調整（倉位倍數、止損止盈）│
  └─────────────────────────────────────────────┘
            ▼  程式硬性約束（不能反向、倍數上限、止損只能收緊）
  ┌──────────┐      ┌────────┐
  │   風控    │ ───▶ │  下單   │
  └──────────┘      └────────┘
            │ 持倉中
            ▼
  B. AI 持倉管理（每 N 分鐘）：維持 / 平倉 / 減倉 / 上移止損（只能降低風險）
  C. AI 參數微調（每 N 小時）：樣本內給 AI 看 → 提案 → 樣本外回測勝出才採用
```

**AI 能做與不能做的事**

| | 可以 | 不可以 |
|---|---|---|
| A 訊號審核 | 放行、否決、倉位 ×0.5～×1.5（可調）、收緊止損、設定止盈 | 改變方向、放寬止損、超過風控上限 |
| B 持倉管理 | 全部平倉、部分減倉、止損往有利方向移動 | 加倉、開新倉、止損往不利方向移動 |
| C 參數微調 | 在你勾選的參數與範圍內調整 | 動未勾選的參數、在樣本外表現較差時套用 |

AI 回傳格式錯誤、連線失敗或信心不足時，一律**保守處理**（不開倉 / 維持現狀）。

**費用估算**：AI 只在「新 K 棒出現進場訊號」時審核一次、持倉中每 N 分鐘檢查一次（預設 60 分鐘），不會每分鐘呼叫。以 15 分鐘 K 線、每天數個訊號計算，每天約數十次呼叫。

**對照組**：開啟 A 或 B 時，系統會用同樣的初始資金建立「不經 AI」的模擬帳本，完全依照策略原始訊號執行。監控頁的「AI 貢獻」= 實際權益 − 對照組權益。

---

## 風控規則

所有訊號（策略、AI、TradingView、手動）下單前都必須通過風控。**平倉永遠放行**，開倉才檢查。

| 參數 | 預設 | 說明 |
|---|---|---|
| `max_position_pct` | 20 | 單筆倉位上限（佔權益 %，未含槓桿）|
| `max_total_exposure_pct` | 100 | 總曝險上限（名目價值佔權益 %，含槓桿）|
| `max_leverage` | 3 | 最大槓桿（也受交易所上限限制）|
| `daily_loss_limit_pct` | 5 | 單日虧損達此 % 即熔斷，當日（UTC）停止開倉 |
| `require_stop_loss` | true | 開倉必須有止損 |
| `default_stop_loss_pct` | 3 | 訊號沒帶止損時自動補上；設為空則直接拒絕 |
| `min_confidence` | 0 | 決策信心低於此值拒絕 |
| `max_orders_per_hour` | 20 | 每小時下單次數上限（防止策略失控）|
| `allow_pyramiding` | false | 是否允許同方向加碼 |

止損 / 止盈由引擎監控（每次輪詢比對最新價，觸發即市價平倉），所有交易所行為一致。**因此 Bot 停止時不會有止損保護**，停止前請自行處理持倉。

「緊急全部停止」只停止 Bot，**不會自動平倉**。

---

## 自訂 Python 策略 API

```python
from goldhunter.strategies.sdk import Strategy, ta


class UserStrategy(Strategy):          # 類別名稱必須是 UserStrategy
    name = "breakout"
    description = "突破 20 根高點做多"
    default_params = {"length": 20, "size_pct": 10, "stop_pct": 2.0}
    warmup = 25                         # 至少需要幾根 K 棒

    def on_bar(self, ctx):              # 每根 K 棒收盤呼叫一次，可寫成 async def
        hh = ta.highest(ctx.high, self.p("length"))
        if ctx.position_size == 0 and ctx.price > hh[-2]:
            return ctx.long(self.p("size_pct"),
                            stop_loss=ctx.price * (1 - self.p("stop_pct") / 100),
                            reasoning="突破前高")
        return None                     # None = 不動作
```

**ctx（StrategyContext）**

| 屬性 / 方法 | 說明 |
|---|---|
| `ctx.open / high / low / close / volume` | `list[float]`，最後一個元素是最新收盤的 K 棒 |
| `ctx.price` | 最新收盤價 |
| `ctx.position_size` | `>0` 多單、`<0` 空單、`0` 空手 |
| `ctx.position` | 持倉物件（均價、槓桿、未實現損益）或 `None` |
| `ctx.balance` | 權益與可用資金 |
| `ctx.capabilities.supports_short` | 是否可放空 |
| `ctx.long(size_pct, stop_loss=None, take_profit=None, leverage=1, reasoning="")` | 做多 |
| `ctx.short(...)` | 做空 |
| `ctx.close_position(reasoning="")` | 平倉 |
| `self.p("參數名")` | 讀取參數 |

**ta 指標**（命名對應 Pine 的 `ta.*`；回傳與輸入等長的 list，資料不足處為 `None`）

`sma` `ema` `rma` `rsi` `atr` `tr` `stdev` `bb`（中、上、下軌）`macd`（線、訊號、柱）`highest` `lowest` `change` `crossover` `crossunder`

**安全限制**：只能 import `math`、`statistics`、`goldhunter.strategies.sdk`；禁止檔案 / 網路 / 系統存取、`eval` / `exec` / `getattr` 等、存取 `_` 開頭屬性。這是防呆與防誤用，**不是作業系統等級的沙箱** —— 請只執行你看得懂的程式碼。

---

## 系統架構

```
goldhunter/
├── backend/                     Python 3.11 · FastAPI · SQLModel(SQLite)
│   └── goldhunter/
│       ├── core/                Instrument、Order、Decision 等跨市場模型；金鑰加密
│       ├── exchanges/           ExchangeAdapter 介面、ccxt（4 家交易所）、模擬交易所
│       ├── ai/                  AIProvider 介面、Claude、OpenAI 相容
│       ├── strategies/          策略介面、ta 指標、內建 / AI / 自訂策略、安全檢查
│       ├── intel/               市場情報：合約數據、情緒、新聞、總經、經濟日曆
│       ├── copilot/             AI 副駕駛：訊號審核、持倉管理、參數微調
│       ├── risk/                風控
│       ├── engine/              Bot 執行引擎、Bot 管理
│       ├── backtest/            回測引擎、歷史資料
│       ├── tradingview/         Pine 轉換、Webhook
│       ├── store/               資料庫模型
│       └── api/                 REST API
└── web/                         React 18 · TypeScript · Vite · Tailwind
```

**統一商品代碼** `market:symbol:type`：`crypto:BTC/USDT:perp`（之後：`us:AAPL:stock`、`tw:2330:stock`）。每個交易所以 `Capabilities` 宣告能否放空、槓桿上限、最小單位、交易時段，策略與風控因此能跨市場共用。

**接新市場**只需實作 `ExchangeAdapter`（`fetch_candles`、`fetch_balance`、`fetch_positions`、`place_order` 等）並在 `exchanges/registry.py` 註冊。

API 文件：啟動後打開 <http://localhost:8000/docs>。

---

## 設定參數

環境變數（或 `.env`，參考 `.env.example`），全部選填：

| 變數 | 預設 | 說明 |
|---|---|---|
| `GOLDHUNTER_DATA_DIR` | `./data` | 資料庫、金鑰、token 存放位置 |
| `GOLDHUNTER_API_TOKEN` | 自動產生 | 登入介面 / 呼叫 API 用的 token |
| `GOLDHUNTER_SECRET_KEY` | 自動產生 | 加密 API Key 用的 AES-256 金鑰（base64 32 bytes）|
| `GOLDHUNTER_RESUME_BOTS` | `true` | 服務重啟後自動恢復原本在跑的 Bot |
| `GOLDHUNTER_CORS_ORIGINS` | `["http://localhost:5173"]` | 前端開發伺服器位址 |

---

## 開發與測試

```bash
cd backend
source .venv/bin/activate
pytest            # 單元測試 + 端對端測試（不需網路、不需 API Key）
ruff check .      # 程式碼檢查

cd ../web
npm run build     # TypeScript 型別檢查 + 打包
```

測試涵蓋：技術指標、模擬撮合、風控、策略安全檢查、Pine 轉換重試、回測無未來函數、AI 副駕駛約束、參數微調、Claude API 請求格式、交易所下單參數，以及透過 REST API 的完整流程（AI 否決 / 調整 / 持倉管理 / 事件避險 / TradingView / 對照組）。

---

## 常見問題

**Q：模擬交易重啟後持倉會不見嗎？**
會。模擬帳戶的狀態存在記憶體，重啟後從初始資金重新開始（成交與決策紀錄會保留在資料庫）。

**Q：止損單會掛在交易所上嗎？**
目前由引擎監控並在觸發時市價平倉，不掛交易所條件單。好處是四家交易所行為一致；缺點是 Bot 停止或主機斷線時沒有保護。建議實盤時另外在交易所設定一個較寬的保險止損。

**Q：AI 會不會亂下單？**
AI 的每個輸出都經過程式硬性約束與風控，無法改變你策略的方向、無法放寬止損、無法超過倉位與槓桿上限。回傳異常時一律不開倉。

**Q：可以只用 AI、不寫策略嗎？**
可以，選 `ai` 策略類型並用中文寫策略指示。但我們建議「你的策略 + AI 副駕駛」，方向有明確規則、AI 負責過濾與微調，比較容易驗證與控制。

**Q：Hyperliquid 為什麼顯示 USDT？**
系統統一以 `BTC/USDT` 表示，送到 Hyperliquid 時自動轉為 `BTC/USDC:USDC`。

---

## 路線圖

- [x] 加密貨幣永續：Binance / OKX / Bybit / Hyperliquid
- [x] 多家 AI、Pine Script 轉換、TradingView Webhook
- [x] AI 副駕駛（訊號審核 / 持倉管理 / 參數微調）、新聞 / 總經 / 合約數據
- [x] 回測、模擬交易、對照組
- [ ] Telegram 通知
- [ ] 交易所端條件止損單
- [ ] 美股（Alpaca）
- [ ] 台股（永豐 Shioaji：整股 / 零股、漲跌停、T+2）
- [ ] 多模型投票

