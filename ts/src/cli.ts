// Usage: node src/cli.ts lower <obligation-set.json> --backend-version <version>
import { readFileSync } from "node:fs";
import { lower, render } from "./adapter.ts";
import { ObligationSetError, parseObligationSet } from "./canonical.ts";

const argv = process.argv.slice(2);
if (argv.length !== 4 || argv[0] !== "lower" || argv[2] !== "--backend-version" || !argv[3]) {
  console.log("usage: cli.ts lower <obligation-set.json> --backend-version <version>");
  process.exit(2);
}
let text: string;
try {
  text = readFileSync(argv[1] as string, "utf8");
} catch (e) {
  console.error(`[ERROR] ${(e as Error).message}`);
  process.exit(2);
}
try {
  process.stdout.write(render(lower(parseObligationSet(text), argv[3])));
} catch (e) {
  if (e instanceof ObligationSetError) {
    console.error(`[FAIL] ${argv[1]}: ${e.message}`);
    process.exit(1);
  }
  throw e;
}
