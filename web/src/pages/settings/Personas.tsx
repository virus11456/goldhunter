import { useEffect, useRef, useState, type DragEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, hasFidelity, type FidelityReport, type Persona, type PersonaDetail, type PersonaQuality } from '../../api'
import { MarkdownLite } from '../../components/markdown'
import { Card, Collapsible, Field, Icons, Modal, Skeleton, Spinner, useAction, useConfirm, useLoader, useToast } from '../../components/ui'
import { fmtNum } from '../../lib/format'
import { GradeBadge, PersonaStatusBadge, SOURCE_LABEL, shortPersonaName } from '../../lib/persona'
import { OverflowMenu } from './Strategies'

const INTRO = '讓 AI 交易員用大師的思維做判斷。每位大師需通過保真度評分，並在模擬帳戶跑滿 7 天、成交 3 筆，才能用在實盤。'
const NUWA_URL = 'https://github.com/alchaincyf/nuwa-skill'

// ============================== small pieces ==============================
function PersonaChips({ p }: { p: Pick<Persona, 'source'> }) {
  return <span className="badge badge-gray">{SOURCE_LABEL[p.source] ?? p.source}</span>
}

function Avatar({ name, className = 'h-10 w-10 text-base' }: { name: string; className?: string }) {
  return (
    <div className={`flex shrink-0 items-center justify-center rounded-full border border-gold/30 bg-gradient-to-br from-gold/25 to-gold/5 font-semibold text-gold ${className}`}>
      {shortPersonaName(name).slice(0, 1)}
    </div>
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

/** nuwa-skill（女媧）蒸餾步驟 */
function NuwaSteps() {
  return (
    <ol className="list-decimal space-y-2 pl-5 text-left text-sm leading-relaxed text-slate-300">
      <li>
        在 Claude Code 或 Claude App 輸入「幫我安裝這個 skill：<span className="break-all font-mono text-xs text-amber-100">{NUWA_URL}</span>」
      </li>
      <li>輸入「蒸餾一個〈人名〉，聚焦他的交易與風險管理思維」</li>
      <li>完成後把整個資料夾壓成 zip（含 SKILL.md 與保真度評分 FIDELITY.md）上傳到這裡；也可以上傳 SKILL.md，再另外附 FIDELITY.md</li>
    </ol>
  )
}

// ============================== fidelity report + profile ==============================
function FidelityView({ f, passScore }: { f: FidelityReport; passScore: number }) {
  const passed = f.passed ?? f.score >= passScore
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <GradeBadge grade={f.grade} score={f.score} size="lg" />
        <div className="min-w-0 flex-1">
          <div className={`text-sm font-medium ${passed ? 'text-green-300' : 'text-red-300'}`}>
            {passed ? `達標（門檻 ${passScore} 分）` : `未達標（門檻 ${passScore} 分），只能用在模擬帳戶`}
          </div>
          {f.summary && <div className="text-xs leading-relaxed text-muted">{f.summary}</div>}
          {f.tested_at && <div className="text-[11px] text-slate-500">測試日期：{f.tested_at}</div>}
        </div>
      </div>
      {f.dimensions?.length > 0 && (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>評分維度</th>
                <th className="r w-28">分數</th>
                <th>說明</th>
              </tr>
            </thead>
            <tbody>
              {f.dimensions.map((d) => {
                const pct = d.max ? Math.max(0, Math.min(100, (d.score / d.max) * 100)) : 0
                const bar = pct >= 80 ? 'bg-up' : pct >= 60 ? 'bg-gold' : 'bg-down'
                return (
                  <tr key={d.name}>
                    <td className="whitespace-nowrap text-slate-200">{d.name}</td>
                    <td className="r">
                      <div className="num font-mono text-xs text-slate-200">
                        {fmtNum(d.score, 0)}
                        <span className="text-muted"> / {fmtNum(d.max, 0)}</span>
                      </div>
                      <div className="ml-auto mt-1 h-1 w-20 overflow-hidden rounded-full bg-panel2">
                        <div className={`h-full rounded-full ${bar}`} style={{ width: `${pct}%` }} />
                      </div>
                    </td>
                    <td className="min-w-[160px] text-xs leading-relaxed text-muted">{d.reason || '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function SectionsInfo({ kept, dropped }: { kept?: string[]; dropped?: string[] }) {
  if (!kept?.length && !dropped?.length) return null
  return (
    <Collapsible title={`讀取的內容（保留 ${kept?.length ?? 0} 段・略過 ${dropped?.length ?? 0} 段）`}>
      <div className="space-y-2.5 text-xs">
        {!!kept?.length && (
          <div>
            <div className="mb-1 text-muted">保留（交易思維）</div>
            <div className="flex flex-wrap gap-1">
              {kept.map((k) => <span key={k} className="badge badge-green">{k}</span>)}
            </div>
          </div>
        )}
        {!!dropped?.length && (
          <div>
            <div className="mb-1 text-muted">略過（角色扮演、範例、調研紀錄等，與交易判斷無關）</div>
            <div className="flex flex-wrap gap-1">
              {dropped.map((k) => <span key={k} className="badge badge-gray">{k}</span>)}
            </div>
          </div>
        )}
      </div>
    </Collapsible>
  )
}

function PersonaView({ p }: { p: PersonaDetail }) {
  const f = hasFidelity(p.fidelity) ? p.fidelity : null
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        <Avatar name={p.name} className="h-12 w-12 text-lg" />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <PersonaStatusBadge p={p} />
            <span className="text-[11px] text-muted">組合：{p.used_by_bots} 個</span>
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
          <div className="flex items-start gap-2 rounded-lg border border-gold/30 bg-gold/[0.06] px-3 py-2 text-xs leading-relaxed text-amber-100">
            <Icons.alert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gold" />
            <span>上傳的檔案沒有附保真度評分（FIDELITY.md），只能用在模擬帳戶。女媧蒸餾時會一併產生，把整個資料夾壓成 zip 重新上傳即可。</span>
          </div>
        )}
      </section>

      {p.meta?.quality && <QualityView q={p.meta.quality} />}
      <SectionsInfo kept={p.meta?.kept_sections} dropped={p.meta?.dropped_sections} />
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
        {p.meta?.files && p.meta.files.length > 1 && (
          <div className="mt-1.5 text-[11px] text-muted">
            已讀取 {p.meta.files.length} 個檔案：{p.meta.files.map((x) => x.split('/').pop()).join('、')}
          </div>
        )}
      </section>
    </div>
  )
}

// ============================== detail / edit modal ==============================
type EditState = { name: string; summary: string }

function DetailModal({ id, initial, startEdit, onClose, onChanged, onPortfolio }: { id: number; initial?: PersonaDetail | null; startEdit?: boolean; onClose: () => void; onChanged: () => void; onPortfolio: (id: number) => void }) {
  const [p, setP] = useState<PersonaDetail | null>(initial ?? null)
  const [edit, setEdit] = useState<EditState | null>(null)
  const { busy, run } = useAction()
  const toast = useToast()
  const toEdit = (d: PersonaDetail): EditState => ({ name: d.name, summary: d.summary })

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
    if (startEdit && p && !edit) setEdit(toEdit(p))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startEdit, p])

  const save = async () => {
    if (!p || !edit) return
    if (!edit.name.trim()) return toast.error('請輸入名字')
    const r = await run('save', () => api.updatePersona(p.id, { name: edit.name.trim(), summary: edit.summary.trim() }), '已儲存')
    if (r) {
      setP(r)
      setEdit(null)
      onChanged()
    }
  }

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
              <button className="btn btn-secondary" onClick={() => setEdit(toEdit(p))}>
                <Icons.edit className="h-4 w-4" /> 編輯
              </button>
            )}
            {p && (
              <button className="btn btn-primary" onClick={() => onPortfolio(p.id)}>
                <Icons.chart className="h-4 w-4" /> 用這位大師建立組合
              </button>
            )}
          </>
        )
      }
    >
      {!p ? (
        <Skeleton rows={5} />
      ) : edit ? (
        <div className="space-y-4">
          <Field label="名字">
            <input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
          </Field>
          <Field label="一句話簡介">
            <input className="input" value={edit.summary} maxLength={300} onChange={(e) => setEdit({ ...edit, summary: e.target.value })} />
          </Field>
          <div className="text-xs text-muted">思維內容無法在這裡修改；請在女媧重新蒸餾後重新上傳。</div>
        </div>
      ) : (
        <PersonaView p={p} />
      )}
    </Modal>
  )
}

// ============================== upload modal ==============================
function DropZone({ file, onFile, accept, title, hint }: { file: File | null; onFile: (f: File | null) => void; accept: string; title: string; hint: string }) {
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const drop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    const f = e.dataTransfer.files?.[0]
    if (f) onFile(f)
  }
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => input.current?.click()}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
      className={`flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed px-4 py-5 text-center transition-colors ${over ? 'border-gold bg-gold/10' : file ? 'border-gold/60 bg-gold/[0.06]' : 'border-line hover:border-gold/50'}`}
    >
      <input ref={input} type="file" accept={accept} className="sr-only" onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
      <Icons.plus className="h-5 w-5 text-gold" />
      {file ? (
        <>
          <span className="break-all text-sm font-medium text-slate-100">{file.name}</span>
          <span className="text-xs text-muted">{fmtNum(file.size / 1024, 1)} KB・點一下或拖曳更換</span>
        </>
      ) : (
        <>
          <span className="text-sm text-slate-200">{title}</span>
          <span className="text-xs text-muted">{hint}</span>
        </>
      )}
    </div>
  )
}

function UploadModal({ onClose, onCreated }: { onClose: () => void; onCreated: (p: PersonaDetail) => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [fid, setFid] = useState<File | null>(null)
  const { busy, run } = useAction()
  const toast = useToast()
  const isZip = !!file && /\.zip$/i.test(file.name)
  const submit = async () => {
    if (!file) return toast.error('請選擇檔案')
    if (file.size > 15_000_000) return toast.error('檔案太大（上限 15 MB）')
    const r = await run('up', async () =>
      api.uploadPersona({
        filename: file.name,
        content_base64: await readFileBase64(file),
        fidelity_filename: !isZip && fid ? fid.name : null,
        fidelity_base64: !isZip && fid ? await readFileBase64(fid) : null,
      }),
    )
    if (r) {
      toast.success(`已匯入「${r.name}」`)
      onCreated(r)
    }
  }
  return (
    <Modal
      open
      onClose={onClose}
      title="上傳女媧蒸餾檔"
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
        <DropZone file={file} onFile={setFile} accept=".zip,.md,.txt" title="拖曳或選擇整個資料夾壓成的 .zip，或 SKILL.md" hint="zip 內的 FIDELITY.md 會一併讀取・上限 15 MB" />
        {file && !isZip && (
          <Field label="保真度評分 FIDELITY.md（選填）" hint="沒有附評分的大師只能用在模擬帳戶">
            <DropZone file={fid} onFile={setFid} accept=".md,.txt" title="拖曳或選擇 FIDELITY.md" hint="女媧蒸餾時產生的保真度報告" />
          </Field>
        )}
        <Collapsible title="怎麼用 nuwa-skill（女媧）蒸餾？">
          <NuwaSteps />
        </Collapsible>
        <div className="flex items-start gap-2 rounded-lg border border-up/30 bg-up/[0.06] px-3 py-2 text-xs leading-relaxed text-green-100">
          <Icons.shield className="mt-0.5 h-3.5 w-3.5 shrink-0 text-up" />
          在 Claude 訂閱中蒸餾不會產生本系統的 API 費用。上傳內容只當作文字給 AI 參考，不會執行任何程式。
        </div>
      </div>
    </Modal>
  )
}

// ============================== upload quality ==============================
const QUALITY_TEXT: Record<string, [string, string]> = {
  good: ['檔案完整', 'badge-green'],
  ok: ['檔案尚可', 'badge-blue'],
  weak: ['檔案不足', 'badge-red'],
}

function QualityBadge({ q }: { q: PersonaQuality }) {
  const [text, cls] = QUALITY_TEXT[q.level] ?? [q.level, 'badge-gray']
  return <span className={`badge ${cls}`} title={`健檢 ${q.passed}/${q.total} 項通過`}>{text} {q.passed}/{q.total}</span>
}

function QualityView({ q }: { q: PersonaQuality }) {
  return (
    <section className="rounded-xl border border-line bg-[#101318] p-4">
      <div className="mb-1 flex items-center gap-2 text-sm font-semibold text-slate-100">
        上傳健檢 <QualityBadge q={q} />
      </div>
      <p className="mb-3 text-xs text-muted">檢查檔案結構與交易相關度（不呼叫 AI、不花錢）。大師組合的表現取決於這份檔案的品質。</p>
      <ul className="space-y-1.5 text-xs">
        {q.checks.map((c) => (
          <li key={c.key} className="flex items-start gap-2">
            <span className={c.ok ? 'text-up' : 'text-down'}>{c.ok ? '✓' : '✗'}</span>
            <span className="text-slate-200">{c.label}</span>
            {c.detail && <span className="text-muted">— {c.detail}</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}

// ============================== persona card ==============================
function PersonaCard({ p, onView, onEdit, onChanged, onPortfolio }: { p: Persona; onView: () => void; onEdit: () => void; onChanged: () => void; onPortfolio: () => void }) {
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
    const ok = await confirm({ title: `刪除「${p.name}」？`, danger: true, confirmText: '刪除', message: '刪除後無法復原；仍有組合（Bot）使用時無法刪除。' })
    if (ok && (await run('del', () => api.deletePersona(p.id), '已刪除'))) onChanged()
  }
  const menu = [
    { label: '編輯', icon: <Icons.edit className="h-3.5 w-3.5" />, onClick: onEdit },
    { label: '刪除', icon: <Icons.trash className="h-3.5 w-3.5" />, onClick: remove, danger: true },
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
        <div className="shrink-0 text-right">{f ? <GradeBadge grade={f.grade} score={f.score} /> : <span className="text-[11px] text-muted">未附保真度評分</span>}</div>
      </div>
      {p.summary && <p className="mt-2.5 line-clamp-2 text-xs leading-relaxed text-slate-400">{p.summary}</p>}
      <div className="mb-3 mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1">
        <PersonaStatusBadge p={p} />
        {p.meta?.quality && <QualityBadge q={p.meta.quality} />}
        <span className="text-[11px] text-muted">組合：{p.used_by_bots} 個</span>
      </div>
      <div className="mt-auto flex flex-wrap items-center gap-1.5 border-t border-line pt-3">
        <button className="btn btn-secondary btn-sm" onClick={onView}>查看</button>
        <button className="btn btn-sm border-gold/50 bg-gold/10 text-gold hover:bg-gold/20" onClick={onPortfolio}>
          <Icons.chart className="h-3.5 w-3.5" /> 建立組合
        </button>
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

function EmptyState({ onUpload }: { onUpload: () => void }) {
  return (
    <div className="mx-auto max-w-xl py-6 text-center">
      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl border border-line bg-panel2 text-gold">
        <Icons.brain className="h-6 w-6" />
      </div>
      <div className="text-sm font-medium text-slate-200">還沒有投資大師</div>
      <div className="mt-1 text-xs text-muted">用 nuwa-skill（女媧）在 Claude 裡蒸餾一位大師，再把檔案上傳到這裡。每位大師之後可以用自己的風格管理一個獨立的量化組合。</div>
      <div className="mt-4 rounded-xl border border-line bg-[#101318] p-4">
        <NuwaSteps />
      </div>
      <button className="btn btn-primary mt-4" onClick={onUpload}>
        <Icons.plus className="h-4 w-4" /> 上傳女媧蒸餾檔
      </button>
    </div>
  )
}

// ============================== page ==============================
export default function Personas() {
  const { data, loading, reload } = useLoader(() => api.listPersonas(), [])
  const [detail, setDetail] = useState<{ id: number; initial?: PersonaDetail; edit?: boolean } | null>(null)
  const [upload, setUpload] = useState(false)
  const navigate = useNavigate()
  const toPortfolio = (id: number) => navigate(`/masters?new=${id}`)

  return (
    <Card
      title="投資大師"
      icon={<Icons.brain />}
      actions={
        data?.length ? (
          <button className="btn btn-primary btn-sm" onClick={() => setUpload(true)}>
            <Icons.plus className="h-3.5 w-3.5" /> 上傳女媧蒸餾檔
          </button>
        ) : undefined
      }
    >
      <p className="mb-4 text-xs leading-relaxed text-muted">{INTRO}</p>
      {loading && !data ? (
        <Skeleton rows={3} />
      ) : !data?.length ? (
        <EmptyState onUpload={() => setUpload(true)} />
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {data.map((p) => (
            <PersonaCard key={p.id} p={p} onView={() => setDetail({ id: p.id })} onEdit={() => setDetail({ id: p.id, edit: true })} onChanged={reload} onPortfolio={() => toPortfolio(p.id)} />
          ))}
        </div>
      )}

      {detail && (
        <DetailModal key={`${detail.id}-${detail.edit ? 'e' : 'v'}`} id={detail.id} initial={detail.initial} startEdit={detail.edit} onClose={() => setDetail(null)} onChanged={reload} onPortfolio={toPortfolio} />
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
    </Card>
  )
}
