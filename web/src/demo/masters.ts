// 展示模式：大師組合、組合計畫、進場分析的模擬資料（只在 demo 打包）。
type Json = any // eslint-disable-line @typescript-eslint/no-explicit-any

// ------------------------------------------------------------------ utils
function rng(seed: number) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
const round = (v: number, d = 2) => Math.round(v * 10 ** d) / 10 ** d
const iso = (ms: number) => new Date(ms).toISOString().replace('.000Z', '+00:00')

const BASE_PRICE: Record<string, number> = { BTC: 126_000, ETH: 4_480, SOL: 232, BNB: 1_120, XRP: 2.95, DOGE: 0.27, AVAX: 38.5, LINK: 26.4 }
const TF_ATR: Record<string, number> = { '15m': 0.0045, '1h': 0.009, '4h': 0.018, '1d': 0.038 }
const TOP_COINS = ['BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'DOGE', 'AVAX', 'LINK']
const MEME = new Set(['DOGE', 'SHIB', 'PEPE', 'WIF', 'BONK'])

export function priceOf(db: Json, inst: string): number {
  const t = (db.trades as Json[]).find((x) => x.instrument === inst && x.price)
  if (t) return t.price
  const base = /^crypto:([^/]+)\//.exec(inst)?.[1]?.toUpperCase() ?? 'BTC'
  return BASE_PRICE[base] ?? 100
}

export function botInstruments(bot: Json): string[] {
  if (bot.universe?.mode === 'rules') {
    const only: string[] = bot.universe.include_only ?? []
    const excl = new Set<string>(bot.universe.exclude ?? [])
    const pool = (only.length ? only : TOP_COINS).filter((c) => !excl.has(c) && !(bot.universe.exclude_meme !== false && MEME.has(c)))
    return pool.slice(0, Math.min(bot.universe.top_n ?? 10, 4)).map((c) => `crypto:${c}/USDT:perp`)
  }
  return bot.symbols ?? []
}

// ------------------------------------------------------------------ 進場分析
export function mockEntryAnalysis(price: number, direction: string, tf: string, seed = 1, stop?: number | null, target?: number | null): Json {
  const r = rng(seed)
  const long = direction === 'long'
  const sgn = long ? 1 : -1
  const atr = price * (TF_ATR[tf] ?? 0.009) * (0.85 + r() * 0.3)
  const st = stop ?? price - sgn * atr * 1.5
  const tg = target ?? price + sgn * atr * 3
  const mk = (key: string, label: string, p: number, fill: number, win: number) => {
    const rr = Math.abs(tg - p) / Math.max(Math.abs(p - st), 1e-9)
    const ev = win * rr - (1 - win)
    return { key, label, price: p, rr: round(rr, 2), fill_prob: round(fill, 2), win_prob: round(win, 2), ev_r: round(ev, 2), ev_per_signal: round(fill * ev, 2), samples: 38 }
  }
  const w = 0.3 + r() * 0.12
  const candidates = [
    mk('market', '市價進場', price, 1, w),
    mk('pullback_05', '回檔 0.5 ATR', price - sgn * atr * 0.5, 0.7, w + 0.03),
    mk('pullback_10', '回檔 1 ATR', price - sgn * atr * 1.0, 0.44, w + 0.05),
    mk('level', long ? '前低支撐' : '前高壓力', price - sgn * atr * 1.2, 0.33, w + 0.06),
  ]
  const best = [...candidates].sort((a, b) => (b.ev_per_signal ?? 0) - (a.ev_per_signal ?? 0))[0]
  const skip = (best.ev_per_signal ?? 0) <= 0
  const fmt = (v: number) => (v >= 1000 ? v.toFixed(0) : v >= 1 ? v.toFixed(2) : v.toFixed(4))
  const recommendation = skip
    ? '所有進場方式的期望值都為負，建議這次不要進場'
    : best.key === 'market'
      ? `建議直接市價進場：盈虧比 1:${best.rr}，等回檔的成交機率太低`
      : `建議在 ${best.label}（${fmt(best.price)}）掛單：盈虧比 1:${best.rr}，約 ${Math.round(best.fill_prob * 100)}% 機率等得到，比市價進場的期望值高`
  return {
    direction, price, atr: round(atr, 6), stop: st, target: tg,
    stop_source: stop ? '訊號止損' : '1.5 ATR', target_source: target ? '訊號目標' : '3 ATR',
    candidates, recommended: skip ? 'skip' : best.key, recommendation, wait_bars: 6,
    signal_samples: 38, sample_basis: '同策略過去 1,000 根 K 棒的歷史訊號', high_frequency: false,
  }
}

// ------------------------------------------------------------------ 組合計畫
export function planFor(name: string): Json {
  if (/巴菲特|芒格|Buffett|Munger/.test(name))
    return {
      suitable: false, style_summary: '只做 BTC / ETH 現貨式多單、不加槓桿的最保守版本', timeframe: '1d', holding_period: '數週到數月',
      reason: '他反對槓桿與投機性資產，加密貨幣永續合約不是他的能力圈。以下是最保守的做法：1 倍、只做多、日線、低倉位。',
      universe: { mode: 'list', top_n: 2, exclude_meme: true, include_only: [], symbols: ['BTC', 'ETH'] },
      max_positions: 2, position_pct: 10, max_leverage: 1, allow_short: false, entry_mode: 'smart',
      instructions: '只在日線明顯超跌、恐懼指數極低時分批做多；不追高、不做空、不加槓桿；沒有安全邊際就不交易。',
    }
  if (/索羅斯|Soros/.test(name))
    return {
      suitable: true, style_summary: '總經與流動性驅動的集中押注，看對時加碼、看錯就走', timeframe: '4h', holding_period: '數天到數週',
      reason: '反身性：趨勢會自我強化直到反轉，所以只在流動性與敘事同向時重倉；「重要的不是對錯，而是對的時候賺多少」→ 集中、可做空、中等槓桿。',
      universe: { mode: 'rules', top_n: 5, exclude_meme: true, include_only: [], symbols: [] },
      max_positions: 3, position_pct: 20, max_leverage: 3, allow_short: true, entry_mode: 'market',
      instructions: '先判斷資金面與總經敘事（ETF 流量、利率預期、資金費率），和趨勢同向才進場；看錯立刻認賠，看對分批加碼。',
    }
  if (/瓊斯|Tudor|Jones/.test(name))
    return {
      suitable: true, style_summary: '防守優先的趨勢交易：200 日線之上做多、之下做空，5:1 盈虧比', timeframe: '4h', holding_period: '數天',
      reason: '「最重要的規則是防守」→ 單筆小倉位、嚴格止損；以 200 日均線判斷多空；只做盈虧比至少 5:1 的機會，等回檔掛單進場。',
      universe: { mode: 'rules', top_n: 6, exclude_meme: true, include_only: [], symbols: [] },
      max_positions: 4, position_pct: 8, max_leverage: 2, allow_short: true, entry_mode: 'smart',
      instructions: '價格在 200 日均線之上只做多、之下只做空；等回檔到關鍵價位再進場，盈虧比不到 1:3 不做；虧損時先減碼。',
    }
  if (/李佛摩|Livermore/.test(name))
    return {
      suitable: true, style_summary: '關鍵點突破的順勢投機，試單確認後加碼', timeframe: '1h', holding_period: '數小時到數天',
      reason: '最小阻力線：方向確認前不動，突破關鍵點才進場；先小部位試單，對了再加碼；錯了立刻出場，絕不攤平。',
      universe: { mode: 'rules', top_n: 3, exclude_meme: true, include_only: [], symbols: [] },
      max_positions: 2, position_pct: 15, max_leverage: 3, allow_short: true, entry_mode: 'market',
      instructions: '只在放量突破整理區時順勢進場；止損放在關鍵點另一側；盤整時什麼都不做；不逆勢抄底。',
    }
  return {
    suitable: true, style_summary: '短線流動性交易：等假突破收回再進場，賺大賠小', timeframe: '15m', holding_period: '數小時',
    reason: '依他的鏈上成交紀錄：平均持倉 6 小時、勝率不到五成但盈虧比高，專做 BTC / ETH / SOL，偏好在掃止損後反向進場。',
    universe: { mode: 'list', top_n: 3, exclude_meme: true, include_only: [], symbols: ['BTC', 'ETH', 'SOL'] },
    max_positions: 3, position_pct: 12, max_leverage: 5, allow_short: true, entry_mode: 'smart',
    instructions: '等明顯高低點被掃過再收回時進場；止損放在掃點外側；一天最多 3 筆；資金費率極端時反向思考。',
  }
}

export function planToBot(plan: Json) {
  const u = plan.universe
  return {
    timeframe: plan.timeframe,
    interval_sec: ({ '15m': 60, '1h': 120, '4h': 300, '1d': 600 } as Record<string, number>)[plan.timeframe] ?? 120,
    universe: u.mode === 'rules' ? { mode: 'rules', top_n: u.top_n, exclude_meme: u.exclude_meme, exclude: [], include_only: u.include_only, refresh_hours: 6 } : { mode: 'list', top_n: 10, exclude_meme: true, exclude: [], include_only: [], refresh_hours: 6 },
    symbols: u.mode === 'list' ? u.symbols.map((s: string) => `crypto:${s}/USDT:perp`) : [],
    risk: { max_position_pct: plan.position_pct, max_leverage: plan.max_leverage, max_positions: plan.max_positions, long_only: !plan.allow_short,
      max_total_exposure_pct: Math.min(1000, plan.position_pct * plan.max_positions * plan.max_leverage) },
    entry: { mode: plan.entry_mode, max_wait_bars: null, skip_negative_ev: false },
  }
}

// ------------------------------------------------------------------ 權益 / 成交
function series(endMs: number, days: number, stepMin: number, start: number, drift: number, vol: number, seed: number): Json[] {
  const r = rng(seed)
  const n = Math.floor((days * 1440) / stepMin)
  let e = start
  const out: Json[] = []
  for (let i = 0; i <= n; i++) {
    const ts = endMs - (n - i) * stepMin * 60_000
    out.push({ ts: iso(ts), equity: round(e, 5), baseline_equity: null })
    e *= 1 + drift / n + (r() - 0.5) * vol
  }
  return out
}

function fakeTrades(botId: number, endMs: number, n: number, winRate: number, avg: number, coins: string[], seed: number, idStart: number): Json[] {
  const r = rng(seed)
  const out: Json[] = []
  for (let i = 0; i < n; i++) {
    const coin = coins[i % coins.length]
    const inst = `crypto:${coin}/USDT:perp`
    const price = (BASE_PRICE[coin] ?? 100) * (0.97 + r() * 0.06)
    const win = r() < winRate
    const pnl = win ? avg * (0.6 + r() * 1.4) : -avg * (0.4 + r() * 0.6)
    const ts = endMs - Math.floor((i + 1) * (6.5 * 86400_000) / (n + 1))
    const qty = round(1200 / price, 6)
    out.push({ id: idStart + i * 2, bot_id: botId, decision_id: null, ts: iso(ts - 3 * 3600_000), instrument: inst, side: 'buy', quantity: qty, price: round(price, 4), fee: 0.6, reduce_only: false, realized_pnl: null, order_id: `paper-m${botId}-${i}o`, status: 'filled', source: 'ai', note: null })
    out.push({ id: idStart + i * 2 + 1, bot_id: botId, decision_id: null, ts: iso(ts), instrument: inst, side: 'sell', quantity: qty, price: round(price * (1 + pnl / 1200), 4), fee: 0.6, reduce_only: true, realized_pnl: round(pnl, 2), order_id: `paper-m${botId}-${i}c`, status: 'filled', source: 'ai', note: null })
  }
  return out
}

// ------------------------------------------------------------------ 舊 fixture → 新模型（女媧上傳、無委員會）
const PTJ_PROFILE = `# 保羅·都鐸·瓊斯（Paul Tudor Jones）· 交易思維作業系統

> 依公開訪談（《金融怪傑》1989、2014 年 UVA 演講等）提煉的框架推斷，非本人觀點。

## 身份卡
總經交易員，1987 年股災前做空獲利。我最在意的是防守：先想會虧多少，再想會賺多少。

## 核心心智模型
1. **防守第一**：每天都假設自己的部位是錯的，先找出場點。
2. **200 日均線**：價格在 200 日線之下的東西都不碰（做多），這條線決定我站哪一邊。
3. **5:1 盈虧比**：只做潛在報酬是風險五倍的交易，勝率 20% 也不會輸。

## 決策規則
- 虧損時減碼，獲利時才加碼
- 從不攤平
- 重大事件前降低部位

## 誠實邊界
- 加密貨幣屬框架推斷，他公開談過比特幣是對抗通膨的工具，但沒有公開交易紀錄
- 高頻、短線的做法不是他的風格
`

export function adaptFixtures(db: Json): void {
  if (db.masters_adapted) return
  db.masters_adapted = true
  const endMs = Date.parse(db.generated_at) || Date.now()
  const detail = db.persona_detail as Record<string, Json>

  // 1) 投資大師：全部視為女媧上傳；移除只當審查委員的芒格；保真度改為 FIDELITY.md 格式
  delete detail['3']
  db.personas = (db.personas as Json[]).filter((p) => p.id !== 3)
  for (const d of Object.values(detail)) {
    d.source = 'nuwa'
    d.role = 'trader'
    if (d.fidelity?.questions) delete d.fidelity.questions
    if (d.fidelity?.score !== undefined) d.fidelity.tested_at = '2026-09-20'
    d.meta = { ...(d.meta || {}), kept_sections: ['身份卡', '核心心智模型', '決策規則', '反模式', '誠實邊界'], dropped_sections: ['角色扮演規則', '回答工作流', '調研來源'], files: ['SKILL.md', 'FIDELITY.md'], filename: `${d.slug || 'persona'}.zip` }
    delete d.meta.estimate
    delete d.meta.onchain
    delete d.meta.hyperliquid_address
    if (/巴菲特/.test(d.name)) d.summary = '價值投資、安全邊際、不用槓桿'
  }
  detail['6'] = {
    id: 6, slug: null, name: '保羅·都鐸·瓊斯', role: 'trader', markets: ['crypto', 'us'], summary: '防守第一、200 日均線定多空、只做 5:1 盈虧比', profile: PTJ_PROFILE,
    source: 'nuwa', status: 'paper_only', approved_at: iso(endMs - 5 * 86400_000), paper_days: 7, min_paper_trades: 3, created_at: iso(endMs - 5 * 86400_000),
    fidelity: { score: 81, grade: 'B', tested_at: '2026-09-22', summary: '防守與 200 日線的立場清楚；加密貨幣部分有標註為推斷',
      dimensions: [
        { name: '立場一致性', score: 25, max: 30, reason: '防守第一、不攤平、200 日線立場正確' },
        { name: '風格辨識度', score: 16, max: 20, reason: '「先想會虧多少」的語氣明顯' },
        { name: '邊緣誠實度', score: 17, max: 20, reason: '加密貨幣題標註為框架推斷' },
        { name: '情境合理性', score: 12, max: 15, reason: '情境題先談止損與部位' },
        { name: '結構完整度', score: 11, max: 15, reason: '心智模型 3 個，略少' },
      ] },
    meta: { kept_sections: ['身份卡', '核心心智模型', '決策規則', '誠實邊界'], dropped_sections: ['角色扮演規則', '示例對話'], files: ['SKILL.md', 'FIDELITY.md'], filename: 'paul-tudor-jones.zip' },
    paper_progress: { days: 5, days_required: 7, trades: 9, trades_required: 3, ready: false }, pass_score: 70,
  }
  db.personas.push({ ...detail['6'], profile: undefined })

  // 2) 李佛摩的 Bot 改成他的大師組合（移除審查委員）
  const livermoreBot = (db.bots as Json[]).find((b) => b.ai_trader?.persona_id === 1)
  if (livermoreBot) {
    livermoreBot.name = '傑西·李佛摩 組合'
    livermoreBot.strategy_name = 'AI 交易員｜傑西·李佛摩 組合'
    livermoreBot.ai_trader = { ...livermoreBot.ai_trader, persona_id: 1 }
    delete livermoreBot.ai_trader.reviewer_ids
    delete livermoreBot.ai_trader.veto_rule
    livermoreBot.risk = { ...livermoreBot.risk, max_positions: 2, long_only: false }
    livermoreBot.entry = { mode: 'market', max_wait_bars: null, skip_negative_ev: false }
    livermoreBot.capital = 10000
    const st = (db.strategies as Json[]).find((s) => s.id === livermoreBot.strategy_id)
    if (st) st.name = livermoreBot.strategy_name
    detail['1'].meta.portfolio_plan = planFor(detail['1'].name)
  }

  // 3) 決策紀錄：移除委員會，並為部分開倉決策附上進場分析
  let k = 0
  for (const d of db.decisions as Json[]) {
    const dec = d.decision || {}
    if (dec.meta?.committee) {
      if (dec.meta.committee.vetoed) d._drop = true
      delete dec.meta.committee
    }
    if ((d.action === 'open_long' || d.action === 'open_short') && k < 40) {
      const inst = d.instrument
      dec.entry_analysis = mockEntryAnalysis(priceOf(db, inst), d.action === 'open_long' ? 'long' : 'short', '1h', d.id, dec.stop_loss, dec.take_profit)
      k++
    }
  }

  db.decisions = (db.decisions as Json[]).filter((d) => !d._drop)

  // 4) 其他大師的組合（各自獨立的模擬資金）
  const mk = (id: number, personaId: number, drift: number, vol: number, seed: number, running: boolean, winRate: number, n: number, avg: number) => {
    const p = detail[String(personaId)]
    const plan = planFor(p.name)
    p.meta.portfolio_plan = plan
    const f = planToBot(plan)
    const coins = plan.universe.mode === 'list' ? plan.universe.symbols : ['BTC', 'ETH', 'SOL']
    const stId = 900 + id
    db.strategies.push({ id: stId, name: `AI 交易員｜${p.name} 組合`, kind: 'ai', code: null, pine_source: null, status: 'active', created_at: iso(endMs - 7 * 86400_000),
      params: { instructions: plan.instructions, reference_strategy_id: null, min_confidence: 0.6, persona_id: personaId, bars: 40 } })
    const eq = series(endMs, 6.5, 30, 10000, drift, vol, seed)
    db.equity[String(id)] = eq
    db.trades.push(...fakeTrades(id, endMs, n, winRate, avg, coins, seed + 7, 50_000 + id * 100))
    const last = eq[eq.length - 1].equity
    db.positions[String(id)] = running
      ? { running: true, balance: { currency: 'USDT', total: last, free: last * 0.8 },
          positions: [{ instrument: `crypto:${coins[0]}/USDT:perp`, quantity: round(1500 / (BASE_PRICE[coins[0]] ?? 100), 6), entry_price: BASE_PRICE[coins[0]] ?? 100, leverage: plan.max_leverage, unrealized_pnl: round((drift > 0 ? 1 : -1) * 23.4, 2), side: 'long', mark_price: (BASE_PRICE[coins[0]] ?? 100) * (1 + drift / 100), stop_loss: (BASE_PRICE[coins[0]] ?? 100) * 0.97, take_profit: null }] }
      : { running: false, positions: [], balance: null }
    db.bots.push({
      id, name: `${p.name} 組合`, account_id: 1, strategy_id: stId, ai_model_id: 1, ...f, capital: 10000,
      copilot: { ...db.meta.copilot_defaults, review: false, manage: false, tune: false }, params_override: {},
      risk: { ...db.meta.risk_defaults, ...f.risk }, status: running ? 'running' : 'stopped', last_error: null, created_at: iso(endMs - 7 * 86400_000),
      webhook_secret: `demo${id}secret`, last_run_at: iso(endMs - 4 * 60_000), strategy_name: `AI 交易員｜${p.name} 組合`, strategy_kind: 'ai',
      mode: 'ai_trader', ai_trader: { instructions: plan.instructions, reference_strategy_id: null, min_confidence: 0.6, persona_id: personaId },
      running, copilot_enabled: false, copilot_active: false, halted_reason: null, equity: last, baseline_equity: null,
    })
  }
  mk(11, 2, 0.047, 0.006, 11, true, 0.58, 9, 62)
  mk(12, 6, 0.016, 0.0035, 12, true, 0.44, 11, 48)
  mk(13, 5, -0.021, 0.009, 13, false, 0.39, 18, 41)
  db.trades.sort((a: Json, b: Json) => (a.ts < b.ts ? 1 : -1))
}

// ------------------------------------------------------------------ GET /masters
export function computeMasters(db: Json, running: (bot: Json) => boolean): Json[] {
  const out: Json[] = []
  for (const bot of db.bots as Json[]) {
    const pid = bot.ai_trader?.persona_id
    if (bot.mode !== 'ai_trader' || !pid) continue
    const p = db.persona_detail[String(pid)]
    const snaps: Json[] = db.equity[String(bot.id)] ?? []
    const trades = (db.trades as Json[]).filter((t) => t.bot_id === bot.id)
    const closed = trades.map((t) => t.realized_pnl).filter((x) => x !== null && x !== undefined) as number[]
    const eq = snaps.map((x) => x.equity as number)
    const start = eq[0] ?? bot.capital ?? null
    let peak = eq[0] ?? 0
    let mdd = 0
    for (const e of eq) {
      peak = Math.max(peak, e)
      mdd = Math.max(mdd, peak ? (peak - e) / peak : 0)
    }
    const step = Math.max(1, Math.floor(snaps.length / 200))
    const isRunning = running(bot)
    out.push({
      bot_id: bot.id, name: bot.name, running: isRunning, status: bot.status,
      persona: p ? { id: p.id, name: p.name, status: p.status, fidelity: p.fidelity?.score ?? null } : null,
      timeframe: bot.timeframe, universe: bot.universe ?? {}, symbols: bot.symbols ?? [],
      risk: { max_leverage: bot.risk?.max_leverage, max_positions: bot.risk?.max_positions, max_position_pct: bot.risk?.max_position_pct, long_only: bot.risk?.long_only },
      plan: p?.meta?.portfolio_plan ?? null,
      start_equity: start, equity: eq.length ? eq[eq.length - 1] : null,
      return_pct: eq.length && start ? round((eq[eq.length - 1] / start - 1) * 100, 2) : null,
      max_drawdown_pct: round(mdd * 100, 2), trades: trades.length, closed_trades: closed.length,
      win_rate_pct: closed.length ? round((closed.filter((x) => x > 0).length / closed.length) * 100, 1) : null,
      realized_pnl: round(closed.reduce((s, x) => s + x, 0), 2),
      open_positions: isRunning ? (db.positions[String(bot.id)]?.positions?.length ?? 0) : null,
      curve: snaps.filter((_, i) => i % step === 0).map((x) => ({ ts: x.ts, pct: start ? round((x.equity / start - 1) * 100, 3) : 0 })),
    })
  }
  return out
}
