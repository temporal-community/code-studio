const escape = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const inline = (s: string) =>
  s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");

/** A small, escaping Markdown subset: paragraphs, lists, code spans and fences, bold. */
export function markdown(text: string): string {
  const out: string[] = [];
  const blocks = escape(text.trim()).split(/\n{2,}/);
  for (const block of blocks) {
    if (block.startsWith("```")) {
      out.push(`<pre>${block.replace(/^```\w*\n?/, "").replace(/```$/, "")}</pre>`);
    } else if (/^\s*([-*]|\d+\.)\s/.test(block)) {
      const items = block.split(/\n(?=\s*(?:[-*]|\d+\.)\s)/).map((i) => `<li>${inline(i.replace(/^\s*([-*]|\d+\.)\s/, ""))}</li>`);
      out.push(/^\s*\d/.test(block) ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
    } else if (/^#{1,4}\s/.test(block)) {
      out.push(`<h4>${inline(block.replace(/^#+\s/, ""))}</h4>`);
    } else {
      out.push(`<p>${inline(block).replace(/\n/g, "<br>")}</p>`);
    }
  }
  return out.join("");
}

/** Inline-only Markdown (code spans, bold, emphasis) for single-line items such as list entries. */
export const markdownInline = (text: string): string => inline(escape(text));
