import type { ReactNode } from "react";

export interface PolicyCitation {
  id: string;
  policyId: string;
  title: string;
  targetId: string;
}

export function PolicyCitationLink({ citation, compact = false }: { citation: PolicyCitation; compact?: boolean }) {
  return <a
    className={compact ? "inline-citation" : undefined}
    href={`#${citation.targetId}`}
    aria-label={`${citation.policyId}: ${citation.title}`}
    title={citation.title}
    onClick={(event) => {
      event.preventDefault();
      const target = document.getElementById(citation.targetId);
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ block: "center" });
    }}
  >{compact ? citation.policyId : <><span>{citation.policyId}</span><span>{citation.title}</span></>}</a>;
}

// No model HTML, URLs, images, or attributes are interpreted. Citation links
// are created exclusively from the validated structured response's allowlist.
function inline(text: string, citations: readonly PolicyCitation[]): ReactNode[] {
  const pattern = /\[[A-Za-z0-9][A-Za-z0-9_-]*#\d+\]|\*\*([^*\n]+)\*\*|__([^_\n]+)__|\*([^*\n]+)\*|_([^_\n]+)_/g;
  const nodes: ReactNode[] = [];
  let offset = 0;
  for (const match of text.matchAll(pattern)) {
    nodes.push(text.slice(offset, match.index));
    const citation = citations.find((item) => item.id === match[0]);
    const bold = match[1] ?? match[2];
    if (match[0].startsWith("[")) {
      nodes.push(citation ? <PolicyCitationLink key={match.index} citation={citation} compact /> : match[0]);
    } else {
      nodes.push(bold !== undefined
        ? <strong key={match.index}>{inline(bold, citations)}</strong>
        : <em key={match.index}>{inline(match[3] ?? match[4] ?? "", citations)}</em>);
    }
    offset = match.index + match[0].length;
  }
  nodes.push(text.slice(offset));
  return nodes;
}

export function PolicyAnswer({ text, streaming = false, citations = [], evidence = false }: {
  text: string;
  streaming?: boolean;
  citations?: readonly PolicyCitation[];
  evidence?: boolean;
}) {
  const activeCitations = streaming ? [] : citations;
  const blocks: ReactNode[] = [];
  let paragraph: string[] = [];
  let items: string[] = [];
  let ordered = false;
  function flush() {
    if (paragraph.length) {
      blocks.push(<p key={blocks.length}>{inline(paragraph.join(" "), activeCitations)}</p>);
      paragraph = [];
    }
    if (items.length) {
      const content = items.map((item, index) => <li key={index}>{inline(item, activeCitations)}</li>);
      blocks.push(ordered ? <ol key={blocks.length}>{content}</ol> : <ul key={blocks.length}>{content}</ul>);
      items = [];
    }
  }
  for (const line of text.split(/\r?\n/)) {
    const bullet = /^\s*(?:[-+*]|(\d+)[.)])\s+(.+)$/.exec(line);
    const heading = /^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$/.exec(line);
    if (!line.trim()) {
      flush();
    } else if (heading) {
      flush();
      blocks.push(<h4 key={blocks.length}>{inline(heading[1] ?? "", activeCitations)}</h4>);
    } else if (bullet) {
      const nextOrdered = bullet[1] !== undefined;
      if (paragraph.length || (items.length && ordered !== nextOrdered)) flush();
      ordered = nextOrdered;
      items.push(bullet[2] ?? "");
    } else {
      if (items.length) flush();
      paragraph.push(line.trim());
    }
  }
  flush();
  return <div className={`policy-markdown ${evidence ? "evidence-excerpt" : "answer-copy"}${streaming ? " answer-copy--streaming" : ""}`}>{blocks}</div>;
}
