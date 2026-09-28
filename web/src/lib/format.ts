/** Backend datetimes are naive UTC ISO strings — treat them as UTC. */
export function parseDate(v: string | number | null | undefined): Date | null {
  if (v === null || v === undefined || v === '') return null
  if (typeof v === 'number') return new Date(v)
  const hasTz = /([zZ]|[+-]\d{2}:?\d{2})$/.test(v)
  const d = new Date(hasTz ? v : `${v}Z`)
  return isNaN(d.getTime()) ? null : d
}

const pad = (n: number) => String(n).padStart(2, '0')

export function fmtTime(v: string | number | null | undefined, withSeconds = false): string {
  const d = parseDate(v)
  if (!d) return '—'
  const s = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  return withSeconds ? `${s}:${pad(d.getSeconds())}` : s
}

export function fmtShortTime(v: string | number | null | undefined): string {
  const d = parseDate(v)
  if (!d) return '—'
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function timeAgo(v: string | null | undefined): string {
  const d = parseDate(v)
  if (!d) return '—'
  const s = Math.round((Date.now() - d.getTime()) / 1000)
  if (s < 60) return `${Math.max(0, s)} 秒前`
  if (s < 3600) return `${Math.round(s / 60)} 分鐘前`
  if (s < 86400) return `${Math.round(s / 3600)} 小時前`
  return `${Math.round(s / 86400)} 天前`
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** Price-like numbers: adaptive precision */
export function fmtPrice(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const a = Math.abs(v)
  const d = a >= 1000 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 5 : 8
  return v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: d })
}

export function fmtQty(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toLocaleString('en-US', { maximumFractionDigits: 8 })
}

export function fmtSigned(v: number | null | undefined, digits = 2, suffix = ''): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const s = fmtNum(Math.abs(v), digits)
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${s}${suffix}`
}

export function pnlClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return 'text-slate-300'
  return v > 0 ? 'text-up' : 'text-down'
}

/** "crypto:BTC/USDT:perp" -> "BTCUSDT" */
export function shortSymbol(inst: string): string {
  const parts = inst.split(':')
  const sym = parts.length === 3 ? parts[1] : inst
  return sym.replace('/', '')
}

export function paramLabel(k: string): string {
  return PARAM_LABELS[k] ?? k
}

const PARAM_LABELS: Record<string, string> = {
  fast: '快線週期',
  slow: '慢線週期',
  size_pct: '倉位 %',
  atr_mult: 'ATR 倍數',
  allow_short: '允許做空',
  leverage: '槓桿',
  length: 'RSI 週期',
  oversold: '超賣門檻',
  exit: '出場門檻',
  stop_pct: '止損 %',
  instructions: '策略指示',
  bars: '提供 K 棒數',
  min_confidence: '最低信心',
}

export const KIND_LABELS: Record<string, string> = {
  ma_cross: '均線交叉',
  rsi_reversion: 'RSI 均值回歸',
  ai: 'AI 交易員',
  python: '自訂 Python',
  tradingview: 'TradingView 訊號',
}

export function kindLabel(k: string | null | undefined): string {
  if (!k) return '—'
  return KIND_LABELS[k] ?? k
}
