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
  // MRSPENCER v4.6B（名稱沿用 Pine 原稿的輸入欄位）
  k: '梯子後綴深度 k',
  addonOn: '啟用爬梯加碼',
  addonAdv: '加碼觸發：逆行 $',
  rangeMin: '區間長度（分鐘）',
  hiTh: '做空門檻 pos ≥',
  loTh: '做多門檻 pos ≤',
  useSess: '限制交易時段',
  sess: '時段（台北，HHMM-HHMM）',
  clockGate: '時鐘閘門',
  clockPer: '時鐘週期（分）',
  clockWin: '時鐘開放窗（分）',
  useFilt: '動能 + 滯留過濾',
  momTh: '動能門檻：30 分位移 $',
  dwellMin: '滯留門檻（分鐘）',
  tgtUsd: '出場目標 $',
  rescueOn: '同向攤平救援',
  rescueStep: '攤平間距 $',
  maxRescue: '攤平段數上限',
  tgtRescue: '攤平後清算目標 $',
  rescueGeo: '攤平幾何倍率',
  stopUsd: '整籃硬停損：逆行 $（0=關）',
  ddStopPct: '權益回撤 % 停損（0=關）',
  lotCap: '總口數上限',
  maxHoldMin: '逾時強平（分鐘，0=關）',
  coolBars: '停損後冷卻 K 棒數',
  tpCoolBars: '止盈後冷卻 K 棒數',
  relayOn: '配對救援',
  trapDD: '被套定義：逆行 $',
  relayScl: '配對規模（× 目前總倉）',
  maxRelay: '配對次數上限',
  relayAltHi: '替代觸發：空單 pos ≥',
  relayAltLo: '替代觸發：多單 pos ≤',
  trapCutMin: '配對逾時砍梯（分鐘，0=關）',
  unit: '1 口 = 幾顆幣',
  scale_with_equity: '口數隨權益放大',
  base_capital: '原策略設計資金（USD）',
  mintick: '最小跳動價位',
  disaster_buffer: '災難止損緩衝 $',
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
  mrspencer: 'MRSPENCER 黃金',
  ai: 'AI 交易員',
  python: '自訂 Python',
  tradingview: 'TradingView 訊號',
}

export function kindLabel(k: string | null | undefined): string {
  if (!k) return '—'
  return KIND_LABELS[k] ?? k
}
