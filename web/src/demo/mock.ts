// 展示模式：不連後端，改用真實後端錄下的模擬交易資料（scripts/generate_demo_data.py 產生）。
// 只在 `vite build --mode demo` 時打包；正式版不會包含這個檔案。
import fixtures from './fixtures.json'
import { ApiError } from '../api'

type Json = any // eslint-disable-line @typescript-eslint/no-explicit-any

const db: Json = JSON.parse(JSON.stringify(fixtures))
let nextId = 1000

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))
const now = () => new Date().toISOString()
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x))

function mask(v?: string | null) {
  return v ? `****${v.slice(-4)}` : null
}

function refreshDashboard() {
  const bots = db.bots as Json[]
  db.dashboard.bots = bots
  db.dashboard.bots_total = bots.length
  db.dashboard.bots_running = bots.filter((b) => b.running).length
  db.dashboard.total_equity = Math.round(bots.reduce((s, b) => s + (b.equity || 0), 0) * 100) / 100
}

function byBot<T extends { bot_id: number }>(rows: T[], q: URLSearchParams, limitDefault: number): T[] {
  const id = Number(q.get('bot_id') || 0)
  const limit = Number(q.get('limit') || limitDefault)
  return (id ? rows.filter((r) => r.bot_id === id) : rows).slice(0, limit)
}

function notFound(): never {
  throw new ApiError(404, '找不到資料')
}

/** 與後端相同：選 AI 交易員時自動建立（或更新）Bot 專屬策略 */
function traderStrategy(body: Json, bot?: Json): Json | null {
  if (!body.ai_trader) return null
  if (!body.ai_model_id) throw new ApiError(400, 'AI 交易員需要選擇 AI 模型')
  const params = { bars: 40, ...body.ai_trader }
  let st = bot && db.strategies.find((s: Json) => s.id === bot.strategy_id && s.kind === 'ai' && s.name.startsWith('AI 交易員｜'))
  if (!st) {
    st = { id: nextId++, name: `AI 交易員｜${body.name}`, kind: 'ai', code: null, pine_source: null, status: 'active', created_at: now() }
    db.strategies.push(st)
  }
  st.params = params
  return st
}

function modeFields(st: Json) {
  const mode = st?.kind === 'ai' ? 'ai_trader' : st?.kind === 'tradingview' ? 'tradingview' : 'strategy'
  const p = st?.params ?? {}
  return {
    mode,
    ai_trader: mode === 'ai_trader' ? { instructions: p.instructions, reference_strategy_id: p.reference_strategy_id ?? null, min_confidence: p.min_confidence ?? 0.6 } : null,
  }
}

export async function mockRequest(method: string, fullPath: string, body?: Json): Promise<Json> {
  await wait(method === 'GET' ? 120 : 350)
  const [path, qs] = fullPath.split('?')
  const q = new URLSearchParams(qs || '')
  const seg = path.split('/').filter(Boolean)
  const id = Number(seg[1])
  const find = (list: Json[], i = id) => list.find((x) => x.id === i) ?? notFound()

  // ---- 讀取 ----
  if (method === 'GET') {
    switch (seg[0]) {
      case 'health':
        return { ok: true }
      case 'meta':
        return db.meta
      case 'accounts':
        return db.accounts
      case 'ai-models':
        return db.ai_models
      case 'strategies':
        return db.strategies
      case 'bots':
        if (seg[2] === 'positions') return db.positions[String(id)] ?? { running: false, positions: [], balance: null }
        return db.bots
      case 'dashboard':
        refreshDashboard()
        return db.dashboard
      case 'trades':
        return byBot(db.trades, q, 200)
      case 'decisions':
        return byBot(q.get('hide_hold') === 'true' ? db.decisions.filter((d: Json) => d.action !== 'hold') : db.decisions, q, 100)
      case 'tuning':
        return byBot(db.tuning, q, 50)
      case 'equity': {
        const pts: Json[] = db.equity[q.get('bot_id') || ''] ?? []
        const hours = Number(q.get('hours') || 168)
        const cutoff = Date.parse(db.generated_at) - hours * 3600_000
        return pts.filter((p) => Date.parse(p.ts.endsWith('Z') || p.ts.includes('+') ? p.ts : p.ts + 'Z') >= cutoff)
      }
      case 'backtests':
        if (seg[1]) return db.backtest_detail[seg[1]] ?? notFound()
        return db.backtests
      case 'intel':
        return seg[1] === 'settings' ? db.intel_settings : db.intel_snapshot
    }
    notFound()
  }

  // ---- 交易所帳戶 ----
  if (seg[0] === 'accounts') {
    if (seg[2] === 'test') {
      const acc = find(db.accounts)
      return acc.paper
        ? { ok: true, balance: { currency: 'USDT', total: acc.paper_cash, free: acc.paper_cash } }
        : { ok: false, error: '展示模式無法連線真實交易所（實際使用時會顯示帳戶餘額）' }
    }
    if (method === 'POST') {
      const acc = { id: nextId++, created_at: now(), testnet: false, paper: true, paper_cash: 10000, ...body,
        api_key: mask(body.api_key), has_secret: !!body.secret, has_passphrase: !!body.passphrase }
      delete acc.secret
      delete acc.passphrase
      db.accounts.push(acc)
      return acc
    }
    if (method === 'PUT') {
      const acc = find(db.accounts)
      Object.assign(acc, { ...body, api_key: body.api_key ? mask(body.api_key) : acc.api_key })
      delete acc.secret
      delete acc.passphrase
      return acc
    }
    if (method === 'DELETE') {
      if (db.bots.some((b: Json) => b.account_id === id)) throw new ApiError(400, '仍有 Bot 使用此帳戶')
      db.accounts = db.accounts.filter((a: Json) => a.id !== id)
      return { ok: true }
    }
  }

  // ---- AI 模型 ----
  if (seg[0] === 'ai-models') {
    if (seg[2] === 'test') return { ok: true, model: find(db.ai_models).model || 'claude-opus-5', reply: { ok: true } }
    if (method === 'POST') {
      const m = { id: nextId++, created_at: now(), options: {}, base_url: null, ...body, api_key: mask(body.api_key) }
      db.ai_models.push(m)
      return m
    }
    if (method === 'PUT') {
      const m = find(db.ai_models)
      Object.assign(m, { ...body, api_key: body.api_key ? mask(body.api_key) : m.api_key })
      return m
    }
    if (method === 'DELETE') {
      if (db.bots.some((b: Json) => b.ai_model_id === id)) throw new ApiError(400, '仍有 Bot 使用此模型')
      db.ai_models = db.ai_models.filter((a: Json) => a.id !== id)
      return { ok: true }
    }
  }

  // ---- 策略 ----
  if (seg[0] === 'strategies') {
    if (seg[1] === 'validate') {
      const code: string = body.code || ''
      const errors: string[] = []
      for (const bad of ['import os', 'import sys', 'subprocess', 'open(', 'eval(', 'exec(', '__']) {
        if (code.includes(bad)) errors.push(`不允許使用 ${bad}`)
      }
      if (!code.includes('class UserStrategy')) errors.push('找不到 class UserStrategy(Strategy)')
      return errors.length ? { ok: false, errors } : { ok: true, errors: [], name: 'user_strategy', default_params: {}, uses_ai: false }
    }
    if (seg[1] === 'convert-pine') {
      await wait(2500)
      // 展示：Pine 內容含「背離」時示範審查未通過，其餘示範通過
      const res = clone(String(body.pine).includes('背離') ? db.convert_failed : db.convert_result)
      if (res.review && !body.tv_csv) {
        const tv = res.review.stages.find((x: Json) => x.key === 'tv')
        if (tv) Object.assign(tv, { status: 'skipped', summary: '未上傳交易清單 CSV（上傳後可確認與 TradingView 結果一致）', details: [] })
      }
      res.strategy = { ...res.strategy, id: nextId++, name: body.name, pine_source: body.pine, created_at: now(),
        review: res.review, approved_at: res.review?.passed ? now() : null,
        paper_progress: res.review?.passed ? { days: 0, days_required: 7, trades: 0, trades_required: 3, ready: false } : null }
      db.strategies.push(res.strategy)
      return res
    }
    if (seg[2] === 'review' || seg[2] === 'fix') {
      await wait(2000)
      const st = find(db.strategies)
      const review = clone(db.convert_result.review)
      if (!body.tv_csv) {
        const tv = review.stages.find((x: Json) => x.key === 'tv')
        if (tv) Object.assign(tv, { status: 'skipped', summary: '未上傳交易清單 CSV（上傳後可確認與 TradingView 結果一致）', details: [] })
      }
      Object.assign(st, { review, status: 'paper_only', status_label: '模擬期', approved_at: now(),
        paper_progress: { days: 0, days_required: 7, trades: 0, trades_required: 3, ready: false } })
      if (seg[2] === 'fix') st.code = db.convert_result.strategy.code
      return { strategy: st, review, conversion: seg[2] === 'fix' ? db.convert_result.conversion : undefined }
    }
    if (seg[2] === 'promote') {
      const st = find(db.strategies)
      Object.assign(st, { status: 'active', status_label: '已啟用', paper_progress: null })
      return st
    }
    if (seg[2] === 'activate') {
      const st = find(db.strategies)
      Object.assign(st, { status: 'active', status_label: '已啟用', paper_progress: null })
      return st
    }
    if (method === 'POST') {
      const st = { id: nextId++, created_at: now(), status: 'active', code: null, pine_source: null, params: {}, ...body }
      db.strategies.push(st)
      return st
    }
    if (method === 'PUT') {
      const st = find(db.strategies)
      if (body.kind === 'python' && body.code !== st.code) st.status = 'pending_review'
      Object.assign(st, body)
      return st
    }
    if (method === 'DELETE') {
      if (db.bots.some((b: Json) => b.strategy_id === id)) throw new ApiError(400, '仍有 Bot 使用此策略')
      db.strategies = db.strategies.filter((s: Json) => s.id !== id)
      return { ok: true }
    }
  }

  // ---- Bots ----
  if (seg[0] === 'bots') {
    if (seg[1] === 'stop-all') {
      const stopped = db.bots.filter((b: Json) => b.running).map((b: Json) => b.id)
      db.bots.forEach((b: Json) => Object.assign(b, { running: false, status: 'stopped', copilot_active: false }))
      return { stopped }
    }
    const bot = seg[1] ? find(db.bots) : null
    if (seg[2] === 'start') {
      const st = db.strategies.find((s: Json) => s.id === bot.strategy_id)
      if (st?.status !== 'active') throw new ApiError(400, '策略尚未審核啟用（Pine 轉換的策略需先檢視程式碼並啟用）')
      Object.assign(bot, { running: true, status: 'running', last_error: null, last_run_at: now(),
        copilot_active: !!bot.copilot_enabled && st.kind !== 'ai' && st.kind !== 'tradingview' })
      return { ok: true }
    }
    if (seg[2] === 'stop') {
      Object.assign(bot, { running: false, status: 'stopped', copilot_active: false })
      return { ok: true }
    }
    if (seg[2] === 'regenerate-webhook-secret') {
      bot.webhook_secret = Math.random().toString(36).slice(2, 12) + Math.random().toString(36).slice(2, 12)
      return { webhook_secret: bot.webhook_secret }
    }
    if (seg[2] === 'signal') {
      if (!bot.running) throw new ApiError(400, 'Bot 未啟動')
      const pos = db.positions[String(id)]
      pos.positions = pos.positions.filter((p: Json) => p.instrument !== body.instrument)
      const entry = { id: nextId++, bot_id: id, ts: now(), instrument: body.instrument, source: 'manual', action: body.action,
        decision: { ...body, reasoning: '手動操作' }, approved: true, reasons: [], ai_model: null, ai_raw: null,
        input_tokens: 0, output_tokens: 0 }
      db.decisions.unshift(entry)
      return entry
    }
    if (seg[2] === 'tune-now') {
      await wait(1800)
      const last = db.tuning.find((t: Json) => t.bot_id === id)
      if (!last) throw new ApiError(400, '需要規則型 / 自訂策略並設定 AI 模型')
      const run = { ...clone(last), id: nextId++, ts: now(), status: 'proposed' }
      db.tuning.unshift(run)
      return run
    }
    if (method === 'POST') {
      const st = traderStrategy(body) ?? db.strategies.find((s: Json) => s.id === body.strategy_id)
      const copilot = { ...db.meta.copilot_defaults, ...(body.copilot || {}) }
      const b = { id: nextId++, created_at: now(), status: 'stopped', running: false, last_error: null, last_run_at: null,
        webhook_secret: Math.random().toString(36).slice(2, 14), equity: null, baseline_equity: null, halted_reason: null,
        params_override: {}, ...body, copilot, risk: { ...db.meta.risk_defaults, ...(body.risk || {}) },
        strategy_name: st?.name ?? null, strategy_kind: st?.kind ?? null,
        copilot_enabled: !!body.ai_model_id && (copilot.review || copilot.manage || copilot.tune), copilot_active: false,
        ...modeFields(st), strategy_id: st?.id ?? null }
      delete b.ai_trader_in
      db.bots.push(b)
      db.equity[String(b.id)] = []
      db.positions[String(b.id)] = { running: false, positions: [], balance: null }
      return b
    }
    if (method === 'PUT') {
      if (bot.running) throw new ApiError(400, '請先停止 Bot 再修改設定')
      const st = traderStrategy(body, bot) ?? db.strategies.find((s: Json) => s.id === body.strategy_id)
      const copilot = { ...db.meta.copilot_defaults, ...(body.copilot || {}) }
      Object.assign(bot, body, modeFields(st), { strategy_id: st?.id, copilot, strategy_name: st?.name, strategy_kind: st?.kind,
        copilot_enabled: !!body.ai_model_id && (copilot.review || copilot.manage || copilot.tune) })
      return bot
    }
    if (method === 'DELETE') {
      db.bots = db.bots.filter((b: Json) => b.id !== id)
      return { ok: true }
    }
  }

  // ---- 參數微調 ----
  if (seg[0] === 'tuning' && seg[2] === 'apply') {
    const run = find(db.tuning)
    run.status = 'applied'
    const bot = db.bots.find((b: Json) => b.id === run.bot_id)
    if (bot) bot.params_override = { ...(bot.params_override || {}), ...run.proposed_params }
    return run
  }

  // ---- 資料來源 ----
  if (seg[0] === 'intel' && method === 'PUT') {
    const { fred_api_key, cryptopanic_token, ...rest } = body
    Object.assign(db.intel_settings, Object.fromEntries(Object.entries(rest).filter(([, v]) => v !== null && v !== undefined)))
    if (fred_api_key) db.intel_settings.fred_api_key = mask(fred_api_key)
    if (cryptopanic_token) db.intel_settings.cryptopanic_token = mask(cryptopanic_token)
    return db.intel_settings
  }

  // ---- 回測 ----
  if (seg[0] === 'backtests') {
    if (method === 'DELETE') {
      db.backtests = db.backtests.filter((b: Json) => b.id !== id)
      return { ok: true }
    }
    await wait(1200)
    const st = db.strategies.find((s: Json) => s.id === body.strategy_id)
    const run = { ...clone(db.backtest_run), id: nextId++, created_at: now(), strategy_id: body.strategy_id,
      strategy_name: st?.name ?? db.backtest_run.strategy_name, instrument: body.symbol, timeframe: body.timeframe,
      config: body }
    const { equity_curve: _e, trades: _t, candles: _c, rejected: _r, ...summary } = run
    void _e, void _t, void _c, void _r
    db.backtests.unshift(summary)
    db.backtest_detail[String(run.id)] = { ...run, candles: undefined, rejected: undefined }
    return run
  }

  throw new ApiError(404, `展示模式未支援：${method} ${path}`)
}
