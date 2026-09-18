#!/usr/bin/env python3
"""Trivy report helper for scripts/ci/scan.sh (stdlib only, runs on py3.9+).

Subcommands
  count <label> <trivy.json>      → print "label|CRITICAL|HIGH|MEDIUM|OTHER"
                                    HIGH/CRITICAL columns only count severities in
                                    ECI_SEVERITY (so `--severity CRITICAL` really means it)
  report <rows.txt> <outdir> <mode>
                                  → writes junit-trivy.xml, trivy-summary.{md,json},
                                    decision (PASS|FAIL) and prints one summary line

Row format in rows.txt (pipe separated, one per target):
  <target>|<critical>|<high>|<medium>|<other>      scan failure: <target>|scan-error|0|0|0
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from xml.etree import ElementTree as ET

GATE_DEFAULT = "HIGH,CRITICAL"


def gate_severities() -> set[str]:
    raw = os.environ.get("ECI_SEVERITY", GATE_DEFAULT)
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def cmd_count(argv: list[str]) -> int:
    label, path = argv[0], argv[1]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:  # an unreadable report is reported, not fatal
        print(f"{label}|scan-error|0|0|0  # {type(exc).__name__}: {exc}")
        return 0

    gate = gate_severities()
    counts: Counter[str] = Counter()
    other = 0
    for res in data.get("Results") or []:
        for vuln in res.get("Vulnerabilities") or []:
            sev = str(vuln.get("Severity", "")).upper()
            counts[sev] += 1
            if sev not in {"LOW", "MEDIUM"} and sev not in gate:
                other += 1

    crit = counts["CRITICAL"] if "CRITICAL" in gate else 0
    high = counts["HIGH"] if "HIGH" in gate else 0
    print(f"{label}|{crit}|{high}|{counts['MEDIUM']}|{other}")
    return 0


def cmd_report(argv: list[str]) -> int:
    rows_path, out_dir, mode = argv[0], argv[1], argv[2]
    rows = []
    with open(rows_path, encoding="utf-8") as rows_fh:
        raw_rows = rows_fh.read().splitlines()
    for line in raw_rows:
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        rows.append(
            {
                "target": parts[0],
                "critical": parts[1] if len(parts) > 1 else "0",
                "high": parts[2] if len(parts) > 2 else "0",
                "medium": parts[3] if len(parts) > 3 else "0",
                "other": parts[4].split("#")[0].strip() if len(parts) > 4 else "0",
            }
        )

    totals: Counter[str] = Counter()
    blocking = 0
    errored = 0
    suite = ET.Element("testsuite", {"name": "trivy-security-gate", "package": "ecom.ci"})
    md = ["| target | CRITICAL | HIGH | MEDIUM | verdict |", "|---|---:|---:|---:|---|"]

    for r in rows:
        if r["critical"] == "scan-error":
            status, detail = "error", "trivy could not scan this target (see .ci-output/trivy/*.txt)"
            errored += 1
            blocking += 1
            crit = high = med = "—"
        else:
            crit, high, med = int(r["critical"]), int(r["high"]), int(r["medium"])
            totals["CRITICAL"] += crit
            totals["HIGH"] += high
            totals["MEDIUM"] += med
            blocking += crit + high
            status = "failure" if crit + high else "passed"
            detail = f"CRITICAL={crit} HIGH={high} MEDIUM={med}"
        md.append(f"| `{r['target']}` | {crit} | {high} | {med} | {status} |")
        case = ET.SubElement(suite, "testcase", {"classname": "security.trivy", "name": r["target"], "time": "0"})
        if status != "passed":
            node = ET.SubElement(case, "error" if status == "error" else "failure", {"message": detail})
            node.text = detail

    suite.set("tests", str(len(rows)))
    suite.set("errors", str(errored))
    suite.set("failures", str(sum(1 for r in rows if r["critical"] != "scan-error" and int(r["critical"]) + int(r["high"]))))
    ET.ElementTree(suite).write(os.path.join(out_dir, "junit-trivy.xml"), encoding="utf-8", xml_declaration=True)

    decision = "PASS"
    if blocking and mode == "gate":
        decision = "FAIL"
    elif blocking:
        decision = "WARN"

    with open(os.path.join(out_dir, "trivy-summary.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))
        fh.write(
            f"\n\nTotals: CRITICAL={totals['CRITICAL']} HIGH={totals['HIGH']} MEDIUM={totals['MEDIUM']}"
            f" · mode `{mode}` · decision **{decision}**\n"
            "\nThe gate blocks on CRITICAL/HIGH that have an upstream fix "
            "(`--ignore-unfixed`); unfixable OS CVEs go on `.trivyignore` with an owner + expiry.\n"
        )
    with open(os.path.join(out_dir, "decision"), "w", encoding="utf-8") as fh:
        fh.write(decision + "\n")
    with open(os.path.join(out_dir, "trivy-summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"totals": dict(totals), "blocking": blocking, "mode": mode,
                   "decision": decision, "targets": rows}, fh, indent=2)
    print(f"CRITICAL={totals['CRITICAL']} HIGH={totals['HIGH']} MEDIUM={totals['MEDIUM']} "
          f"blocking={blocking} targets={len(rows)} decision={decision}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "report":
        return cmd_report(argv[1:4])
    if len(argv) >= 3 and argv[0] == "count":
        return cmd_count(argv[1:3])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
