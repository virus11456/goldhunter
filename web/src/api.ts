// GoldHunter API client — types mirror backend/goldhunter (store/db.py, api/*.py)

const TOKEN_KEY = 'goldhunter_token'

export const DEMO = import.meta.env.VITE_DEMO === '1'

export function getToken(): string | null {
  if (DEMO) return 'demo'
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type UnauthorizedHandler = () => void
let onUnauthorized: UnauthorizedHandler = () => {}
export function setUnauthorizedHandler(fn: UnauthorizedHandler): void {
  onUnauthorized = fn
}

function detailToMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail
  // FastAPI 422 validation errors: [{loc, msg, type}]
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (d && typeof d === 'object' && 'msg' in d) {
          const o = d as { loc?: unknown[]; msg: string }
          const loc = Array.isArray(o.loc) ? o.loc.filter((x) => x !== 'body').join('.') : ''
          return loc ? `${loc}：${o.msg}` : o.msg
        }
        return String(d)
      })
      .join('；')
  }
  return JSON.stringify(detail)
}

async function request<T>(method: string, path: string, body?: unknown, auth = true): Promise<T> {
  if (DEMO) {
    const { mockRequest } = await import('./demo/mock')
    return (await mockRequest(method, path, body)) as T
  }
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) {
    const t = getToken()
    if (t) headers['Authorization'] = `Bearer ${t}`
  }
  let res: Response
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      credentials: 'same-origin',
      cache: 'no-store',
    })
  } catch (e) {
    throw new ApiError(0, `無法連線到伺服器：${e instanceof Error ? e.message : String(e)}`)
  }
  if (res.status === 401 && auth) {
    onUnauthorized()
  }
  const text = await res.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    if (data && typeof data === 'object' && 'detail' in data) {
      msg = detailToMessage((data as { detail: unknown }).detail)
    } else if (typeof data === 'string' && data) {
      msg = `${msg}：${data.slice(0, 200)}`
    }
    throw new ApiError(res.status, msg)
  }
  return data as T
}

const get = <T>(p: string) => request<T>('GET', p)
const post = <T>(p: string, b?: unknown) => request<T>('POST', p, b ?? {})
const put = <T>(p: string, b: unknown) => request<T>('PUT', p, b)
const del = <T>(p: string) => request<T>('DELETE', p)

// ------------------------------ Types ------------------------------
export type ISODate = string // naive UTC ISO string from backend

export interface ExchangeMeta {
  id: string
  market: 'crypto' | 'us' | 'tw' | string
  label: string
  needs_passphrase: boolean
  max_leverage?: number
  planned?: boolean
}

export interface AIProviderMeta {
  id: string
  label: string
  default_model: string
  needs_key: boolean
  default_base_url?: string
}

export type ParamValue = number | string | boolean | null
export type Params = Record<string, ParamValue | unknown>

export interface StrategyTypeMeta {
  type: string
  description: string
  default_params: Params
  uses_ai: boolean
}

export interface RiskConfig {
  max_position_pct: number
  max_total_exposure_pct: number
  max_leverage: number
  daily_loss_limit_pct: number
  require_stop_loss: boolean
  default_stop_loss_pct: number | null
  min_confidence: number
  max_orders_per_hour: number
  allow_pyramiding: boolean
}

export interface CopilotConfig {
  review: boolean
  review_min_confidence: number
  size_mult_min: number
  size_mult_max: number
  manage: boolean
  manage_interval_min: number
  tune: boolean
  tune_interval_hours: number
  tune_lookback_bars: number
  tune_auto_apply: boolean
  tune_ranges: Record<string, [number, number] | number[]>
  event_blackout_min: number
  notes: string
}

export interface Meta {
  exchanges: ExchangeMeta[]
  ai_providers: AIProviderMeta[]
  strategy_types: StrategyTypeMeta[]
  timeframes: string[]
  risk_defaults: RiskConfig
  copilot_defaults: CopilotConfig
  strategy_template: string
}

export interface Account {
  id: number
  name: string
  exchange_id: string
  testnet: boolean
  paper: boolean
  paper_cash: number
  created_at: ISODate
  api_key: string // masked
  has_secret: boolean
  has_passphrase: boolean
}

export interface AccountIn {
  name: string
  exchange_id: string
  api_key?: string | null
  secret?: string | null
  passphrase?: string | null
  testnet: boolean
  paper: boolean
  paper_cash: number
}

export interface Balance {
  currency: string
  total: number
  free: number
}

export type TestAccountResult = { ok: true; balance: Balance } | { ok: false; error: string }

export interface AIModel {
  id: number
  name: string
  provider: string
  model: string
  base_url: string | null
  options: Record<string, unknown>
  api_key: string // masked
  created_at: ISODate
}

export interface AIModelIn {
  name: string
  provider: string
  model: string
  api_key?: string | null
  base_url?: string | null
  options: Record<string, unknown>
}

export type TestAIResult = { ok: true; model: string; reply: unknown } | { ok: false; error: string }

export type StrategyStatus = 'active' | 'pending_review' | 'paper_only' | string

export interface PaperProgress {
  days: number
  days_required: number
  trades: number
  trades_required: number
  ready: boolean
}

export interface ReviewStage {
  key: 'safety' | 'smoke' | 'cross' | 'tv' | string
  name: string
  status: 'passed' | 'failed' | 'skipped' | string
  summary: string
  details: string[]
}

export interface ReviewReport {
  passed: boolean
  stages: ReviewStage[]
  reviewed_at: string
  symbol: string
  timeframe: string
  exchange_id?: string
  metrics: StrategyMetrics
}

export interface Strategy {
  id: number
  name: string
  kind: string
  params: Params
  code: string | null
  pine_source: string | null
  status: StrategyStatus
  status_label?: string
  paper_progress?: PaperProgress | null
  review?: ReviewReport | Record<string, never> | null
  metrics?: StrategyMetrics | null
  approved_at?: ISODate | null
  paper_days?: number
  min_paper_trades?: number
  created_at: ISODate
}

/** Settings used by the automatic review (market data + optional TradingView trade list CSV) */
export interface ReviewSettings {
  exchange_id?: string
  symbol?: string
  timeframe?: string
  tv_csv?: string | null
}

export interface ReviewIn extends ReviewSettings {
  ai_model_id: number
}

export interface ConvertPineIn extends ReviewSettings {
  name: string
  pine: string
  ai_model_id: number
}

export interface StrategyIn {
  name: string
  kind: string
  params: Params
  code?: string | null
}

export type ValidateResult =
  | { ok: true; errors: string[]; name: string; default_params: Params; uses_ai?: boolean }
  | { ok: false; errors: string[] }

export interface ConversionResult {
  code: string
  ok: boolean
  errors: string[]
  attempts: number
  notes: string[]
}

export interface ConvertPineResult {
  strategy: Strategy
  conversion: ConversionResult
  review: ReviewReport | null
}

export interface ReviewResult {
  strategy: Strategy
  review: ReviewReport | null
}

export interface FixResult extends ReviewResult {
  conversion: ConversionResult
}

export interface Bot {
  id: number
  name: string
  account_id: number
  strategy_id: number
  ai_model_id: number | null
  symbols: string[]
  timeframe: string
  interval_sec: number
  risk: Partial<RiskConfig>
  copilot: CopilotConfig
  params_override: Params
  status: 'stopped' | 'running' | 'error' | string
  webhook_secret: string
  last_error: string | null
  last_run_at: ISODate | null
  created_at: ISODate
  // computed
  strategy_name: string | null
  strategy_kind: string | null
  running: boolean
  halted_reason: string | null
  equity: number | null
  baseline_equity: number | null
  copilot_active: boolean
  copilot_enabled: boolean
  mode: BotMode
  ai_trader: AITraderIn | null
  universe?: UniverseRules | Record<string, never> | null
}

export type BotMode = 'ai_trader' | 'strategy' | 'tradingview'

export type VetoRule = 'any' | 'majority'

export interface AITraderIn {
  instructions: string
  reference_strategy_id: number | null
  min_confidence: number
  persona_id?: number | null // 交易大腦（投資大師）
  reviewer_ids?: number[] // 審查委員
  veto_rule?: VetoRule | string
}

/** 標的範圍（backend engine/universe.py UniverseRules）；空物件＝手動清單 */
export interface UniverseRules {
  mode: 'list' | 'rules' | string
  top_n?: number
  exclude_meme?: boolean
  exclude?: string[]
  include_only?: string[]
  refresh_hours?: number
}

export interface BotIn {
  name: string
  account_id: number
  strategy_id: number | null
  ai_trader?: AITraderIn | null
  ai_model_id: number | null
  symbols: string[]
  timeframe: string
  interval_sec: number
  risk: Partial<RiskConfig>
  copilot: Partial<CopilotConfig>
  params_override: Params
  universe?: UniverseRules | Record<string, never>
}

export interface Dashboard {
  bots_total: number
  bots_running: number
  total_equity: number
  realized_pnl_today: number
  trades_today: number
  bots: Bot[]
}

export interface PositionOut {
  instrument: string
  quantity: number
  entry_price: number
  leverage: number
  unrealized_pnl: number
  side: 'long' | 'short' | 'flat'
  mark_price: number | null
  stop_loss: number | null
  take_profit: number | null
}

export interface PositionsResponse {
  running: boolean
  positions: PositionOut[]
  balance: Balance | null
}

export type Action = 'open_long' | 'open_short' | 'close' | 'hold'

export interface ManualSignal {
  instrument: string
  action: Action
  size_pct?: number
  leverage?: number
  stop_loss?: number | null
  take_profit?: number | null
}

export interface CopilotReviewInfo {
  verdict: 'approve' | 'adjust' | 'veto' | string
  reasoning: string
  notes: string[]
  original: DecisionPayload
}

export interface DecisionPayload {
  instrument?: unknown
  action?: Action | string
  close_pct?: number
  new_stop?: number | null
  copilot?: CopilotReviewInfo
  size_pct?: number
  leverage?: number
  stop_loss?: number | null
  take_profit?: number | null
  confidence?: number
  reasoning?: string
  source?: string
  meta?: DecisionMeta | null
}

export interface CommitteeOpinion {
  name: string
  verdict: 'approve' | 'veto' | string
  reasoning: string
}

export interface DecisionMeta {
  persona?: string
  committee?: {
    rule: VetoRule | string
    vetoed: boolean
    proposed?: Action | string
    opinions: CommitteeOpinion[]
  }
}

export interface DecisionLog {
  id: number
  bot_id: number
  ts: ISODate
  instrument: string
  source: string
  action: string
  decision: DecisionPayload
  approved: boolean
  reasons: string[]
  ai_model: string | null
  ai_raw: string | null
  input_tokens: number
  output_tokens: number
}

export interface Trade {
  id: number
  bot_id: number
  decision_id: number | null
  ts: ISODate
  instrument: string
  side: 'buy' | 'sell' | string
  quantity: number
  price: number | null
  fee: number
  reduce_only: boolean
  realized_pnl: number | null
  order_id: string | null
  status: string
  source: string
  note: string | null
}

export interface EquityPoint {
  ts: ISODate
  equity: number
  baseline_equity: number | null
}

export type TuningStatus = 'proposed' | 'applied' | 'rejected' | 'failed' | string

export interface TuningRun {
  id: number
  bot_id: number
  ts: ISODate
  current_params: Params
  proposed_params: Params
  current_metrics: BacktestMetrics
  proposed_metrics: BacktestMetrics
  reasoning: string
  status: TuningStatus
  note: string | null
}

export interface IntelSettings {
  derivatives: boolean
  fear_greed: boolean
  news: boolean
  macro: boolean
  calendar: boolean
  rss_urls: string[]
  fred_api_key: string // masked
  cryptopanic_token: string // masked
}

export interface IntelSettingsIn {
  derivatives?: boolean
  fear_greed?: boolean
  news?: boolean
  macro?: boolean
  calendar?: boolean
  rss_urls?: string[]
  fred_api_key?: string | null
  cryptopanic_token?: string | null
}

export interface IntelNews {
  title: string
  source: string
  published: string | null
  url: string
}

export interface IntelMacro {
  label: string
  date: string
  value: number
  previous?: number
  yoy_pct?: number
}

export interface IntelEvent {
  title: string
  time: string
  country: string
  forecast: string | null
  previous: string | null
}

export interface IntelSnapshot {
  instrument: string
  fetched_at: string
  derivatives: {
    funding_rate_pct?: number
    next_funding?: string
    open_interest?: number
    long_short_ratio?: number
  } & Record<string, unknown>
  fear_greed: { value: number; label: string; yesterday: number | null } | null
  news: IntelNews[]
  macro: Record<string, IntelMacro>
  events: IntelEvent[]
  prompt: string
}

// ------------------------------ 投資大師（Persona） ------------------------------
export type PersonaRole = 'trader' | 'reviewer' | 'both'
export type PersonaSource = 'builtin' | 'upload' | 'distill'
export type PersonaStatus = 'draft' | 'paper_only' | 'active'
export type PersonaMarket = 'crypto' | 'us' | 'tw'

export interface FidelityDimension {
  name: string
  score: number
  max: number
  reason: string
}

export interface FidelityQuestion {
  type: 'stance' | 'out_of_scope' | 'scenario' | string
  question: string
  expected: string
  answer: string
}

export interface FidelityReport {
  score: number
  grade: 'A' | 'B' | 'C' | 'D' | string
  passed: boolean
  dimensions: FidelityDimension[]
  questions: FidelityQuestion[]
  summary: string
}

export interface CostEstimate {
  depth: 'quick' | 'standard' | string
  depth_label: string
  input_tokens: number
  output_tokens: number
  web_searches: number
  usd: number | null
  note: string
  model?: string
}

export interface OnchainSummary {
  fills: number
  period_days?: number
  top_coins?: [string, number][]
  open_trades?: number
  long_ratio_pct?: number | null
  trades_per_day?: number
  win_rate_pct?: number | null
  avg_win?: number | null
  avg_loss?: number | null
  total_closed_pnl?: number
  median_notional_usd?: number
  avg_hold_hours?: number | null
  most_active_utc_hours?: number[]
}

export interface PersonaMeta {
  files?: string[]
  truncated?: boolean
  filename?: string
  depth?: string
  model?: string
  onchain?: OnchainSummary | null
  hyperliquid_address?: string | null
  estimate?: CostEstimate
  [k: string]: unknown
}

export interface Persona {
  id: number
  slug: string | null
  name: string
  role: PersonaRole | string
  markets: (PersonaMarket | string)[]
  summary: string
  source: PersonaSource | string
  status: PersonaStatus | string
  status_label?: string
  fidelity: FidelityReport | Record<string, never>
  approved_at: ISODate | null
  paper_days: number
  min_paper_trades: number
  meta: PersonaMeta
  created_at: ISODate
  // computed
  paper_progress: PaperProgress | null
  profile_chars: number
  used_by_bots: number
  pass_score: number
}

export interface PersonaDetail extends Persona {
  profile: string
}

export interface PersonaUploadIn {
  filename: string
  content_base64: string
  role: PersonaRole | string
  markets: string[]
}

export interface PersonaDistillIn {
  name: string
  ai_model_id: number
  depth: 'quick' | 'standard'
  corpus?: string | null
  hyperliquid_address?: string | null
  role: PersonaRole | string
  markets: string[]
}

export interface PersonaUpdateIn {
  name: string
  role: PersonaRole | string
  markets: string[]
  summary: string
  profile: string
}

export const hasFidelity = (f: Persona['fidelity'] | null | undefined): f is FidelityReport =>
  !!f && typeof (f as FidelityReport).score === 'number'

export interface BacktestIn {
  strategy_id: number
  params?: Params | null
  ai_model_id?: number | null
  exchange_id: string
  symbol: string
  timeframe: string
  start: string
  end: string
  initial_cash: number
  fee_rate: number
  slippage: number
  risk: Partial<RiskConfig>
  max_ai_calls: number
}

/** Keys produced by backend backtest/engine.py compute_metrics (percentages already ×100). Older records may lack keys. */
export interface StrategyMetrics {
  // 報酬
  initial_equity?: number
  final_equity?: number
  total_return_pct?: number
  cagr_pct?: number
  buy_and_hold_pct?: number
  excess_return_pct?: number
  // 風險
  max_drawdown_pct?: number
  max_drawdown_bars?: number
  max_drawdown_days?: number
  volatility_pct?: number
  // 風險調整後
  sharpe?: number
  sortino?: number
  calmar?: number | null
  // 交易品質
  trades?: number
  closed_trades?: number
  win_rate_pct?: number
  payoff_ratio?: number | null
  profit_factor?: number | null
  expectancy?: number
  avg_win?: number
  avg_loss?: number
  best_trade?: number | null
  worst_trade?: number | null
  max_consecutive_losses?: number
  // 效率與成本
  avg_hold_hours?: number | null
  exposure_pct?: number
  total_fees?: number
  bars?: number
  days?: number
  // context (StrategyConfig.metrics only)
  symbol?: string
  timeframe?: string
  start?: string
  end?: string
  source?: 'backtest' | 'review' | string
}

/** @deprecated use StrategyMetrics */
export type BacktestMetrics = StrategyMetrics

export interface BacktestTrade {
  ts: number // ms
  side: 'buy' | 'sell' | string
  quantity: number
  price: number
  fee: number
  reduce_only: boolean
  realized_pnl: number | null
  reason: string
}

export interface BacktestSummary {
  id: number
  strategy_id: number | null
  strategy_name: string
  instrument: string
  timeframe: string
  config: Partial<BacktestIn> & Record<string, unknown>
  metrics: StrategyMetrics
  created_at: ISODate
}

export interface BacktestRun extends BacktestSummary {
  equity_curve: [number, number][]
  trades: BacktestTrade[]
  // only on POST response
  rejected?: string[]
  candles?: [number, number, number, number, number][]
}

// ------------------------------ Endpoints ------------------------------
export const api = {
  health: () => request<{ ok: boolean }>('GET', '/health', undefined, false),
  meta: () => get<Meta>('/meta'),

  // accounts
  listAccounts: () => get<Account[]>('/accounts'),
  createAccount: (b: AccountIn) => post<Account>('/accounts', b),
  updateAccount: (id: number, b: AccountIn) => put<Account>(`/accounts/${id}`, b),
  deleteAccount: (id: number) => del<{ ok: boolean }>(`/accounts/${id}`),
  testAccount: (id: number) => post<TestAccountResult>(`/accounts/${id}/test`),

  // ai models
  listAIModels: () => get<AIModel[]>('/ai-models'),
  createAIModel: (b: AIModelIn) => post<AIModel>('/ai-models', b),
  updateAIModel: (id: number, b: AIModelIn) => put<AIModel>(`/ai-models/${id}`, b),
  deleteAIModel: (id: number) => del<{ ok: boolean }>(`/ai-models/${id}`),
  testAIModel: (id: number) => post<TestAIResult>(`/ai-models/${id}/test`),

  // strategies
  listStrategies: () => get<Strategy[]>('/strategies'),
  createStrategy: (b: StrategyIn) => post<Strategy>('/strategies', b),
  updateStrategy: (id: number, b: StrategyIn) => put<Strategy>(`/strategies/${id}`, b),
  deleteStrategy: (id: number) => del<{ ok: boolean }>(`/strategies/${id}`),
  activateStrategy: (id: number) => post<Strategy>(`/strategies/${id}/activate`),
  validateStrategy: (code: string) => post<ValidateResult>('/strategies/validate', { code }),
  convertPine: (b: ConvertPineIn) => post<ConvertPineResult>('/strategies/convert-pine', b),
  reviewStrategy: (id: number, b: ReviewIn) => post<ReviewResult>(`/strategies/${id}/review`, b),
  fixStrategy: (id: number, b: ReviewIn) => post<FixResult>(`/strategies/${id}/fix`, b),
  promoteStrategy: (id: number) => post<Strategy>(`/strategies/${id}/promote`),

  // bots
  listBots: () => get<Bot[]>('/bots'),
  createBot: (b: BotIn) => post<Bot>('/bots', b),
  updateBot: (id: number, b: BotIn) => put<Bot>(`/bots/${id}`, b),
  deleteBot: (id: number) => del<{ ok: boolean }>(`/bots/${id}`),
  startBot: (id: number) => post<{ ok: boolean }>(`/bots/${id}/start`),
  stopBot: (id: number) => post<{ ok: boolean }>(`/bots/${id}/stop`),
  stopAll: () => post<{ stopped: number[] }>('/bots/stop-all'),
  regenWebhookSecret: (id: number) => post<{ webhook_secret: string }>(`/bots/${id}/regenerate-webhook-secret`),
  botPositions: (id: number) => get<PositionsResponse>(`/bots/${id}/positions`),
  botSignal: (id: number, b: ManualSignal) => post<DecisionLog>(`/bots/${id}/signal`, b),

  // personas（投資大師）
  listPersonas: () => get<Persona[]>('/personas'),
  getPersona: (id: number) => get<PersonaDetail>(`/personas/${id}`),
  uploadPersona: (b: PersonaUploadIn) => post<PersonaDetail>('/personas/upload', b),
  estimatePersona: (b: PersonaDistillIn) => post<CostEstimate>('/personas/estimate', b),
  distillPersona: (b: PersonaDistillIn) => post<PersonaDetail>('/personas/distill', b),
  personaFidelity: (id: number, ai_model_id: number) => post<PersonaDetail>(`/personas/${id}/fidelity`, { ai_model_id }),
  updatePersona: (id: number, b: PersonaUpdateIn) => put<PersonaDetail>(`/personas/${id}`, b),
  promotePersona: (id: number) => post<Persona>(`/personas/${id}/promote`),
  deletePersona: (id: number) => del<{ ok: boolean }>(`/personas/${id}`),

  // records
  trades: (botId?: number | null, limit = 200) =>
    get<Trade[]>(`/trades?limit=${limit}${botId ? `&bot_id=${botId}` : ''}`),
  decisions: (botId?: number | null, limit = 100, hideHold = false) =>
    get<DecisionLog[]>(`/decisions?limit=${limit}${botId ? `&bot_id=${botId}` : ''}${hideHold ? '&hide_hold=true' : ''}`),
  equity: (botId: number, hours = 168) => get<EquityPoint[]>(`/equity?bot_id=${botId}&hours=${hours}`),
  dashboard: () => get<Dashboard>('/dashboard'),

  // AI tuning
  tuning: (botId?: number | null) => get<TuningRun[]>(`/tuning${botId ? `?bot_id=${botId}` : ''}`),
  applyTuning: (id: number) => post<TuningRun>(`/tuning/${id}/apply`),
  tuneNow: (botId: number) => post<TuningRun>(`/bots/${botId}/tune-now`),

  // market intel
  intelSettings: () => get<IntelSettings>('/intel/settings'),
  saveIntelSettings: (b: IntelSettingsIn) => put<IntelSettings>('/intel/settings', b),
  intelSnapshot: (symbol: string, exchangeId: string) =>
    get<IntelSnapshot>(
      `/intel/snapshot?symbol=${encodeURIComponent(symbol)}&exchange_id=${encodeURIComponent(exchangeId)}`,
    ),

  // backtests
  runBacktest: (b: BacktestIn) => post<BacktestRun>('/backtests', b),
  listBacktests: () => get<BacktestSummary[]>('/backtests'),
  getBacktest: (id: number) => get<BacktestRun>(`/backtests/${id}`),
  deleteBacktest: (id: number) => del<{ ok: boolean }>(`/backtests/${id}`),
}

export function errMsg(e: unknown): string {
  if (e instanceof Error) return e.message
  return String(e)
}
