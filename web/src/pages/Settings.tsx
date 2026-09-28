import { useSearchParams } from 'react-router-dom'
import Accounts from './settings/Accounts'
import AIModels from './settings/AIModels'
import Bots from './settings/Bots'
import Intel from './settings/Intel'
import Strategies from './settings/Strategies'

const TABS = [
  { id: 'accounts', label: '交易所帳戶' },
  { id: 'ai', label: 'AI 模型' },
  { id: 'strategies', label: '策略' },
  { id: 'bots', label: 'Bots 機器人' },
  { id: 'intel', label: '資料來源' },
] as const

type TabId = (typeof TABS)[number]['id']

export default function Settings() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab')
  const tab: TabId = TABS.some((t) => t.id === raw) ? (raw as TabId) : 'accounts'
  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold text-slate-50">設定</h1>
      <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
        <div className="flex min-w-max gap-1 border-b border-line">
          {TABS.map((t) => (
            <button key={t.id} className={`tab ${tab === t.id ? 'tab-active' : ''}`} onClick={() => setParams({ tab: t.id })}>
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <div key={tab} className="anim-fade">
        {tab === 'accounts' && <Accounts />}
        {tab === 'ai' && <AIModels />}
        {tab === 'strategies' && <Strategies />}
        {tab === 'bots' && <Bots />}
        {tab === 'intel' && <Intel />}
      </div>
    </div>
  )
}
