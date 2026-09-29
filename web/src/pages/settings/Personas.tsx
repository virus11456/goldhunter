import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  api,
  errMsg,
  hasFidelity,
  type AIModel,
  type CostEstimate,
  type FidelityReport,
  type Persona,
  type PersonaDetail,
  type PersonaDistillIn,
} from '../../api'
import { MarkdownLite } from '../../components/markdown'
import { Card, Collapsible, Empty, Field, Icons, Modal, Skeleton, Spinner, useAction, useConfirm, useLoader, useToast } from '../../components/ui'
import { fmtNum } from '../../lib/format'
import { GradeBadge, MARKET_LABEL, MARKETS, PersonaStatusBadge, ROLE_LABEL, SOURCE_LABEL, shortPersonaName } from '../../lib/persona'
import { OverflowMenu } from './Strategies'

const INTRO = '讓 AI 交易員用大師的思維做判斷。每位大師需通過保真度評分，並在模擬帳戶跑滿 7 天、成交 3 筆，才能用在實盤。'

// ============================== small pieces ==============================
function Chip({ children, cls = 'badge-gray' }: { children: ReactNode; cls?: string }) {
  return <span className={`badge ${cls}`}>{children}</span>
}

function PersonaChips({ p }: { p: Pick<Persona, 'source' | 'role' | 'markets'> }) {
  return (
    <div className="flex flex-wrap gap-1">
      <Chip>{SOURCE_LABEL[p.source] ?? p.source}</Chip>
      <Chip cls="badge-gold">{ROLE_LABEL[p.role] ?? p.role}</Chip>
      {p.markets.map((m) => (
        <span key={m} className="rounded border border-line px-1.5 py-0.5 text-[11px] leading-4 text-slate-400">{MARKET_LABEL[m] ?? m}</span>
      ))}
    </div>
  )
}

function Avatar({ name, className = 'h-10 w-10 text-base' }: { name: string; className?: string }) {
  const s = shortPersonaName(name)
  return (
    <div className={`flex shrink-0 items-center justify-center rounded-full border border-gold/30 bg-gradient-to-br from-gold/25 to-gold/5 font-semibold text-gold ${className}`}>
      {s.slice(0, 1)}
    </div>
  )
}

function MarketPicker({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {MARKETS.map((m) => {
        const on = value.includes(m)
        return (
          <button
            key={m}
            type="button"
            aria-pressed={on}
            className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${on ? 'border-gold/60 bg-gold/15 text-gold' : 'border-line text-muted hover:text-slate-200'}`}
            onClick={() => onChange(on ? value.filter((x) => x !== m) : [...value, m])}
          >
            {on && <Icons.check className="mr-1 inline h-3.5 w-3.5" />}
            {MARKET_LABEL[m]}
          </button>
        )
      })}
    </div>
  )
}

function RoleSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="trader">交易大腦（提出交易）</option>
      <option value="reviewer">審查委員（可否決）</option>
      <option value="both">兩者皆可</option>
    </select>
  )
}

function ModelSelect({ models, value, onChange }: { models: AIModel[] | null; value: number | ''; onChange: (v: number | '') => void }) {
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : '')}>
      {!models?.length && <option value="">請先到「AI 模型」新增模型</option>}
      {models?.map((m) => (
        <option key={m.id} value={m.id}>{m.name}{m.model ? `（${m.model}）` : ''}</option>
      ))}
    </select>
  )
}

function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf)
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(bin)
}

function readFileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader()
    fr.onload = () => resolve(arrayBufferToBase64(fr.result as ArrayBuffer))
    fr.onerror = () => reject(new Error('讀取檔案失敗'))
    fr.readAsArrayBuffer(file)
  })
}

// ============================== fidelity runner (model + button) ==============================
function FidelityRunner({
  persona,
  models,
  onDone,
  compact,
}: {
  persona: Pick<Persona, 'id' | 'name'>
  models: AIModel[] | null
  onDone: (p: PersonaDetail) => void
  compact?: boolean
}) {
  const [model, setModel] = useState<number | ''>(models?.[0]?.id ?? '')
  const [busy, setBusy] = useState(false)
  const toast = useToast()
  useEffect(() => {
    if (model === '' && models?.length) setModel(models[0].id)
  }, [models, model])
  const go = async () => {
    if (!model) return toast.error('請先選擇 AI 模型')
    setBusy(true)
    try {
      const r = await api.personaFidelity(persona.id, Number(model))
      const f = r.fidelity as FidelityReport
      if (hasFidelity(f)) (f.passed ? toast.success : toast.info)(`「${r.name}」保真度 ${f.score} 分（${f.grade}）${f.passed ? '，已進入模擬期' : `，未達 ${r.pass_score} 分`}`)
      onDone(r)
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(false)
    }
  }
  if (busy)
    return (
      <div className="flex items-center gap-2.5 py-1 text-sm text-slate-200">
        <Spinner className="h-5 w-5 text-gold" />
        <div>
          評分中…約 30 秒
          <div className="text-xs text-muted">出題、作答、評分由三次獨立的 AI 呼叫完成</div>
        </div>
      </div>
    )
  return (
    <div className={compact ? 'space-y-2' : 'flex flex-wrap items-end gap-2'}>
      <div className={compact ? '' : 'min-w-[200px] flex-1'}>
        <label className="label">用哪個 AI 模型評分</label>
        <ModelSelect models={models} value={model} onChange={setModel} />
      </div>
      <button className={`btn btn-primary ${compact ? 'w-full' : ''}`} onClick={go} disabled={!model}>
        <Icons.check className="h-4 w-4" /> 開始評分
      </button>
      <div className="w-full text-xs text-muted">約 3 次 AI 呼叫；70 分以上進入模擬期。</div>
    </div>
  )
}

function Popover({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    const t = setTimeout(() => document.addEventListener('mousedown', onDoc), 0)
    return () => {
      clearTimeout(t)
      document.removeEventListener('mousedown', onDoc)
    }
  }, [open, onClose])
  if (!open) return null
  return (
    <div ref={ref} className="anim-fade absolute bottom-full left-0 right-0 z-30 mb-2 rounded-xl border border-line bg-panel2 p-3 shadow-2xl">
      {children}
    </div>
  )
}

// ============================== fidelity report + profile ==============================
const Q_TYPE: Record<string, string> = { stance: '立場題', out_of_scope: '超範圍題', scenario: '情境題' }

function FidelityView({ f, passScore }: { f: FidelityReport; passScore: number }) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <GradeBadge grade={f.grade} score={f.score} size="lg" />
        <div className="min-w-0 flex-1">
          <div className={`text-sm font-medium ${f.passed ? 'text-green-300' : 'text-red-300'}`}>
            {f.passed ? `通過（門檻 ${passScore} 分）` : `未通過（門檻 ${passScore} 分）`}
          </div>
          {f.summary && <div className="text-xs leading-relaxed text-muted">{f.summary}</div>}
        </div>
      </div>
      <div className="space-y-2.5">
        {f.dimensions.map((d) => {
          const pct = d.max ? Math.max(0, Math.min(100, (d.score / d.max) * 100)) : 0
          const bar = pct >= 80 ? 'bg-up' : pct >= 60 ? 'bg-gold' : 'bg-down'
          return (
            <div key={d.name}>
              <div className="flex items-baseline justify-between gap-2 text-sm">
                <span className="text-slate-200">{d.name}</span>
                <span className="num font-mono text-xs text-slate-300">
                  {fmtNum(d.score, 0)}<span className="text-muted"> / {fmtNum(d.max, 0)}</span>
                </span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-panel2">
                <div className={`h-full rounded-full ${bar}`} style={{ width: `${pct}%` }} />
              </div>
              {d.reason && <div className="mt-1 text-xs leading-relaxed text-muted">{d.reason}</div>}
            </div>
          )
        })}
      </div>
      {f.questions?.length > 0 && (
        <Collapsible title={`測驗題目（${f.questions.length} 題）`}>
          <ol className="space-y-3">
            {f.questions.map((q, i) => (
              <li key={i} className="rounded-lg border border-line bg-black/20 p-3 text-sm">
                <div className="flex items-start gap-2">
                  <span className="badge badge-gray shrink-0">{Q_TYPE[q.type] ?? q.type}</span>
                  <span className="font-medium text-slate-100">{q.question}</span>
                </div>
                <dl className="mt-2 grid grid-cols-[4.5em_1fr] gap-x-2 gap-y-1 text-xs leading-relaxed">
                  <dt className="text-muted">真實立場</dt>
                  <dd className="text-slate-300">{q.expected}</dd>
                  <dt className="text-muted">作答</dt>
                  <dd className="text-slate-200">{q.answer || '—'}</dd>
                </dl>
              </li>
            ))}
          </ol>
        </Collapsible>
      )}
    </div>
  )
}

function OnchainBox({ o }: { o: NonNullable<PersonaDetail['meta']['onchain']> }) {
  if (!o.fills) return <div className="rounded-lg border border-line p-3 text-xs text-muted">鏈上交易紀錄：查無成交</div>
  const items: [string, ReactNode][] = [
    ['成交筆數', `${o.fills} 筆・${fmtNum(o.period_days, 0)} 天`],
    ['勝率', o.win_rate_pct != null ? `${fmtNum(o.win_rate_pct, 1)}%` : '—'],
    ['做多比例', o.long_ratio_pct != null ? `${fmtNum(o.long_ratio_pct, 1)}%` : '—'],
    ['平均持倉', o.avg_hold_hours != null ? `${fmtNum(o.avg_hold_hours, 1)} 小時` : '—'],
    ['每天開倉', o.trades_per_day != null ? `${fmtNum(o.trades_per_day, 1)} 次` : '—'],
    ['已實現損益', o.total_closed_pnl != null ? `${fmtNum(o.total_closed_pnl, 0)} USD` : '—'],
  ]
  return (
    <div className="rounded-xl border border-sky-500/30 bg-sky-500/[0.06] p-3">
      <div className="mb-2 text-xs font-semibold text-sky-300">鏈上交易紀錄（Hyperliquid）</div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-2 sm:grid-cols-3">
        {items.map(([k, v]) => (
          <div key={k}>
            <div className="text-[10.5px] text-muted">{k}</div>
            <div className="num font-mono text-sm text-slate-100">{v}</div>
          </div>
        ))}
      </div>
      {o.top_coins?.length ? <div className="mt-2 text-xs text-muted">常交易：{o.top_coins.map(([c, n]) => `${c}（${n}）`).join('、')}</div> : null}
    </div>
  )
}

function PersonaView({ p, models, onUpdated }: { p: PersonaDetail; models: AIModel[] | null; onUpdated: (p: PersonaDetail) => void }) {
  const f = hasFidelity(p.fidelity) ? p.fidelity : null
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        <Avatar name={p.name} className="h-12 w-12 text-lg" />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-base font-semibold text-slate-50">{p.name}</span>
            <PersonaStatusBadge p={p} />
          </div>
          <PersonaChips p={p} />
          {p.summary && <div className="text-xs leading-relaxed text-muted">{p.summary}</div>}
        </div>
      </div>

      <section className="rounded-xl border border-line bg-[#101318] p-4">
        <div className="mb-3 text-sm font-semibold text-slate-100">保真度評分</div>
        {f ? (
          <FidelityView f={f} passScore={p.pass_score} />
        ) : (
          <div className="space-y-3">
            <div className="flex items-start gap-2 rounded-lg border border-gold/30 bg-gold/[0.06] px-3 py-2 text-xs text-amber-100">
              <Icons.alert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gold" />
              尚未評分。評分通過（{p.pass_score} 分以上）後進入模擬期，未評分前只能用在模擬帳戶。
            </div>
            <FidelityRunner persona={p} models={models} onDone={onUpdated} />
          </div>
        )}
      </section>

      {p.meta?.onchain && <OnchainBox o={p.meta.onchain} />}
      {p.meta?.truncated && (
        <div className="rounded-lg border border-gold/30 bg-gold/[0.06] px-3 py-2 text-xs text-amber-100">
          檔案較長，只保留前 4 萬字（每次 AI 決策都會帶入，太長會變貴）。
        </div>
      )}

      <section>
        <div className="mb-2 flex items-baseline justify-between gap-2">
          <span className="text-sm font-semibold text-slate-100">思維檔案</span>
          <span className="text-xs text-muted">{p.profile.length.toLocaleString()} 字</span>
        </div>
        <div className="max-h-[480px] overflow-y-auto rounded-xl border border-line bg-black/20 p-4">
          <MarkdownLite text={p.profile} />
        </div>
      </section>
    </div>
  )
}

// ============================== detail / edit modal ==============================
function DetailModal({
  id,
  initial,
  startEdit,
  models,
  onClose,
  onChanged,
}: {
  id: number
  initial?: PersonaDetail | null
  startEdit?: boolean
  models: AIModel[] | null
  onClose: () => void
  onChanged: () => void
}) {
  const [p, setP] = useState<PersonaDetail | null>(initial ?? null)
  const [edit, setEdit] = useState<{ name: string; role: string; markets: string[]; summary: string; profile: string } | null>(null)
  const { busy, run } = useAction()
  const toast = useToast()

  useEffect(() => {
    let alive = true
    if (!initial)
      api
        .getPersona(id)
        .then((d) => alive && setP(d))
        .catch((e) => toast.error(e))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])
  useEffect(() => {
    if (startEdit && p && !edit) setEdit({ name: p.name, role: p.role, markets: [...p.markets], summary: p.summary, profile: p.profile })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startEdit, p])

  const save = async () => {
    if (!p || !edit) return
    if (!edit.name.trim()) return toast.error('請輸入名字')
    if (!edit.markets.length) return toast.error('至少選一個市場')
    const r = await run('save', () => api.updatePersona(p.id, { ...edit, name: edit.name.trim() }), '已儲存')
    if (r) {
      setP(r)
      setEdit(null)
      onChanged()
    }
  }
  const updated = (d: PersonaDetail) => {
    setP(d)
    onChanged()
  }
  const profileChanged = !!(edit && p && edit.profile !== p.profile)

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={edit ? `編輯「${p?.name ?? ''}」` : p?.name ?? '投資大師'}
      footer={
        edit ? (
          <>
            <button className="btn btn-secondary" onClick={() => setEdit(null)}>取消</button>
            <button className="btn btn-primary" onClick={save} disabled={busy === 'save'}>
              {busy === 'save' && <Spinner />} 儲存
            </button>
          </>
        ) : (
          <>
            {p && (
              <button className="btn btn-secondary" onClick={() => setEdit({ name: p.name, role: p.role, markets: [...p.markets], summary: p.summary, profile: p.profile })}>
                <Icons.edit className="h-4 w-4" /> 編輯
              </button>
            )}
            <button className="btn btn-primary" onClick={onClose}>完成</button>
          </>
        )
      }
    >
      {!p ? (
        <Skeleton rows={5} />
      ) : edit ? (
        <div className="space-y-4">
          <div className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${profileChanged ? 'border-gold/50 bg-gold/10 text-amber-100' : 'border-line text-muted'}`}>
            <Icons.alert className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${profileChanged ? 'text-gold' : ''}`} />
            修改內容後需要重新評分{profileChanged ? '（儲存後會回到「未評分」，只能用在模擬帳戶）' : '；只改名字、角色、市場不影響評分。'}
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <Field label="名字">
              <input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
            </Field>
            <Field label="角色">
              <RoleSelect value={edit.role} onChange={(role) => setEdit({ ...edit, role })} />
            </Field>
            <Field label="適合的市場" className="md:col-span-2">
              <MarketPicker value={edit.markets} onChange={(markets) => setEdit({ ...edit, markets })} />
            </Field>
            <Field label="一句話簡介" className="md:col-span-2">
              <input className="input" value={edit.summary} maxLength={200} onChange={(e) => setEdit({ ...edit, summary: e.target.value })} />
            </Field>
          </div>
          <Field label="思維檔案（Markdown）">
            <textarea className="code-area min-h-[360px]" value={edit.profile} onChange={(e) => setEdit({ ...edit, profile: e.target.value })} spellCheck={false} />
          </Field>
        </div>
      ) : (
        <PersonaView p={p} models={models} onUpdated={updated} />
      )}
    </Modal>
  )
}

// ============================== upload modal ==============================
function UploadModal({ onClose, onCreated }: { onClose: () => void; onCreated: (p: PersonaDetail) => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [role, setRole] = useState('trader')
  const [markets, setMarkets] = useState<string[]>(['crypto'])
  const { busy, run } = useAction()
  const toast = useToast()
  const submit = async () => {
    if (!file) return toast.error('請選擇檔案')
    if (file.size > 10_000_000) return toast.error('檔案太大（上限 10 MB）')
    if (!markets.length) return toast.error('至少選一個市場')
    const r = await run('up', async () => api.uploadPersona({ filename: file.name, content_base64: await readFileBase64(file), role, markets }))
    if (r) {
      toast.success(`已匯入「${r.name}」，建議接著做保真度評分`)
      onCreated(r)
    }
  }
  return (
    <Modal
      open
      onClose={onClose}
      title="上傳蒸餾檔"
      footer={
        <>
          <button className="btn btn-secondary" onClick={onClose}>取消</button>
          <button className="btn btn-primary" onClick={submit} disabled={!file || busy === 'up'}>
            {busy === 'up' && <Spinner />} 上傳
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <label className={`flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed px-4 py-6 text-center transition-colors ${file ? 'border-gold/60 bg-gold/[0.06]' : 'border-line hover:border-gold/50'}`}>
          <input type="file" accept=".md,.txt,.zip" className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <Icons.plus className="h-5 w-5 text-gold" />
          {file ? (
            <>
              <span className="break-all text-sm font-medium text-slate-100">{file.name}</span>
              <span className="text-xs text-muted">{fmtNum(file.size / 1024, 1)} KB・點一下更換</span>
            </>
          ) : (
            <>
              <span className="text-sm text-slate-200">選擇 SKILL.md、.txt 或整個資料夾壓成的 .zip</span>
              <span className="text-xs text-muted">上限 10 MB</span>
            </>
          )}
        </label>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="角色">
            <RoleSelect value={role} onChange={setRole} />
          </Field>
          <Field label="適合的市場">
            <MarketPicker value={markets} onChange={setMarkets} />
          </Field>
        </div>
        <Collapsible title="怎麼用 nuwa-skill 自己蒸餾？">
          <ol className="list-decimal space-y-2 pl-5 text-sm leading-relaxed text-slate-300">
            <li>
              在 Claude Code 或 Claude App 輸入「幫我安裝這個 skill：
              <span className="break-all font-mono text-xs text-amber-100">https://github.com/alchaincyf/nuwa-skill</span>」
            </li>
            <li>輸入「蒸餾一個〈人名〉，聚焦他的交易與風險管理思維」</li>
            <li>完成後把產生的 SKILL.md（或整個資料夾壓成 zip）上傳到這裡</li>
          </ol>
        </Collapsible>
        <div className="flex items-start gap-2 rounded-lg border border-up/30 bg-up/[0.06] px-3 py-2 text-xs leading-relaxed text-green-100">
          <Icons.shield className="mt-0.5 h-3.5 w-3.5 shrink-0 text-up" />
          在 Claude 訂閱中蒸餾不會產生本系統的 API 費用。上傳內容只當作文字給 AI 參考，不會執行任何程式。
        </div>
      </div>
    </Modal>
  )
}

// ============================== distill modal ==============================
const DISTILL_STEPS = ['調研', '撰寫思維檔案', '保真度評分']

function DistillProgress() {
  const [i, setI] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setI((x) => Math.min(x + 1, DISTILL_STEPS.length - 1)), 40000)
    return () => clearInterval(t)
  }, [])
  return (
    <div className="mx-auto max-w-sm py-6">
      <div className="mb-5 text-center">
        <Spinner className="mx-auto h-8 w-8 text-gold" />
        <div className="mt-3 text-sm text-slate-200">蒸餾中，約需 1–3 分鐘，請勿關閉視窗</div>
      </div>
      <ol className="space-y-2">
        {DISTILL_STEPS.map((s, k) => {
          const done = k < i
          const active = k === i
          return (
            <li key={s} className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-sm ${active ? 'border-gold/40 bg-gold/[0.06] text-slate-100' : 'border-line text-muted'}`}>
              <span className={`flex h-5 w-5 items-center justify-center rounded-full ${done ? 'bg-up/20 text-up' : active ? 'text-gold' : 'bg-panel2 text-slate-600'}`}>
                {done ? <Icons.check className="h-3.5 w-3.5" /> : active ? <Spinner className="h-4 w-4" /> : <span className="text-[10px]">{k + 1}</span>}
              </span>
              {s}
            </li>
          )
        })}
      </ol>
    </div>
  )
}

function tokensText(n: number): string {
  return n >= 10_000 ? `約 ${fmtNum(n / 10_000, n >= 100_000 ? 0 : 1)} 萬 tokens` : `約 ${n.toLocaleString()} tokens`
}

function CostBox({ est, loading }: { est: CostEstimate | null; loading: boolean }) {
  return (
    <div className="rounded-xl border border-gold/40 bg-gold/[0.07] p-3">
      {est ? (
        <>
          {est.usd !== null ? (
            <div className="text-sm text-slate-100">
              預估費用約 <span className="num font-mono text-lg font-semibold text-gold">US${est.usd.toFixed(2)}</span>
              <span className="text-muted">
                （{est.web_searches ? `網路搜尋 ${est.web_searches} 次、` : ''}{tokensText(est.input_tokens + est.output_tokens)}）
              </span>
            </div>
          ) : (
            <div className="text-sm text-slate-100">
              無法估算金額<span className="text-muted">（{tokensText(est.input_tokens + est.output_tokens)}）</span>
            </div>
          )}
          <div className="mt-1 text-xs leading-relaxed text-muted">{est.note}</div>
        </>
      ) : (
        <div className="flex items-center gap-2 text-sm text-muted">
          {loading ? <><Spinner className="h-4 w-4" /> 估算費用中…</> : '選擇 AI 模型後會顯示預估費用'}
        </div>
      )}
      {loading && est && <div className="mt-1 text-[11px] text-muted">更新中…</div>}
    </div>
  )
}

function DistillModal({ models, onClose, onCreated }: { models: AIModel[] | null; onClose: () => void; onCreated: () => void }) {
  const [f, setF] = useState<{ name: string; model: number | ''; depth: 'quick' | 'standard'; address: string; corpus: string; role: string; markets: string[] }>({
    name: '',
    model: models?.[0]?.id ?? '',
    depth: 'standard',
    address: '',
    corpus: '',
    role: 'trader',
    markets: ['crypto'],
  })
  const [est, setEst] = useState<CostEstimate | null>(null)
  const [estLoading, setEstLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<PersonaDetail | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const toast = useToast()

  useEffect(() => {
    if (f.model === '' && models?.length) setF((x) => ({ ...x, model: models[0].id }))
  }, [models, f.model])

  const body = (): PersonaDistillIn => ({
    name: f.name.trim() || '（未填）',
    ai_model_id: Number(f.model),
    depth: f.depth,
    corpus: f.corpus.trim() || null,
    hyperliquid_address: f.address.trim() || null,
    role: f.role,
    markets: f.markets,
  })

  // 名字 / 模型 / 檔位改變時重新估價（防抖）
  useEffect(() => {
    if (!f.model) {
      setEst(null)
      return
    }
    let alive = true
    setEstLoading(true)
    const t = setTimeout(() => {
      api
        .estimatePersona(body())
        .then((r) => alive && setEst(r))
        .catch(() => alive && setEst(null))
        .finally(() => alive && setEstLoading(false))
    }, 450)
    return () => {
      alive = false
      clearTimeout(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f.name, f.model, f.depth])

  const start = async () => {
    if (!f.name.trim()) return toast.error('請輸入要蒸餾的人名')
    if (!f.model) return toast.error('請選擇 AI 模型')
    if (!f.markets.length) return toast.error('至少選一個市場')
    if (f.address.trim() && !/^0x[0-9a-fA-F]{40}$/.test(f.address.trim())) return toast.error('錢包地址格式錯誤（應為 0x 開頭的 42 碼）')
    setErr(null)
    setRunning(true)
    try {
      const r = await api.distillPersona(body())
      setResult(r)
      onCreated()
    } catch (e) {
      setErr(errMsg(e))
    } finally {
      setRunning(false)
    }
  }

  const usd = est?.usd
  const footer = result ? (
    <button className="btn btn-primary" onClick={onClose}>完成</button>
  ) : running ? undefined : (
    <>
      <button className="btn btn-secondary" onClick={onClose}>取消</button>
      <button className="btn btn-primary" onClick={start} disabled={!f.model || !f.name.trim()}>
        <Icons.sparkle className="h-4 w-4" />
        {usd !== null && usd !== undefined ? `確認花費約 US$${usd.toFixed(2)}，開始蒸餾` : '開始蒸餾'}
      </button>
    </>
  )

  return (
    <Modal open onClose={running ? () => {} : onClose} size={result ? 'lg' : 'md'} title={result ? `蒸餾完成：${result.name}` : '系統內蒸餾'} footer={footer}>
      {running ? (
        <DistillProgress />
      ) : result ? (
        <PersonaView p={result} models={models} onUpdated={setResult} />
      ) : (
        <div className="space-y-4">
          {err && (
            <div className="flex items-start gap-2 rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-xs text-red-200">
              <Icons.alert className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {err}
            </div>
          )}
          <Field label="人名">
            <input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="例如：段永平、塔雷伯、某位加密貨幣交易員" autoFocus maxLength={80} />
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_auto]">
            <Field label="AI 模型">
              <ModelSelect models={models} value={f.model} onChange={(model) => setF({ ...f, model })} />
            </Field>
            <Field label="檔位">
              <div className="flex rounded-lg border border-line p-0.5">
                {([
                  ['quick', '快速'],
                  ['standard', '標準'],
                ] as const).map(([d, l]) => (
                  <button key={d} type="button" className={`flex-1 rounded-md px-4 py-1.5 text-sm transition-colors ${f.depth === d ? 'bg-gold/15 text-gold' : 'text-muted hover:text-slate-200'}`} onClick={() => setF({ ...f, depth: d })}>
                    {l}
                  </button>
                ))}
              </div>
            </Field>
          </div>
          <CostBox est={est} loading={estLoading} />
          <Collapsible title="進階（選填）">
            <div className="space-y-3">
              <Field label="Hyperliquid 錢包地址" hint="加密貨幣交易員可填公開錢包，系統會抓他的真實成交紀錄一起分析">
                <input className="input font-mono" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} placeholder="0x…" spellCheck={false} />
              </Field>
              <Field label="一手資料" hint="書摘、訪談稿等，會優先採用">
                <textarea className="input min-h-[96px]" value={f.corpus} onChange={(e) => setF({ ...f, corpus: e.target.value })} />
              </Field>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="角色">
                  <RoleSelect value={f.role} onChange={(role) => setF({ ...f, role })} />
                </Field>
                <Field label="適合的市場">
                  <MarketPicker value={f.markets} onChange={(markets) => setF({ ...f, markets })} />
                </Field>
              </div>
            </div>
          </Collapsible>
        </div>
      )}
    </Modal>
  )
}

// ============================== persona card ==============================
function PersonaCard({
  p,
  models,
  onView,
  onEdit,
  onChanged,
  onScored,
}: {
  p: Persona
  models: AIModel[] | null
  onView: () => void
  onEdit: () => void
  onChanged: () => void
  onScored: (d: PersonaDetail) => void
}) {
  const [scoreOpen, setScoreOpen] = useState(false)
  const { busy, run } = useAction()
  const confirm = useConfirm()
  const f = hasFidelity(p.fidelity) ? p.fidelity : null

  const promote = async () => {
    const g = p.paper_progress
    const ok = await confirm({
      title: `開放「${p.name}」用在實盤？`,
      confirmText: '開放實盤',
      message: (
        <>
          開放後可用在實盤帳戶的 Bot。
          {g && (
            <div className="mt-2 text-xs text-muted">
              目前進度：模擬 {fmtNum(g.days, 1)}/{g.days_required} 天、成交 {g.trades}/{g.trades_required} 筆。期滿會自動開放，不需手動操作。
            </div>
          )}
        </>
      ),
    })
    if (ok && (await run('promote', () => api.promotePersona(p.id), '已開放實盤'))) onChanged()
  }
  const remove = async () => {
    const ok = await confirm({ title: `刪除「${p.name}」？`, danger: true, confirmText: '刪除', message: '刪除後無法復原；仍有 Bot 使用時無法刪除。' })
    if (ok && (await run('del', () => api.deletePersona(p.id), '已刪除'))) onChanged()
  }

  const menu = [
    { label: '編輯', icon: <Icons.edit className="h-3.5 w-3.5" />, onClick: onEdit },
    ...(f ? [{ label: '重新評分', icon: <Icons.refresh className="h-3.5 w-3.5" />, onClick: () => setScoreOpen(true) }] : []),
    ...(p.source !== 'builtin' ? [{ label: '刪除', icon: <Icons.trash className="h-3.5 w-3.5" />, onClick: remove, danger: true }] : []),
  ]

  return (
    <div className="flex flex-col rounded-xl border border-line bg-[#0f1216] p-4 transition-colors hover:border-[#3a414b]">
      <div className="flex items-start gap-3">
        <Avatar name={p.name} />
        <div className="min-w-0 flex-1">
          <button className="block max-w-full truncate text-left font-semibold text-slate-100 hover:text-gold" onClick={onView}>
            {p.name}
          </button>
          <div className="mt-1">
            <PersonaChips p={p} />
          </div>
        </div>
        <div className="shrink-0 text-right">
          {f ? <GradeBadge grade={f.grade} score={f.score} /> : <span className="text-xs text-muted">未評分</span>}
        </div>
      </div>
      {p.summary && <p className="mt-2.5 line-clamp-2 text-xs leading-relaxed text-slate-400">{p.summary}</p>}
      <div className="mb-3 mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1">
        <PersonaStatusBadge p={p} />
        <span className="text-[11px] text-muted">使用中：{p.used_by_bots} 個 Bot</span>
      </div>
      <div className="relative mt-auto flex flex-wrap items-center gap-1.5 border-t border-line pt-3">
        <Popover open={scoreOpen} onClose={() => setScoreOpen(false)}>
          <div className="mb-2 text-sm font-medium text-slate-100">保真度評分：{p.name}</div>
          <FidelityRunner
            persona={p}
            models={models}
            compact
            onDone={(d) => {
              setScoreOpen(false)
              onScored(d)
            }}
          />
        </Popover>
        <button className="btn btn-secondary btn-sm" onClick={onView}>查看</button>
        {!f && (
          <button className="btn btn-sm border-gold/50 bg-gold/10 text-gold hover:bg-gold/20" onClick={() => setScoreOpen(true)}>
            <Icons.check className="h-3.5 w-3.5" /> 保真度評分
          </button>
        )}
        {p.status === 'paper_only' && (
          <button className="btn btn-success btn-sm" onClick={promote} disabled={busy === 'promote'}>
            {busy === 'promote' ? <Spinner className="h-3.5 w-3.5" /> : <Icons.bolt className="h-3.5 w-3.5" />} 開放實盤
          </button>
        )}
        <div className="ml-auto">
          <OverflowMenu items={menu} />
        </div>
      </div>
    </div>
  )
}

// ============================== page ==============================
export default function Personas() {
  const { data, loading, reload } = useLoader(() => api.listPersonas(), [])
  const { data: models } = useLoader(() => api.listAIModels(), [])
  const [detail, setDetail] = useState<{ id: number; initial?: PersonaDetail; edit?: boolean } | null>(null)
  const [upload, setUpload] = useState(false)
  const [distill, setDistill] = useState(false)

  return (
    <Card
      title="投資大師"
      icon={<Icons.brain />}
      actions={
        <>
          <button className="btn btn-secondary btn-sm" onClick={() => setDistill(true)}>
            <Icons.sparkle className="h-3.5 w-3.5" /> 系統內蒸餾
          </button>
          <button className="btn btn-primary btn-sm" onClick={() => setUpload(true)}>
            <Icons.plus className="h-3.5 w-3.5" /> 上傳蒸餾檔
            <span className="rounded bg-black/25 px-1 text-[10px] font-semibold leading-4">省錢</span>
          </button>
        </>
      }
    >
      <p className="mb-4 text-xs leading-relaxed text-muted">{INTRO}</p>
      {loading && !data ? (
        <Skeleton rows={3} />
      ) : !data?.length ? (
        <Empty icon={<Icons.brain className="h-5 w-5" />} title="還沒有投資大師" hint="上傳用 nuwa-skill 蒸餾好的檔案，或直接在系統內蒸餾。" />
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {data.map((p) => (
            <PersonaCard
              key={p.id}
              p={p}
              models={models}
              onView={() => setDetail({ id: p.id })}
              onEdit={() => setDetail({ id: p.id, edit: true })}
              onChanged={reload}
              onScored={(d) => {
                reload()
                setDetail({ id: d.id, initial: d })
              }}
            />
          ))}
        </div>
      )}

      {detail && (
        <DetailModal key={`${detail.id}-${detail.edit ? 'e' : 'v'}`} id={detail.id} initial={detail.initial} startEdit={detail.edit} models={models} onClose={() => setDetail(null)} onChanged={reload} />
      )}
      {upload && (
        <UploadModal
          onClose={() => setUpload(false)}
          onCreated={(p) => {
            setUpload(false)
            reload()
            setDetail({ id: p.id, initial: p })
          }}
        />
      )}
      {distill && <DistillModal models={models} onClose={() => setDistill(false)} onCreated={reload} />}
    </Card>
  )
}
