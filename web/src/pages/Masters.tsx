import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipProps } from 'recharts'
import { api, errMsg, hasFidelity, type MasterPortfolio, type PortfolioPlan, type PortfolioResult } from '../api'
import { CHART } from '../components/charts'
import { CoinChipsInput, NumInput, Segmented } from '../components/forms'
import { BotStatusBadge, Card, Empty, Field, Icons, Modal, Skeleton, Spinner, Toggle, useLoader, useToast } from '../components/ui'
import { fmtNum, fmtShortTime, fmtSigned, fmtTime, parseDate, pnlClass } from '../lib/format'
import { GradeBadge, personaScoreText, universeLabel } from '../lib/persona'

const COLORS = ['#f0b90b', '#38bdf8', '#34d399', '#a78bfa', '#fb7185', '#fb923c', '#e2e8f0']
const TF_LABEL: Record<string, string> = { '15m': '15 分鐘', '1h': '1 小時', '4h': '4 小時', '1d': '日線' }

// ============================== overlay chart ==============================
function CurveTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-line bg-[#0f1216]/95 px-3 py-2 text-xs shadow-xl backdrop-blur">
      <div className="mb-1 font-mono text-muted">{fmtTime(label as number)}</div>
      {[...payload]
        .sort((a, b) => Number(b.value) - Number(a.value))
        .map((p) => (
          <div key={String(p.dataKey)} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5 text-slate-300">
              <span className="inline-block h-0.5 w-3" style={{ background: p.color }} />
              {p.name}
            </span>
            <span className={`num font-mono ${pnlClass(Number(p.value))}`}>{fmtSigned(Number(p.value), 2, '%')}</span>
          </div>
        ))}
    </div>
  )
}

function OverlayChart({ masters, colorOf }: { masters: MasterPortfolio[]; colorOf: (id: number) => string }) {
  const data = useMemo(() => {
    const rows = new Map<number, Record<string, number>>()
    for (const m of masters)
      for (const p of m.curve) {
        const t = parseDate(p.ts)?.getTime()
        if (!t) continue
        const row = rows.get(t) ?? { t }
        row[`b${m.bot_id}`] = p.pct
        rows.set(t, row)
      }
    return [...rows.values()].sort((a, b) => a.t - b.t)
  }, [masters])
  const withCurve = masters.filter((m) => m.curve.length > 0)
  if (!data.length) return <Empty title="組合還沒有權益資料" hint="組合啟動後每次輪詢會記錄一次權益。" />
  const spanMs = data[data.length - 1].t - data[0].t
  return (
    <div className="h-[300px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke={CHART.grid} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={(v: number) => (spanMs > 3 * 86400_000 ? fmtShortTime(v).slice(0, 5) : fmtShortTime(v).slice(6))}
            stroke={CHART.axis}
            tickLine={false}
            axisLine={{ stroke: CHART.grid }}
            minTickGap={40}
          />
          <YAxis stroke={CHART.axis} tickLine={false} axisLine={false} width={52} tickFormatter={(v: number) => `${fmtNum(v, 1)}%`} />
          <ReferenceLine y={0} stroke="#3a414b" strokeDasharray="4 4" />
          <Tooltip content={<CurveTooltip />} cursor={{ stroke: '#3a414b', strokeDasharray: '3 3' }} />
          <Legend verticalAlign="top" height={28} iconType="plainline" wrapperStyle={{ fontSize: 12, color: CHART.text }} />
          {withCurve.map((m) => (
            <Line
              key={m.bot_id}
              type="monotone"
              dataKey={`b${m.bot_id}`}
              name={m.persona?.name ?? m.name}
              stroke={colorOf(m.bot_id)}
              strokeWidth={2}
              dot={false}
              connectNulls
              isAnimationActive={false}
              activeDot={{ r: 3.5, stroke: CHART.surface, strokeWidth: 2 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

// ============================== master card ==============================
function Stat({ label, children, cls = 'text-slate-100' }: { label: string; children: ReactNode; cls?: string }) {
  return (
    <div>
      <div className="text-[10.5px] text-muted">{label}</div>
      <div className={`num font-mono text-sm font-semibold ${cls}`}>{children}</div>
    </div>
  )
}

function MasterCard({ m, color }: { m: MasterPortfolio; color: string }) {
  const scope = universeLabel(m.universe) ?? (m.symbols.length ? m.symbols.map((s) => s.replace(/^crypto:|\/USDT:perp$/g, '')).join('、') : '—')
  return (
    <div className="flex flex-col rounded-xl border border-line bg-[#0f1216] p-4">
      <div className="flex items-start gap-2.5">
        <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color }} />
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold text-slate-100">{m.persona?.name ?? m.name}</div>
          <div className="truncate text-xs text-muted">{m.name}</div>
        </div>
        <BotStatusBadge running={m.running} status={m.status} />
      </div>
      {m.plan?.style_summary && <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-slate-400">{m.plan.style_summary}</p>}
      <div className="mt-3 grid grid-cols-3 gap-x-3 gap-y-2.5">
        <Stat label="報酬" cls={pnlClass(m.return_pct)}>{m.return_pct === null ? '—' : fmtSigned(m.return_pct, 2, '%')}</Stat>
        <Stat label="最大回撤" cls={m.max_drawdown_pct ? 'text-down' : 'text-slate-100'}>{fmtNum(m.max_drawdown_pct, 2)}%</Stat>
        <Stat label="勝率">{m.win_rate_pct === null ? '—' : `${fmtNum(m.win_rate_pct, 1)}%`}</Stat>
        <Stat label="交易數">
          {m.closed_trades}
          <span className="text-[11px] font-normal text-muted"> 筆平倉</span>
        </Stat>
        <Stat label="持倉">{m.open_positions ?? '—'}</Stat>
        <Stat label="已實現損益" cls={pnlClass(m.realized_pnl)}>{fmtSigned(m.realized_pnl)}</Stat>
      </div>
      <div className="mb-3 mt-3 flex flex-wrap gap-1 text-[11px]">
        <span className="rounded border border-line px-1.5 py-0.5 text-slate-400">{TF_LABEL[m.timeframe] ?? m.timeframe}</span>
        <span className="rounded border border-line px-1.5 py-0.5 text-slate-400">{scope}</span>
        {m.risk.max_leverage ? <span className="rounded border border-line px-1.5 py-0.5 text-slate-400">最多 {m.risk.max_leverage}x</span> : null}
        {m.risk.long_only && <span className="rounded border border-line px-1.5 py-0.5 text-slate-400">只做多</span>}
      </div>
      <div className="mt-auto flex items-center justify-between gap-2 border-t border-line pt-3">
        <span className="text-[11px] text-muted">{m.equity !== null ? `權益 ${fmtNum(m.equity)}` : '尚無權益資料'}</span>
        <Link to={`/monitor?bot=${m.bot_id}`} className="btn btn-ghost btn-sm">
          在監控查看 <Icons.chevron className="h-3.5 w-3.5" />
        </Link>
      </div>
    </div>
  )
}

// ============================== new portfolio stepper ==============================
const STEPS = ['選大師', '確認組合計畫', '帳戶與資金']

function StepHeader({ step }: { step: number }) {
  return (
    <ol className="mb-4 flex items-center gap-2 text-xs">
      {STEPS.map((s, i) => (
        <li key={s} className="flex items-center gap-2">
          <span
            className={`flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-semibold ${
              i < step ? 'bg-up/20 text-up' : i === step ? 'bg-gold text-black' : 'bg-panel2 text-slate-500'
            }`}
          >
            {i < step ? <Icons.check className="h-3 w-3" /> : i + 1}
          </span>
          <span className={i === step ? 'text-slate-100' : 'text-muted'}>{s}</span>
          {i < STEPS.length - 1 && <span className="h-px w-5 bg-line sm:w-8" />}
        </li>
      ))}
    </ol>
  )
}

function PlanForm({ plan, onChange }: { plan: PortfolioPlan; onChange: (p: PortfolioPlan) => void }) {
  const set = (patch: Partial<PortfolioPlan>) => onChange({ ...plan, ...patch })
  const setU = (patch: Partial<PortfolioPlan['universe']>) => onChange({ ...plan, universe: { ...plan.universe, ...patch } })
  return (
    <div className="space-y-4">
      {!plan.suitable && (
        <div className="flex items-start gap-2 rounded-lg border border-gold/50 bg-gold/10 px-3 py-2 text-xs leading-relaxed text-amber-100">
          <Icons.alert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gold" />
          <span>這位大師不太適合加密貨幣永續合約，以下是最保守的版本，請自行決定要不要使用。</span>
        </div>
      )}
      {(plan.style_summary || plan.reason) && (
        <div className="rounded-xl border border-line bg-[#101318] p-3">
          {plan.style_summary && <div className="text-sm font-medium text-slate-100">{plan.style_summary}</div>}
          {plan.holding_period && <div className="mt-0.5 text-xs text-muted">持倉時間：{plan.holding_period}</div>}
          {plan.reason && <p className="mt-2 text-xs leading-relaxed text-slate-400">{plan.reason}</p>}
        </div>
      )}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Field label="K 線週期">
          <select className="input" value={plan.timeframe} onChange={(e) => set({ timeframe: e.target.value })}>
            {Object.entries(TF_LABEL).map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
        </Field>
        <Field label="最多持有">
          <NumInput value={plan.max_positions} step={1} onChange={(v) => set({ max_positions: Math.max(1, Math.min(20, Math.round(v ?? 3))) })} />
        </Field>
        <Field label="單一標的 %">
          <NumInput value={plan.position_pct} step={1} onChange={(v) => set({ position_pct: Math.max(1, Math.min(50, v ?? 10)) })} />
        </Field>
        <Field label="最大槓桿">
          <NumInput value={plan.max_leverage} step={1} onChange={(v) => set({ max_leverage: Math.max(1, Math.min(10, Math.round(v ?? 1))) })} />
        </Field>
      </div>
      <div>
        <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
          <span className="label mb-0">標的範圍</span>
          <Segmented value={plan.universe.mode === 'list' ? 'list' : 'rules'} options={[['rules', '依成交量挑選'], ['list', '固定幣種']] as const} onChange={(mode) => setU({ mode })} />
        </div>
        {plan.universe.mode === 'list' ? (
          <CoinChipsInput value={plan.universe.symbols} onChange={(symbols) => setU({ symbols })} placeholder="例如 BTC，按 Enter" />
        ) : (
          <div className="flex flex-wrap items-end gap-x-5 gap-y-3">
            <Field label="成交量前 N 名">
              <div className="w-24">
                <NumInput value={plan.universe.top_n} step={1} onChange={(v) => setU({ top_n: Math.max(1, Math.min(50, Math.round(v ?? 10))) })} />
              </div>
            </Field>
            <div className="pb-2">
              <Toggle checked={plan.universe.exclude_meme} onChange={(exclude_meme) => setU({ exclude_meme })} label="排除迷因幣" />
            </div>
            <Field label="只在這些幣裡挑（選填）" className="min-w-[200px] flex-1">
              <CoinChipsInput value={plan.universe.include_only} onChange={(include_only) => setU({ include_only })} placeholder="不填＝全部幣種" />
            </Field>
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <Toggle checked={plan.allow_short} onChange={(allow_short) => set({ allow_short })} label="可以做空" />
        <div className="flex items-center gap-2">
          <span className="text-sm text-slate-200">進場方式</span>
          <Segmented value={plan.entry_mode === 'smart' ? 'smart' : 'market'} options={[['market', '市價'], ['smart', '智慧掛單']] as const} onChange={(entry_mode) => set({ entry_mode })} />
        </div>
      </div>
      <Field label="交易偏好（給 AI 交易員）">
        <textarea className="input min-h-[84px]" value={plan.instructions} onChange={(e) => set({ instructions: e.target.value })} />
      </Field>
    </div>
  )
}

function NewPortfolioModal({ initialPersona, onClose, onCreated }: { initialPersona: number | null; onClose: () => void; onCreated: () => void }) {
  const { data: personas } = useLoader(() => api.listPersonas(), [])
  const { data: models } = useLoader(() => api.listAIModels(), [])
  const { data: accounts } = useLoader(() => api.listAccounts(), [])
  const toast = useToast()
  const [step, setStep] = useState(0)
  const [personaId, setPersonaId] = useState<number | ''>(initialPersona ?? '')
  const [modelId, setModelId] = useState<number | ''>('')
  const [plan, setPlan] = useState<PortfolioPlan | null>(null)
  const [accountId, setAccountId] = useState<number | ''>('')
  const [capital, setCapital] = useState<number>(10000)
  const [name, setName] = useState('')
  const [start, setStart] = useState(true)
  const [busy, setBusy] = useState<'plan' | 'create' | null>(null)
  const [result, setResult] = useState<PortfolioResult | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (personaId === '' && personas?.length) setPersonaId(personas[0].id)
  }, [personas, personaId])
  useEffect(() => {
    if (modelId === '' && models?.length) setModelId(models[0].id)
  }, [models, modelId])
  useEffect(() => {
    if (accountId === '' && accounts?.length) setAccountId((accounts.find((a) => a.paper) ?? accounts[0]).id)
  }, [accounts, accountId])

  const persona = personas?.find((p) => p.id === personaId)
  const account = accounts?.find((a) => a.id === accountId)

  const design = async () => {
    if (!personaId || !modelId) return toast.error('請選擇大師與 AI 模型')
    setErr(null)
    setBusy('plan')
    try {
      setPlan(await api.portfolioPlan(Number(personaId), Number(modelId)))
      setStep(1)
    } catch (e) {
      setErr(errMsg(e))
    } finally {
      setBusy(null)
    }
  }
  const create = async () => {
    if (!plan || !personaId || !modelId || !accountId) return
    if (plan.universe.mode === 'list' && !plan.universe.symbols.length) return toast.error('固定幣種至少要有一個')
    setErr(null)
    setBusy('create')
    try {
      const r = await api.createPortfolio(Number(personaId), {
        ai_model_id: Number(modelId),
        account_id: Number(accountId),
        plan,
        capital: account?.paper ? capital : null,
        name: name.trim() || null,
        start,
      })
      setResult(r)
      onCreated()
      if (r.started || !start) {
        toast.success(r.started ? '組合已建立並啟動' : '組合已建立')
        onClose()
      }
    } catch (e) {
      setErr(errMsg(e))
    } finally {
      setBusy(null)
    }
  }

  const footer =
    result && !result.started && start ? (
      <>
        <Link to={`/monitor?bot=${result.bot_id}`} className="btn btn-secondary">前往監控</Link>
        <button className="btn btn-primary" onClick={onClose}>完成</button>
      </>
    ) : busy === 'plan' ? undefined : (
      <>
        {step > 0 ? (
          <button className="btn btn-secondary" onClick={() => setStep(step - 1)} disabled={!!busy}>上一步</button>
        ) : (
          <button className="btn btn-secondary" onClick={onClose}>取消</button>
        )}
        {step === 0 && (
          <button className="btn btn-primary" onClick={design} disabled={!personaId || !modelId}>
            <Icons.sparkle className="h-4 w-4" /> 讓 AI 設計組合
          </button>
        )}
        {step === 1 && (
          <button className="btn btn-primary" onClick={() => setStep(2)}>下一步</button>
        )}
        {step === 2 && (
          <button className="btn btn-primary" onClick={create} disabled={!accountId || busy === 'create'}>
            {busy === 'create' && <Spinner />} {start ? '建立並啟動' : '建立組合'}
          </button>
        )}
      </>
    )

  return (
    <Modal open onClose={busy ? () => {} : onClose} size="lg" title="新增大師組合" footer={footer}>
      <StepHeader step={step} />
      {err && (
        <div className="mb-3 flex items-start gap-2 rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-xs text-red-200">
          <Icons.alert className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {err}
        </div>
      )}
      {result && !result.started && start ? (
        <div className="space-y-2 py-4 text-center">
          <div className="text-sm text-slate-100">組合已建立，但沒有啟動成功</div>
          <div className="mx-auto max-w-md rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-xs text-red-200">{result.error || '未知錯誤'}</div>
          <div className="text-xs text-muted">修正後可到「監控」頁手動啟動。</div>
        </div>
      ) : busy === 'plan' ? (
        <div className="py-10 text-center">
          <Spinner className="mx-auto h-8 w-8 text-gold" />
          <div className="mt-3 text-sm text-slate-200">AI 正在依「{persona?.name}」的思維設計組合…</div>
          <div className="mt-1 text-xs text-muted">約 20–60 秒（一次 AI 呼叫）</div>
        </div>
      ) : step === 0 ? (
        !personas ? (
          <Skeleton rows={2} />
        ) : !personas.length ? (
          <Empty
            icon={<Icons.brain className="h-5 w-5" />}
            title="還沒有投資大師"
            hint="先用女媧（nuwa-skill）蒸餾一位大師，再到「設定 → 投資大師」上傳。"
            action={
              <Link to="/settings?tab=personas" className="btn btn-primary btn-sm" onClick={onClose}>
                前往上傳
              </Link>
            }
          />
        ) : (
          <div className="space-y-4">
            <Field label="大師">
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {personas.map((p) => {
                  const on = p.id === personaId
                  return (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => setPersonaId(p.id)}
                      className={`flex items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-colors ${on ? 'border-gold/60 bg-gold/10' : 'border-line hover:border-slate-500'}`}
                    >
                      <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${on ? 'border-gold' : 'border-slate-500'}`}>
                        {on && <span className="h-2 w-2 rounded-full bg-gold" />}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-sm text-slate-100">{p.name}</span>
                      {hasFidelity(p.fidelity) ? <GradeBadge grade={p.fidelity.grade} score={p.fidelity.score} /> : <span className="text-[11px] text-muted">{personaScoreText(p)}</span>}
                    </button>
                  )
                })}
              </div>
            </Field>
            <Field label="AI 模型" hint="用來設計組合，之後也是這個組合的 AI 交易員模型">
              <select className="input" value={modelId} onChange={(e) => setModelId(e.target.value ? Number(e.target.value) : '')}>
                {!models?.length && <option value="">請先到「設定 → AI 模型」新增</option>}
                {models?.map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
              </select>
            </Field>
            <div className="text-xs text-muted">AI 會讀這位大師的思維檔案，設計最能發揮他風格的組合（週期、選幣、持倉數、倉位、槓桿、能否做空）。下一步可以修改。</div>
          </div>
        )
      ) : step === 1 && plan ? (
        <PlanForm plan={plan} onChange={setPlan} />
      ) : (
        <div className="space-y-4">
          <Field label="交易所帳戶">
            <select className="input" value={accountId} onChange={(e) => setAccountId(e.target.value ? Number(e.target.value) : '')}>
              {accounts?.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                  {a.paper ? '（模擬）' : '（實盤）'}
                </option>
              ))}
            </select>
          </Field>
          {account?.paper ? (
            <Field label="這個組合的模擬資金（USDT）" hint="每個大師組合各自獨立計算，方便比較績效">
              <NumInput value={capital} step={1000} onChange={(v) => setCapital(Math.max(100, v ?? 10000))} />
            </Field>
          ) : (
            account && (
              <div className="flex items-start gap-2 rounded-lg border border-gold/40 bg-gold/10 px-3 py-2 text-xs text-amber-100">
                <Icons.alert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gold" />
                實盤帳戶會使用帳戶的真實資金。{persona && persona.status !== 'active' && `「${persona.name}」尚未開放實盤，只能用在模擬帳戶。`}
              </div>
            )
          )}
          <Field label="組合名稱（選填）">
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={persona ? `${persona.name} 組合` : ''} />
          </Field>
          <Toggle checked={start} onChange={setStart} label="建立後立即啟動" />
        </div>
      )}
    </Modal>
  )
}

// ============================== page ==============================
export default function Masters() {
  const { data, loading, reload } = useLoader(() => api.masters(), [], 15_000)
  const [sp, setSp] = useSearchParams()
  const newParam = sp.get('new')
  const [creating, setCreating] = useState<{ persona: number | null } | null>(newParam !== null ? { persona: Number(newParam) || null } : null)
  useEffect(() => {
    if (newParam !== null) {
      setCreating({ persona: Number(newParam) || null })
      setSp({}, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newParam])
  const sorted = useMemo(() => [...(data ?? [])].sort((a, b) => (b.return_pct ?? -1e9) - (a.return_pct ?? -1e9)), [data])
  const colorOf = useMemo(() => {
    const ids = [...(data ?? [])].map((m) => m.bot_id).sort((a, b) => a - b)
    return (id: number) => COLORS[ids.indexOf(id) % COLORS.length]
  }, [data])

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-50">大師組合</h1>
          <p className="text-xs text-muted">每位大師用自己的風格，各自管理一個獨立的量化組合</p>
        </div>
        <button className="btn btn-primary" onClick={() => setCreating({ persona: null })}>
          <Icons.plus className="h-4 w-4" /> 新增大師組合
        </button>
      </div>

      {loading && !data ? (
        <div className="skeleton h-[340px] rounded-xl" />
      ) : !sorted.length ? (
        <div className="card">
          <div className="mx-auto max-w-lg py-10 text-center">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl border border-line bg-panel2 text-gold">
              <Icons.brain className="h-6 w-6" />
            </div>
            <div className="text-sm font-medium text-slate-200">還沒有大師組合</div>
            <p className="mt-1.5 text-xs leading-relaxed text-muted">
              每位大師會用他自己的風格——順勢或逆勢、持倉長短、集中或分散、槓桿與做空的態度——獨立管理一個量化組合，各自有獨立的模擬資金，方便比較誰的方法在現在的市場最有效。
            </p>
            <button className="btn btn-primary mt-4" onClick={() => setCreating({ persona: null })}>
              <Icons.plus className="h-4 w-4" /> 新增大師組合
            </button>
          </div>
        </div>
      ) : (
        <>
          <Card title="報酬比較" icon={<Icons.chart />} actions={<span className="text-xs text-muted">累積報酬 %</span>}>
            <OverlayChart masters={sorted} colorOf={colorOf} />
          </Card>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {sorted.map((m) => (
              <MasterCard key={m.bot_id} m={m} color={colorOf(m.bot_id)} />
            ))}
          </div>
        </>
      )}

      {creating && <NewPortfolioModal initialPersona={creating.persona} onClose={() => setCreating(null)} onCreated={reload} />}
    </div>
  )
}

