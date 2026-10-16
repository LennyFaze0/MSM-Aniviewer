export interface ManualBlock {
  id: string;
  kind: 'title' | 'heading' | 'subheading' | 'paragraph' | 'space';
  text: string;
  label?: string;
}

export interface ManualSection {
  id: string;
  label: string;
  blocks: ManualBlock[];
}

export interface ManualDocument {
  title: string;
  text: string;
  sections: ManualSection[];
}

export interface ManualMatch { start: number; end: number }

export function parseManualDocument(source: string): ManualDocument {
  // Only standalone authoring dividers are omitted. All other source characters,
  // including punctuation, spelling and whitespace, remain in their original order.
  const text = source.replace(/^(?:[0o]{8,}|X{8,}|-{8,})(?:\r?\n|$)/gm, '');
  const sections: ManualSection[] = [];
  let title = '';
  let section: ManualSection | undefined;
  let index = 0;
  for (const part of text.split(/((?:\r?\n){2,})/).filter(Boolean)) {
    const line = part.trimEnd();
    const space = /^\s*$/.test(part);
    const heading = /^\({2,3}[^\r\n]+\){2,3}[^\r\n]*$/.test(line);
    const first = !title && !space;
    if (first) title = line;
    if (!section || heading) {
      section = { id: `manual-section-${sections.length}`, label: first ? line : heading ? line : '', blocks: [] };
      sections.push(section);
    }
    if (!section.label && first) section.label = line;
    const subheading = /^[^\r\n]+ -$/.test(line);
    const label = subheading ? line : line.match(/^([^\r\n]+?(?:[Ss]ection|Browser|Grabber)) - /)?.[1];
    section.blocks.push({
      id: `manual-block-${index++}`,
      kind: space ? 'space' : first ? 'title' : heading ? 'heading' : subheading ? 'subheading' : 'paragraph',
      text: part,
      label: first || heading ? undefined : label,
    });
  }
  return { title, text, sections };
}

export function findManualMatches(text: string, query: string): ManualMatch[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  const haystack = text.toLowerCase();
  const matches: ManualMatch[] = [];
  let from = 0;
  while (from < haystack.length) {
    const start = haystack.indexOf(needle, from);
    if (start < 0) break;
    matches.push({ start, end: start + needle.length });
    from = start + needle.length;
  }
  return matches;
}
