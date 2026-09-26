#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const dir = dirname(fileURLToPath(import.meta.url));
const summary = JSON.parse(readFileSync(join(dir, "gcal-summary.json"), "utf8"));
const n = summary.descensos;
const owns = summary.owns_total;
if (owns !== 22) {
  console.error(`G-CAL FAIL: owns_total=${owns} expected 22`);
  process.exit(1);
}
if (n !== 0) {
  console.error(`G-CAL FAIL: descensos=${n}`);
  for (const d of summary.descensos_list || []) {
    console.error(`  DESCENSO ${d.id} clase=${d.clase_mecanica} floor=${d.oracle_floor}`);
  }
  process.exit(1);
}
console.log("G-CAL PASS: 0 descensos on 22 OWNS");
