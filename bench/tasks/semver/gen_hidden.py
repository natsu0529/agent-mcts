"""Build hidden/test_hidden_semver.py: expected values come from node-semver (the copy
bundled with npm), and reference.py must agree with every one of them.

    python gen_hidden.py   # needs node and npm on PATH

Run `ruff format` on the generated file afterwards.
"""

import json
import subprocess
from pathlib import Path

import reference

VERSIONS = [
    "0.0.0",
    "0.0.3",
    "0.0.4",
    "0.1.0",
    "0.2.3",
    "0.2.9",
    "0.3.0",
    "1.0.0-alpha",
    "1.0.0-alpha.1",
    "1.0.0-alpha.beta",
    "1.0.0-beta.2",
    "1.0.0-beta.11",
    "1.0.0-rc.1",
    "1.0.0",
    "1.2.2",
    "1.2.3-alpha.3",
    "1.2.3-alpha.7",
    "1.2.3-a10",
    "1.2.3-a9",
    "1.2.3-11",
    "1.2.3-2",
    "1.2.3",
    "v1.2.3",
    " 1.2.3+build.5 ",
    "1.2.9",
    "1.2.10",
    "1.3.0-0",
    "1.3.0",
    "1.9.9",
    "2.0.0-0",
    "2.0.0-rc.1",
    "2.0.0",
    "2.3.4",
    "2.3.5",
    "2.4.0-pre",
    "2.9.0",
    "3.0.0",
    "3.4.5-alpha.9",
    "10.0.0",
]

RANGES = [
    "",
    "*",
    "x",
    "1.2.3",
    "=1.2.3",
    "v1.2.3",
    "1.2.3+other",
    ">1.2.3",
    ">=1.2.3",
    "<1.2.3",
    "<=1.2.3",
    ">= 1.2.3",
    ">=1.2.3 <2",
    ">= 1.2.3 < 2.0.0",
    "1.x",
    "1",
    "1.2.x",
    "1.2",
    "1.2.*",
    "1.X",
    ">1",
    ">1.2",
    ">=1.2",
    "<1.2",
    "<=1.2",
    "<=1",
    "<1",
    "=1.x",
    ">*",
    "<*",
    "~1.2.3",
    "~1.2",
    "~1",
    "~0.2.3",
    "~1.2.3-alpha.3",
    "^1.2.3",
    "^0.2.3",
    "^0.0.3",
    "^1.2.3-alpha.3",
    "^0.0.3-beta",
    "^1.2.x",
    "^1.2",
    "^0.0.x",
    "^0.0",
    "^1.x",
    "^1",
    "^0.x",
    "^0",
    "1.2.3 - 2.3.4",
    "1.2 - 2.3.4",
    "1.2.3 - 2.3",
    "1.2.3 - 2",
    "1.2.3-alpha.3 - 2.0.0",
    ">1.2.3-alpha.3",
    ">=1.0.0-alpha.1 <1.0.0",
    ">=1.0.0-beta.2",
    "<1.0.0-rc.1",
    "1.0.0-alpha.beta",
    "1.x || >=3.0.0",
    "<0.1.0 || >=2.3.5 <3",
    "~1.2.3 || ^2.3.4",
    "<2.0.0",
    ">1.2.3 <1.2.3",
]

BAD_VERSIONS = [
    "1.2",
    "01.2.3",
    "1.02.3",
    "1.2.3-01",
    "1.2.3-",
    "1.2.3.4",
    "a.b.c",
    "1.2.3-al$pha",
    "",
]
BAD_RANGES = [">>1.2.3", "1.2.3.4", "abc", "^^1.2.3", "~1.2.3.4", ">=1.2.3 <a.b.c", "1.2.3 || >>2"]

NODE = """
const semver = require(process.argv[1]);
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = {
  pairs: input.pairs.map(([v, r]) => semver.satisfies(v, r)),
  badVersions: input.badVersions.map(v => semver.valid(v) === null),
  badRanges: input.badRanges.map(r => {
    try { new semver.Range(r); return false } catch { return true }
  }),
};
process.stdout.write(JSON.stringify(out));
"""


def node_semver_path() -> str:
    root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, check=True)
    return str(Path(root.stdout.strip()) / "npm" / "node_modules" / "semver")


def main() -> None:
    pairs = [(v, r) for r in RANGES for v in VERSIONS]
    payload = {"pairs": pairs, "badVersions": BAD_VERSIONS, "badRanges": BAD_RANGES}
    node = subprocess.run(
        ["node", "-e", NODE, node_semver_path()],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    oracle = json.loads(node.stdout)
    assert all(oracle["badVersions"]), "node-semver accepts a version listed as invalid"
    assert all(oracle["badRanges"]), "node-semver accepts a range listed as invalid"

    mismatches = []
    for (v, r), expected in zip(pairs, oracle["pairs"], strict=True):
        got = reference.satisfies(v, r)
        if got != expected:
            mismatches.append((v, r, expected, got))
    for v in BAD_VERSIONS:
        try:
            reference.satisfies(v, "*")
            mismatches.append((v, "*", "ValueError", "no error"))
        except ValueError:
            pass
    for r in BAD_RANGES:
        try:
            reference.satisfies("1.2.3", r)
            mismatches.append(("1.2.3", r, "ValueError", "no error"))
        except ValueError:
            pass
    if mismatches:
        for m in mismatches:
            print("MISMATCH", m)
        raise SystemExit(1)

    # One test per range, checked against every version: a test per (version, range)
    # pair would let an implementation that always answers False score about 80%.
    expected = dict(zip(pairs, oracle["pairs"], strict=True))
    lines = [
        "import pytest",
        "from semverrange import satisfies",
        "",
        f"VERSIONS = {VERSIONS!r}",
        "",
        "# range -> the versions in VERSIONS that it matches",
        "MATCHES = {",
        *(f"    {r!r}: {[v for v in VERSIONS if expected[(v, r)]]!r},," for r in RANGES),
        "}",
        "",
        f"BAD_VERSIONS = {BAD_VERSIONS!r}",
        f"BAD_RANGES = {BAD_RANGES!r}",
        "",
        "",
        '@pytest.mark.parametrize("range_", list(MATCHES))',
        "def test_satisfies(range_):",
        "    got = [v for v in VERSIONS if satisfies(v, range_)]",
        "    assert got == MATCHES[range_]",
        "",
        "",
        '@pytest.mark.parametrize("version", BAD_VERSIONS)',
        "def test_bad_version(version):",
        "    with pytest.raises(ValueError):",
        '        satisfies(version, "*")',
        "",
        "",
        '@pytest.mark.parametrize("range_", BAD_RANGES)',
        "def test_bad_range(range_):",
        "    with pytest.raises(ValueError):",
        '        satisfies("1.2.3", range_)',
        "",
    ]
    text = "\n".join(lines).replace(",,", ",")
    out = Path(__file__).parent / "hidden" / "test_hidden_semver.py"
    out.write_text(text)
    true_count = sum(oracle["pairs"])
    errors = len(BAD_VERSIONS) + len(BAD_RANGES)
    print(f"wrote {len(pairs)} pairs ({true_count} true) + {errors} error cases")


if __name__ == "__main__":
    main()
