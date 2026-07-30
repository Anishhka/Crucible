#!/usr/bin/env python3
"""Conformance self-check for a Crucible implementation.

This runs the same mechanical checks that will be run against your submission,
against the public fixtures. It does not check whether your chemistry is right.
It checks whether your program keeps its contract.

Nothing here is a secret. If a check fails, the failure is yours to fix before
submitting; there is no credit for discovering these at review time.

Requirements: python3 >= 3.11, docker, and `jsonschema` importable by the python
you run this with (`pip install jsonschema`). That dependency is for this script
only and has nothing to do with your implementation.

    scripts/selfcheck.sh                    # build the image, run everything
    scripts/selfcheck.sh --image crucible:dev --skip-build
    scripts/selfcheck.sh --only contract,determinism
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

PACKET = Path(__file__).resolve().parent.parent
SCHEMA_DIR = PACKET / "schema"
FIXTURES = PACKET / "fixtures" / "public"

GRADED_ARTIFACTS = ["verdicts.jsonl", "violations.jsonl", "data_quality.json", "run_manifest.json"]
REQUIRED_ARTIFACTS = GRADED_ARTIFACTS + ["feedback/index.json", "report.html"]

# fixture -> the exit codes a conforming implementation may return. 01_baseline
# admits both 0 and 4: nothing in it forces a degraded run, but CONTRACT.md §3
# requires exit 4 whenever a lookup was unavailable, and an honest corpus that
# does not cover all six compositions produces exactly that. See EXPECTATIONS.md.
FIXTURE_EXPECTATIONS = {
    "01_baseline.proposals.json": (0, 4),
    "02_messy.proposals.json": (4,),
    "03_adversarial.proposals.json": (4,),
    "04_malformed_nonfinite.proposals.json": (3,),
    "05_malformed_duplicate_key.proposals.json": (3,),
    "06_malformed_depth.proposals.json": (3,),
    "07_unsupported_version.proposals.json": (3,),
}

FIXED_NOW = "2026-01-01T00:00:00Z"
FIXED_SEED = "12345"

# The compute envelope of CONTRACT.md section 8.1. Every invocation below runs
# inside it, so a submission that only works on a large machine fails here rather
# than at review time.
GRADED_CPUS = "2"
GRADED_MEMORY = "4g"
MAX_IMAGE_BYTES = 2 * 1024**3
WALLCLOCK_BUDGET_SECONDS = 600

ACCELERATOR_PACKAGES = (
    "pytorch-cuda", "cudatoolkit", "cuda-toolkit", "cudnn", "nccl", "cutensor",
    "cusparselt", "nvshmem", "tensorflow-gpu", "tensorrt", "onnxruntime-gpu",
    "cupy-cuda", "jaxlib-cuda", "rocm", "hip-", "oneapi", "intel-extension-for-pytorch",
)


# --------------------------------------------------------------------------- #
# result plumbing
# --------------------------------------------------------------------------- #

@dataclass
class Results:
    passed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def ok(self, name: str) -> None:
        self.passed.append(name)
        print(f"  \033[32mPASS\033[0m  {name}")

    def bad(self, name: str, detail: str) -> None:
        self.failed.append((name, detail))
        print(f"  \033[31mFAIL\033[0m  {name}")
        for line in detail.strip().splitlines():
            print(f"          {line}")

    def skip(self, name: str, why: str) -> None:
        self.skipped.append((name, why))
        print(f"  \033[33mSKIP\033[0m  {name} ({why})")


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


# --------------------------------------------------------------------------- #
# docker helpers
# --------------------------------------------------------------------------- #

def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return run(["docker", "info"]).returncode == 0


def build_image(repo: Path, image: str, res: Results) -> bool:
    section("Build")
    dockerfile = repo / "Dockerfile"
    if not dockerfile.exists():
        res.bad("Dockerfile exists", f"no Dockerfile at {dockerfile}")
        return False
    res.ok("Dockerfile exists")

    print(f"  building {image} (this is not timed, but a slow build is a finding) ...")
    proc = run(["docker", "build", "-t", image, "-f", str(dockerfile), str(repo)])
    if proc.returncode != 0:
        res.bad("docker build succeeds", (proc.stderr or proc.stdout)[-4000:])
        return False
    res.ok("docker build succeeds")
    return True


def container_run(
    image: str,
    args: list[str],
    *,
    in_dir: Path | None = None,
    out_dir: Path | None = None,
    network: str = "none",
    extra_env: dict[str, str] | None = None,
    read_only: bool = True,
    timeout: int = 1800,
) -> subprocess.CompletedProcess:
    """Invoke the image the way it will be invoked at review time."""
    cmd = [
        "docker", "run", "--rm",
        f"--network={network}",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges=true",
        "--cpus", GRADED_CPUS,
        "--memory", GRADED_MEMORY, "--memory-swap", GRADED_MEMORY,
        "--pids-limit", "512",
    ]
    if read_only:
        cmd += ["--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=512m"]
    env = {
        "PYTHONHASHSEED": "0",
        "TZ": "UTC",
        "LC_ALL": "C",
        "SOURCE_DATE_EPOCH": "1767225600",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "MPLBACKEND": "Agg",
    }
    env.update(extra_env or {})
    for k, v in sorted(env.items()):
        cmd += ["-e", f"{k}={v}"]
    if in_dir is not None:
        cmd += ["-v", f"{in_dir}:/data/in:ro"]
    if out_dir is not None:
        cmd += ["-v", f"{out_dir}:/data/out:rw"]
    cmd += [image] + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, 124, exc.stdout or "", (exc.stderr or "") + "\n[timed out]")


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #

def check_cli(image: str, res: Results) -> None:
    section("CLI contract")

    proc = container_run(image, ["--version"])
    if proc.returncode != 0:
        res.bad("--version exits 0", f"exit={proc.returncode}\nstderr={proc.stderr[-1500:]}")
    else:
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        if len(lines) != 1:
            res.bad("--version prints exactly one line to stdout", f"got {len(lines)} lines: {lines!r}")
        else:
            res.ok("--version prints exactly one line to stdout")

    proc = container_run(image, ["--help"])
    res.ok("--help exits 0") if proc.returncode == 0 else res.bad("--help exits 0", f"exit={proc.returncode}")

    proc = container_run(image, ["run", "--nonexistent-flag"])
    if proc.returncode == 2:
        res.ok("unknown flag exits 2")
    else:
        res.bad("unknown flag exits 2", f"exit={proc.returncode} (usage errors are exit 2)")

    proc = container_run(image, ["run"])
    if proc.returncode == 2:
        res.ok("missing required arguments exits 2")
    else:
        res.bad("missing required arguments exits 2", f"exit={proc.returncode}")

    proc = container_run(image, ["schema", "--emit", "verdict"])
    if proc.returncode == 0:
        try:
            json.loads(proc.stdout)
            res.ok("schema --emit verdict prints JSON to stdout")
        except json.JSONDecodeError as exc:
            res.bad("schema --emit verdict prints JSON to stdout", str(exc))
    else:
        res.bad("schema --emit verdict exits 0", f"exit={proc.returncode}\n{proc.stderr[-1500:]}")


def load_schemas() -> dict[str, dict]:
    # Path.stem strips only the final suffix, so "verdict.schema.json" -> "verdict.schema".
    # The lookups below use the bare name, so drop the middle component too.
    return {
        p.stem.replace(".schema", ""): json.loads(p.read_text())
        for p in SCHEMA_DIR.glob("*.schema.json")
    }


def validate_artifacts(out: Path, schemas: dict, res: Results, label: str) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        res.skip(f"[{label}] artifacts validate against schemas", "pip install jsonschema")
        return

    def one(path: Path, schema_key: str, jsonl: bool) -> None:
        name = f"[{label}] {path.name} validates"
        if not path.exists():
            res.bad(name, f"missing: {path}")
            return
        validator = Draft202012Validator(schemas[schema_key])
        errors: list[str] = []
        if jsonl:
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(f"line {i}: not JSON: {exc}")
                    continue
                for err in validator.iter_errors(obj):
                    errors.append(f"line {i}: {'/'.join(map(str, err.absolute_path))}: {err.message}")
                if len(errors) > 12:
                    break
        else:
            try:
                obj = json.loads(path.read_text())
            except json.JSONDecodeError as exc:
                res.bad(name, f"not JSON: {exc}")
                return
            for err in validator.iter_errors(obj):
                errors.append(f"{'/'.join(map(str, err.absolute_path))}: {err.message}")
        if errors:
            res.bad(name, "\n".join(errors[:12]))
        else:
            res.ok(name)

    one(out / "verdicts.jsonl", "verdict", jsonl=True)
    one(out / "violations.jsonl", "violation", jsonl=True)
    one(out / "run_manifest.json", "run_manifest", jsonl=False)
    one(out / "data_quality.json", "data_quality", jsonl=False)
    one(out / "feedback" / "index.json", "feedback_bundle", jsonl=False)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def strip_exempt(obj):
    """Blank the one subtree exempt from byte-identity.

    CONTRACT.md §6 exempts `provenance.environment` and nothing else. Blanking
    every key named `environment`, wherever it appears, would also excuse a
    nondeterministic `config.resolved.environment`, which is not exempt.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "provenance" and isinstance(v, dict):
                out[k] = {kk: (None if kk == "environment" else strip_exempt(vv)) for kk, vv in v.items()}
            else:
                out[k] = strip_exempt(v)
        return out
    if isinstance(obj, list):
        return [strip_exempt(v) for v in obj]
    return obj


def graded_digest(out: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for name in GRADED_ARTIFACTS:
        p = out / name
        if not p.exists():
            digests[name] = "<missing>"
            continue
        if name == "run_manifest.json":
            try:
                obj = strip_exempt(json.loads(p.read_text()))
            except json.JSONDecodeError:
                digests[name] = "<unparseable>"
                continue
            canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            digests[name] = hashlib.sha256(canonical.encode()).hexdigest()
        else:
            digests[name] = sha256_file(p)
    # Every JSON figure companion is graded too. Keyed by path relative to the
    # output directory rather than by basename, because figures/<id>/data.json is
    # a reasonable layout and every companion would otherwise collapse onto one key.
    figs = out / "figures"
    if figs.is_dir():
        for comp in sorted(p for p in figs.rglob("*.json") if p.is_file()):
            digests[f"figures::{comp.relative_to(out).as_posix()}"] = sha256_file(comp)
    return digests


def graded_facts(out: Path) -> dict[str, tuple]:
    """The conclusions that MUST NOT move when only --seed changes (CONTRACT.md §6)."""
    facts: dict[str, tuple] = {}
    verdicts = out / "verdicts.jsonl"
    if not verdicts.exists():
        return facts
    for line in verdicts.read_text().splitlines():
        if not line.strip():
            continue
        try:
            v = json.loads(line)
        except json.JSONDecodeError:
            continue
        facts[str(v.get("proposal_id"))] = (
            (v.get("verdict") or {}).get("status"),
            (v.get("novelty") or {}).get("tier"),
            (v.get("feasibility") or {}).get("verdict"),
            tuple(sorted(x.get("code") for x in (v.get("violations") or []))),
        )
    return facts


def check_fixtures(image: str, schemas: dict, res: Results, workdir: Path) -> None:
    section("Fixtures and exit codes")
    for fixture, accepted in sorted(FIXTURE_EXPECTATIONS.items()):
        expected = accepted[0]
        src = FIXTURES / fixture
        if not src.exists():
            res.skip(f"{fixture}", "fixture not present")
            continue

        in_dir = workdir / f"in-{fixture}"
        out_dir = workdir / f"out-{fixture}"
        in_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, in_dir / fixture)

        proc = container_run(
            image,
            ["run", "--input", f"/data/in/{fixture}", "--out", "/data/out",
             "--offline", "--seed", FIXED_SEED, "--now", FIXED_NOW],
            in_dir=in_dir, out_dir=out_dir,
        )

        name = f"{fixture} exits {' or '.join(str(c) for c in accepted)}"
        if proc.returncode in accepted:
            res.ok(name)
        else:
            res.bad(name, f"got exit={proc.returncode}\nstdout={proc.stdout[-800:]}\nstderr={proc.stderr[-2000:]}")

        if expected in (0, 4):
            missing = [a for a in REQUIRED_ARTIFACTS if not (out_dir / a).exists()]
            if missing:
                res.bad(f"{fixture} produces required artifacts", f"missing: {missing}")
            else:
                res.ok(f"{fixture} produces required artifacts")
            validate_artifacts(out_dir, schemas, res, fixture)
            check_manifest_identities(out_dir, res, fixture)
            check_no_absolute_paths(out_dir, res, fixture)
        else:
            if (out_dir / "run_manifest.json").exists():
                res.ok(f"{fixture} still writes a run manifest on rejection")
            else:
                res.bad(f"{fixture} still writes a run manifest on rejection",
                        "an envelope rejection must still be recorded")


def check_manifest_identities(out: Path, res: Results, label: str) -> None:
    """Internal consistency the manifest asserts about itself."""
    mp = out / "run_manifest.json"
    if not mp.exists():
        return
    try:
        m = json.loads(mp.read_text())
    except json.JSONDecodeError:
        return

    counts = m.get("counts", {})
    try:
        lhs = counts["proposals_in"]
        rhs = counts["verdicts_out"] + counts["quarantined"]
    except (KeyError, TypeError):
        res.bad(f"[{label}] manifest counts present", f"counts={counts!r}")
        return
    if lhs == rhs:
        res.ok(f"[{label}] proposals_in == verdicts_out + quarantined")
    else:
        res.bad(f"[{label}] proposals_in == verdicts_out + quarantined",
                f"{lhs} != {rhs} — records are being lost or invented")

    verdicts = out / "verdicts.jsonl"
    if verdicts.exists():
        n = sum(1 for ln in verdicts.read_text().splitlines() if ln.strip())
        if n == counts.get("verdicts_out"):
            res.ok(f"[{label}] verdicts.jsonl line count matches manifest")
        else:
            res.bad(f"[{label}] verdicts.jsonl line count matches manifest",
                    f"file has {n} lines, manifest says {counts.get('verdicts_out')}")
        terminal = counts.get("accepted", 0) + counts.get("rejected", 0) + counts.get("unverifiable", 0)
        if terminal == counts.get("verdicts_out"):
            res.ok(f"[{label}] status counts sum to verdicts_out")
        else:
            res.bad(f"[{label}] status counts sum to verdicts_out",
                    f"accepted+rejected+unverifiable={terminal}, verdicts_out={counts.get('verdicts_out')}")

    ref = m.get("reference_data")
    if isinstance(ref, list) and ref:
        bad = [r for r in ref if str(r.get("snapshot", "")).lower() in ("", "latest", "none", "null")]
        if bad:
            res.bad(f"[{label}] reference_data snapshots are pinned",
                    f"{len(bad)} entries have an unpinned snapshot; 'latest' is not a version")
        else:
            res.ok(f"[{label}] reference_data snapshots are pinned")
    else:
        res.bad(f"[{label}] reference_data is non-empty",
                "a novelty verdict without a corpus version is not a reproducible claim")


def check_no_absolute_paths(out: Path, res: Results, label: str) -> None:
    """Graded artifacts must not leak the filesystem they were produced on."""
    offenders: list[str] = []
    for name in GRADED_ARTIFACTS:
        p = out / name
        if not p.exists():
            continue
        text = p.read_text(errors="replace")
        if name == "run_manifest.json":
            try:
                obj = json.loads(text)
                obj.get("provenance", {}).pop("environment", None)
                text = json.dumps(obj)
            except json.JSONDecodeError:
                pass
        for needle in ("/data/in/", "/data/out/", "/Users/", "/home/", "/root/", "C:\\\\"):
            if needle in text:
                offenders.append(f"{name}: contains {needle!r}")
    if offenders:
        res.bad(f"[{label}] graded artifacts contain no host paths", "\n".join(offenders[:8]))
    else:
        res.ok(f"[{label}] graded artifacts contain no host paths")


def check_determinism(image: str, res: Results, workdir: Path) -> None:
    section("Determinism")
    fixture = "02_messy.proposals.json"
    src = FIXTURES / fixture
    if not src.exists():
        res.skip("byte-identical reruns", "fixture missing")
        return

    in_dir = workdir / "det-in"
    in_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, in_dir / fixture)

    def once(tag: str, extra_args: list[str], extra_env: dict[str, str] | None = None) -> Path | None:
        out_dir = workdir / f"det-out-{tag}"
        out_dir.mkdir(parents=True, exist_ok=True)
        proc = container_run(
            image,
            ["run", "--input", f"/data/in/{fixture}", "--out", "/data/out",
             "--offline", "--now", FIXED_NOW] + extra_args,
            in_dir=in_dir, out_dir=out_dir, extra_env=extra_env,
        )
        if proc.returncode not in (0, 4):
            res.bad(f"determinism run [{tag}] completes",
                    f"exit={proc.returncode}\n{proc.stderr[-1500:]}")
            return None
        return out_dir

    def compare(name: str, a: Path, b: Path) -> None:
        da, db = graded_digest(a), graded_digest(b)
        diff = sorted(set(da) | set(db))
        diff = [k for k in diff if da.get(k, "<absent>") != db.get(k, "<absent>")]
        if diff:
            res.bad(name, "\n".join(
                f"{k}: {da.get(k, '<absent>')[:16]} != {db.get(k, '<absent>')[:16]}" for k in diff[:12]))
        else:
            res.ok(name)

    base = once("1", ["--seed", FIXED_SEED])
    same = once("2", ["--seed", FIXED_SEED])
    if base is None or same is None:
        return
    compare("byte-identical reruns", base, same)

    # §6: the guarantee holds at --max-workers 1 and at --max-workers 8.
    w1 = once("w1", ["--seed", FIXED_SEED, "--max-workers", "1"])
    w8 = once("w8", ["--seed", FIXED_SEED, "--max-workers", "8"])
    if w1 is not None and w8 is not None:
        compare("byte-identical across --max-workers 1 and 8", w1, w8)

    # §6: the graded environment variables reduce the surface area; they are not
    # the mechanism. A run without them must still match.
    hostile = once("env", ["--seed", FIXED_SEED],
                   extra_env={"PYTHONHASHSEED": "random", "TZ": "Asia/Kolkata", "LC_ALL": "en_US.UTF-8"})
    if hostile is not None:
        compare("byte-identical without the graded environment", base, hostile)

    # §6: a different --seed MAY move layout and equivalent-item tie-breaks. It
    # MUST NOT move a conclusion.
    other = once("seed", ["--seed", "98765"])
    if other is not None:
        fa, fb = graded_facts(base), graded_facts(other)
        moved = [pid for pid in sorted(set(fa) | set(fb)) if fa.get(pid) != fb.get(pid)]
        if moved:
            res.bad("--seed does not change any conclusion",
                    "these proposals changed status, novelty tier, feasibility verdict or violation "
                    "codes when only --seed changed:\n  " + "\n  ".join(moved[:12]))
        else:
            res.ok("--seed does not change any conclusion")


def check_offline_is_fail_closed(image: str, res: Results, workdir: Path) -> None:
    section("Offline behaviour")
    fixture = "01_baseline.proposals.json"
    src = FIXTURES / fixture
    in_dir = workdir / "off-in"
    in_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, in_dir / fixture)

    out_dir = workdir / "off-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = container_run(
        image,
        ["run", "--input", f"/data/in/{fixture}", "--out", "/data/out",
         "--offline", "--seed", FIXED_SEED, "--now", FIXED_NOW],
        in_dir=in_dir, out_dir=out_dir, network="none",
    )
    if proc.returncode in (0, 4):
        res.ok("offline run completes with no network namespace")
    else:
        res.bad("offline run completes with no network namespace",
                f"exit={proc.returncode}\n{proc.stderr[-2000:]}")

    # Without --offline and without a network, the program must fail loudly rather
    # than silently degrade into pretending it checked something.
    out_dir2 = workdir / "off-out-2"
    out_dir2.mkdir(parents=True, exist_ok=True)
    proc2 = container_run(
        image,
        ["run", "--input", f"/data/in/{fixture}", "--out", "/data/out",
         "--seed", FIXED_SEED, "--now", FIXED_NOW],
        in_dir=in_dir, out_dir=out_dir2, network="none",
    )
    if proc2.returncode == 0:
        res.bad("online run without network does not silently claim success",
                "exit=0 with no network reachable: either the network was never needed, "
                "or an unavailable provider was recorded as a completed check")
    else:
        res.ok("online run without network does not silently claim success")


def _from_is_pinned(line: str, dockerfile_text: str) -> bool:
    """Whether one FROM line names a digest-pinned image.

    Three things are pinned without carrying a digest themselves: `FROM scratch`,
    a reference to an earlier stage in the same file, and a `${VAR}` whose ARG or
    ENV default is itself pinned. Flag arguments such as --platform are skipped.
    """
    parts = [t for t in line.split()[1:] if not t.startswith("--")]
    if not parts:
        return True
    target = parts[0]

    defaults: dict[str, str] = {}
    for ln in dockerfile_text.splitlines():
        m = re.match(r"\s*(?:ARG|ENV)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\S+)", ln, re.IGNORECASE)
        if m:
            defaults[m.group(1)] = m.group(2).strip('"\'')
    for _ in range(4):
        m = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", target)
        if not m or m.group(1) not in defaults:
            break
        target = defaults[m.group(1)]

    if target == "scratch" or "@sha256:" in target:
        return True
    stages = {m.group(1).lower() for m in
              re.finditer(r"^\s*FROM\s+.*\sAS\s+(\S+)", dockerfile_text, re.IGNORECASE | re.MULTILINE)}
    return target.lower() in stages


def check_container_hardening(image: str, repo: Path, res: Results) -> None:
    section("Container")

    proc = run(["docker", "inspect", "--format", "{{.Config.User}}", image])
    user = proc.stdout.strip()
    if not user or user in ("root", "0"):
        res.bad("image declares a non-root numeric USER", f"Config.User={user!r}")
    elif not user.split(":")[0].isdigit():
        res.bad("image declares a non-root numeric USER",
                f"Config.User={user!r} — use a numeric uid so the check does not depend on /etc/passwd")
    else:
        res.ok("image declares a non-root numeric USER")

    proc = container_run(image, ["--version"], read_only=True)
    if proc.returncode == 0:
        res.ok("image runs with a read-only root filesystem")
    else:
        res.bad("image runs with a read-only root filesystem",
                f"exit={proc.returncode}\n{proc.stderr[-1200:]}")

    dockerfile = repo / "Dockerfile"
    if dockerfile.exists():
        text = dockerfile.read_text()
        unpinned = [ln.strip() for ln in text.splitlines()
                    if ln.strip().upper().startswith("FROM ") and not _from_is_pinned(ln, text)]
        if unpinned:
            res.bad("every FROM is pinned by digest", "\n".join(unpinned))
        else:
            res.ok("every FROM is pinned by digest")

        lowered = text.lower()
        smells = [w for w in ("privileged", "seccomp=unconfined", "apparmor=unconfined",
                              "sys_admin", "docker.sock") if w in lowered]
        if smells:
            res.bad("Dockerfile contains no privilege escalation", f"found: {smells}")
        else:
            res.ok("Dockerfile contains no privilege escalation")

    env_dump = run(["docker", "inspect", "--format", "{{json .Config.Env}}", image]).stdout
    leaks = [t for t in ("API_KEY=", "SECRET=", "TOKEN=", "PASSWORD=") if t in env_dump.upper()]
    if leaks:
        res.bad("no credentials baked into image env", f"found: {leaks}")
    else:
        res.ok("no credentials baked into image env")

    hist = run(["docker", "history", "--no-trunc", "--format", "{{.CreatedBy}}", image]).stdout.upper()
    hist_leaks = [t for t in ("API_KEY=", "MP_API_KEY", "SECRET=", "TOKEN=") if t in hist]
    if hist_leaks:
        res.bad("no credentials in image history", f"found: {hist_leaks}")
    else:
        res.ok("no credentials in image history")

    # One `command -v` per tool: dash resolves only the first operand, so the
    # single-call form silently tests for curl and nothing else.
    probe = run(["docker", "run", "--rm", "--entrypoint", "sh", image, "-c",
                 "for t in curl wget nc netcat gcc cc g++; do command -v \"$t\" 2>/dev/null; done "
                 "| tr '\\n' ' '"])
    found = probe.stdout.strip()
    if found:
        res.bad("no network or build tooling in the final image",
                f"present: {found} — use a multi-stage build so these stay in the builder")
    else:
        res.ok("no network or build tooling in the final image")


def check_conda(image: str, repo: Path, res: Results) -> None:
    section("Conda environment")

    env_yml = None
    for cand in ("environment.yml", "environment.yaml", "conda/environment.yml"):
        if (repo / cand).exists():
            env_yml = repo / cand
            break
    if env_yml is None:
        res.bad("environment.yml is committed", "no environment.yml found at the repository root")
    else:
        res.ok("environment.yml is committed")
        text = env_yml.read_text()
        if not re.search(r"^\s*name\s*:", text, re.MULTILINE):
            res.bad("environment.yml declares a name", "the env must be named, not defaulted")
        else:
            res.ok("environment.yml declares a name")
        # Section-aware: only entries under `dependencies:` are dependencies. A
        # channel listed under `channels:` is not a package and has no version.
        unpinned = []
        in_deps = False
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if re.match(r"^\s*dependencies\s*:", line):
                in_deps = True
                continue
            if re.match(r"^\S+\s*:", line) and not line.startswith((" ", "\t")):
                in_deps = s.startswith("dependencies")
                continue
            if not in_deps or not s.startswith("-"):
                continue
            item = re.sub(r"^-\s*", "", s).strip()
            if not item or item.endswith(":") or item == "pip":
                continue
            if item.startswith(("-e ", "--", "git+", "http")):
                continue
            # Accept both the pip form (numpy==2.1) and the conda MatchSpec
            # space form (numpy 2.1).
            if re.search(r"[=<>~!]=?\s*[0-9]", item) or re.search(r"\s[0-9]", item):
                continue
            unpinned.append(item)
        if unpinned:
            res.bad("every environment.yml dependency carries a version",
                    "unpinned: " + ", ".join(sorted(set(unpinned))[:12]))
        else:
            res.ok("every environment.yml dependency carries a version")

    # A prefix environment created with `-p /opt/crucible` is a perfectly legitimate
    # conda environment whose path says nothing about conda, so the presence of a
    # conda-meta directory is what actually settles the question.
    script = (
        "python -c 'import sys,os,json;"
        "print(json.dumps({\"prefix\": sys.prefix,"
        " \"conda_prefix\": os.environ.get(\"CONDA_PREFIX\", \"\"),"
        " \"conda_meta\": os.path.isdir(os.path.join(sys.prefix, \"conda-meta\")),"
        " \"has_envs_dir\": os.path.isdir(os.path.join(sys.prefix, \"envs\"))}))'"
    )
    probe = run(["docker", "run", "--rm", "--entrypoint", "sh", image, "-c", script])
    if probe.returncode != 0:
        res.bad("python in the image resolves inside the conda environment", probe.stderr[-1000:])
        return
    try:
        info = json.loads(probe.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        res.bad("python in the image resolves inside the conda environment",
                f"could not read the probe output: {probe.stdout[-500:]!r}")
        return

    prefix = info["prefix"]
    if "conda" in prefix.lower() or "/envs/" in prefix or info["conda_prefix"] or info["conda_meta"]:
        res.ok("python in the image resolves inside the conda environment")
    else:
        res.bad("python in the image resolves inside the conda environment",
                f"sys.prefix={prefix!r} CONDA_PREFIX={info['conda_prefix']!r}, and there is no "
                f"conda-meta directory under it — the entrypoint is not running inside the "
                f"environment you built")

    # CONTRACT.md §8 item 3: a named environment, not the installation's base.
    if info["has_envs_dir"]:
        res.bad("the environment is not the conda base",
                f"sys.prefix={prefix!r} contains an 'envs' directory, so it is the root prefix. "
                f"Install into a named environment, or the pinned set cannot be told apart from "
                f"whatever the base image shipped")
    else:
        res.ok("the environment is not the conda base")


def check_resource_budget(image: str, repo: Path, res: Results, workdir: Path) -> None:
    """CONTRACT.md section 8.1. Every other invocation in this script already runs
    inside the envelope; these are the checks that cannot be expressed that way."""
    section("Resource budget")

    proc = run(["docker", "image", "inspect", "--format", "{{.Size}}", image])
    try:
        size = int(proc.stdout.strip())
    except ValueError:
        res.skip("image is within the size ceiling", "could not read image size")
    else:
        gib = size / 1024**3
        if size <= MAX_IMAGE_BYTES:
            res.ok(f"image is within the size ceiling ({gib:.2f} GiB)")
        else:
            res.bad("image is within the size ceiling",
                    f"{gib:.2f} GiB against a {MAX_IMAGE_BYTES / 1024**3:.0f} GiB ceiling")

    probe = run(["docker", "run", "--rm", "--entrypoint", "sh", image, "-c",
                 "for d in /usr/local/cuda /usr/local/nvidia /opt/rocm /opt/intel; do "
                 "[ -e \"$d\" ] && echo \"DIR $d\"; done; "
                 "find / -xdev \\( -name 'libcudart*' -o -name 'libcudnn*' -o -name 'libnccl*' "
                 "-o -name 'libnvidia*' -o -name 'libamdhip*' \\) 2>/dev/null | head -6"])
    found = [ln for ln in probe.stdout.strip().splitlines() if ln.strip()]
    if found:
        res.bad("no accelerator runtime in the image", "\n".join(found[:6]))
    else:
        res.ok("no accelerator runtime in the image")

    hits = []
    for pattern in ("environment*.y*ml", "conda/environment*.y*ml", "*.lock",
                    "requirements*.txt", "pyproject.toml"):
        for p in repo.rglob(pattern):
            if not p.is_file() or "/.git/" in str(p):
                continue
            for line in p.read_text(encoding="utf-8", errors="replace").lower().splitlines():
                s = line.split("#", 1)[0].strip()
                if s and any(tok in s for tok in ACCELERATOR_PACKAGES):
                    hits.append(f"{p.relative_to(repo)}: {s[:90]}")
                    break
    if hits:
        res.bad("no accelerator packages are declared", "\n".join(sorted(set(hits))[:8]))
    else:
        res.ok("no accelerator packages are declared")

    fixture = "02_messy.proposals.json"
    src = FIXTURES / fixture
    if not src.exists():
        res.skip("completes within the wall-clock budget", "fixture missing")
        return
    in_dir = workdir / "budget-in"
    out_dir = workdir / "budget-out"
    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, in_dir / fixture)

    import time as _time
    t0 = _time.time()
    proc = container_run(
        image,
        ["run", "--input", f"/data/in/{fixture}", "--out", "/data/out",
         "--offline", "--seed", FIXED_SEED, "--now", FIXED_NOW],
        in_dir=in_dir, out_dir=out_dir, timeout=WALLCLOCK_BUDGET_SECONDS + 120,
    )
    elapsed = _time.time() - t0
    if proc.returncode not in (0, 4):
        res.bad("runs inside the compute envelope",
                f"exit={proc.returncode} at --cpus {GRADED_CPUS} --memory {GRADED_MEMORY}\n"
                f"{proc.stderr[-1500:]}")
    elif elapsed > WALLCLOCK_BUDGET_SECONDS:
        res.bad("completes within the wall-clock budget",
                f"{elapsed:.0f}s against a {WALLCLOCK_BUDGET_SECONDS}s ceiling")
    else:
        res.ok(f"runs inside the compute envelope ({elapsed:.0f}s on {GRADED_CPUS} cores)")


def check_deliverables(repo: Path, res: Results) -> None:
    section("Deliverables")
    required = ["README.md", "CONTRACT.md", "ASSUMPTIONS.md", "LIMITATIONS.md", "AI_USAGE.md",
                "DECISIONS.md", "Dockerfile", "environment.yml"]
    for name in required:
        p = repo / name
        if p.exists() and p.stat().st_size > 200:
            res.ok(f"{name} present and non-trivial")
        elif p.exists():
            res.bad(f"{name} present and non-trivial", f"{name} is {p.stat().st_size} bytes")
        else:
            res.bad(f"{name} present and non-trivial", f"missing: {name}")

    lock_names = ("conda-lock.yml", "conda-lock.yaml", "conda-linux-64.lock",
                  "environment.lock.yml", "environment-export.yml")
    locks = [p for p in repo.glob("*") if p.name in lock_names or p.suffix == ".lock"]
    if locks:
        res.ok(f"a lock or export of the conda environment is committed ({locks[0].name})")
    else:
        res.bad("a lock or export of the conda environment is committed",
                "CONTRACT.md §11 names the accepted filenames: " + ", ".join(lock_names) + ", or *.lock")

    assumptions = repo / "ASSUMPTIONS.md"
    if assumptions.exists():
        cited = {int(m.group(1)) for m in
                 re.finditer(r"§\s*7\.(\d+)", assumptions.read_text(errors="replace"))}
        missing = sorted(set(range(1, 11)) - cited)
        if missing:
            res.bad("ASSUMPTIONS.md addresses each open decision under its own heading",
                    f"no heading cites PROJECT.md §7 decision(s) {missing} — CONTRACT.md §12 asks "
                    f"for one heading per decision, e.g. '## §7.3 Formula equality'")
        else:
            res.ok("ASSUMPTIONS.md addresses each open decision under its own heading")

    if (repo / ".github" / "workflows").is_dir() and any((repo / ".github" / "workflows").glob("*.y*ml")):
        res.ok("a CI workflow is committed")
    else:
        res.bad("a CI workflow is committed", "no workflow under .github/workflows")

    if (repo / ".git").exists():
        n = run(["git", "-C", str(repo), "rev-list", "--count", "HEAD"]).stdout.strip()
        try:
            if int(n) >= 10:
                res.ok(f"repository has a real history ({n} commits)")
            else:
                res.bad("repository has a real history",
                        f"{n} commits — a single dump is not a history")
        except ValueError:
            res.skip("repository has a real history", "could not count commits")
    else:
        res.skip("repository has a real history", "not a git checkout")


# --------------------------------------------------------------------------- #

ALL_GROUPS = ["build", "cli", "container", "conda", "budget", "fixtures", "determinism",
              "offline", "deliverables"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".", help="path to your implementation (default: cwd)")
    ap.add_argument("--image", default="crucible:selfcheck", help="image tag to build/use")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--only", default="", help=f"comma-separated subset of: {','.join(ALL_GROUPS)}")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    groups = [g.strip() for g in args.only.split(",") if g.strip()] or ALL_GROUPS
    unknown = set(groups) - set(ALL_GROUPS)
    if unknown:
        print(f"unknown group(s): {sorted(unknown)}", file=sys.stderr)
        return 2

    print(f"repository: {repo}")
    print(f"image:      {args.image}")
    print(f"groups:     {', '.join(groups)}")

    res = Results()

    if not docker_available():
        print("\ndocker is not available; this check requires it.", file=sys.stderr)
        return 2

    if "build" in groups and not args.skip_build:
        if not build_image(repo, args.image, res):
            summarise(res)
            return 1
    elif "build" in groups:
        print("\n(build skipped)")

    schemas = load_schemas()

    with tempfile.TemporaryDirectory(prefix="crucible-selfcheck-") as td:
        workdir = Path(td)
        if "cli" in groups:
            check_cli(args.image, res)
        if "container" in groups:
            check_container_hardening(args.image, repo, res)
        if "conda" in groups:
            check_conda(args.image, repo, res)
        if "budget" in groups:
            check_resource_budget(args.image, repo, res, workdir)
        if "fixtures" in groups:
            check_fixtures(args.image, schemas, res, workdir)
        if "determinism" in groups:
            check_determinism(args.image, res, workdir)
        if "offline" in groups:
            check_offline_is_fail_closed(args.image, res, workdir)

    if "deliverables" in groups:
        check_deliverables(repo, res)

    return summarise(res)


def summarise(res: Results) -> int:
    print("\n" + "=" * 68)
    print(f"passed {len(res.passed)}   failed {len(res.failed)}   skipped {len(res.skipped)}")
    if res.failed:
        print("\nfailures:")
        for name, _ in res.failed:
            print(f"  - {name}")
        print("\nEvery one of these is checked again at review time.")
        return 1
    print("\nAll mechanical checks pass. Nothing here says your chemistry is right.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
