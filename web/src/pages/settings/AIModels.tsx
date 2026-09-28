import { useState } from 'react'
import { api, type AIModel, type AIModelIn } from '../../api'
import { Card, Empty, Field, Icons, ListRow, Modal, Skeleton, Spinner, useAction, useConfirm, useLoader, useToast } from '../../components/ui'
import { useMeta } from '../../lib/meta'

const EFFORTS = ['', 'low', 'medium', 'high', 'xhigh', 'max']

export default function AIModels() {
  const meta = useMeta()
  const { data, loading, reload } = useLoader(() => api.listAIModels(), [])
  const [editing, setEditing] = useState<{ id: number | null; form: AIModelIn; m?: AIModel } | null>(null)
  const [testResult, setTestResult] = useState<Record<number, { ok: boolean; text: string }>>({})
  const { busy, run } = useAction()
  const confirm = useConfirm()
  const toast = useToast()
  const prov = (id: string) => meta.ai_providers.find((p) => p.id === id)

  const save = async () => {
    if (!editing) return
    const f = editing.form
    if (!f.name.trim()) return toast.error('請輸入名稱')
    const options = { ...f.options }
    if (f.provider !== 'anthropic' || !options.effort) delete options.effort
    const body: AIModelIn = { ...f, api_key: f.api_key || null, base_url: f.base_url || null, options }
    const r = await run('save', () => (editing.id ? api.updateAIModel(editing.id, body) : api.createAIModel(body)), '已儲存')
    if (r) {
      setEditing(null)
      reload()
    }
  }

  const test = async (m: AIModel) => {
    const r = await run(`test-${m.id}`, () => api.testAIModel(m.id))
    if (!r) return
    setTestResult((x) => ({ ...x, [m.id]: r.ok ? { ok: true, text: `連線成功｜模型 ${r.model}` } : { ok: false, text: r.error } }))
  }

  const remove = async (m: AIModel) => {
    if (!(await confirm({ title: `刪除 AI 模型「${m.name}」？`, danger: true, confirmText: '刪除' }))) return
    if (await run(`del-${m.id}`, () => api.deleteAIModel(m.id), '已刪除')) reload()
  }

  const f = editing?.form
  const p = f ? prov(f.provider) : undefined
  const set = (patch: Partial<AIModelIn>) => editing && setEditing({ ...editing, form: { ...editing.form, ...patch } })

  return (
    <Card
      title="AI 模型"
      icon={<Icons.brain />}
      actions={
        <button className="btn btn-primary btn-sm" onClick={() => setEditing({ id: null, form: { name: '', provider: meta.ai_providers[0]?.id ?? 'anthropic', model: '', api_key: '', base_url: '', options: {} } })}>
          <Icons.plus className="h-3.5 w-3.5" /> 新增模型
        </button>
      }
    >
      {loading ? (
        <Skeleton />
      ) : !data?.length ? (
        <Empty icon={<Icons.brain className="h-5 w-5" />} title="尚未新增 AI 模型" hint="AI 交易員、AI 審核與 Pine Script 轉換都需要一個 AI 模型。" />
      ) : (
        <div className="space-y-2.5">
          {data.map((m) => (
            <ListRow
              key={m.id}
              icon={<Icons.brain className="h-5 w-5" />}
              title={m.name}
              subtitle={
                <>
                  {prov(m.provider)?.label ?? m.provider} · {m.model || prov(m.provider)?.default_model}
                  <span className="text-muted">
                    {m.api_key ? ` · ${m.api_key}` : ''}
                    {typeof m.options?.effort === 'string' && m.options.effort ? ` · effort ${m.options.effort}` : ''}
                  </span>
                </>
              }
              actions={
                <>
                  <button className="btn btn-secondary btn-sm" onClick={() => test(m)} disabled={busy === `test-${m.id}`}>
                    {busy === `test-${m.id}` ? <Spinner className="h-3.5 w-3.5" /> : <Icons.bolt className="h-3.5 w-3.5" />} 測試
                  </button>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => setEditing({ id: m.id, m, form: { name: m.name, provider: m.provider, model: m.model, api_key: '', base_url: m.base_url ?? '', options: { ...m.options } } })}
                  >
                    <Icons.edit className="h-3.5 w-3.5" /> 編輯
                  </button>
                  <button className="btn btn-danger btn-sm" onClick={() => remove(m)} aria-label="刪除">
                    <Icons.trash className="h-3.5 w-3.5" />
                  </button>
                </>
              }
            >
              {testResult[m.id] ? <div className={`break-words text-xs ${testResult[m.id].ok ? 'text-green-300' : 'text-red-300'}`}>{testResult[m.id].text}</div> : undefined}
            </ListRow>
          ))}
        </div>
      )}

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        title={editing?.id ? '編輯 AI 模型' : '新增 AI 模型'}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setEditing(null)}>取消</button>
            <button className="btn btn-primary" onClick={save} disabled={busy === 'save'}>
              {busy === 'save' && <Spinner />} 儲存
            </button>
          </>
        }
      >
        {f && (
          <div className="space-y-4">
            <Field label="名稱">
              <input className="input" value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="例如：Claude 主力" autoFocus />
            </Field>
            <Field label="供應商">
              <select className="input" value={f.provider} onChange={(e) => set({ provider: e.target.value })}>
                {meta.ai_providers.map((x) => (
                  <option key={x.id} value={x.id}>{x.label}</option>
                ))}
              </select>
            </Field>
            <Field label="模型" hint="留空＝使用預設模型">
              <input className="input font-mono" value={f.model} onChange={(e) => set({ model: e.target.value })} placeholder={p?.default_model} />
            </Field>
            <Field
              label="API Key"
              hint={editing?.m ? `目前：${editing.m.api_key || '（未設定）'}，留空＝不修改` : p && !p.needs_key ? '此供應商不需要 API Key' : undefined}
            >
              <input className="input font-mono" type="password" autoComplete="new-password" value={f.api_key ?? ''} onChange={(e) => set({ api_key: e.target.value })} />
            </Field>
            <Field label="Base URL" hint="留空＝使用預設">
              <input className="input font-mono" value={f.base_url ?? ''} onChange={(e) => set({ base_url: e.target.value })} placeholder={p?.default_base_url ?? '預設'} />
            </Field>
            {f.provider === 'anthropic' && (
              <Field label="推理強度（effort）" hint="越高越仔細，但較慢、費用較高">
                <select className="input" value={String(f.options.effort ?? '')} onChange={(e) => set({ options: { ...f.options, effort: e.target.value } })}>
                  {EFFORTS.map((x) => (
                    <option key={x} value={x}>{x || '預設'}</option>
                  ))}
                </select>
              </Field>
            )}
          </div>
        )}
      </Modal>
    </Card>
  )
}
