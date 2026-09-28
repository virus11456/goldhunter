import { Link } from 'react-router-dom'
import { api } from '../api'
import { BotAiBadge, BotStatusBadge, Card, Empty, Icons, Skeleton, Spinner, StatCard, useAction, useConfirm, useLoader } from '../components/ui'
import { baseOf } from '../components/forms'
import { fmtNum, fmtSigned, kindLabel, pnlClass, timeAgo } from '../lib/format'

export default function Dashboard() {
  const { data, loading, reload } = useLoader(() => api.dashboard(), [], 10_000)
  const confirm = useConfirm()
  const { busy, run } = useAction()

  const stopAll = async () => {
    const ok = await confirm({
      title: '緊急全部停止',
      danger: true,
      confirmText: '全部停止',
      message: (
        <>
          將立即停止所有運行中的 Bot。
          <br />
          <b className="text-red-300">注意：不會自動平倉</b>，現有持倉請到「監控」頁自行決定是否平倉。
        </>
      ),
    })
    if (!ok) return
    const r = await run('stop', () => api.stopAll())
    if (r) {
      await reload()
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-50">總覽</h1>
          <p className="text-xs text-muted">每 10 秒自動更新</p>
        </div>
        <button
          className="btn border-down bg-down px-4 py-2 font-semibold text-white shadow-[0_6px_20px_-8px_rgba(246,70,93,0.8)] hover:brightness-110"
          onClick={stopAll}
          disabled={busy === 'stop'}
        >
          {busy === 'stop' ? <Spinner /> : <Icons.stop />}
          緊急全部停止
        </button>
      </div>

      {loading && !data ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="skeleton h-[104px] rounded-xl" />
          ))}
        </div>
      ) : data ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard label="總權益 Total Equity" value={<>{fmtNum(data.total_equity)} <span className="text-sm text-muted">USDT</span></>} sub="所有 Bot 最新權益加總" accent />
          <StatCard
            label="運行中 Bots"
            value={
              <>
                <span className={data.bots_running ? 'text-up' : ''}>{data.bots_running}</span>
                <span className="text-lg text-muted"> / {data.bots_total}</span>
              </>
            }
            sub={data.bots_running ? '正在交易' : '目前無運行中的 Bot'}
          />
          <StatCard
            label="今日已實現損益"
            value={<span className={pnlClass(data.realized_pnl_today)}>{fmtSigned(data.realized_pnl_today)}</span>}
            sub="UTC 00:00 起算"
          />
          <StatCard label="今日成交筆數" value={data.trades_today} sub="UTC 00:00 起算" />
        </div>
      ) : null}

      <Card
        title="Bots"
        icon={<Icons.bot />}
        actions={
          <Link to="/settings?tab=bots" className="btn btn-primary btn-sm">
            <Icons.plus className="h-3.5 w-3.5" /> 建立 Bot
          </Link>
        }
      >
        {loading && !data ? (
          <Skeleton rows={3} />
        ) : !data?.bots.length ? (
          <Empty
            icon={<Icons.bot className="h-5 w-5" />}
            title="還沒有 Bot"
            hint="先到「設定」新增交易所帳戶與策略，再建立 Bot。"
            action={
              <Link to="/settings" className="btn btn-primary btn-sm">
                前往設定
              </Link>
            }
          />
        ) : (
          <div className="space-y-2.5">
            {data.bots.map((b) => (
              <Link
                key={b.id}
                to={`/monitor?bot=${b.id}`}
                className="group flex flex-wrap items-center gap-3 rounded-xl border border-line bg-[#0f1216] px-4 py-3 transition-all hover:border-gold/40 hover:bg-[#13171c]"
              >
                <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${b.running ? 'bg-gold/15 text-gold' : 'bg-panel2 text-muted'}`}>
                  <Icons.bot className="h-5 w-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate font-semibold text-slate-100">{b.name}</span>
                    <BotAiBadge bot={b} />
                    {b.halted_reason && <span className="badge badge-red">熔斷</span>}
                  </div>
                  <div className="mt-0.5 truncate text-xs">
                    <span className="text-gold/90">{b.strategy_name ?? '—'}</span>
                    <span className="text-muted"> · {kindLabel(b.strategy_kind)} · {b.symbols.map(baseOf).join(', ')} · {b.timeframe}</span>
                  </div>
                </div>
                <div className="text-right">
                  <div className="num font-mono text-sm text-slate-100">{b.equity !== null ? fmtNum(b.equity) : '—'}</div>
                  <div className="text-[11px] text-muted">{b.last_run_at ? `更新 ${timeAgo(b.last_run_at)}` : '權益'}</div>
                </div>
                <BotStatusBadge running={b.running} status={b.status} error={b.last_error} />
                <Icons.chevron className="hidden h-4 w-4 text-muted transition-transform group-hover:translate-x-0.5 group-hover:text-gold sm:block" />
              </Link>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
