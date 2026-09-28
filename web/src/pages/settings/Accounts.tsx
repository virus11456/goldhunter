import { useState } from 'react'
import { api, type Account, type AccountIn } from '../../api'
import { NumInput } from '../../components/forms'
import { Card, Empty, Field, Icons, ListRow, Modal, Skeleton, Spinner, Toggle, useAction, useConfirm, useLoader, useToast } from '../../components/ui'
import { useMeta } from '../../lib/meta'
import { fmtNum } from '../../lib/format'

const blank = (exchange_id: string): AccountIn => ({
  name: '',
  exchange_id,
  api_key: '',
  secret: '',
  passphrase: '',
  testnet: false,
  paper: true,
  paper_cash: 10000,
})

export default function Accounts() {
  const meta = useMeta()
  const { data, loading, reload } = useLoader(() => api.listAccounts(), [])
  const [editing, setEditing] = useState<{ id: number | null; form: AccountIn; acc?: Account } | null>(null)
  const [testResult, setTestResult] = useState<Record<number, { ok: boolean; text: string }>>({})
  const { busy, run } = useAction()
  const confirm = useConfirm()
  const toast = useToast()
  const firstEx = meta.exchanges.find((e) => !e.planned)?.id ?? 'binance'
  const exLabel = (id: string) => meta.exchanges.find((e) => e.id === id)?.label ?? id

  const save = async () => {
    if (!editing) return
    const f = editing.form
    if (!f.name.trim()) return toast.error('請輸入名稱')
    const body: AccountIn = {
      ...f,
      api_key: f.api_key || null,
      secret: f.secret || null,
      passphrase: f.passphrase || null,
    }
    const r = await run('save', () => (editing.id ? api.updateAccount(editing.id, body) : api.createAccount(body)), '已儲存')
    if (r) {
      setEditing(null)
      reload()
    }
  }

  const test = async (a: Account) => {
    const r = await run(`test-${a.id}`, () => api.testAccount(a.id))
    if (!r) return
    setTestResult((x) => ({
      ...x,
      [a.id]: r.ok ? { ok: true, text: `連線成功｜權益 ${fmtNum(r.balance.total)} ${r.balance.currency}，可用 ${fmtNum(r.balance.free)}` } : { ok: false, text: r.error },
    }))
  }

  const remove = async (a: Account) => {
    if (!(await confirm({ title: `刪除帳戶「${a.name}」？`, danger: true, confirmText: '刪除', message: '此動作無法復原。' }))) return
    if (await run(`del-${a.id}`, () => api.deleteAccount(a.id), '已刪除')) reload()
  }

  const f = editing?.form
  const ex = f ? meta.exchanges.find((e) => e.id === f.exchange_id) : undefined
  const isHL = f?.exchange_id === 'hyperliquid'
  const set = (patch: Partial<AccountIn>) => editing && setEditing({ ...editing, form: { ...editing.form, ...patch } })

  return (
    <Card
      title="交易所帳戶"
      icon={<Icons.exchange />}
      actions={
        <button className="btn btn-primary btn-sm" onClick={() => setEditing({ id: null, form: blank(firstEx) })}>
          <Icons.plus className="h-3.5 w-3.5" /> 新增帳戶
        </button>
      }
    >
      {loading ? (
        <Skeleton />
      ) : !data?.length ? (
        <Empty icon={<Icons.exchange className="h-5 w-5" />} title="尚未新增交易所帳戶" hint="建議先用「模擬交易」帳戶，不需要 API Key 即可使用真實行情模擬下單。" />
      ) : (
        <div className="space-y-2.5">
          {data.map((a) => (
            <ListRow
              key={a.id}
              icon={<Icons.exchange className="h-5 w-5" />}
              title={a.name}
              subtitle={
                <>
                  {exLabel(a.exchange_id)}
                  <span className="text-muted">
                    {a.paper ? ` · 模擬資金 ${fmtNum(a.paper_cash, 0)} USDT` : a.api_key ? ` · ${a.api_key}` : ''}
                  </span>
                </>
              }
              badges={
                <>
                  {a.paper ? <span className="badge badge-blue">模擬交易</span> : <span className="badge badge-gold">實盤</span>}
                  {a.testnet && <span className="badge badge-gray">測試網</span>}
                </>
              }
              actions={
                <>
                  <button className="btn btn-secondary btn-sm" onClick={() => test(a)} disabled={busy === `test-${a.id}`}>
                    {busy === `test-${a.id}` ? <Spinner className="h-3.5 w-3.5" /> : <Icons.bolt className="h-3.5 w-3.5" />} 測試連線
                  </button>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => setEditing({ id: a.id, acc: a, form: { name: a.name, exchange_id: a.exchange_id, testnet: a.testnet, paper: a.paper, paper_cash: a.paper_cash, api_key: '', secret: '', passphrase: '' } })}
                  >
                    <Icons.edit className="h-3.5 w-3.5" /> 編輯
                  </button>
                  <button className="btn btn-danger btn-sm" onClick={() => remove(a)} aria-label="刪除">
                    <Icons.trash className="h-3.5 w-3.5" />
                  </button>
                </>
              }
            >
              {testResult[a.id] ? (
                <div className={`text-xs ${testResult[a.id].ok ? 'text-green-300' : 'text-red-300'}`}>{testResult[a.id].text}</div>
              ) : undefined}
            </ListRow>
          ))}
        </div>
      )}

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        title={editing?.id ? '編輯交易所帳戶' : '新增交易所帳戶'}
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
              <input className="input" value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="例如：Binance 模擬" autoFocus />
            </Field>
            <Field label="交易所">
              <select className="input" value={f.exchange_id} onChange={(e) => set({ exchange_id: e.target.value })}>
                {meta.exchanges.map((e) => (
                  <option key={e.id} value={e.id} disabled={e.planned}>
                    {e.label}
                    {e.planned && !e.label.includes('規劃中') ? '（規劃中）' : ''}
                  </option>
                ))}
              </select>
            </Field>
            <div className="rounded-xl border border-line bg-[#12161b] p-3.5">
              <Toggle checked={f.paper} onChange={(v) => set({ paper: v })} label="模擬交易" />
              <div className="hint">使用該交易所的真實行情，但下單只在本機模擬，不動用真實資金。</div>
              {f.paper && (
                <Field label="模擬初始資金（USDT）" className="mt-3">
                  <NumInput value={f.paper_cash} onChange={(v) => set({ paper_cash: v ?? 10000 })} />
                </Field>
              )}
            </div>
            {!f.paper && (
              <div className="rounded-lg border border-gold/30 bg-gold/10 px-3 py-2 text-xs text-amber-100">
                實盤模式會使用真實資金下單。建議 API Key 只開啟「交易」權限、關閉提領，並設定 IP 白名單。
              </div>
            )}
            <div className="grid grid-cols-1 gap-3">
              <Field label={isHL ? '錢包地址' : 'API Key'} hint={editing?.acc ? `目前：${editing.acc.api_key || '（未設定）'}，留空＝不修改` : f.paper ? '模擬交易可留空' : undefined}>
                <input className="input font-mono" autoComplete="off" value={f.api_key ?? ''} onChange={(e) => set({ api_key: e.target.value })} placeholder={isHL ? '0x…' : ''} />
              </Field>
              <Field label={isHL ? 'API 錢包私鑰' : 'Secret'} hint={editing?.acc ? (editing.acc.has_secret ? '已設定，留空＝不修改' : '尚未設定') : undefined}>
                <input className="input font-mono" type="password" autoComplete="new-password" value={f.secret ?? ''} onChange={(e) => set({ secret: e.target.value })} />
              </Field>
              {ex?.needs_passphrase && (
                <Field label="Passphrase" hint={editing?.acc ? (editing.acc.has_passphrase ? '已設定，留空＝不修改' : '尚未設定') : undefined}>
                  <input className="input font-mono" type="password" autoComplete="new-password" value={f.passphrase ?? ''} onChange={(e) => set({ passphrase: e.target.value })} />
                </Field>
              )}
            </div>
            <Toggle checked={f.testnet} onChange={(v) => set({ testnet: v })} label="使用測試網（Testnet）" />
          </div>
        )}
      </Modal>
    </Card>
  )
}
