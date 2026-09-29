import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  type AIModel,
  type ConversionResult,
  type Params,
  type ReviewReport,
  type ReviewSettings,
  type Strategy,
  type ValidateResult,
} from '../../api'
import { POPULAR_COINS, ParamsForm, baseOf, buildPerp } from '../../components/forms'
import { MetricsStrip, hasMetrics } from '../../components/metrics'
import { Card, Collapsible, Empty, Field, Icons, ListRow, Modal, Skeleton, Spinner, useAction, useConfirm, useLoader, useToast } from '../../components/ui'
import { fmtNum, fmtTime, kindLabel } from '../../lib/format'
import { useMeta } from '../../lib/meta'

// ============================== shared bits ==============================
export function StrategyStatusBadge({ status, progress }: { status: string; progress?: Strategy['paper_progress'] }) {
  if (status === 'active') return <span className="badge badge-green">已啟用</span>
  if (status === 'paper_only')
    return (
      <span className="badge badge-blue" title="審查已通過；模擬期間只能用在模擬帳戶，期滿自動開放實盤">
        模擬期
        {progress && (
          <span className="num font-mono text-sky-200/80">
            ・模擬 {fmtNum(Math.min(progress.days, progress.days_required), 1)}/{progress.days_required} 天・成交 {progress.trades}/{progress.trades_required} 筆
          </span>
        )}
      </span>
    )
  return <span className="badge badge-gold">待審核</span>
}

const hasReview = (r: Strategy['review']): r is ReviewReport => !!r && Array.isArray((r as ReviewReport).stages)

const isAiTraderStrategy = (s: Strategy) => s.kind === 'ai' && s.name.startsWith('AI 交易員｜')

function kindIcon(kind: string) {
  if (kind === 'python') return <Icons.code className="h-5 w-5" />
  if (kind === 'tradingview') return <Icons.webhook className="h-5 w-5" />
  if (kind === 'ai') return <Icons.brain className="h-5 w-5" />
  return <Icons.chart className="h-5 w-5" />
}

/** Read an uploaded file as text */
function readFileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader()
    fr.onload = () => resolve(String(fr.result ?? ''))
    fr.onerror = () => reject(new Error('讀取檔案失敗'))
    fr.readAsText(file)
  })
}

const TV_CSV_HELP = 'TradingView → 策略測試器 → 交易清單 → 匯出（Export）。上傳後會逐筆比對進場，確認和 TradingView 結果一致。'

interface Form {
  id: number | null
  name: string
  kind: string
  params: Params
  code: string
  orig?: Strategy
}

type ReportTarget = { strategy: Strategy; conversion?: ConversionResult | null; settings?: ReviewSettings; autoFix?: boolean }

// ============================== page ==============================
export default function Strategies() {
  const meta = useMeta()
  const { data: all, loading, reload } = useLoader(() => api.listStrategies(), [])
  // AI 交易員的專屬策略由 Bot 設定管理，不在這裡顯示
  const data = all?.filter((s) => !isAiTraderStrategy(s))
  const creatableTypes = meta.strategy_types.filter((t) => t.type !== 'ai')
  const { data: models } = useLoader(() => api.listAIModels(), [])
  const [form, setForm] = useState<Form | null>(null)
  const [validation, setValidation] = useState<ValidateResult | null>(null)
  const [pineOpen, setPineOpen] = useState(false)
  const [report, setReport] = useState<ReportTarget | null>(null)
  const { busy, run } = useAction()
  const confirm = useConfirm()
  const toast = useToast()

  const typeMeta = (k: string) => meta.strategy_types.find((t) => t.type === k)

  const openCreate = (kind?: string) => {
    const k = kind ?? creatableTypes[0]?.type ?? 'python'
    setValidation(null)
    setForm({ id: null, name: '', kind: k, params: { ...(typeMeta(k)?.default_params ?? {}) }, code: meta.strategy_template })
  }
  const openEdit = (s: Strategy) => {
    setValidation(null)
    setForm({ id: s.id, name: s.name, kind: s.kind, params: { ...(s.params ?? {}) }, code: s.code ?? '', orig: s })
  }
  const changeKind = (k: string) => {
    if (!form) return
    setValidation(null)
    setForm({ ...form, kind: k, params: { ...(typeMeta(k)?.default_params ?? {}) }, code: form.code || meta.strategy_template })
  }

  const validate = async () => {
    if (!form) return
    const r = await run('validate', () => api.validateStrategy(form.code))
    if (!r) return
    setValidation(r)
    if (r.ok) {
      toast.success('程式碼檢查通過')
      // merge detected default params (keep user edits for existing keys)
      setForm((f) => (f ? { ...f, params: { ...r.default_params, ...pick(f.params, Object.keys(r.default_params)) } } : f))
    }
  }

  const save = async () => {
    if (!form) return
    if (!form.name.trim()) return toast.error('請輸入策略名稱')
    const body = {
      name: form.name.trim(),
      kind: form.kind,
      params: form.kind === 'tradingview' ? {} : form.params,
      code: form.kind === 'python' ? form.code : null,
    }
    const r = await run('save', () => (form.id ? api.updateStrategy(form.id, body) : api.createStrategy(body)), '已儲存')
    if (r) {
      setForm(null)
      reload()
      if (r.status === 'pending_review') toast.info('策略為「待審核」，請在清單按「審查報告」執行自動審查')
    }
  }

  const promote = async (s: Strategy) => {
    const p = s.paper_progress
    const ok = await confirm({
      title: `開放「${s.name}」實盤交易？`,
      confirmText: '開放實盤',
      danger: true,
      message: (
        <>
          <p>模擬期是用來確認策略在真實行情中的表現和回測一致。提前開放後，此策略可以用在<b className="text-slate-100">真實資金帳戶</b>，虧損由你自行承擔。</p>
          {p && (
            <p className="mt-2 text-xs text-muted">
              目前進度：模擬 {fmtNum(p.days, 1)}/{p.days_required} 天、成交 {p.trades}/{p.trades_required} 筆。期滿會自動開放，不需手動操作。
            </p>
          )}
        </>
      ),
    })
    if (!ok) return
    if (await run(`promote-${s.id}`, () => api.promoteStrategy(s.id), '已開放實盤')) reload()
  }

  const remove = async (s: Strategy) => {
    if (!(await confirm({ title: `刪除策略「${s.name}」？`, danger: true, confirmText: '刪除' }))) return
    if (await run(`del-${s.id}`, () => api.deleteStrategy(s.id), '已刪除')) reload()
  }

  const codeChanged = form?.orig && form.kind === 'python' && form.code !== (form.orig.code ?? '')

  return (
    <div className="space-y-5">
      {/* ---------- primary CTA ---------- */}
      <section className="card relative overflow-hidden border-gold/30">
        <div className="pointer-events-none absolute -right-16 -top-16 h-48 w-48 rounded-full bg-gold/10 blur-3xl" />
        <div className="relative flex flex-col gap-4 px-5 py-5 sm:flex-row sm:items-center">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-gold/15 text-gold">
            <Icons.sparkle className="h-6 w-6" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold text-slate-50">貼上 TradingView 策略</h2>
            <p className="mt-1 text-sm leading-relaxed text-muted">
              貼上 Pine Script，AI 會自動轉成 Python、自動審查，通過後先在模擬帳戶試跑。
            </p>
          </div>
          <button className="btn btn-primary px-5 py-2.5" onClick={() => setPineOpen(true)}>
            <Icons.code className="h-4 w-4" /> 貼上 Pine Script
          </button>
        </div>
      </section>

      {/* ---------- list ---------- */}
      <Card title="我的策略" icon={<Icons.chart />}>
        {loading ? (
          <Skeleton />
        ) : !data?.length ? (
          <Empty icon={<Icons.chart className="h-5 w-5" />} title="尚未建立策略" hint="從上方貼上 TradingView 策略開始，或在下方用其他方式建立。" />
        ) : (
          <div className="space-y-2.5">
            {data.map((s) => (
              <StrategyRow
                key={s.id}
                s={s}
                busy={busy}
                onReport={() => setReport({ strategy: s })}
                onFix={() => setReport({ strategy: s, autoFix: true })}
                onPromote={() => promote(s)}
                onEdit={() => openEdit(s)}
                onDelete={() => remove(s)}
              />
            ))}
          </div>
        )}
      </Card>

      {/* ---------- other creation methods ---------- */}
      <Collapsible title="其他方式：內建策略 / 自己寫 Python / TradingView 訊號（Webhook）" icon={<Icons.plus className="h-4 w-4" />}>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {[
            { kind: creatableTypes.find((t) => t.type !== 'python' && t.type !== 'tradingview')?.type, icon: <Icons.chart className="h-5 w-5" />, title: '內建策略', desc: '均線交叉、RSI 等現成策略，調參數即可使用。' },
            { kind: 'python', icon: <Icons.code className="h-5 w-5" />, title: '自己寫 Python', desc: '用範本撰寫自訂策略，建立後需通過審查。' },
            { kind: 'tradingview', icon: <Icons.webhook className="h-5 w-5" />, title: 'TradingView 訊號（Webhook）', desc: '由 TradingView Alert 直接下單，不需轉換程式碼。' },
          ]
            .filter((x) => x.kind && creatableTypes.some((t) => t.type === x.kind))
            .map((x) => (
              <button
                key={x.title}
                type="button"
                onClick={() => openCreate(x.kind)}
                className="flex items-start gap-3 rounded-xl border border-line bg-[#0f1216] px-4 py-3 text-left transition-colors hover:border-gold/50"
              >
                <span className="mt-0.5 text-muted">{x.icon}</span>
                <span>
                  <span className="block text-sm font-semibold text-slate-100">{x.title}</span>
                  <span className="mt-0.5 block text-xs text-muted">{x.desc}</span>
                </span>
              </button>
            ))}
        </div>
      </Collapsible>

      {/* ---------- create / edit ---------- */}
      <Modal
        open={!!form}
        onClose={() => setForm(null)}
        size={form?.kind === 'python' ? 'xl' : 'lg'}
        title={form?.id ? '編輯策略' : '新增策略'}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setForm(null)}>取消</button>
            <button className="btn btn-primary" onClick={save} disabled={busy === 'save'}>
              {busy === 'save' && <Spinner />} 儲存
            </button>
          </>
        }
      >
        {form && (
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="名稱">
                <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} autoFocus />
              </Field>
              <Field label="類型">
                <select className="input" value={form.kind} onChange={(e) => changeKind(e.target.value)} disabled={!!form.id}>
                  {(form.kind === 'ai' ? meta.strategy_types : creatableTypes).map((t) => (
                    <option key={t.type} value={t.type}>{kindLabel(t.type)}</option>
                  ))}
                </select>
              </Field>
            </div>
            <div className="rounded-lg border border-line bg-[#12161b] px-3 py-2 text-xs leading-relaxed text-muted">
              {typeMeta(form.kind)?.description}
              {typeMeta(form.kind)?.uses_ai && <span className="text-gold">（需要 AI 模型，會產生 API 費用）</span>}
            </div>

            {form.kind === 'tradingview' && (
              <div className="rounded-xl border border-sky-500/30 bg-sky-500/10 p-4 text-sm leading-relaxed text-sky-100">
                <div className="mb-1 flex items-center gap-2 font-semibold"><Icons.webhook /> 由 TradingView Webhook 觸發</div>
                此策略沒有參數，不會自行產生訊號。建立 Bot 並選擇此策略後，在 Bot 設定中可看到 Webhook 網址與 Alert 訊息範本，貼到 TradingView 的 Alert 設定即可。所有訊號同樣會經過風控。
              </div>
            )}

            {form.kind === 'python' && (
              <>
                <div className="rounded-lg border border-gold/30 bg-gold/10 px-3 py-2 text-xs text-amber-100">
                  {form.orig ? '修改程式碼後，策略會重設為「待審核」，需重新審查才能給 Bot 使用。' : '自訂 Python 策略建立後為「待審核」，請在清單按「審查報告」執行自動審查。'}
                  {codeChanged && <b className="ml-1">（程式碼已變更）</b>}
                </div>
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <label className="label mb-0">Python 程式碼</label>
                    <div className="flex gap-2">
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => setForm({ ...form, code: meta.strategy_template })}>
                        還原範本
                      </button>
                      <button type="button" className="btn btn-secondary btn-sm" onClick={validate} disabled={busy === 'validate'}>
                        {busy === 'validate' ? <Spinner className="h-3.5 w-3.5" /> : <Icons.check className="h-3.5 w-3.5" />} 檢查程式碼
                      </button>
                    </div>
                  </div>
                  <textarea
                    className="code-area h-[380px] resize-y whitespace-pre"
                    spellCheck={false}
                    value={form.code}
                    onChange={(e) => {
                      setForm({ ...form, code: e.target.value })
                      setValidation(null)
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Tab') {
                        e.preventDefault()
                        const t = e.currentTarget
                        const s = t.selectionStart
                        const v = form.code.slice(0, s) + '    ' + form.code.slice(t.selectionEnd)
                        setForm({ ...form, code: v })
                        requestAnimationFrame(() => (t.selectionStart = t.selectionEnd = s + 4))
                      }
                    }}
                  />
                </div>
                {validation && (
                  <div className={`rounded-lg border px-3 py-2 text-xs ${validation.ok ? 'border-up/30 bg-up/10 text-green-200' : 'border-down/30 bg-down/10 text-red-200'}`}>
                    {validation.ok ? (
                      <>
                        檢查通過｜策略 <b>{validation.name}</b>
                        {validation.uses_ai && <span className="text-gold">（使用 AI）</span>}，偵測到參數：
                        <span className="font-mono"> {JSON.stringify(validation.default_params)}</span>
                      </>
                    ) : (
                      <ul className="list-inside list-disc space-y-0.5">
                        {validation.errors.map((e, i) => <li key={i}>{e}</li>)}
                      </ul>
                    )}
                  </div>
                )}
                {form.orig?.pine_source && (
                  <Collapsible title="原始 Pine Script" icon={<Icons.code className="h-4 w-4" />}>
                    <pre className="code-area max-h-[300px] overflow-auto whitespace-pre">{form.orig.pine_source}</pre>
                  </Collapsible>
                )}
              </>
            )}

            {form.kind !== 'tradingview' && (form.kind !== 'python' || Object.keys(form.params).length > 0) && (
              <div>
                <div className="label">策略參數</div>
                <ParamsForm defaults={form.kind === 'python' ? form.params : typeMeta(form.kind)?.default_params ?? {}} value={form.params} onChange={(p) => setForm({ ...form, params: p })} />
              </div>
            )}
          </div>
        )}
      </Modal>

      <PineFlow
        open={pineOpen}
        onClose={() => setPineOpen(false)}
        models={models ?? []}
        timeframes={meta.timeframes}
        exchanges={meta.exchanges.filter((e) => e.market === 'crypto' && !e.planned).map((e) => ({ id: e.id, label: e.label }))}
        onCreated={(t) => {
          setPineOpen(false)
          setReport(t)
          reload()
        }}
      />

      {report && (
        <ReportModal
          key={report.strategy.id}
          target={report}
          models={models ?? []}
          onClose={() => setReport(null)}
          onChanged={reload}
        />
      )}
    </div>
  )
}

function pick(o: Params, keys: string[]): Params {
  const r: Params = {}
  for (const k of keys) if (k in o) r[k] = o[k]
  return r
}

// ============================== list row ==============================
function StrategyRow({
  s,
  busy,
  onReport,
  onFix,
  onPromote,
  onEdit,
  onDelete,
}: {
  s: Strategy
  busy: string | null
  onReport: () => void
  onFix: () => void
  onPromote: () => void
  onEdit: () => void
  onDelete: () => void
}) {
  const canBacktest = s.kind !== 'tradingview'
  const review = hasReview(s.review) ? s.review : null
  const canFix = s.kind === 'python' && s.status === 'pending_review' && !!s.pine_source && !!review && !review.passed
  return (
    <ListRow
      icon={kindIcon(s.kind)}
      title={s.name}
      subtitle={
        <>
          {kindLabel(s.kind)}
          {s.pine_source && <span className="text-muted"> · 由 Pine Script 轉換</span>}
        </>
      }
      badges={<StrategyStatusBadge status={s.status} progress={s.paper_progress} />}
      actions={
        <>
          {canFix && (
            <button className="btn btn-primary btn-sm" onClick={onFix}>
              <Icons.sparkle className="h-3.5 w-3.5" /> 讓 AI 修正
            </button>
          )}
          {s.status === 'paper_only' && (
            <button className="btn btn-secondary btn-sm" onClick={onPromote} disabled={busy === `promote-${s.id}`}>
              {busy === `promote-${s.id}` ? <Spinner className="h-3.5 w-3.5" /> : <Icons.bolt className="h-3.5 w-3.5" />} 開放實盤
            </button>
          )}
          {s.kind === 'python' && (
            <button className="btn btn-secondary btn-sm" onClick={onReport}>
              <Icons.shield className="h-3.5 w-3.5" /> 審查報告
            </button>
          )}
          {canBacktest && (
            <Link to={`/backtest?strategy=${s.id}`} className="btn btn-secondary btn-sm">
              <Icons.chart className="h-3.5 w-3.5" /> 回測
            </Link>
          )}
          <OverflowMenu
            items={[
              { label: '編輯', icon: <Icons.edit className="h-3.5 w-3.5" />, onClick: onEdit },
              { label: '刪除', icon: <Icons.trash className="h-3.5 w-3.5" />, onClick: onDelete, danger: true },
            ]}
          />
        </>
      }
    >
      {s.kind === 'tradingview' ? (
        <div className="text-xs text-muted">由 TradingView Alert 觸發下單，無法回測。</div>
      ) : hasMetrics(s.metrics) ? (
        <MetricsStrip m={s.metrics} />
      ) : (
        <div className="flex items-center gap-2 text-xs text-muted">
          尚未回測
          <Link to={`/backtest?strategy=${s.id}`} className="text-gold hover:underline">回測 →</Link>
        </div>
      )}
    </ListRow>
  )
}

export function OverflowMenu({ items }: { items: { label: string; icon?: ReactNode; onClick: () => void; danger?: boolean }[] }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])
  return (
    <div ref={ref} className="relative">
      <button className="btn btn-ghost btn-sm px-2" onClick={() => setOpen(!open)} aria-label="更多" aria-expanded={open}>
        <span className="text-base leading-none">⋯</span>
      </button>
      {open && (
        <div className="anim-fade absolute right-0 top-full z-20 mt-1 min-w-[120px] overflow-hidden rounded-lg border border-line bg-panel2 py-1 shadow-2xl">
          {items.map((it) => (
            <button
              key={it.label}
              type="button"
              className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-white/5 ${it.danger ? 'text-down' : 'text-slate-200'}`}
              onClick={() => {
                setOpen(false)
                it.onClick()
              }}
            >
              {it.icon}
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ============================== progress (single long request) ==============================
const REVIEW_STEPS = ['安全檢查', '試跑', 'AI 交叉檢查', 'TradingView 對帳']

function StepProgress({ steps, hasCsv, note }: { steps: string[]; hasCsv: boolean; note?: string }) {
  // The API is one request, so steps are animated as an estimate: advance every ~12s, park on the last one.
  const [i, setI] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setI((x) => Math.min(x + 1, steps.length - 1)), 12000)
    return () => clearInterval(t)
  }, [steps.length])
  return (
    <div className="mx-auto max-w-md py-6">
      <div className="mb-5 text-center">
        <Spinner className="mx-auto h-8 w-8 text-gold" />
        <div className="mt-3 text-sm text-slate-200">處理中，約需 1–2 分鐘，請勿關閉視窗</div>
        {note && <div className="mt-1 text-xs text-muted">{note}</div>}
      </div>
      <ol className="space-y-2">
        {steps.map((s, k) => {
          const done = k < i
          const active = k === i
          const skip = s === 'TradingView 對帳' && !hasCsv
          return (
            <li key={s} className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-sm ${active ? 'border-gold/40 bg-gold/[0.06] text-slate-100' : 'border-line text-muted'}`}>
              <span className={`flex h-5 w-5 items-center justify-center rounded-full ${done ? 'bg-up/20 text-up' : active ? 'text-gold' : 'bg-panel2 text-slate-600'}`}>
                {done ? <Icons.check className="h-3.5 w-3.5" /> : active ? <Spinner className="h-4 w-4" /> : <span className="text-[10px]">{k + 1}</span>}
              </span>
              {s}
              {skip && <span className="ml-auto text-[11px] text-slate-500">未上傳 CSV，將略過</span>}
            </li>
          )
        })}
      </ol>
    </div>
  )
}

// ============================== Pine flow ==============================
function PineFlow({
  open,
  onClose,
  models,
  timeframes,
  exchanges,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  models: AIModel[]
  timeframes: string[]
  exchanges: { id: string; label: string }[]
  onCreated: (t: ReportTarget) => void
}) {
  const [name, setName] = useState('')
  const [modelId, setModelId] = useState<number | ''>('')
  const [pine, setPine] = useState('')
  const [base, setBase] = useState('BTC')
  const [timeframe, setTimeframe] = useState('1h')
  const [exchange, setExchange] = useState('binance')
  const [csv, setCsv] = useState<{ name: string; text: string } | null>(null)
  const { busy, run } = useAction()
  const toast = useToast()
  const running = busy === 'convert'

  const convert = async () => {
    if (!name.trim() || !pine.trim()) return toast.error('請填寫策略名稱與 Pine Script')
    const mid = modelId || models[0]?.id
    if (!mid) return toast.error('請先在「AI 模型」新增一個模型')
    const settings: ReviewSettings = { exchange_id: exchange, symbol: buildPerp(base || 'BTC'), timeframe, tv_csv: csv?.text ?? null }
    const r = await run('convert', () => api.convertPine({ name: name.trim(), pine, ai_model_id: Number(mid), ...settings }))
    if (r) {
      setName('')
      setPine('')
      setCsv(null)
      onCreated({ strategy: { ...r.strategy, review: r.review ?? r.strategy.review }, conversion: r.conversion, settings: { ...settings, tv_csv: null } })
    }
  }

  const close = () => {
    if (running) return
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={close}
      size="lg"
      title={
        <span className="flex items-center gap-2">
          <Icons.sparkle className="h-4 w-4 text-gold" /> 貼上 TradingView 策略
        </span>
      }
      footer={
        running ? undefined : (
          <>
            <button className="btn btn-secondary" onClick={close}>取消</button>
            <button className="btn btn-primary" onClick={convert} disabled={!models.length}>
              <Icons.sparkle className="h-4 w-4" /> 轉換並審查
            </button>
          </>
        )
      }
    >
      {running ? (
        <StepProgress steps={['轉換中', ...REVIEW_STEPS]} hasCsv={!!csv} />
      ) : (
        <div className="space-y-4">
          {!models.length && (
            <div className="flex items-start gap-2 rounded-lg border border-gold/40 bg-gold/10 px-3 py-2 text-xs text-amber-100">
              <Icons.alert className="mt-0.5 h-4 w-4 shrink-0 text-gold" />
              <span>
                需要先新增一個 AI 模型才能轉換。
                <Link to="/settings?tab=ai" className="ml-1 text-gold underline" onClick={onClose}>前往 AI 模型設定</Link>
              </span>
            </div>
          )}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="策略名稱">
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：Supertrend" autoFocus />
            </Field>
            <Field label="AI 模型">
              <select className="input" value={modelId || models[0]?.id || ''} onChange={(e) => setModelId(e.target.value ? Number(e.target.value) : '')}>
                {models.length === 0 && <option value="">（請先新增 AI 模型）</option>}
                {models.map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
              </select>
            </Field>
          </div>
          <Field label="Pine Script">
            <textarea
              className="code-area h-[300px] resize-y whitespace-pre"
              spellCheck={false}
              value={pine}
              onChange={(e) => setPine(e.target.value)}
              placeholder={'//@version=5\nstrategy("My Strategy", overlay=true)\n...'}
            />
          </Field>
          <Collapsible title="進階（選填）">
            <div className="space-y-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <Field label="審查用交易對" hint="用這個幣的歷史 K 線試跑">
                  <div className="flex items-stretch overflow-hidden rounded-lg border border-line bg-[#0f1216] focus-within:border-gold/70">
                    <input
                      className="w-full min-w-0 bg-transparent px-3 py-2 font-mono text-sm uppercase text-slate-100 focus:outline-none"
                      value={base}
                      list="pine-coins"
                      onChange={(e) => setBase(e.target.value.toUpperCase().replace(/USDT$/, '').replace(/[^A-Z0-9]/g, ''))}
                      placeholder="BTC"
                    />
                    <span className="flex items-center whitespace-nowrap border-l border-line bg-panel2 px-2 font-mono text-[11px] text-muted">/USDT</span>
                  </div>
                  <datalist id="pine-coins">
                    {POPULAR_COINS.map((c) => <option key={c} value={c} />)}
                  </datalist>
                </Field>
                <Field label="K 線週期">
                  <select className="input" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                    {timeframes.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </Field>
                <Field label="交易所">
                  <select className="input" value={exchange} onChange={(e) => setExchange(e.target.value)}>
                    {!exchanges.some((e) => e.id === exchange) && <option value={exchange}>{exchange}</option>}
                    {exchanges.map((e) => (
                      <option key={e.id} value={e.id}>{e.label}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <Field label="TradingView 交易清單 CSV" hint={TV_CSV_HELP}>
                <CsvPicker value={csv} onChange={setCsv} />
              </Field>
            </div>
          </Collapsible>
        </div>
      )}
    </Modal>
  )
}

function CsvPicker({ value, onChange }: { value: { name: string; text: string } | null; onChange: (v: { name: string; text: string } | null) => void }) {
  const ref = useRef<HTMLInputElement>(null)
  const toast = useToast()
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input
        ref={ref}
        type="file"
        accept=".csv,text/csv"
        className="hidden"
        onChange={async (e) => {
          const f = e.target.files?.[0]
          e.target.value = ''
          if (!f) return
          try {
            onChange({ name: f.name, text: await readFileText(f) })
          } catch (err) {
            toast.error(err)
          }
        }}
      />
      <button type="button" className="btn btn-secondary btn-sm" onClick={() => ref.current?.click()}>
        <Icons.plus className="h-3.5 w-3.5" /> {value ? '換一個檔案' : '選擇 CSV 檔'}
      </button>
      {value && (
        <span className="inline-flex items-center gap-1 rounded-md border border-line bg-panel2 py-0.5 pl-2 pr-1 font-mono text-xs text-slate-200">
          {value.name}
          <button type="button" className="rounded p-0.5 text-muted hover:text-white" onClick={() => onChange(null)} aria-label="移除">
            <Icons.x className="h-3 w-3" />
          </button>
        </span>
      )}
    </div>
  )
}

// ============================== review report ==============================
function ReportModal({
  target,
  models,
  onClose,
  onChanged,
}: {
  target: ReportTarget
  models: AIModel[]
  onClose: () => void
  onChanged: () => void
}) {
  const [strategy, setStrategy] = useState<Strategy>(target.strategy)
  const [conversion, setConversion] = useState<ConversionResult | null>(target.conversion ?? null)
  const [modelId, setModelId] = useState<number | ''>(models[0]?.id ?? '')
  const [running, setRunning] = useState<null | { kind: 'fix' | 'review'; hasCsv: boolean }>(null)
  const [showCode, setShowCode] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const { run } = useAction()
  const confirm = useConfirm()
  const toast = useToast()
  const autoFixed = useRef(false)

  useEffect(() => {
    if (!modelId && models.length) setModelId(models[0].id)
  }, [models, modelId])

  const review = hasReview(strategy.review) ? strategy.review : null
  // 重審沿用上次審查的行情設定
  const settings: ReviewSettings = {
    exchange_id: target.settings?.exchange_id ?? review?.exchange_id ?? 'binance',
    symbol: target.settings?.symbol ?? review?.symbol ?? 'crypto:BTC/USDT:perp',
    timeframe: target.settings?.timeframe ?? review?.timeframe ?? '1h',
  }
  const tvStage = review?.stages.find((x) => x.key === 'tv')
  const canFix = !!strategy.pine_source && !!review && !review.passed
  const needCsv = !review || !tvStage || tvStage.status !== 'passed'

  const requireModel = (): number | null => {
    const mid = modelId || models[0]?.id
    if (!mid) {
      toast.error('請先在「AI 模型」新增一個模型')
      return null
    }
    return Number(mid)
  }

  const doFix = async () => {
    const mid = requireModel()
    if (!mid) return
    setRunning({ kind: 'fix', hasCsv: false })
    const r = await run('fix', () => api.fixStrategy(strategy.id, { ...settings, ai_model_id: mid }))
    setRunning(null)
    if (!r) return
    setStrategy({ ...r.strategy, review: r.review ?? r.strategy.review })
    setConversion(r.conversion)
    onChanged()
    if (!r.conversion.ok) toast.error('AI 修正後的程式碼仍有錯誤，請查看程式碼')
    else if (r.review?.passed) toast.success('修正後審查通過')
    else toast.info('已修正並重審，仍有未通過的項目')
  }

  const doReview = async (tvCsv: string | null) => {
    const mid = requireModel()
    if (!mid) return
    setRunning({ kind: 'review', hasCsv: !!tvCsv })
    const r = await run('review', () => api.reviewStrategy(strategy.id, { ...settings, tv_csv: tvCsv, ai_model_id: mid }))
    setRunning(null)
    if (!r) return
    setStrategy({ ...r.strategy, review: r.review ?? r.strategy.review })
    onChanged()
    if (r.review?.passed) toast.success('審查通過')
  }

  useEffect(() => {
    if (target.autoFix && !autoFixed.current && canFix && models.length) {
      autoFixed.current = true
      void doFix()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [models.length])

  const skipReview = async () => {
    const ok = await confirm({
      title: '略過審查，直接啟用？',
      danger: true,
      confirmText: '我了解風險，直接啟用',
      message: (
        <>
          自動審查沒有通過（或尚未執行）。直接啟用會跳過模擬期，策略可立即用在<b className="text-slate-100">真實資金帳戶</b>。
          AI 轉換的程式碼可能和原本的 Pine Script 行為不同，請確認你已看過程式碼並跑過回測。
        </>
      ),
    })
    if (!ok) return
    const r = await run('activate', () => api.activateStrategy(strategy.id), '已啟用')
    if (r) {
      setStrategy({ ...r, review: r.review ?? strategy.review })
      onChanged()
    }
  }

  const onCsv = async (f: File | undefined) => {
    if (!f) return
    try {
      await doReview(await readFileText(f))
    } catch (e) {
      toast.error(e)
    }
  }

  const close = () => {
    if (running) return
    onClose()
  }

  return (
    <Modal
      open
      onClose={close}
      size={showCode ? 'xl' : 'lg'}
      title={
        <span className="flex items-center gap-2">
          <Icons.shield className="h-4 w-4 text-gold" /> 審查報告｜{strategy.name}
        </span>
      }
      footer={
        running ? undefined : (
          <>
            {strategy.status === 'pending_review' && (
              <button className="btn btn-ghost btn-sm mr-auto text-xs text-muted" onClick={skipReview}>
                略過審查，直接啟用
              </button>
            )}
            {models.length > 1 && (canFix || needCsv || !review) && (
              <select className="input w-auto py-1.5 text-xs" value={modelId} onChange={(e) => setModelId(e.target.value ? Number(e.target.value) : '')} title="修正 / 重審使用的 AI 模型">
                {models.map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
              </select>
            )}
            {(strategy.code || strategy.pine_source) && (
              <button className="btn btn-secondary" onClick={() => setShowCode(!showCode)}>
                <Icons.code className="h-4 w-4" /> {showCode ? '收起程式碼' : '查看程式碼'}
              </button>
            )}
            {review && needCsv && (
              <button className="btn btn-secondary" onClick={() => fileRef.current?.click()}>
                <Icons.refresh className="h-4 w-4" /> 上傳 CSV 重新對帳
              </button>
            )}
            {!review && (
              <button className="btn btn-primary" onClick={() => doReview(null)}>
                <Icons.shield className="h-4 w-4" /> 執行審查
              </button>
            )}
            {canFix && (
              <button className="btn btn-primary" onClick={doFix}>
                <Icons.sparkle className="h-4 w-4" /> 讓 AI 修正並重審
              </button>
            )}
            {review?.passed && (
              <button className="btn btn-primary" onClick={onClose}>完成</button>
            )}
          </>
        )
      }
    >
      <input ref={fileRef} type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; void onCsv(f) }} />
      {running ? (
        <StepProgress
          steps={running.kind === 'fix' ? ['AI 修正中', ...REVIEW_STEPS] : REVIEW_STEPS}
          hasCsv={running.hasCsv}
          note={running.kind === 'fix' ? 'AI 會依審查報告的問題修改程式碼，再重新跑一次審查' : undefined}
        />
      ) : (
        <div className="space-y-4">
          <Verdict strategy={strategy} review={review} conversion={conversion} />

          {review && (
            <ol className="space-y-2">
              {review.stages.map((st) => (
                <StageItem key={st.key} stage={st} />
              ))}
            </ol>
          )}

          {conversion && conversion.notes.length > 0 && (
            <Collapsible title={`AI 轉換備註（${conversion.notes.length}）`}>
              <ul className="list-inside list-disc space-y-0.5 text-xs text-slate-300">
                {conversion.notes.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            </Collapsible>
          )}

          <div className="rounded-xl border border-line bg-[#0f1216] px-4 py-3">
            <div className="mb-2 text-xs font-semibold text-slate-300">績效摘要</div>
            {hasMetrics(strategy.metrics) ? (
              <MetricsStrip m={strategy.metrics} />
            ) : (
              <div className="text-xs text-muted">尚無績效資料</div>
            )}
            <Link to={`/backtest?strategy=${strategy.id}`} className="mt-2 inline-block text-xs text-gold hover:underline">
              完整回測與所有指標 →
            </Link>
          </div>

          {review && (
            <div className="text-[11px] text-slate-500">
              審查行情：{baseOf(review.symbol)} {review.timeframe} · {fmtTime(review.reviewed_at)}
            </div>
          )}

          {showCode && (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <div>
                <div className="label">Pine Script（原始）</div>
                <pre className="code-area h-[420px] overflow-auto whitespace-pre">{strategy.pine_source || '（此策略不是由 Pine Script 轉換）'}</pre>
              </div>
              <div>
                <div className="label">Python（AI 產生，唯讀）</div>
                <pre className="code-area h-[420px] overflow-auto whitespace-pre text-green-100/90">{strategy.code}</pre>
              </div>
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}

function Verdict({ strategy, review, conversion }: { strategy: Strategy; review: ReviewReport | null; conversion: ConversionResult | null }) {
  if (!review) {
    if (conversion && !conversion.ok) {
      return (
        <Banner tone="red" title="轉換失敗">
          AI 產生的程式碼無法通過檢查（嘗試 {conversion.attempts} 次），因此沒有進行審查。
          {conversion.errors.length > 0 && (
            <ul className="mt-1.5 list-inside list-disc space-y-0.5">
              {conversion.errors.map((e, i) => <li key={i}>{e}</li>)}
            </ul>
          )}
        </Banner>
      )
    }
    return (
      <Banner tone="amber" title="尚未審查">
        按「執行審查」會自動做安全檢查、試跑與 AI 交叉檢查，通過後進入模擬期。
      </Banner>
    )
  }
  if (!review.passed) {
    return (
      <Banner tone="red" title="審查未通過">
        策略維持「待審核」，不能交易。{strategy.pine_source ? '可以讓 AI 依下方問題修正後重審' : '請依下方問題修改程式碼後重審'}，或補上 TradingView CSV 對帳。
      </Banner>
    )
  }
  if (strategy.status === 'active') {
    return <Banner tone="green" title="審查通過・已開放實盤">此策略可以用在所有帳戶。</Banner>
  }
  const p = strategy.paper_progress
  return (
    <Banner tone="green" title="審查通過・已進入模擬期">
      接下來 {strategy.paper_days ?? 7} 天只能用在模擬帳戶，模擬成交滿 {strategy.min_paper_trades ?? 3} 筆後自動開放實盤。
      {p && (
        <div className="num mt-1 font-mono text-xs opacity-80">
          目前：模擬 {fmtNum(p.days, 1)}/{p.days_required} 天・成交 {p.trades}/{p.trades_required} 筆
        </div>
      )}
    </Banner>
  )
}

function Banner({ tone, title, children }: { tone: 'green' | 'red' | 'amber'; title: string; children?: ReactNode }) {
  const cls = {
    green: 'border-up/40 bg-up/10 text-green-100',
    red: 'border-down/40 bg-down/10 text-red-100',
    amber: 'border-gold/40 bg-gold/10 text-amber-100',
  }[tone]
  const icon = {
    green: <Icons.check className="h-6 w-6 text-up" />,
    red: <Icons.x className="h-6 w-6 text-down" />,
    amber: <Icons.alert className="h-6 w-6 text-gold" />,
  }[tone]
  return (
    <div className={`flex items-start gap-3 rounded-xl border px-4 py-3.5 ${cls}`}>
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0 text-sm leading-relaxed">
        <div className="text-base font-semibold">{title}</div>
        {children && <div className="mt-0.5 opacity-90">{children}</div>}
      </div>
    </div>
  )
}

function StageItem({ stage }: { stage: ReviewReport['stages'][number] }) {
  const [open, setOpen] = useState(stage.status === 'failed')
  const mark =
    stage.status === 'passed' ? (
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-up/15 text-up"><Icons.check className="h-4 w-4" /></span>
    ) : stage.status === 'failed' ? (
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-down/15 text-down"><Icons.x className="h-4 w-4" /></span>
    ) : (
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-panel2 text-slate-500">–</span>
    )
  const has = stage.details.length > 0
  return (
    <li className={`rounded-xl border ${stage.status === 'failed' ? 'border-down/30' : 'border-line'} bg-[#0f1216]`}>
      <button type="button" className="flex w-full items-start gap-3 px-4 py-2.5 text-left" onClick={() => has && setOpen(!open)} disabled={!has}>
        {mark}
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-slate-100">
            {stage.name}
            {stage.status === 'skipped' && <span className="ml-2 text-xs font-normal text-slate-500">已略過</span>}
          </div>
          <div className="mt-0.5 text-xs text-muted">{stage.summary}</div>
        </div>
        {has && <Icons.chevron className={`mt-1 h-4 w-4 text-muted transition-transform ${open ? 'rotate-90' : ''}`} />}
      </button>
      {has && open && (
        <ul className="anim-fade space-y-1 border-t border-line px-4 py-2.5 pl-[52px] text-xs leading-relaxed text-slate-300">
          {stage.details.map((d, i) => (
            <li key={i} className="list-disc">{d}</li>
          ))}
        </ul>
      )}
    </li>
  )
}
