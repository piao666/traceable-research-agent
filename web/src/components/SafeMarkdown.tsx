import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { safeExternalUrl, type CitationTarget } from "../lib/evidence";

/** A deliberately bounded Markdown reader: all text is rendered by React.
 * No raw HTML, remote images, embedded frames, scripts or arbitrary URL schemes.
 * Unsupported Markdown remains readable literal text; downloads keep originals.
 */
export function SafeMarkdown({ markdown, runId, citations }: { markdown: string; runId: string; citations: Map<string, CitationTarget> }) {
  function inline(value: string): ReactNode[] {
    const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^\s)]+\)|\[CIT-[\w-]+\])/g;
    const nodes: ReactNode[] = [];
    let offset = 0;
    for (const match of value.matchAll(pattern)) {
      const token = match[0], position = match.index!;
      nodes.push(value.slice(offset, position));
      if (token.startsWith("`")) nodes.push(<code key={position}>{token.slice(1, -1)}</code>);
      else if (token.startsWith("**")) nodes.push(<strong key={position}>{inline(token.slice(2, -2))}</strong>);
      else {
        const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
        if (link) {
          const safe = safeExternalUrl(link[2]);
          nodes.push(safe ? <a key={position} href={safe} aria-label={`${link[1]}（新窗口）`} target="_blank" rel="noopener noreferrer">{link[1]}</a> : <span key={position}>{token}</span>);
        } else {
          const label = token.slice(1, -1), target = citations.get(label);
          nodes.push(target?.resolved ? <Link key={position} className="citation-link" to={`/runs/${encodeURIComponent(runId)}/evidence?citation=${encodeURIComponent(label)}`} title="查看对应原始片段">{token}</Link> : <span key={position} className="unresolved-citation" title="没有可解析的对应证据，不能视作受支持引用">{token}（未解析）</span>);
        }
      }
      offset = position + token.length;
    }
    nodes.push(value.slice(offset));
    return nodes;
  }
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  const cells = (line: string) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i], key = i;
    if (!line.trim()) continue;
    if (/^\s*```/.test(line)) {
      const code: string[] = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i]);
      blocks.push(<pre key={key}><code>{code.join("\n")}</code></pre>); continue;
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      const Tag = `h${Math.min(heading[1].length + 1, 6)}` as "h2" | "h3" | "h4" | "h5" | "h6";
      blocks.push(<Tag key={key}>{inline(heading[2])}</Tag>); continue;
    }
    if (line.includes("|") && i + 1 < lines.length && cells(lines[i + 1]).every((cell) => /^:?-{3,}:?$/.test(cell))) {
      const headers = cells(line), rows: string[][] = []; i++;
      while (i + 1 < lines.length && lines[i + 1].includes("|") && lines[i + 1].trim()) rows.push(cells(lines[++i]));
      blocks.push(<div className="markdown-table" role="region" aria-label="报告表格（可横向滚动）" tabIndex={0} key={key}><table><thead><tr>{headers.map((cell, j) => <th key={j} scope="col">{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row, j) => <tr key={j}>{row.map((cell, k) => <td key={k}>{inline(cell)}</td>)}</tr>)}</tbody></table></div>); continue;
    }
    const bullet = /^\s*([-*+] |\d+\. )/.exec(line);
    if (bullet) {
      const ordered = /^\d/.test(bullet[1]), items = [line.replace(/^\s*(?:[-*+] |\d+\. )/, "")];
      const matcher = ordered ? /^\s*\d+\. / : /^\s*[-*+] /;
      while (i + 1 < lines.length && matcher.test(lines[i + 1])) items.push(lines[++i].replace(matcher, ""));
      const List = ordered ? "ol" : "ul";
      blocks.push(<List key={key}>{items.map((item, j) => <li key={j}>{inline(item)}</li>)}</List>); continue;
    }
    if (/^>\s?/.test(line)) { blocks.push(<blockquote key={key}>{inline(line.replace(/^>\s?/, ""))}</blockquote>); continue; }
    if (/^\s*(?:---+|\*\*\*+)\s*$/.test(line)) { blocks.push(<hr key={key} />); continue; }
    blocks.push(<p key={key}>{inline(line)}</p>);
  }
  return <article className="safe-markdown">{blocks.map((block, index) => <Fragment key={index}>{block}</Fragment>)}</article>;
}
