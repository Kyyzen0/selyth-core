// Strict JSON parser: refuses what JSON.parse silently accepts or repairs.
// Duplicate object keys (JSON.parse keeps the last), NaN/Infinity and lone
// surrogates are errors. Numbers are kept as strings' absence: the Obligation
// Set schema allows none, so any number parsed here is rejected by validation.

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

export class StrictJsonError extends Error {}

export function parseStrict(text: string): Json {
  if (text.charCodeAt(0) === 0xfeff) throw new StrictJsonError("byte-order mark");
  const seen = new WeakMap<object, Set<string>>();
  let i = 0;
  const fail = (message: string): never => {
    throw new StrictJsonError(`${message} at offset ${i}`);
  };
  const skip = (): void => {
    while (i < text.length && " \t\n\r".includes(text[i] as string)) i++;
  };
  const string = (): string => {
    i++; // opening quote
    let out = "";
    for (;;) {
      const c = text[i];
      if (c === undefined) return fail("unterminated string");
      if (c === '"') { i++; break; }
      if (c < " ") return fail("control character in string");
      if (c === "\\") {
        const e = text[i + 1];
        const simple: Record<string, string> = { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" };
        if (e !== undefined && e in simple) { out += simple[e]; i += 2; continue; }
        if (e === "u" && /^[0-9a-fA-F]{4}$/.test(text.slice(i + 2, i + 6))) {
          out += String.fromCharCode(parseInt(text.slice(i + 2, i + 6), 16));
          i += 6;
          continue;
        }
        return fail("bad escape");
      }
      out += c;
      i++;
    }
    if (!out.isWellFormed()) return fail("lone surrogate");
    return out;
  };
  const value = (): Json => {
    skip();
    const c = text[i];
    if (c === '"') return string();
    if (c === "{") {
      i++;
      const object: { [key: string]: Json } = {};
      const keys = new Set<string>();
      seen.set(object, keys);
      skip();
      if (text[i] === "}") { i++; return object; }
      for (;;) {
        skip();
        if (text[i] !== '"') return fail("object key expected");
        const key = string();
        if (keys.has(key)) return fail(`duplicate object key ${JSON.stringify(key)}`);
        keys.add(key);
        skip();
        if (text[i] !== ":") return fail("':' expected");
        i++;
        Object.defineProperty(object, key, { value: value(), enumerable: true, writable: true, configurable: true });
        skip();
        if (text[i] === ",") { i++; continue; }
        if (text[i] === "}") { i++; return object; }
        return fail("',' or '}' expected");
      }
    }
    if (c === "[") {
      i++;
      const array: Json[] = [];
      skip();
      if (text[i] === "]") { i++; return array; }
      for (;;) {
        array.push(value());
        skip();
        if (text[i] === ",") { i++; continue; }
        if (text[i] === "]") { i++; return array; }
        return fail("',' or ']' expected");
      }
    }
    for (const [literal, v] of [["true", true], ["false", false], ["null", null]] as const) {
      if (text.startsWith(literal, i)) { i += literal.length; return v; }
    }
    const number = /^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?/.exec(text.slice(i));
    if (number) { i += number[0].length; return Number(number[0]); }
    return fail("unexpected token");
  };
  const result = value();
  skip();
  if (i !== text.length) fail("trailing content");
  return result;
}
