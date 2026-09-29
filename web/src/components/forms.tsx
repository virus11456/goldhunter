import { useState, type ReactNode } from 'react'
import type { CopilotConfig, Params, RiskConfig } from '../api'
import { paramLabel } from '../lib/format'
import { Collapsible, Field, Icons, Toggle } from './ui'

// ------------------------------ Symbols (USDT perpetual only) ------------------------------
export const POPULAR_COINS = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE']

export function buildPerp(base: string): string {
  return `crypto:${base.trim().toUpperCase()}/USDT:perp`
}

export function baseOf(inst: string): string {
  const m = /^crypto:([^/]+)\/USDT:perp$/i.exec(inst)
  return m ? m[1].toUpperCase() : inst
}

const cleanBase = (s: string) => s.toUpperCase().replace(/USDT$/, '').replace(/[^A-Z0-9]/g, '')

/** Multi-symbol chip input. Builds `crypto:{BASE}/USDT:perp`. */
export function SymbolsInput({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [text, setText] = useState('')
  const add = (b: string) => {
    const base = cleanBase(b)
    if (!base) return
    const s = buildPerp(base)
    if (!value.includes(s)) onChange([...value, s])
    setText('')
  }
  return (
    <div>
      <div className="flex min-h-[42px] flex-wrap items-center gap-1.5 rounded-lg border border-line bg-[#0f1216] px-2 py-1.5 focus-within:border-gold/70">
        {value.map((s) => (
          <span key={s} className="inline-flex items-center gap-1 rounded-md border border-gold/30 bg-gold/10 py-0.5 pl-2 pr-1 font-mono text-xs text-amber-100" title={s}>
            {baseOf(s)}
            <span className="text-muted">/USDT 永續</span>
            <button type="button" className="rounded p-0.5 text-muted hover:bg-black/30 hover:text-white" onClick={() => onChange(value.filter((x) => x !== s))} aria-label="移除">
              <Icons.x className="h-3 w-3" />
            </button>
          </span>
        ))}
        <input
          className="min-w-[110px] flex-1 bg-transparent px-1 py-1 font-mono text-sm uppercase text-slate-100 placeholder:normal-case placeholder:text-slate-500 focus:outline-none"
          placeholder={value.length ? '再加一個…' : '輸入幣種，如 BTC，按 Enter'}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ',' || e.key === ' ') {
              e.preventDefault()
              add(text)
            } else if (e.key === 'Backspace' && !text && value.length) {
              onChange(value.slice(0, -1))
            }
          }}
          onBlur={() => text && add(text)}
        />
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-slate-500">快速加入：</span>
        {POPULAR_COINS.filter((c) => !value.includes(buildPerp(c))).map((c) => (
          <button key={c} type="button" className="rounded-md border border-line px-2 py-0.5 font-mono text-[11px] text-muted transition-colors hover:border-gold/50 hover:text-gold" onClick={() => add(c)}>
            +{c}
          </button>
        ))}
      </div>
      <div className="hint">僅支援 USDT 永續合約，格式 crypto:BTC/USDT:perp</div>
    </div>
  )
}

/** Small segmented control */
export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: readonly (readonly [T, string])[]; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-line p-0.5">
      {options.map(([v, l]) => (
        <button key={v} type="button" className={`rounded-md px-3 py-1 text-sm transition-colors ${value === v ? 'bg-gold/15 text-gold' : 'text-muted hover:text-slate-200'}`} onClick={() => onChange(v)}>
          {l}
        </button>
      ))}
    </div>
  )
}

/** Bare coin chip input (e.g. ["DOGE", "PEPE"]) for universe rules */
export function CoinChipsInput({ value, onChange, placeholder = '輸入幣種，按 Enter' }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [text, setText] = useState('')
  const add = (b: string) => {
    const base = cleanBase(b)
    if (base && !value.includes(base)) onChange([...value, base])
    setText('')
  }
  return (
    <div className="flex min-h-[40px] flex-wrap items-center gap-1.5 rounded-lg border border-line bg-[#0f1216] px-2 py-1.5 focus-within:border-gold/70">
      {value.map((c) => (
        <span key={c} className="inline-flex items-center gap-1 rounded-md border border-line bg-panel2 py-0.5 pl-2 pr-1 font-mono text-xs text-slate-200">
          {c}
          <button type="button" className="rounded p-0.5 text-muted hover:bg-black/30 hover:text-white" onClick={() => onChange(value.filter((x) => x !== c))} aria-label={`移除 ${c}`}>
            <Icons.x className="h-3 w-3" />
          </button>
        </span>
      ))}
      <input
        className="min-w-[90px] flex-1 bg-transparent px-1 py-0.5 font-mono text-sm uppercase text-slate-100 placeholder:normal-case placeholder:text-slate-500 focus:outline-none"
        placeholder={value.length ? '' : placeholder}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',' || e.key === ' ') {
            e.preventDefault()
            add(text)
          } else if (e.key === 'Backspace' && !text && value.length) {
            onChange(value.slice(0, -1))
          }
        }}
        onBlur={() => text && add(text)}
      />
    </div>
  )
}

/** Single symbol picker for backtest / intel preview */
export function SymbolPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const base = baseOf(value)
  return (
    <div>
      <div className="flex items-stretch overflow-hidden rounded-lg border border-line bg-[#0f1216] focus-within:border-gold/70">
        <input
          className="w-full min-w-0 bg-transparent px-3 py-2 font-mono text-sm uppercase text-slate-100 focus:outline-none"
          value={base}
          onChange={(e) => onChange(buildPerp(cleanBase(e.target.value)))}
          placeholder="BTC"
        />
        <span className="flex items-center whitespace-nowrap border-l border-line bg-panel2 px-3 font-mono text-xs text-muted">/USDT 永續</span>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {POPULAR_COINS.map((c) => (
          <button
            key={c}
            type="button"
            className={`rounded-md border px-2 py-0.5 font-mono text-[11px] transition-colors ${base === c ? 'border-gold/60 bg-gold/15 text-gold' : 'border-line text-muted hover:border-gold/50 hover:text-gold'}`}
            onClick={() => onChange(buildPerp(c))}
          >
            {c}
          </button>
        ))}
      </div>
    </div>
  )
}

// ------------------------------ Number input helper ------------------------------
export function NumInput({
  value,
  onChange,
  placeholder,
  step = 'any',
  className = '',
}: {
  value: number | null | undefined
  onChange: (v: number | null) => void
  placeholder?: string
  step?: string | number
  className?: string
}) {
  const [text, setText] = useState<string>(value === null || value === undefined ? '' : String(value))
  const [last, setLast] = useState(value)
  if (value !== last) {
    setLast(value)
    const cur = text === '' ? null : Number(text)
    if (cur !== value) setText(value === null || value === undefined ? '' : String(value))
  }
  return (
    <input
      type="number"
      inputMode="decimal"
      step={step}
      className={`input num font-mono ${className}`}
      value={text}
      placeholder={placeholder}
      onChange={(e) => {
        const t = e.target.value
        setText(t)
        if (t === '') onChange(null)
        else if (!Number.isNaN(Number(t))) onChange(Number(t))
      }}
    />
  )
}

// ------------------------------ Risk ------------------------------
type RiskKey = keyof RiskConfig
const RISK_FIELDS: { key: RiskKey; label: string; kind: 'num' | 'bool'; hint?: string; step?: number; nullable?: boolean }[] = [
  { key: 'max_position_pct', label: '單筆倉位上限 %', kind: 'num', hint: '佔權益 %（未含槓桿）' },
  { key: 'max_total_exposure_pct', label: '總曝險上限 %', kind: 'num', hint: '名目價值佔權益 %（含槓桿）' },
  { key: 'max_leverage', label: '最大槓桿', kind: 'num', step: 1 },
  { key: 'daily_loss_limit_pct', label: '單日虧損熔斷 %', kind: 'num', hint: '達到後當日停止開倉；0＝關閉' },
  { key: 'default_stop_loss_pct', label: '預設止損 %', kind: 'num', hint: '訊號未帶止損時自動補上；留空＝直接拒絕', nullable: true },
  { key: 'min_confidence', label: '最低信心', kind: 'num', hint: '0 ~ 1', step: 0.05 },
  { key: 'max_orders_per_hour', label: '每小時下單上限', kind: 'num', step: 1 },
  { key: 'require_stop_loss', label: '強制止損', kind: 'bool' },
  { key: 'max_positions', label: '最多同時持有標的數', kind: 'num', step: 1, hint: '0＝不限' },
  { key: 'allow_pyramiding', label: '允許加碼', kind: 'bool', hint: '同方向已有持倉時仍可再開' },
  { key: 'long_only', label: '只做多', kind: 'bool', hint: '不開空單' },
  { key: 'exchange_stop', label: '交易所掛止損單', kind: 'bool', hint: '實盤帳戶：Bot 停止或主機斷線時持倉仍受保護' },
]

export function RiskForm({ value, defaults, onChange }: { value: Partial<RiskConfig>; defaults: RiskConfig; onChange: (v: Partial<RiskConfig>) => void }) {
  const get = <K extends RiskKey>(k: K): RiskConfig[K] => (k in value ? (value[k] as RiskConfig[K]) : defaults[k])
  const set = (k: RiskKey, v: unknown) => onChange({ ...value, [k]: v })
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {RISK_FIELDS.map((f) =>
        f.kind === 'bool' ? (
          <div key={f.key} className="flex flex-col justify-center">
            <Toggle checked={Boolean(get(f.key))} onChange={(v) => set(f.key, v)} label={f.label} />
            {f.hint && <div className="hint">{f.hint}</div>}
          </div>
        ) : (
          <Field key={f.key} label={f.label} hint={f.hint}>
            <NumInput
              value={get(f.key) as number | null}
              step={f.step ?? 'any'}
              placeholder={defaults[f.key] === null ? '留空' : String(defaults[f.key] ?? 0)}
              onChange={(v) => {
                if (v === null && !f.nullable) {
                  const { [f.key]: _omit, ...rest } = value
                  void _omit
                  onChange(rest)
                } else set(f.key, v)
              }}
            />
          </Field>
        ),
      )}
      <div className="sm:col-span-2">
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange({})}>
          <Icons.refresh className="h-3.5 w-3.5" /> 恢復預設值
        </button>
      </div>
    </div>
  )
}

// ------------------------------ Strategy params ------------------------------
export function ParamsForm({
  defaults,
  value,
  onChange,
}: {
  defaults: Params
  value: Params
  onChange: (v: Params) => void
}) {
  const keys = Array.from(new Set([...Object.keys(defaults), ...Object.keys(value)]))
  if (!keys.length) return <div className="text-sm text-muted">此策略沒有可調整的參數。</div>
  const set = (k: string, v: unknown) => onChange({ ...value, [k]: v })
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {keys.map((k) => {
        const def = defaults[k]
        const cur = k in value ? value[k] : def
        const label = (
          <span>
            {paramLabel(k)} <span className="font-mono text-[10px] text-slate-500">{k}</span>
          </span>
        )
        if (typeof def === 'boolean' || typeof cur === 'boolean') {
          return (
            <div key={k} className="flex items-center">
              <Toggle checked={Boolean(cur)} onChange={(v) => set(k, v)} label={label} />
            </div>
          )
        }
        if (typeof def === 'number' || typeof cur === 'number') {
          return (
            <Field key={k} label={label}>
              <NumInput value={cur as number} placeholder={String(def ?? '')} onChange={(v) => set(k, v ?? def)} />
            </Field>
          )
        }
        if (typeof def === 'string' || typeof cur === 'string' || def === null || def === undefined) {
          const long = k === 'instructions' || String(cur ?? '').length > 60
          return (
            <Field key={k} label={label} className={long ? 'sm:col-span-2' : ''}>
              {long ? (
                <textarea className="input min-h-[110px]" value={String(cur ?? '')} onChange={(e) => set(k, e.target.value)} />
              ) : (
                <input className="input" value={String(cur ?? '')} onChange={(e) => set(k, e.target.value)} />
              )}
            </Field>
          )
        }
        return (
          <Field key={k} label={label} hint="JSON 格式" className="sm:col-span-2">
            <JsonInput value={cur} onChange={(v) => set(k, v)} />
          </Field>
        )
      })}
    </div>
  )
}

function JsonInput({ value, onChange }: { value: unknown; onChange: (v: unknown) => void }) {
  const [text, setText] = useState(JSON.stringify(value))
  const [bad, setBad] = useState(false)
  return (
    <input
      className={`input font-mono ${bad ? 'border-down/70' : ''}`}
      value={text}
      onChange={(e) => {
        setText(e.target.value)
        try {
          onChange(JSON.parse(e.target.value))
          setBad(false)
        } catch {
          setBad(true)
        }
      }}
    />
  )
}

// ------------------------------ AI Co-pilot ------------------------------
function CopilotCard({
  letter,
  title,
  desc,
  checked,
  onToggle,
  children,
}: {
  letter: string
  title: string
  desc: string
  checked: boolean
  onToggle: (v: boolean) => void
  children?: ReactNode
}) {
  return (
    <div className={`rounded-xl border p-3.5 transition-all duration-200 ${checked ? 'border-gold/50 bg-gradient-to-br from-gold/[0.08] to-transparent shadow-[0_0_0_1px_rgba(240,185,11,0.08)]' : 'border-line bg-[#12161b]'}`}>
      <div className="flex items-start gap-3">
        <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg font-mono text-sm font-bold ${checked ? 'bg-gold text-black' : 'bg-panel2 text-muted'}`}>{letter}</div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <div className={`text-sm font-semibold ${checked ? 'text-gold' : 'text-slate-200'}`}>{title}</div>
            <Toggle checked={checked} onChange={onToggle} />
          </div>
          <p className="mt-1 text-xs leading-relaxed text-muted">{desc}</p>
        </div>
      </div>
      {checked && children && <div className="anim-fade mt-3 border-t border-line/70 pt-3">{children}</div>}
    </div>
  )
}

export function CopilotForm({
  value,
  defaults,
  onChange,
  strategyParams,
  strategyUsesAi,
}: {
  value: CopilotConfig
  defaults: CopilotConfig
  onChange: (v: CopilotConfig) => void
  strategyParams: Params
  strategyUsesAi: boolean
}) {
  const set = <K extends keyof CopilotConfig>(k: K, v: CopilotConfig[K]) => onChange({ ...value, [k]: v })
  const numericParams = Object.entries(strategyParams).filter(([, v]) => typeof v === 'number') as [string, number][]
  const num = (k: keyof CopilotConfig, label: string, hint?: string, step: number | string = 'any') => (
    <Field label={label} hint={hint}>
      <NumInput value={value[k] as number} step={step} placeholder={String(defaults[k])} onChange={(v) => set(k, (v ?? defaults[k]) as never)} />
    </Field>
  )

  return (
    <div className="space-y-3">
      {strategyUsesAi && (
        <div className="rounded-lg border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-xs text-sky-200">
          此策略本身就是 AI 交易員，訊號審核與持倉管理不會另外介入（事件避險仍有效）。
        </div>
      )}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <CopilotCard
          letter="A"
          title="訊號審核"
          desc="你的策略決定方向，AI 結合新聞、總經、合約數據判斷放行／否決／微調倉位與止損"
          checked={value.review}
          onToggle={(v) => set('review', v)}
        >
          <div className="grid grid-cols-1 gap-2.5">
            {num('review_min_confidence', 'AI 最低信心', '低於此值視為否決（0~1）', 0.05)}
            <div className="grid grid-cols-2 gap-2">
              {num('size_mult_min', '倉位倍數下限', '0 ~ 1', 0.1)}
              {num('size_mult_max', '倉位倍數上限', '1 ~ 3', 0.1)}
            </div>
          </div>
        </CopilotCard>
        <CopilotCard
          letter="B"
          title="持倉管理"
          desc="持倉中定期檢查，只能提前平倉、減倉或上移止損"
          checked={value.manage}
          onToggle={(v) => set('manage', v)}
        >
          {num('manage_interval_min', '檢查間隔（分鐘）', '最少 5 分鐘', 5)}
        </CopilotCard>
        <CopilotCard
          letter="C"
          title="參數微調"
          desc="定期提出新參數，樣本外回測較佳才套用"
          checked={value.tune}
          onToggle={(v) => set('tune', v)}
        >
          <div className="grid grid-cols-1 gap-2.5">
            <div className="grid grid-cols-2 gap-2">
              {num('tune_interval_hours', '間隔（小時）', '168＝每週', 1)}
              {num('tune_lookback_bars', '回測 K 棒數', '200 ~ 5000', 100)}
            </div>
            <Toggle checked={value.tune_auto_apply} onChange={(v) => set('tune_auto_apply', v)} label="通過回測後自動套用" />
            <div className="text-xs text-slate-500">關閉時只產生建議，需在監控頁手動按「套用」。</div>
          </div>
        </CopilotCard>
      </div>

      {value.tune && (
        <div className="anim-fade rounded-xl border border-line bg-[#12161b] p-3.5">
          <div className="mb-1 text-sm font-medium text-slate-200">允許 AI 調整的參數範圍</div>
          <div className="mb-3 text-xs text-muted">勾選的參數 AI 才能動，且只能落在 [最小, 最大] 範圍內。</div>
          {numericParams.length === 0 ? (
            <div className="text-xs text-slate-500">請先選擇有數值參數的策略。</div>
          ) : (
            <div className="space-y-2">
              {numericParams.map(([k, cur]) => {
                const r = value.tune_ranges[k]
                const on = Array.isArray(r)
                const setRange = (lo: number, hi: number) => set('tune_ranges', { ...value.tune_ranges, [k]: [lo, hi] })
                return (
                  <div key={k} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 sm:grid-cols-[minmax(0,1fr)_110px_110px]">
                    <label className="flex cursor-pointer items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-[#f0b90b]"
                        checked={on}
                        onChange={(e) => {
                          const next = { ...value.tune_ranges }
                          if (e.target.checked) {
                            const lo = cur > 0 ? +(cur * 0.5).toPrecision(4) : cur - 1
                            const hi = cur > 0 ? +(cur * 1.5).toPrecision(4) : cur + 1
                            next[k] = [lo, hi]
                          } else delete next[k]
                          set('tune_ranges', next)
                        }}
                      />
                      <span className="text-slate-200">{paramLabel(k)}</span>
                      <span className="font-mono text-[11px] text-slate-500">
                        {k} = {cur}
                      </span>
                    </label>
                    {on ? (
                      <div className="col-span-2 grid grid-cols-2 gap-2 sm:col-span-2">
                        <NumInput value={r[0]} placeholder="最小" onChange={(v) => setRange(v ?? 0, r[1])} />
                        <NumInput value={r[1]} placeholder="最大" onChange={(v) => setRange(r[0], v ?? 0)} />
                      </div>
                    ) : (
                      <span className="hidden text-xs text-slate-600 sm:col-span-2 sm:block">未開放</span>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[220px_minmax(0,1fr)]">
        <Field label="事件避險（分鐘）" hint="重大經濟事件前後 N 分鐘不開新倉；0＝關閉">
          <NumInput value={value.event_blackout_min} step={5} placeholder={String(defaults.event_blackout_min)} onChange={(v) => set('event_blackout_min', v ?? 0)} />
        </Field>
        <Field label="給 AI 的交易偏好說明" hint="例如：我偏好順勢，不要逆勢抄底">
          <textarea className="input min-h-[64px]" value={value.notes} onChange={(e) => set('notes', e.target.value)} placeholder="（選填）" />
        </Field>
      </div>
    </div>
  )
}

export function copilotNeedsAi(c: CopilotConfig | Partial<CopilotConfig>): boolean {
  return Boolean(c.review || c.manage || c.tune)
}

export { Collapsible }
