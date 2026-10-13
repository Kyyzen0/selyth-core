// Evaluation oracle: which obligations of the ledger does a candidate implementation violate?
//
//   node eval/oracle.ts <candidate ledger.ts>   ->   {"violated": [...], "necessity_lost": [...]}
//
// It runs the predicates of test/ledger-properties.ts on pseudo-random inputs of the candidate.
// It shares nothing with Dafny, the translator or the proof model, so it can label a submission
// without trusting the tool under evaluation. Sampling is not proof: a violation it misses is a
// label error of the evaluation (the harness reports the predicates' blind spots), and the
// preconditions are reported apart, since a candidate does not "violate" a precondition: it
// only stops needing it ("necessity_lost").
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { buildProperties, SAMPLES, type Ledger } from "../test/ledger-properties.ts";

const here = new URL("../../docs/specifications/examples/ledger/ledger.obligations.json", import.meta.url);
const kinds = new Map(
  (JSON.parse(readFileSync(here, "utf8")) as { obligations: Array<{ obligation_id: string; kind: string }> })
    .obligations.map((o) => [o.obligation_id, o.kind]),
);

const target = process.argv[2];
if (!target) {
  console.error("usage: oracle.ts <candidate ledger.ts>");
  process.exit(2);
}
const candidate = (await import(pathToFileURL(resolve(target)).href)) as Ledger;

const holds = (property: () => boolean): boolean => {
  try {
    for (let i = 0; i < SAMPLES; i++) if (!property()) return false;
    return true;
  } catch {
    return false;                                   // a crash, or a loop that does not stop, is a violation
  }
};

const violated: string[] = [], necessityLost: string[] = [];
for (const [id, property] of Object.entries(buildProperties(candidate)).sort(([a], [b]) => a.localeCompare(b))) {
  if (kinds.get(id) === "precondition") {
    if (!holds(property)) necessityLost.push(id);   // those predicates say: where the precondition fails, the code breaks
  } else if (!holds(property)) {
    violated.push(id);
  }
}
console.log(JSON.stringify({ violated, necessity_lost: necessityLost }));
