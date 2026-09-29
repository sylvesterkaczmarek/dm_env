import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

meta = json.loads((Path(__file__).parent / "case.json").read_text())
work = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
python = sys.executable
is_rpc = meta["repo"].endswith("dm_env_rpc")
package = "dm_env_rpc" if is_rpc else "dm_env"
source = work / meta["source"]
fixed = source.read_bytes()
summary = {}


def run(label, command, expected=0, cwd=work, timeout=180):
    with (evidence / (label + ".log")).open("w") as log:
        result = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                timeout=timeout, env=os.environ.copy())
    output = (evidence / (label + ".log")).read_text(errors="replace")
    print(label, "exit", result.returncode, output[-500:], flush=True)
    assert result.returncode == expected, (label, result.returncode, output[-12000:])
    return output


def test(label, files, expected=0, cover=False, cwd=work):
    command = [python, "-m", "pytest", *files, "-q", "--tb=short",
               "--junitxml=" + str(evidence / (label + ".xml"))]
    if cover:
        command += ["--cov=" + package, "--cov-branch",
                    "--cov-report=json:" + str(evidence / "coverage.json")]
    run(label, command, expected=expected, cwd=cwd)
    suites = list(ET.parse(evidence / (label + ".xml")).getroot().iter("testsuite"))
    counts = [sum(int(s.attrib.get(key, 0)) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")]
    summary[label] = counts
    print(label, counts, flush=True)
    return counts


assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=work, text=True).strip() == meta["head"]
run("versions", [python, "-m", "pip", "freeze"])
run("dependency-check", [python, "-m", "pip", "check"])
expected_new = 32 if is_rpc else 38
expected_failures = 19 if is_rpc else 14
original = subprocess.check_output(["git", "show", meta["base"] + ":" + meta["source"]], cwd=work)
try:
    counts = test("fixed-focused", [meta["test"]], cover=True)
    assert counts == [expected_new, 0, 0, 0]
    counts = test("fixed-full", [])
    assert counts[1:] == [0, 0, 0]
    assert counts[0] >= (417 if is_rpc else 202)
    source.write_bytes(original)
    counts = test("original-focused", [meta["test"]], expected=1)
    assert counts == [expected_new, expected_failures, 0, 0]
    counts = test("original-existing", ["--ignore=" + meta["test"]])
    assert counts[1:] == [0, 0, 0]
    assert counts[0] + expected_new == summary["fixed-full"][0]
    if is_rpc:
        output = run("baseline-sdist-roundtrip", [python, "-m", "build", "--no-isolation",
                     "--outdir", str(evidence / "baseline-dist")], expected=1)
        assert "Cannot find third_party/api-common-protos" in output
finally:
    source.write_bytes(fixed)
assert source.read_bytes() == fixed
assert test("restored-focused", [meta["test"]]) == [expected_new, 0, 0, 0]
run("new-test-lint", [python, "-m", "ruff", "check", "--isolated", "--select", "E9,F", meta["test"]])
run("source-correctness-lint", [python, "-m", "ruff", "check", "--isolated", "--select", "E9,F63,F7,F82", meta["source"]])
format_diff = run("new-test-format", [python, "-m", "yapf", "--style={based_on_style: google, indent_width: 2}", "-d", meta["test"]])
assert not format_diff.strip(), format_diff
run("patch-check", ["git", "diff", "--check", meta["base"]])
run("source-restoration", ["git", "diff", "--exit-code"])
if is_rpc:
    for kind in ("sdist", "wheel"):
        run("build-" + kind, [python, "-m", "build", "--no-isolation", "--" + kind,
                             "--outdir", str(evidence / "dist")])
    sdist, = (evidence / "dist").glob("*.tar.gz")
    with tempfile.TemporaryDirectory() as directory:
        output = run("fixed-sdist-roundtrip", [python, "-m", "pip", "wheel", "--no-deps",
                     "--no-build-isolation", "--wheel-dir", directory, str(sdist)], expected=1)
        assert "Cannot find third_party/api-common-protos" in output
else:
    run("package-build", [python, "-m", "build", "--no-isolation",
                          "--outdir", str(evidence / "dist")])
wheel, = (evidence / "dist").glob("*.whl")
run("install-wheel", [python, "-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel)])
with tempfile.TemporaryDirectory() as directory:
    outside = Path(directory)
    copied = outside / Path(meta["test"]).name
    shutil.copyfile(work / meta["test"], copied)
    code = f"import {package}; from pathlib import Path; p=Path({package}.__file__).resolve(); print(p); assert not p.is_relative_to(Path({str(work)!r}))"
    run("installed-import", [python, "-c", code], cwd=outside)
    assert test("installed-focused", [str(copied)], cwd=outside) == [expected_new, 0, 0, 0]
(evidence / "summary.json").write_text(json.dumps(summary, indent=2))
print("VALIDATION COMPLETE", meta["head"], flush=True)
