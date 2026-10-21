/** Tokenizer shared by the editable workspace and original DAG preview. */
export interface Token {
  cls: string;
  text: string;
}

const KEYWORDS = new Set(
  "and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case type".split(" ")
);
const CONSTANTS = new Set(["None", "True", "False"]);
const BUILTINS = new Set(
  "print len range min max sorted sum dict list str int float bool enumerate zip any all round abs set tuple isinstance".split(" ")
);
const HOST = new Set(["run_agent"]);

const TOKEN =
  /(#[^\n]*)|([rRbBfFuU]{0,2}(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?))|(\b\d[\d_]*(?:\.\d+)?\b)|([A-Za-z_]\w*)|(\s+)|([^\sA-Za-z_\d])/gu;

/** Python source as lines of classed tokens; a token spanning lines is split across them. */
export function highlight(source: string): Token[][] {
  const lines: Token[][] = [[]];
  let previous = "";
  const push = (cls: string, text: string) => {
    text.split("\n").forEach((piece, i) => {
      if (i > 0) lines.push([]);
      if (piece) lines[lines.length - 1]!.push({ cls, text: piece });
    });
  };
  for (const m of source.matchAll(TOKEN)) {
    const [text, comment, string, number, name, space] = m;
    let cls = "op";
    if (comment) cls = "comment";
    else if (string) cls = string.includes('"""') || string.includes("'''") ? "string doc" : "string";
    else if (number) cls = "number";
    else if (space) cls = "";
    else if (name) {
      const next = source.slice((m.index ?? 0) + text.length).match(/^\s*(\S)/)?.[1];
      if (KEYWORDS.has(name)) cls = "keyword";
      else if (CONSTANTS.has(name)) cls = "const";
      else if (HOST.has(name)) cls = "host";
      else if (previous === "def") cls = "fn def";
      else if (BUILTINS.has(name) && next === "(") cls = "builtin";
      else if (next === "(") cls = "fn";
      else if (/^[A-Z][A-Z0-9_]+$/.test(name)) cls = "const";
      else cls = "name";
    }
    if (!space) previous = text;
    push(cls, text);
  }
  return lines;
}

