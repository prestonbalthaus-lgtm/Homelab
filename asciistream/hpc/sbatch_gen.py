"""Slurm .sbatch generator for headless asciistream cluster runs.

Emits a batch script that launches ``chassis_cfd.py --worker`` inside the
Apptainer image built from hpc/Apptainer.def, via ``srun --mpi=pmix``,
with UCX/RDMA environment set and ranks pinned to the NUMA layout of a
dual-socket Xeon Platinum 8160 node (2 x 24 cores, 2 threads/core).

Design constraints (deliberate):

* stdlib only -- importable and unit-testable on any host with neither
  Slurm nor Apptainer installed (that is exactly how it IS tested; see
  tests/test_sbatch_gen.py).
* pure function of its input: the same ``SbatchSpec`` always produces
  byte-identical output (no timestamps), so generated scripts diff
  cleanly.
* a cluster run is HEADLESS. The generated script never passes
  ``--callback-port`` (the TUI socket) or ``--viz-every`` (the host
  viewer's mid-run export), and never touches Qt/PyVista windows. The
  solver's VTU/PVTU output lands in the shared-filesystem directory the
  user names (``output_dir``), because chassis_cfd.py writes its output
  files relative to the process CWD and the script sets the container
  CWD there (``--pwd /out``).

HONESTY -- what is verified vs. assumed
---------------------------------------
Verified on this development host: the flag names/limits below mirror
chassis_cfd.py (mesh presets, MESH_MM_FLOOR = 0.5 mm, FAN_DUTY range
[0.05, 1.5], worker defaults), and a worker without --callback-port
runs headless. The emitted script passes ``bash -n``.

NOT verified -- no Slurm/Apptainer/InfiniBand exists here: that sbatch
accepts these directives on the target cluster, that ``srun --mpi=pmix``
wires PMIx to the in-container Open MPI, that the UCX variables engage
RDMA, and the exact NUMA effect of --ntasks-per-socket with
--distribution=block:block on the 8160 nodes. Treat the script as a
well-informed draft to validate on the real system.
"""
from __future__ import annotations

import argparse
import dataclasses
import math
import re
import shlex
import sys
from pathlib import Path
from typing import Optional, Union

GENERATOR_VERSION = "1"

# --- target-cluster topology (roadmap.md Phase 2) ---------------------------
# Dell R640: dual Intel Xeon Platinum 8160 -- 24 cores/socket, 2 sockets,
# 2 hyperthreads/core -> 48 physical cores / 96 hardware threads per node.
# ASSUMPTION: taken from the roadmap + Intel ARK, not read from a real node.
SOCKETS_PER_NODE = 2
CORES_PER_SOCKET = 24
CORES_PER_NODE = SOCKETS_PER_NODE * CORES_PER_SOCKET          # 48
THREADS_PER_NODE = 2 * CORES_PER_NODE                          # 96
DEFAULT_MAX_NODES = 15          # the Phase-2 cluster is 15 nodes

# --- limits mirrored from chassis_cfd.py (single source of truth is there;
# these copies exist so this module stays stdlib-importable without pulling
# in the solver module. Keep in sync -- tests/test_sbatch_gen.py asserts
# equality against chassis_cfd's constants.) ---------------------------------
MESH_PRESETS = ("coarse", "medium", "fine", "ultra")
MESH_MM_FLOOR = 0.5             # chassis_cfd.MESH_MM_FLOOR
FAN_DUTY_MIN = 0.05             # chassis_cfd.FAN_DUTY_MIN
FAN_DUTY_MAX = 1.5              # chassis_cfd.FAN_DUTY_MAX
ENGINES = ("2d", "3d")

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# conservative charset for paths that end up on raw #SBATCH lines, where
# no shell quoting protects them (Slurm parses those lines itself)
_SBATCH_PATH_RE = re.compile(r"^/[A-Za-z0-9/._+-]*$")
_UCX_DEV_RE = re.compile(r"^[A-Za-z0-9_:,.-]+$")
_UCX_TLS_RE = re.compile(r"^[A-Za-z0-9_,^]+$")


@dataclasses.dataclass(frozen=True)
class SbatchSpec:
    """Everything needed to emit one batch script. Validated as a whole
    by validate(); generate_sbatch() validates automatically."""
    # -- Slurm shape --
    job_name: str
    partition: str
    nodes: int
    ranks_per_node: int
    walltime: Union[str, int]        # "HH:MM:SS", "D-HH:MM:SS", or minutes
    # -- filesystem (all shared-FS absolute paths, visible on every node) --
    output_dir: str                  # VTU/PVTU + slurm log land here
    sif_image: str                   # the built Apptainer image
    repo_dir: str                    # checkout containing chassis_cfd.py
    # -- asciistream worker flags --
    profile: str
    mesh: Union[str, float] = "coarse"
    engine: str = "3d"
    sim_time: float = 30.0           # worker default (chassis_cfd.py)
    dt: Optional[float] = None       # None -> omit flag, worker default
    fan_duty: Optional[float] = None  # None -> omit flag (duty 1.0)
    fan: Optional[str] = None        # None -> omit flag, worker default
    # -- optional Slurm knobs --
    account: Optional[str] = None
    exclusive: bool = True
    use_hyperthreads: bool = False   # allow ranks_per_node up to 96
    max_nodes: int = DEFAULT_MAX_NODES
    # -- fabric environment --
    ucx_net_devices: Optional[str] = "mlx5_0:1"  # None -> let UCX choose
    ucx_tls: Optional[str] = "rc_x,sm,self"      # None -> let UCX choose
    fabric_check: bool = True        # pre-flight ucx_info probe step


def _fail(msg: str) -> None:
    raise ValueError(f"sbatch_gen: {msg}")


def _require_int(value, what: str) -> int:
    # bool is an int subclass; reject it explicitly
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{what} must be an integer, got {value!r}")
    return value


def _require_finite_pos(value, what: str) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        _fail(f"{what} must be a number, got {value!r}")
    if isinstance(value, bool) or not math.isfinite(f) or f <= 0:
        _fail(f"{what} must be a finite positive number, got {value!r}")
    return f


def _validate_name(value, what: str, maxlen: int = 64) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{what} must be a non-empty string, got {value!r}")
    if len(value) > maxlen:
        _fail(f"{what} is longer than {maxlen} characters: {value!r}")
    if not _NAME_RE.match(value):
        _fail(f"{what} may only contain [A-Za-z0-9._-] and must start "
              f"alphanumeric, got {value!r}")
    return value


def _validate_walltime(value) -> str:
    """Accept Slurm time formats we can check strictly:
    integer minutes, "MM:SS", "H:MM:SS" / "HH:MM:SS", "D-HH[:MM[:SS]]".
    Returns the normalised string. Rejects a zero total."""
    if isinstance(value, bool):
        _fail(f"walltime must be minutes or a time string, got {value!r}")
    if isinstance(value, int):
        if value <= 0:
            _fail(f"walltime minutes must be positive, got {value}")
        return str(value)
    if not isinstance(value, str):
        _fail(f"walltime must be minutes or a time string, got {value!r}")
    s = value.strip()
    m = re.match(r"^(\d+)-(\d{1,2})(?::(\d{1,2})(?::(\d{1,2}))?)?$", s)
    if m:  # D-HH[:MM[:SS]]
        d, h = int(m.group(1)), int(m.group(2))
        mi = int(m.group(3) or 0)
        se = int(m.group(4) or 0)
        if h > 23 or mi > 59 or se > 59:
            _fail(f"walltime {value!r}: fields out of range in D-HH:MM:SS")
        if d == h == mi == se == 0:
            _fail(f"walltime {value!r} is zero")
        return s
    m = re.match(r"^(\d{1,4}):(\d{1,2}):(\d{1,2})$", s)
    if m:  # H:MM:SS
        h, mi, se = (int(g) for g in m.groups())
        if mi > 59 or se > 59:
            _fail(f"walltime {value!r}: minutes/seconds must be < 60")
        if h == mi == se == 0:
            _fail(f"walltime {value!r} is zero")
        return s
    m = re.match(r"^(\d{1,5}):(\d{1,2})$", s)
    if m:  # Slurm reads MM:SS
        mi, se = (int(g) for g in m.groups())
        if se > 59:
            _fail(f"walltime {value!r}: seconds must be < 60")
        if mi == se == 0:
            _fail(f"walltime {value!r} is zero")
        return s
    if re.match(r"^\d+$", s):
        if int(s) == 0:
            _fail(f"walltime {value!r} is zero")
        return s
    _fail(f"walltime {value!r} is not a recognised Slurm time "
          "(use minutes, MM:SS, HH:MM:SS or D-HH:MM:SS)")


def _validate_sbatch_path(value, what: str) -> str:
    """Paths that appear on raw #SBATCH lines: absolute, conservative
    charset (Slurm parses those lines itself -- shell quoting does not
    apply there)."""
    if not isinstance(value, str) or not value:
        _fail(f"{what} must be a non-empty string, got {value!r}")
    v = value.rstrip("/") or "/"
    if not _SBATCH_PATH_RE.match(v):
        _fail(f"{what} must be an absolute path using only "
              f"[A-Za-z0-9/._+-] (no spaces), got {value!r}")
    return v


def _validate_abs_path(value, what: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        _fail(f"{what} must be an absolute path, got {value!r}")
    if "\n" in value:
        _fail(f"{what} must not contain newlines")
    return value.rstrip("/") or "/"


def _validate_mesh(value) -> str:
    """Preset name or literal millimetre size -- same contract as the
    worker's --mesh (chassis_cfd.mesh_level_lc), including the 0.5 mm
    floor, so a bad value fails at generation time, not on the cluster."""
    if isinstance(value, str) and value in MESH_PRESETS:
        return value
    try:
        mm = float(value)
    except (TypeError, ValueError):
        _fail(f"mesh must be one of {MESH_PRESETS} or an element size "
              f"in mm, got {value!r}")
    if isinstance(value, bool) or not (mm >= MESH_MM_FLOOR):
        _fail(f"mesh element size {value!r} mm is below the "
              f"{MESH_MM_FLOOR:g} mm floor (chassis_cfd.MESH_MM_FLOOR)")
    return f"{mm:g}"


def _validate_free_string(value, what: str) -> str:
    """Values that only ever appear shell-quoted inside the script body
    (never on #SBATCH lines): printable, no newlines."""
    if not isinstance(value, str) or not value.strip():
        _fail(f"{what} must be a non-empty string, got {value!r}")
    if any(c in value for c in "\n\r\x00"):
        _fail(f"{what} must not contain newlines/control characters")
    return value


def validate(spec: SbatchSpec) -> None:
    """Raise ValueError on the first problem; return None if usable."""
    _validate_name(spec.job_name, "job_name")
    _validate_name(spec.partition, "partition")
    if spec.account is not None:
        _validate_name(spec.account, "account")

    nodes = _require_int(spec.nodes, "nodes")
    max_nodes = _require_int(spec.max_nodes, "max_nodes")
    if max_nodes < 1:
        _fail(f"max_nodes must be >= 1, got {max_nodes}")
    if nodes < 1:
        _fail(f"nodes must be >= 1, got {nodes}")
    if nodes > max_nodes:
        _fail(f"nodes={nodes} exceeds the cluster size "
              f"(max_nodes={max_nodes})")

    rpn = _require_int(spec.ranks_per_node, "ranks_per_node")
    if rpn < 1:
        _fail(f"ranks_per_node must be >= 1, got {rpn}")
    cap = THREADS_PER_NODE if spec.use_hyperthreads else CORES_PER_NODE
    if rpn > cap:
        hint = ("" if spec.use_hyperthreads else
                f" ({CORES_PER_NODE} physical cores/node; pass "
                f"use_hyperthreads=True to allow up to "
                f"{THREADS_PER_NODE})")
        _fail(f"ranks_per_node={rpn} exceeds {cap} per R640 node{hint}")

    _validate_walltime(spec.walltime)
    _validate_sbatch_path(spec.output_dir, "output_dir")
    _validate_abs_path(spec.sif_image, "sif_image")
    _validate_abs_path(spec.repo_dir, "repo_dir")

    _validate_free_string(spec.profile, "profile")
    if spec.fan is not None:
        _validate_free_string(spec.fan, "fan")
    _validate_mesh(spec.mesh)
    if spec.engine not in ENGINES:
        _fail(f"engine must be one of {ENGINES}, got {spec.engine!r}")

    sim_time = _require_finite_pos(spec.sim_time, "sim_time")
    if spec.dt is not None:
        dt = _require_finite_pos(spec.dt, "dt")
        if dt > sim_time:
            _fail(f"dt={dt:g} s exceeds sim_time={sim_time:g} s "
                  "(zero solve steps)")
    if spec.fan_duty is not None:
        try:
            duty = float(spec.fan_duty)
        except (TypeError, ValueError):
            _fail(f"fan_duty must be a number, got {spec.fan_duty!r}")
        if isinstance(spec.fan_duty, bool) or not math.isfinite(duty) \
                or not (FAN_DUTY_MIN <= duty <= FAN_DUTY_MAX):
            _fail(f"fan_duty must be within [{FAN_DUTY_MIN:g}, "
                  f"{FAN_DUTY_MAX:g}] (fraction of rated RPM), "
                  f"got {spec.fan_duty!r}")

    if spec.ucx_net_devices is not None and \
            not _UCX_DEV_RE.match(spec.ucx_net_devices):
        _fail(f"ucx_net_devices looks invalid: {spec.ucx_net_devices!r}")
    if spec.ucx_tls is not None and not _UCX_TLS_RE.match(spec.ucx_tls):
        _fail(f"ucx_tls looks invalid: {spec.ucx_tls!r}")


def ranks_total(spec: SbatchSpec) -> int:
    return spec.nodes * spec.ranks_per_node


def ranks_per_socket(spec: SbatchSpec) -> int:
    """#SBATCH --ntasks-per-socket value: split each node's ranks across
    the two sockets, odd counts rounded up (Slurm needs the ceiling)."""
    return math.ceil(spec.ranks_per_node / SOCKETS_PER_NODE)


def _worker_cmd_lines(spec: SbatchSpec) -> list:
    """The containerised worker invocation, one flag per line. Everything
    user-supplied is shell-quoted. Headless on purpose: no
    --callback-port, no --viz-every, no TUI, no Qt."""
    q = shlex.quote
    lines = [
        "python3 /work/chassis_cfd.py --worker \\",
        "         --config /work/server_configs.json \\",
        f"         --profile {q(spec.profile)} \\",
        f"         --mesh {q(_validate_mesh(spec.mesh))} \\",
        f"         --engine {q(spec.engine)} \\",
        f"         --sim-time {float(spec.sim_time):g}",
    ]
    if spec.dt is not None:
        lines[-1] += " \\"
        lines.append(f"         --dt {float(spec.dt):g}")
    if spec.fan is not None:
        lines[-1] += " \\"
        lines.append(f"         --fan {q(spec.fan)}")
    if spec.fan_duty is not None:
        lines[-1] += " \\"
        lines.append(f"         --fan-duty {float(spec.fan_duty):g}")
    return lines


def generate_sbatch(spec: SbatchSpec) -> str:
    """Validate `spec` and return the batch script as a string."""
    validate(spec)
    q = shlex.quote
    out_dir = _validate_sbatch_path(spec.output_dir, "output_dir")
    sif = _validate_abs_path(spec.sif_image, "sif_image")
    repo = _validate_abs_path(spec.repo_dir, "repo_dir")

    L = []
    a = L.append
    a("#!/usr/bin/env bash")
    a("# " + "-" * 74)
    a(f"# asciistream Slurm job -- GENERATED by hpc/sbatch_gen.py "
      f"(v{GENERATOR_VERSION}). Regenerate")
    a("# rather than editing by hand.")
    a("#")
    a("# HONESTY: this launch recipe has NEVER been executed on a real "
      "cluster --")
    a("# no Slurm, Apptainer or InfiniBand existed where it was written. "
      "The srun/")
    a("# PMIx/UCX wiring below is a best-informed draft; validate it on "
      "your")
    a("# system before trusting any numbers (see hpc/README.md).")
    a("#")
    a("# Submit with (Slurm opens the log below at job start, so the "
      "directory")
    a("# must exist BEFORE submission):")
    a(f"#   mkdir -p {out_dir} && sbatch <this file>")
    a("# " + "-" * 74)
    a(f"#SBATCH --job-name={spec.job_name}")
    a(f"#SBATCH --partition={spec.partition}")
    if spec.account is not None:
        a(f"#SBATCH --account={spec.account}")
    a(f"#SBATCH --nodes={spec.nodes}")
    a(f"#SBATCH --ntasks={ranks_total(spec)}")
    a(f"#SBATCH --ntasks-per-node={spec.ranks_per_node}")
    a(f"# dual Platinum 8160: {CORES_PER_SOCKET} cores/socket -- keep "
      "the per-node ranks")
    a("# split evenly across the two NUMA domains (no inline comments "
      "on #SBATCH")
    a("# lines: several Slurm versions reject them):")
    a(f"#SBATCH --ntasks-per-socket={ranks_per_socket(spec)}")
    a(f"#SBATCH --time={_validate_walltime(spec.walltime)}")
    a(f"#SBATCH --output={out_dir}/%x-%j.out")
    if spec.exclusive:
        a("#SBATCH --exclusive")
    if not spec.use_hyperthreads:
        a("# bind to the 48 physical cores only, skip the HT siblings:")
        a("#SBATCH --hint=nomultithread")
    a("")
    a("set -euo pipefail")
    a("")
    a("# Shared-filesystem paths -- every compute node must see all "
      "three.")
    a(f"SIF={q(sif)}")
    a(f"REPO_DIR={q(repo)}")
    a(f"OUT_DIR={q(out_dir)}")
    a("")
    a('mkdir -p "$OUT_DIR"')
    a("")
    a("# --- UCX / RDMA environment "
      "------------------------------------------------")
    a("# OMPI_MCA_pml=ucx is the no-silent-fallback guard: if UCX cannot")
    a("# initialise on the MCX-456A fabric the job ABORTS instead of "
      "quietly")
    a("# degrading to TCP over the management network. Apptainer passes "
      "the host")
    a("# environment into the container by default (do NOT add "
      "--cleanenv here --")
    a("# these exports and Slurm's PMIx variables must reach the "
      "in-container MPI).")
    if spec.ucx_net_devices is not None:
        a(f"export UCX_NET_DEVICES={q(spec.ucx_net_devices)}")
    else:
        a("# UCX_NET_DEVICES unset: UCX auto-selects the best device")
    if spec.ucx_tls is not None:
        a(f"export UCX_TLS={q(spec.ucx_tls)}")
    else:
        a("# UCX_TLS unset: UCX auto-selects transports")
    a("export OMPI_MCA_pml=ucx")
    a("export OMPI_MCA_osc=ucx")
    a("")
    a("# --- container binds "
      "---------------------------------------------------------")
    a("# Repo mounts read-only at /work (the solver only READS code+config"
      "); output")
    a("# directory mounts at /out and becomes the CWD, because "
      "chassis_cfd.py writes")
    a("# its VTU/PVTU series relative to the CWD. The IB device/config "
      "paths are")
    a("# bound explicitly so the script also works on sites that "
      "configure")
    a("# apptainer with contained /dev (default configs mount host /dev "
      "wholesale).")
    a('BIND_ARGS=( --bind "$REPO_DIR:/work:ro" --bind "$OUT_DIR:/out" )')
    a("for _d in /dev/infiniband /sys/class/infiniband "
      "/etc/libibverbs.d; do")
    a('    [[ -e "$_d" ]] && BIND_ARGS+=( --bind "$_d" )')
    a("done")
    a("")
    if spec.fabric_check:
        a("# --- pre-flight: is RDMA actually visible in the container? "
          "----------------")
        a("# One probe rank per node. ucx_info -d lists usable "
          "transports; no rc/dc")
        a("# transport means the job WOULD run, but over TCP -- exactly "
          "the silent")
        a("# failure this guard exists to catch. Skip with")
        a("# ASCIISTREAM_SKIP_FABRIC_CHECK=1 (e.g. deliberate "
          "single-node TCP test).")
        a('if [[ "${ASCIISTREAM_SKIP_FABRIC_CHECK:-0}" != 1 ]]; then')
        a('    if ! srun --ntasks="$SLURM_JOB_NUM_NODES" '
          "--ntasks-per-node=1 \\")
        a('          apptainer exec "${BIND_ARGS[@]}" "$SIF" \\')
        a("          bash -c 'ucx_info -d 2>/dev/null | grep -Eq "
          "\"Transport: *(rc|dc)\"'; then")
        a('        echo "[preflight] FAILED: UCX sees no RC/DC (RDMA) '
          'transport inside the" >&2')
        a('        echo "[preflight] container. Check ibv_devinfo on the '
          'nodes, the OFED stack" >&2')
        a('        echo "[preflight] and hpc/README.md; or set '
          'ASCIISTREAM_SKIP_FABRIC_CHECK=1." >&2')
        a("        exit 1")
        a("    fi")
        a("fi")
        a("")
    a("# --- distributed solve "
      "-------------------------------------------------------")
    a("# srun (not mpirun) launches every rank and wires them together "
      "via PMIx;")
    a("# --cpu-bind=cores + block:block packs consecutive ranks socket "
      "by socket so")
    a("# DoF-neighbouring ranks share a NUMA domain "
      "(--ntasks-per-socket above keeps")
    a("# the split even). Headless on purpose: no --callback-port, no "
      "--viz-every,")
    a("# no TUI, no Qt -- VTU output in $OUT_DIR is the whole product.")
    a("srun --mpi=pmix --kill-on-bad-exit=1 \\")
    a("     --cpu-bind=cores --distribution=block:block \\")
    a('     apptainer exec "${BIND_ARGS[@]}" --pwd /out "$SIF" \\')
    for line in _worker_cmd_lines(spec):
        a("     " + line)
    a("")
    a('echo "[job] solve finished; VTU output in $OUT_DIR"')
    a('ls -1 "$OUT_DIR"/*.pvtu >/dev/null 2>&1 && '
      'ls -1 "$OUT_DIR"/*.pvtu | tail -5 || true')
    a("")
    return "\n".join(L)


def write_sbatch(spec: SbatchSpec, path) -> Path:
    """Generate and write to `path`; returns the Path written."""
    p = Path(path)
    p.write_text(generate_sbatch(spec))
    return p


# --- CLI --------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sbatch_gen.py",
        description="Generate a Slurm batch script for a headless "
                    "asciistream cluster run (see hpc/README.md).")
    g = p.add_argument_group("Slurm shape")
    g.add_argument("--job-name", default="asciistream")
    g.add_argument("--partition", required=True)
    g.add_argument("--account", default=None)
    g.add_argument("--nodes", type=int, required=True)
    g.add_argument("--ranks-per-node", type=int, required=True)
    g.add_argument("--walltime", required=True,
                   help="minutes, MM:SS, HH:MM:SS or D-HH:MM:SS")
    g.add_argument("--max-nodes", type=int, default=DEFAULT_MAX_NODES)
    g.add_argument("--use-hyperthreads", action="store_true",
                   help=f"allow up to {THREADS_PER_NODE} ranks/node "
                        f"instead of {CORES_PER_NODE}")
    g.add_argument("--no-exclusive", dest="exclusive",
                   action="store_false")
    g = p.add_argument_group("shared filesystem paths")
    g.add_argument("--output-dir", required=True,
                   help="absolute shared-FS path for VTU output + log")
    g.add_argument("--sif", dest="sif_image", required=True,
                   help="absolute path to the built .sif image")
    g.add_argument("--repo-dir", required=True,
                   help="absolute path of the asciistream checkout")
    g = p.add_argument_group("asciistream worker flags")
    g.add_argument("--profile", required=True)
    g.add_argument("--mesh", default="coarse",
                   help="coarse|medium|fine|ultra or element size in mm")
    g.add_argument("--engine", default="3d", choices=ENGINES)
    g.add_argument("--sim-time", type=float, default=30.0)
    g.add_argument("--dt", type=float, default=None)
    g.add_argument("--fan", default=None)
    g.add_argument("--fan-duty", type=float, default=None)
    g = p.add_argument_group("fabric")
    g.add_argument("--ucx-net-devices", default="mlx5_0:1")
    g.add_argument("--ucx-tls", default="rc_x,sm,self")
    g.add_argument("--no-fabric-check", dest="fabric_check",
                   action="store_false")
    p.add_argument("-o", "--output", default="-",
                   help="output file, or - for stdout (default)")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    spec = SbatchSpec(
        job_name=args.job_name, partition=args.partition,
        nodes=args.nodes, ranks_per_node=args.ranks_per_node,
        walltime=args.walltime, output_dir=args.output_dir,
        sif_image=args.sif_image, repo_dir=args.repo_dir,
        profile=args.profile, mesh=args.mesh, engine=args.engine,
        sim_time=args.sim_time, dt=args.dt, fan_duty=args.fan_duty,
        fan=args.fan, account=args.account, exclusive=args.exclusive,
        use_hyperthreads=args.use_hyperthreads, max_nodes=args.max_nodes,
        ucx_net_devices=args.ucx_net_devices, ucx_tls=args.ucx_tls,
        fabric_check=args.fabric_check)
    try:
        script = generate_sbatch(spec)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.output == "-":
        sys.stdout.write(script)
    else:
        Path(args.output).write_text(script)
        print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
