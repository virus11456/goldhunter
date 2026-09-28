import { useEffect, useRef } from 'react'
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  type IChartApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipProps,
} from 'recharts'
import type { BacktestTrade } from '../api'
import { fmtNum, fmtPrice, fmtShortTime, fmtTime } from '../lib/format'

export const CHART = {
  gold: '#f0b90b',
  baseline: '#848e9c',
  up: '#0ecb81',
  down: '#f6465d',
  grid: '#1f242b',
  axis: '#5e6673',
  text: '#848e9c',
  surface: '#15191e',
}

export interface EquityDatum {
  t: number // ms
  equity: number
  baseline?: number | null
}

function EquityTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null
  const eq = payload.find((p) => p.dataKey === 'equity')?.value
  const bl = payload.find((p) => p.dataKey === 'baseline')?.value
  const diff = typeof eq === 'number' && typeof bl === 'number' ? eq - bl : null
  return (
    <div className="rounded-lg border border-line bg-[#0f1216]/95 px-3 py-2 text-xs shadow-xl backdrop-blur">
      <div className="mb-1 font-mono text-muted">{fmtTime(label as number)}</div>
      {payload.map((p) => (
        <div key={String(p.dataKey)} className="flex items-center justify-between gap-4">
          <span className="flex items-center gap-1.5 text-slate-300">
            <span className="inline-block h-0.5 w-3" style={{ background: p.color }} />
            {p.name}
          </span>
          <span className="num font-mono text-slate-100">{fmtNum(p.value as number)}</span>
        </div>
      ))}
      {diff !== null && (
        <div className="mt-1 flex justify-between gap-4 border-t border-line pt-1">
          <span className="text-muted">差額</span>
          <span className={`num font-mono ${diff >= 0 ? 'text-up' : 'text-down'}`}>
            {diff >= 0 ? '+' : ''}
            {fmtNum(diff)}
          </span>
        </div>
      )}
    </div>
  )
}

export function EquityChart({ data, height = 260, initial }: { data: EquityDatum[]; height?: number; initial?: number }) {
  const hasBaseline = data.some((d) => typeof d.baseline === 'number')
  const spanMs = data.length > 1 ? data[data.length - 1].t - data[0].t : 0
  const tickFmt = (v: number) => (spanMs > 3 * 86400_000 ? fmtShortTime(v).slice(0, 5) : fmtShortTime(v).slice(6))
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke={CHART.grid} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={tickFmt}
            stroke={CHART.axis}
            tickLine={false}
            axisLine={{ stroke: CHART.grid }}
            minTickGap={40}
          />
          <YAxis
            domain={['auto', 'auto']}
            stroke={CHART.axis}
            tickLine={false}
            axisLine={false}
            width={64}
            tickFormatter={(v: number) => fmtNum(v, v >= 1000 ? 0 : 2)}
          />
          <Tooltip content={<EquityTooltip />} cursor={{ stroke: '#3a414b', strokeDasharray: '3 3' }} />
          {initial !== undefined && <ReferenceLine y={initial} stroke="#3a414b" strokeDasharray="4 4" />}
          {hasBaseline && <Legend verticalAlign="top" height={24} iconType="plainline" wrapperStyle={{ fontSize: 12, color: CHART.text }} />}
          {hasBaseline && (
            <Line
              type="monotone"
              dataKey="baseline"
              name="無 AI 對照組"
              stroke={CHART.baseline}
              strokeWidth={1.5}
              strokeDasharray="5 4"
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          )}
          <Line
            type="monotone"
            dataKey="equity"
            name={hasBaseline ? '有 AI 副駕駛' : '權益'}
            stroke={CHART.gold}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, stroke: CHART.surface, strokeWidth: 2 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

/** Candles [ts,o,h,l,c] with buy/sell markers from backtest trades. */
export function CandleChart({ candles, trades, height = 380 }: { candles: [number, number, number, number, number][]; trades: BacktestTrade[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const chart = createChart(el, {
      height,
      width: el.clientWidth,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: CHART.text,
        fontSize: 11,
        attributionLogo: false,
      },
      grid: { vertLines: { color: CHART.grid }, horzLines: { color: CHART.grid } },
      rightPriceScale: { borderColor: '#2a2f36' },
      timeScale: { borderColor: '#2a2f36', timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
      localization: {
        timeFormatter: (t: Time) => fmtTime((t as number) * 1000),
        priceFormatter: (p: number) => fmtPrice(p),
      },
    })
    chartRef.current = chart
    const series = chart.addSeries(CandlestickSeries, {
      upColor: CHART.up,
      downColor: CHART.down,
      borderUpColor: CHART.up,
      borderDownColor: CHART.down,
      wickUpColor: CHART.up,
      wickDownColor: CHART.down,
    })
    // dedupe + sort by time (lightweight-charts requires strictly ascending)
    const seen = new Set<number>()
    const bars = candles
      .map(([ts, o, h, l, c]) => ({ time: Math.floor(ts / 1000) as UTCTimestamp, open: o, high: h, low: l, close: c }))
      .filter((b) => (seen.has(b.time) ? false : (seen.add(b.time), true)))
      .sort((a, b) => a.time - b.time)
    series.setData(bars)

    // snap trade timestamps to nearest bar time <= trade ts (candles may be down-sampled)
    const times = bars.map((b) => b.time as number)
    const snap = (sec: number): number => {
      let lo = 0
      let hi = times.length - 1
      if (!times.length) return sec
      if (sec <= times[0]) return times[0]
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1
        if (times[mid] <= sec) lo = mid
        else hi = mid - 1
      }
      return times[lo]
    }
    const markers: SeriesMarker<Time>[] = trades
      .map((t) => {
        const buy = t.side === 'buy'
        const text = t.reduce_only ? (buy ? '平空' : '平多') : buy ? '買' : '賣'
        return {
          time: snap(Math.floor(t.ts / 1000)) as UTCTimestamp,
          position: buy ? ('belowBar' as const) : ('aboveBar' as const),
          color: t.reduce_only ? '#c7cdd6' : buy ? CHART.up : CHART.down,
          shape: buy ? ('arrowUp' as const) : ('arrowDown' as const),
          text,
        }
      })
      .sort((a, b) => (a.time as number) - (b.time as number))
    createSeriesMarkers(series, markers)
    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }))
    ro.observe(el)
    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [candles, trades, height])

  return <div ref={ref} className="w-full" style={{ height }} />
}
