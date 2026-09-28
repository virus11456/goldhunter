import type { ReactNode } from 'react'
import type { StrategyMetrics } from '../api'
import { fmtNum, fmtSigned } from '../lib/format'
import { baseOf } from './forms'

// ------------------------------ formatting ------------------------------
const isNum = (v: unknown): v is number => typeof v === 'number' && !Number.isNaN(v)
const DASH = <span className="text-muted">—</span>

type Tone = 'signed' | 'good-high' | 'bad' | 'plain'

/** Color by meaning: signed → green/red by sign; bad → always red when > 0 (drawdown, losses) */
function toneClass(v: number, tone: Tone, goodAt?: number): string {
  if (tone === 'signed') return v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-slate-200'
  if (tone === 'bad') return v > 0 ? 'text-down' : 'text-slate-200'
  if (tone === 'good-high' && goodAt !== undefined) return v >= goodAt ? 'text-up' : 'text-down'
  return 'text-slate-100'
}

interface Fmt {
  digits?: number
  suffix?: string
  signed?: boolean
  tone?: Tone
  goodAt?: number
}

function Val({ v, digits = 2, suffix = '', signed, tone = 'plain', goodAt }: { v: number | null | undefined } & Fmt) {
  if (!isNum(v)) return DASH
  const text = signed ? fmtSigned(v, digits, suffix) : `${fmtNum(v, digits)}${suffix}`
  return <span className={toneClass(v, tone, goodAt)}>{text}</span>
}

function holdText(h: number | null | undefined): ReactNode {
  if (!isNum(h)) return DASH
  return h >= 48 ? `${fmtNum(h / 24, 1)} 天` : `${fmtNum(h, 1)} 小時`
}

// ------------------------------ definitions ------------------------------
interface Def {
  key: keyof StrategyMetrics
  label: string
  tip: string
  render: (m: StrategyMetrics) => ReactNode
}

const d = (key: keyof StrategyMetrics, label: string, tip: string, fmt: Fmt = {}): Def => ({
  key,
  label,
  tip,
  render: (m) => <Val v={m[key] as number | null | undefined} {...fmt} />,
})

const GROUPS: { title: string; items: Def[] }[] = [
  {
    title: '報酬',
    items: [
      d('total_return_pct', '總報酬', '整段回測期間的資金成長百分比。', { signed: true, suffix: '%', tone: 'signed' }),
      d('cagr_pct', '年化報酬', '把總報酬換算成每年的複利報酬率（CAGR）。', { signed: true, suffix: '%', tone: 'signed' }),
      d('buy_and_hold_pct', '買入持有', '同期間一開始買進、一直不動的報酬，作為比較基準。', { signed: true, suffix: '%', tone: 'signed' }),
      d('excess_return_pct', '超額報酬', '策略總報酬減去買入持有，正數代表策略跑贏大盤。', { signed: true, suffix: '%', tone: 'signed' }),
    ],
  },
  {
    title: '風險',
    items: [
      d('max_drawdown_pct', '最大回撤', '資金從高點往下跌的最大幅度，越小越好。', { suffix: '%', tone: 'bad' }),
      d('max_drawdown_days', '最長回撤期間', '資金低於前高、還沒創新高的最長時間。', { digits: 1, suffix: ' 天' }),
      d('volatility_pct', '年化波動率', '權益報酬的年化標準差，數字越大起伏越劇烈。', { suffix: '%' }),
    ],
  },
  {
    title: '風險調整後',
    items: [
      d('sharpe', 'Sharpe', '每承擔一單位波動所換到的報酬；> 1 算不錯，> 2 很好。', { tone: 'good-high', goodAt: 1 }),
      d('sortino', 'Sortino', '和 Sharpe 類似，但只把下跌的波動當成風險。', { tone: 'good-high', goodAt: 1 }),
      d('calmar', 'Calmar', '年化報酬除以最大回撤；越高代表賺同樣的錢承受的回撤越小。', { tone: 'good-high', goodAt: 1 }),
    ],
  },
  {
    title: '交易品質',
    items: [
      {
        key: 'trades',
        label: '交易筆數',
        tip: '所有成交筆數（含開倉與平倉），括號內為已平倉筆數。',
        render: (m) =>
          isNum(m.trades) ? (
            <span className="text-slate-100">
              {m.trades}
              {isNum(m.closed_trades) && <span className="text-xs text-muted">（已平倉 {m.closed_trades}）</span>}
            </span>
          ) : (
            DASH
          ),
      },
      d('win_rate_pct', '勝率', '已平倉交易中獲利的比例。', { digits: 1, suffix: '%', tone: 'good-high', goodAt: 50 }),
      d('payoff_ratio', '盈虧比', '平均獲利除以平均虧損；> 1 代表賺的時候比賠的時候多。', { tone: 'good-high', goodAt: 1 }),
      d('profit_factor', '獲利因子', '總獲利除以總虧損；> 1 才代表整體賺錢，> 1.5 算穩健。', { tone: 'good-high', goodAt: 1 }),
      d('expectancy', '每筆期望值', '平均每一筆平倉交易賺或賠多少（USDT）。', { signed: true, tone: 'signed' }),
      {
        key: 'best_trade',
        label: '最大單筆獲利 / 虧損',
        tip: '單筆平倉交易中賺最多與賠最多的金額（USDT）。',
        render: (m) => (
          <span>
            <Val v={m.best_trade} signed tone="signed" />
            <span className="text-muted"> / </span>
            <Val v={m.worst_trade} signed tone="signed" />
          </span>
        ),
      },
      d('max_consecutive_losses', '最多連續虧損', '連續虧損的最多筆數，用來評估心理與資金壓力。', { digits: 0, suffix: ' 筆' }),
    ],
  },
  {
    title: '效率與成本',
    items: [
      { key: 'avg_hold_hours', label: '平均持倉', tip: '每筆交易從開倉到平倉的平均時間。', render: (m) => <span className="text-slate-100">{holdText(m.avg_hold_hours)}</span> },
      d('exposure_pct', '持倉時間佔比', '有持倉的 K 線佔全部 K 線的比例，越低代表資金閒置越多。', { digits: 1, suffix: '%' }),
      d('total_fees', '總手續費', '整段期間支付的手續費總額（USDT）。'),
    ],
  },
]

// ------------------------------ Full panel ------------------------------
function Headline({ label, tip, children, sub }: { label: string; tip: string; children: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-panel px-3.5 py-3">
      <div className="cursor-help text-[10.5px] font-medium uppercase tracking-[0.1em] text-muted" title={tip}>{label}</div>
      <div className="num mt-1.5 font-mono text-xl font-semibold text-slate-50">{children}</div>
      {sub && <div className="num mt-0.5 text-[11px] text-muted">{sub}</div>}
    </div>
  )
}

/** Full grouped metrics (Backtest page) */
export function MetricsPanel({ m }: { m: StrategyMetrics | null | undefined }) {
  const x = m ?? {}
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Headline label="總報酬" tip="整段回測期間的資金成長百分比，下方為同期間買入持有的報酬。" sub={<>買入持有 <Val v={x.buy_and_hold_pct} signed suffix="%" tone="signed" /></>}>
          <Val v={x.total_return_pct} signed suffix="%" tone="signed" />
        </Headline>
        <Headline label="最大回撤" tip="資金從高點往下跌的最大幅度，越小越好。" sub={isNum(x.max_drawdown_days) ? `最長 ${fmtNum(x.max_drawdown_days, 1)} 天未創新高` : undefined}>
          <Val v={x.max_drawdown_pct} suffix="%" tone="bad" />
        </Headline>
        <Headline label="Sharpe" tip="每承擔一單位波動所換到的報酬；> 1 算不錯，> 2 很好。" sub={isNum(x.sortino) ? `Sortino ${fmtNum(x.sortino)}` : undefined}>
          <Val v={x.sharpe} tone="good-high" goodAt={1} />
        </Headline>
        <Headline label="勝率" tip="已平倉交易中獲利的比例。" sub={isNum(x.closed_trades) ? `已平倉 ${x.closed_trades} 筆` : undefined}>
          <Val v={x.win_rate_pct} digits={1} suffix="%" />
        </Headline>
      </div>

      <section className="card">
        <header className="card-header">
          <h2 className="card-title">績效指標</h2>
          {isNum(x.bars) && (
            <span className="num text-xs text-muted">
              K 棒 {x.bars} 根{isNum(x.days) ? ` · ${fmtNum(x.days, 1)} 天` : ''}
              {isNum(x.final_equity) ? ` · 期末權益 ${fmtNum(x.final_equity)}` : ''}
            </span>
          )}
        </header>
        <div className="grid grid-cols-1 gap-x-8 gap-y-5 px-5 py-4 sm:grid-cols-2 xl:grid-cols-3">
          {GROUPS.map((g) => (
            <div key={g.title}>
              <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-gold/90">{g.title}</div>
              <dl className="divide-y divide-line/60">
                {g.items.map((it) => (
                  <div key={it.label} className="flex items-baseline justify-between gap-3 py-1.5 text-sm">
                    <dt className="cursor-help text-muted underline decoration-dotted decoration-slate-600 underline-offset-4" title={it.tip}>
                      {it.label}
                    </dt>
                    <dd className="num text-right font-mono tabular-nums">{it.render(x)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}

// ------------------------------ Compact strip ------------------------------
const STRIP: { label: string; tip: string; render: (m: StrategyMetrics) => ReactNode }[] = [
  { label: '總報酬', tip: '整段期間的資金成長百分比。', render: (m) => <Val v={m.total_return_pct} signed suffix="%" tone="signed" /> },
  { label: '年化', tip: '換算成每年的複利報酬率。', render: (m) => <Val v={m.cagr_pct} signed suffix="%" tone="signed" /> },
  { label: '最大回撤', tip: '資金從高點往下跌的最大幅度。', render: (m) => <Val v={m.max_drawdown_pct} suffix="%" tone="bad" /> },
  { label: 'Sharpe', tip: '風險調整後報酬；> 1 算不錯。', render: (m) => <Val v={m.sharpe} tone="good-high" goodAt={1} /> },
  { label: '勝率', tip: '已平倉交易中獲利的比例。', render: (m) => <Val v={m.win_rate_pct} digits={1} suffix="%" /> },
  { label: '獲利因子', tip: '總獲利 ÷ 總虧損；> 1 才賺錢。', render: (m) => <Val v={m.profit_factor} tone="good-high" goodAt={1} /> },
  { label: '交易數', tip: '成交筆數（含開倉與平倉）。', render: (m) => (isNum(m.trades) ? <span className="text-slate-100">{m.trades}</span> : DASH) },
]

export function hasMetrics(m: StrategyMetrics | null | undefined): m is StrategyMetrics {
  return !!m && isNum(m.total_return_pct)
}

/** 「最近回測：BTC 1h」/「審查試跑：BTC 1h」 */
export function metricsCaption(m: StrategyMetrics): string {
  const src = m.source === 'review' ? '審查試跑' : '最近回測'
  const where = [m.symbol ? baseOf(m.symbol) : null, m.timeframe].filter(Boolean).join(' ')
  const range = m.start && m.end ? `（${m.start.slice(0, 10)} → ${m.end.slice(0, 10)}）` : ''
  return `${src}${where ? `：${where}` : ''}${range}`
}

/** Compact metrics row (strategy list / review result). Caller handles the empty state. */
export function MetricsStrip({ m, caption = true }: { m: StrategyMetrics; caption?: boolean }) {
  return (
    <div>
      <div className="grid grid-cols-4 gap-x-3 gap-y-2 sm:grid-cols-7">
        {STRIP.map((s) => (
          <div key={s.label} className="min-w-0">
            <div className="cursor-help truncate text-[10.5px] text-muted" title={s.tip}>{s.label}</div>
            <div className="num truncate font-mono text-sm font-medium tabular-nums">{s.render(m)}</div>
          </div>
        ))}
      </div>
      {caption && <div className="mt-1.5 text-[11px] text-slate-500">{metricsCaption(m)}</div>}
    </div>
  )
}
