import { Fragment, type ReactNode } from 'react'

// 極簡、安全的 Markdown 顯示：只處理標題、清單、引用、粗體、行內程式碼。
// 全部以 React 節點輸出（React 會跳脫文字），不使用 dangerouslySetInnerHTML。

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('**')) out.push(<strong key={`${keyBase}-${i++}`} className="font-semibold text-slate-50">{tok.slice(2, -2)}</strong>)
    else out.push(<code key={`${keyBase}-${i++}`} className="rounded bg-black/40 px-1 font-mono text-[12px] text-amber-100">{tok.slice(1, -1)}</code>)
    last = m.index + tok.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function MarkdownLite({ text, className = '' }: { text: string; className?: string }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let list: { ordered: boolean; items: { indent: number; text: string }[] } | null = null
  let para: string[] = []
  let inCode = false
  let code: string[] = []

  const flushPara = () => {
    if (para.length) {
      const k = `p${blocks.length}`
      blocks.push(<p key={k} className="leading-relaxed text-slate-300">{inline(para.join(' '), k)}</p>)
      para = []
    }
  }
  const flushList = () => {
    if (list) {
      const k = `l${blocks.length}`
      const Tag = list.ordered ? 'ol' : 'ul'
      blocks.push(
        <Tag key={k} className={`space-y-1 pl-5 text-slate-300 ${list.ordered ? 'list-decimal' : 'list-disc'} marker:text-muted`}>
          {list.items.map((it, i) => (
            <li key={i} className={`leading-relaxed ${it.indent ? 'ml-4 list-[circle] text-slate-400' : ''}`}>{inline(it.text, `${k}-${i}`)}</li>
          ))}
        </Tag>,
      )
      list = null
    }
  }

  for (const raw of lines) {
    if (raw.trim().startsWith('```')) {
      if (inCode) {
        blocks.push(<pre key={`c${blocks.length}`} className="code-area overflow-x-auto whitespace-pre-wrap">{code.join('\n')}</pre>)
        code = []
        inCode = false
      } else {
        flushPara()
        flushList()
        inCode = true
      }
      continue
    }
    if (inCode) {
      code.push(raw)
      continue
    }
    const line = raw.trimEnd()
    if (!line.trim()) {
      flushPara()
      flushList()
      continue
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    if (h) {
      flushPara()
      flushList()
      const lvl = h[1].length
      const k = `h${blocks.length}`
      const cls =
        lvl === 1
          ? 'text-lg font-semibold text-gold'
          : lvl === 2
            ? 'mt-2 border-b border-line pb-1 text-base font-semibold text-slate-100'
            : 'text-sm font-semibold text-slate-200'
      blocks.push(<div key={k} className={cls}>{inline(h[2], k)}</div>)
      continue
    }
    if (line.startsWith('>')) {
      flushPara()
      flushList()
      const k = `q${blocks.length}`
      blocks.push(
        <blockquote key={k} className="border-l-2 border-gold/50 bg-gold/[0.04] py-1 pl-3 text-xs leading-relaxed text-muted">
          {inline(line.replace(/^>\s?/, ''), k)}
        </blockquote>,
      )
      continue
    }
    const li = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(line)
    if (li) {
      flushPara()
      const ordered = /\d/.test(li[2])
      const indent = li[1].length >= 2 ? 1 : 0
      if (!list || (list.ordered !== ordered && !indent)) {
        flushList()
        list = { ordered, items: [] }
      }
      list.items.push({ indent, text: li[3] })
      continue
    }
    if (/^(-{3,}|\*{3,})$/.test(line.trim())) {
      flushPara()
      flushList()
      blocks.push(<hr key={`r${blocks.length}`} className="border-line" />)
      continue
    }
    flushList()
    para.push(line.trim())
  }
  if (inCode && code.length) blocks.push(<pre key="c-end" className="code-area whitespace-pre-wrap">{code.join('\n')}</pre>)
  flushPara()
  flushList()
  return <div className={`space-y-2.5 text-sm ${className}`}>{blocks.map((b, i) => <Fragment key={i}>{b}</Fragment>)}</div>
}
