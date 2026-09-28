import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ConvertPineResult, type Params, type Strategy, type ValidateResult } from '../../api'
import { ParamsForm } from '../../components/forms'
import { Card, Collapsible, Empty, Field, Icons, ListRow, Modal, Skeleton, Spinner, useAction, useConfirm, useLoader, useToast } from '../../components/ui'
import { kindLabel, paramLabel } from '../../lib/format'
import { useMeta } from '../../lib/meta'

export function StrategyStatusBadge({ status }: { status: string }) {
  return status === 'active' ? <span className="badge badge-green">已啟用</span> : <span className="badge badge-gold">待審核</span>
}

interface Form {
  id: number | null
  name: string
  kind: string
  params: Params
  code: string
  orig?: Strategy
}

export default function Strategies() {
  const meta = useMeta()
  const { data, loading, reload } = useLoader(() => api.listStrategies(), [])
  const { data: models } = useLoader(() => api.listAIModels(), [])
  const [form, setForm] = useState<Form | null>(null)
  const [validation, setValidation] = useState<ValidateResult | null>(null)
  const [pineOpen, setPineOpen] = useState(false)
  const { busy, run } = useAction()
  const confirm = useConfirm()
  const toast = useToast()

  const typeMeta = (k: string) => meta.strategy_types.find((t) => t.type === k)

  const openCreate = () => {
    const t = meta.strategy_types[0]
    setValidation(null)
    setForm({ id: null, name: '', kind: t.type, params: { ...t.default_params }, code: meta.strategy_template })
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
      if (r.status === 'pending_review') toast.info('策略為「待審核」，請先跑回測確認後再按「審核啟用」')
    }
  }

  const activate = async (s: Strategy) => {
    const ok = await confirm({
      title: `審核啟用「${s.name}」？`,
      confirmText: '審核啟用',
      message: '請確認你已檢視過程式碼並跑過回測。啟用後即可被 Bot 使用。',
    })
    if (!ok) return
    if (await run(`act-${s.id}`, () => api.activateStrategy(s.id), '已啟用')) reload()
  }

  const remove = async (s: Strategy) => {
    if (!(await confirm({ title: `刪除策略「${s.name}」？`, danger: true, confirmText: '刪除' }))) return
    if (await run(`del-${s.id}`, () => api.deleteStrategy(s.id), '已刪除')) reload()
  }

  const codeChanged = form?.orig && form.kind === 'python' && form.code !== (form.orig.code ?? '')

  return (
    <div className="space-y-5">
      <Card
        title="策略"
        icon={<Icons.chart />}
        actions={
          <>
            <button className="btn btn-secondary btn-sm" onClick={() => setPineOpen(true)}>
              <Icons.code className="h-3.5 w-3.5" /> Pine Script 轉 Python
            </button>
            <button className="btn btn-primary btn-sm" onClick={openCreate}>
              <Icons.plus className="h-3.5 w-3.5" /> 新增策略
            </button>
          </>
        }
      >
        {loading ? (
          <Skeleton />
        ) : !data?.length ? (
          <Empty icon={<Icons.chart className="h-5 w-5" />} title="尚未建立策略" hint="可使用內建策略（均線交叉、RSI、AI 決策）、自訂 Python，或把 TradingView Pine Script 轉成 Python。" />
        ) : (
          <div className="space-y-2.5">
            {data.map((s) => (
              <ListRow
                key={s.id}
                icon={s.kind === 'python' ? <Icons.code className="h-5 w-5" /> : s.kind === 'tradingview' ? <Icons.webhook className="h-5 w-5" /> : s.kind === 'ai' ? <Icons.brain className="h-5 w-5" /> : <Icons.chart className="h-5 w-5" />}
                title={s.name}
                subtitle={
                  <>
                    {kindLabel(s.kind)}
                    {s.pine_source && <span className="text-muted"> · 由 Pine Script 轉換</span>}
                    {Object.keys(s.params ?? {}).length > 0 && (
                      <span className="text-muted">
                        {' · '}
                        {Object.entries(s.params)
                          .filter(([, v]) => typeof v !== 'object' && String(v).length < 20)
                          .slice(0, 4)
                          .map(([k, v]) => `${paramLabel(k)} ${String(v)}`)
                          .join('、')}
                      </span>
                    )}
                  </>
                }
                badges={<StrategyStatusBadge status={s.status} />}
                actions={
                  <>
                    {s.status === 'pending_review' && (
                      <>
                        <Link to={`/backtest?strategy=${s.id}`} className="btn btn-secondary btn-sm">
                          <Icons.chart className="h-3.5 w-3.5" /> 回測
                        </Link>
                        <button className="btn btn-primary btn-sm" onClick={() => activate(s)} disabled={busy === `act-${s.id}`}>
                          {busy === `act-${s.id}` ? <Spinner className="h-3.5 w-3.5" /> : <Icons.check className="h-3.5 w-3.5" />} 審核啟用
                        </button>
                      </>
                    )}
                    <button className="btn btn-ghost btn-sm" onClick={() => openEdit(s)}>
                      <Icons.edit className="h-3.5 w-3.5" /> 編輯
                    </button>
                    <button className="btn btn-danger btn-sm" onClick={() => remove(s)} aria-label="刪除">
                      <Icons.trash className="h-3.5 w-3.5" />
                    </button>
                  </>
                }
              />
            ))}
          </div>
        )}
      </Card>

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
                  {meta.strategy_types.map((t) => (
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
                {form.orig && (
                  <div className="rounded-lg border border-gold/30 bg-gold/10 px-3 py-2 text-xs text-amber-100">
                    修改程式碼後，策略會重設為「待審核」，需重新回測並審核啟用才能給 Bot 使用。
                    {codeChanged && <b className="ml-1">（程式碼已變更）</b>}
                  </div>
                )}
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

      <PineConverter
        open={pineOpen}
        onClose={() => setPineOpen(false)}
        models={models ?? []}
        onDone={reload}
        onActivate={async (s) => {
          await activate(s)
        }}
      />
    </div>
  )
}

function pick(o: Params, keys: string[]): Params {
  const r: Params = {}
  for (const k of keys) if (k in o) r[k] = o[k]
  return r
}

function PineConverter({
  open,
  onClose,
  models,
  onDone,
  onActivate,
}: {
  open: boolean
  onClose: () => void
  models: { id: number; name: string }[]
  onDone: () => void
  onActivate: (s: Strategy) => Promise<void>
}) {
  const [name, setName] = useState('')
  const [modelId, setModelId] = useState<number | ''>('')
  const [pine, setPine] = useState('')
  const [result, setResult] = useState<ConvertPineResult | null>(null)
  const { busy, run } = useAction()
  const toast = useToast()

  const convert = async () => {
    if (!name.trim() || !pine.trim()) return toast.error('請填寫名稱與 Pine Script')
    const mid = modelId || models[0]?.id
    if (!mid) return toast.error('請先在「AI 模型」新增一個模型')
    const r = await run('convert', () => api.convertPine({ name: name.trim(), pine, ai_model_id: mid }))
    if (r) {
      setResult(r)
      onDone()
      if (r.conversion.ok) toast.success(`轉換完成（嘗試 ${r.conversion.attempts} 次）`)
      else toast.error('轉換完成但程式碼仍有錯誤，請檢視')
    }
  }

  const close = () => {
    if (busy === 'convert') return
    onClose()
  }

  const reset = () => {
    setResult(null)
    setPine('')
    setName('')
  }

  return (
    <Modal
      open={open}
      onClose={close}
      size="xl"
      title={
        <span className="flex items-center gap-2">
          <Icons.code className="h-4 w-4 text-gold" /> Pine Script 轉 Python
        </span>
      }
      footer={
        result ? (
          <>
            <button className="btn btn-secondary" onClick={reset}>再轉一個</button>
            <Link to={`/backtest?strategy=${result.strategy.id}`} className="btn btn-secondary" onClick={onClose}>
              <Icons.chart className="h-4 w-4" /> 前往回測
            </Link>
            {result.conversion.ok && (
              <button
                className="btn btn-primary"
                onClick={async () => {
                  await onActivate(result.strategy)
                }}
              >
                <Icons.check className="h-4 w-4" /> 審核啟用
              </button>
            )}
          </>
        ) : (
          <>
            <button className="btn btn-secondary" onClick={close} disabled={busy === 'convert'}>取消</button>
            <button className="btn btn-primary" onClick={convert} disabled={busy === 'convert'}>
              {busy === 'convert' ? <Spinner /> : <Icons.sparkle className="h-4 w-4" />}
              {busy === 'convert' ? 'AI 轉換中（約 1 分鐘）…' : '開始轉換'}
            </button>
          </>
        )
      }
    >
      {!result ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="策略名稱">
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：Supertrend 轉換" />
            </Field>
            <Field label="AI 模型">
              <select className="input" value={modelId} onChange={(e) => setModelId(e.target.value ? Number(e.target.value) : '')}>
                {models.length === 0 && <option value="">（請先新增 AI 模型）</option>}
                {models.map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
              </select>
            </Field>
          </div>
          <Field label="Pine Script 原始碼" hint="貼上 TradingView 的 strategy 或 indicator 腳本。AI 會轉成 Python 並自動檢查，最多重試 3 次。">
            <textarea className="code-area h-[380px] resize-y whitespace-pre" spellCheck={false} value={pine} onChange={(e) => setPine(e.target.value)} placeholder={'//@version=5\nstrategy("My Strategy", overlay=true)\n...'} disabled={busy === 'convert'} />
          </Field>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex items-start gap-3 rounded-xl border border-gold/40 bg-gold/10 px-4 py-3 text-sm text-amber-100">
            <Icons.alert className="mt-0.5 h-5 w-5 shrink-0 text-gold" />
            <div>
              <b>請檢視程式碼並先跑回測，確認無誤再按「審核啟用」</b>
              <div className="mt-0.5 text-xs text-amber-200/80">
                已建立策略「{result.strategy.name}」（待審核）。AI 轉換可能有誤差，請務必比對邏輯。
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            {result.conversion.ok ? <span className="badge badge-green">程式碼檢查通過</span> : <span className="badge badge-red">程式碼仍有錯誤</span>}
            <span className="badge badge-gray">嘗試 {result.conversion.attempts} 次</span>
          </div>
          {result.conversion.errors.length > 0 && (
            <div className="rounded-lg border border-down/30 bg-down/10 px-3 py-2 text-xs text-red-200">
              <div className="mb-1 font-semibold">錯誤</div>
              <ul className="list-inside list-disc space-y-0.5">
                {result.conversion.errors.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            </div>
          )}
          {result.conversion.notes.length > 0 && (
            <div className="rounded-lg border border-line bg-[#12161b] px-3 py-2 text-xs text-slate-300">
              <div className="mb-1 font-semibold text-gold">未轉換項目</div>
              <ul className="list-inside list-disc space-y-0.5">
                {result.conversion.notes.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            </div>
          )}
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <div>
              <div className="label">Pine Script（原始）</div>
              <pre className="code-area h-[420px] overflow-auto whitespace-pre">{pine}</pre>
            </div>
            <div>
              <div className="label">Python（AI 產生）</div>
              <pre className="code-area h-[420px] overflow-auto whitespace-pre text-green-100/90">{result.conversion.code}</pre>
            </div>
          </div>
          <div className="text-xs text-muted">如需修改程式碼，可在策略清單按「編輯」。</div>
        </div>
      )}
    </Modal>
  )
}
