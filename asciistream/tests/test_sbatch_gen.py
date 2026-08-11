"""Host-side tests for hpc/sbatch_gen.py -- the Slurm job generator.

Everything here runs with NO Slurm, NO Apptainer and NO cluster (none
exist on this machine): the tests cover what a host CAN verify -- flag
pass-through, node/rank arithmetic, rejection of invalid combinations,
headlessness of the emitted script, and `bash -n` syntax validity.
Whether the script actually launches on real hardware is explicitly NOT
tested (see hpc/README.md's assumption list).
"""
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

import chassis_cfd as cc          # path bootstrapped by tests/conftest.py
from hpc import sbatch_gen as sg

REPO = Path(__file__).resolve().parents[1]


def spec(**overrides):
    """A known-good baseline spec; tests override single fields."""
    base = dict(
        job_name="chassis-6029U",
        partition="compute",
        nodes=4,
        ranks_per_node=48,
        walltime="04:00:00",
        output_dir="/scratch/asciistream/run1",
        sif_image="/shared/apptainer/asciistream-hpc.sif",
        repo_dir="/shared/src/asciistream",
        profile="6029U",
        mesh="fine",
        engine="3d",
        sim_time=30.0,
        dt=0.002,
        fan_duty=0.8,
    )
    base.update(overrides)
    return sg.SbatchSpec(**base)


def command_lines(script):
    """Non-comment lines of an emitted script."""
    return [ln for ln in script.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


# ---------------------------------------------------------------- emission

def test_generates_a_bash_script():
    s = sg.generate_sbatch(spec())
    assert s.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in s


def test_sbatch_directives_present():
    s = sg.generate_sbatch(spec())
    assert "#SBATCH --job-name=chassis-6029U" in s
    assert "#SBATCH --partition=compute" in s
    assert "#SBATCH --nodes=4" in s
    assert "#SBATCH --ntasks-per-node=48" in s
    assert "#SBATCH --time=04:00:00" in s
    assert "#SBATCH --output=/scratch/asciistream/run1/%x-%j.out" in s


def test_no_inline_comments_on_sbatch_lines():
    # several Slurm versions reject "#SBATCH --x=y  # comment"
    for ln in sg.generate_sbatch(spec()).splitlines():
        if ln.startswith("#SBATCH"):
            assert "#" not in ln[len("#SBATCH"):], ln


def test_worker_flag_passthrough():
    s = sg.generate_sbatch(spec())
    assert "--profile 6029U" in s
    assert "--mesh fine" in s
    assert "--engine 3d" in s
    assert "--sim-time 30" in s
    assert "--dt 0.002" in s
    assert "--fan-duty 0.8" in s
    assert "--config /work/server_configs.json" in s
    assert "--worker" in s


def test_profile_with_space_is_shell_quoted():
    s = sg.generate_sbatch(spec(profile="My Server"))
    assert "--profile 'My Server'" in s


def test_numeric_mesh_passthrough():
    assert "--mesh 4" in sg.generate_sbatch(spec(mesh=4.0))
    assert "--mesh 2.5" in sg.generate_sbatch(spec(mesh="2.5"))


def test_optional_flags_omitted_when_none():
    s = sg.generate_sbatch(spec(dt=None, fan_duty=None, fan=None))
    assert "--dt" not in s
    assert "--fan-duty" not in s
    assert "--fan " not in s          # worker defaults apply instead
    assert "--sim-time 30" in s       # sim-time is always explicit


def test_fan_flag_when_given():
    s = sg.generate_sbatch(spec(fan="arctic-s8038-10k"))
    assert "--fan arctic-s8038-10k" in s


def test_account_line_optional():
    assert "--account" not in sg.generate_sbatch(spec())
    s = sg.generate_sbatch(spec(account="hpc123"))
    assert "#SBATCH --account=hpc123" in s


def test_exclusive_toggle():
    assert "#SBATCH --exclusive" in sg.generate_sbatch(spec())
    s = sg.generate_sbatch(spec(exclusive=False))
    assert "#SBATCH --exclusive" not in s


def test_srun_pmix_apptainer_launch():
    s = sg.generate_sbatch(spec())
    assert "srun --mpi=pmix" in s
    assert "apptainer exec" in s
    assert "--pwd /out" in s
    assert '--bind "$REPO_DIR:/work:ro"' in s
    assert '--bind "$OUT_DIR:/out"' in s
    assert "/dev/infiniband" in s


def test_ucx_environment_set():
    s = sg.generate_sbatch(spec())
    assert "export UCX_NET_DEVICES=mlx5_0:1" in s
    assert "export UCX_TLS=rc_x,sm,self" in s
    assert "export OMPI_MCA_pml=ucx" in s     # the anti-TCP-fallback guard


def test_ucx_env_omitted_when_none():
    s = sg.generate_sbatch(spec(ucx_net_devices=None, ucx_tls=None))
    assert "export UCX_NET_DEVICES" not in s
    assert "export UCX_TLS" not in s
    assert "export OMPI_MCA_pml=ucx" in s     # guard stays regardless


def test_fabric_check_toggle():
    assert "ASCIISTREAM_SKIP_FABRIC_CHECK" in sg.generate_sbatch(spec())
    s = sg.generate_sbatch(spec(fabric_check=False))
    assert "ASCIISTREAM_SKIP_FABRIC_CHECK" not in s
    assert "ucx_info" not in s


def test_headless_no_tui_viewer_or_callback():
    """A batch job must never try to reach the TUI socket, trigger the
    host viewer's mid-run export, or open any window."""
    s = sg.generate_sbatch(spec())
    for ln in command_lines(s):     # comments may MENTION the TUI/viewer
        for word in ("--callback-port", "--viz-every", "--cols",
                     "viewer_sidecar", "Qt", "xdg-open", "DISPLAY"):
            assert word not in ln, (word, ln)


def test_determinism():
    assert sg.generate_sbatch(spec()) == sg.generate_sbatch(spec())


def test_output_dir_trailing_slash_normalised():
    s = sg.generate_sbatch(spec(output_dir="/scratch/run2/"))
    assert "#SBATCH --output=/scratch/run2/%x-%j.out" in s
    assert "run2//" not in s


def test_write_sbatch(tmp_path):
    p = sg.write_sbatch(spec(), tmp_path / "job.sbatch")
    assert p.read_text() == sg.generate_sbatch(spec())


# ---------------------------------------------------------- rank arithmetic

@pytest.mark.parametrize("nodes,rpn,total", [
    (1, 1, 1), (4, 48, 192), (15, 48, 720), (2, 7, 14),
])
def test_ntasks_arithmetic(nodes, rpn, total):
    s = sg.generate_sbatch(spec(nodes=nodes, ranks_per_node=rpn))
    assert f"#SBATCH --ntasks={total}" in s
    assert f"#SBATCH --ntasks-per-node={rpn}" in s


@pytest.mark.parametrize("rpn,per_socket", [
    (48, 24), (7, 4), (1, 1), (2, 1), (47, 24),
])
def test_ntasks_per_socket_is_ceiling(rpn, per_socket):
    s = sg.generate_sbatch(spec(ranks_per_node=rpn))
    assert f"#SBATCH --ntasks-per-socket={per_socket}" in s


def test_nomultithread_hint_default():
    assert "#SBATCH --hint=nomultithread" in sg.generate_sbatch(spec())


def test_hyperthreads_drop_hint_and_raise_cap():
    s = sg.generate_sbatch(spec(ranks_per_node=96, use_hyperthreads=True))
    assert "#SBATCH --hint=nomultithread" not in s
    assert "#SBATCH --ntasks-per-socket=48" in s


def test_more_ranks_than_physical_cores_refused_without_ht():
    with pytest.raises(ValueError, match="use_hyperthreads"):
        sg.generate_sbatch(spec(ranks_per_node=49))


def test_more_ranks_than_hw_threads_always_refused():
    with pytest.raises(ValueError, match="ranks_per_node"):
        sg.generate_sbatch(spec(ranks_per_node=97, use_hyperthreads=True))


def test_more_nodes_than_cluster_refused():
    with pytest.raises(ValueError, match="max_nodes"):
        sg.generate_sbatch(spec(nodes=16))
    # ...unless the cluster really is bigger
    s = sg.generate_sbatch(spec(nodes=16, max_nodes=20))
    assert "#SBATCH --nodes=16" in s


# ------------------------------------------------------ invalid combinations

@pytest.mark.parametrize("field,value", [
    ("nodes", 0), ("nodes", -1), ("nodes", 2.5), ("nodes", True),
    ("nodes", "4"),
    ("ranks_per_node", 0), ("ranks_per_node", -4), ("ranks_per_node", None),
    ("walltime", "banana"), ("walltime", 0), ("walltime", -30),
    ("walltime", "00:00:00"), ("walltime", "0"), ("walltime", "4:61:00"),
    ("walltime", "4:00:61"), ("walltime", "1-25:00:00"), ("walltime", ""),
    ("walltime", "-10"), ("walltime", None), ("walltime", True),
    ("job_name", ""), ("job_name", "has space"), ("job_name", "-leading"),
    ("job_name", "x" * 65), ("job_name", "a;rm -rf /"),
    ("partition", ""), ("partition", "two words"),
    ("account", "bad account"),
    ("engine", "4d"), ("engine", ""), ("engine", "3D"),
    ("mesh", "banana"), ("mesh", 0.4), ("mesh", -3), ("mesh", 0),
    ("mesh", True),
    ("sim_time", 0), ("sim_time", -1), ("sim_time", float("nan")),
    ("sim_time", float("inf")), ("sim_time", "soon"),
    ("dt", 0), ("dt", -0.001), ("dt", float("nan")),
    ("fan_duty", 0.01), ("fan_duty", 1.6), ("fan_duty", -0.5),
    ("fan_duty", float("nan")), ("fan_duty", True),
    ("profile", ""), ("profile", "   "), ("profile", "a\nb"),
    ("fan", ""),
    ("output_dir", "relative/path"), ("output_dir", ""),
    ("output_dir", "/has space/x"), ("output_dir", "/bad;dir"),
    ("sif_image", "image.sif"), ("sif_image", ""),
    ("repo_dir", "asciistream"),
    ("ucx_net_devices", "mlx5_0:1; rm -rf /"),
    ("ucx_tls", "rc x"),
])
def test_invalid_specs_are_refused(field, value):
    with pytest.raises(ValueError):
        sg.generate_sbatch(spec(**{field: value}))


def test_dt_exceeding_sim_time_refused():
    with pytest.raises(ValueError, match="sim_time"):
        sg.generate_sbatch(spec(sim_time=1.0, dt=2.0))


@pytest.mark.parametrize("wt,rendered", [
    ("04:00:00", "04:00:00"),      # HH:MM:SS
    ("1-12:00:00", "1-12:00:00"),  # D-HH:MM:SS
    ("2-00", "2-00"),              # D-HH
    ("30:00", "30:00"),            # Slurm reads this as MM:SS
    (90, "90"),                    # minutes
])
def test_valid_walltime_formats(wt, rendered):
    s = sg.generate_sbatch(spec(walltime=wt))
    assert f"#SBATCH --time={rendered}" in s


# ------------------------------------------- consistency with chassis_cfd

def test_mesh_presets_match_solver():
    assert set(sg.MESH_PRESETS) == set(cc.DEFAULT_MESH_SETTINGS)


def test_limits_match_solver():
    assert sg.MESH_MM_FLOOR == cc.MESH_MM_FLOOR
    assert sg.FAN_DUTY_MIN == cc.FAN_DUTY_MIN
    assert sg.FAN_DUTY_MAX == cc.FAN_DUTY_MAX


def test_fan_duty_bounds_inclusive():
    # the worker accepts the closed interval; the generator must too
    for duty in (sg.FAN_DUTY_MIN, sg.FAN_DUTY_MAX):
        assert f"--fan-duty {duty:g}" in \
            sg.generate_sbatch(spec(fan_duty=duty))


@pytest.mark.parametrize("preset", sorted(sg.MESH_PRESETS))
def test_every_mesh_preset_accepted(preset):
    assert f"--mesh {preset}" in sg.generate_sbatch(spec(mesh=preset))


# ----------------------------------------------------------- bash validity

def bash_n(path):
    return subprocess.run(["bash", "-n", str(path)],
                          capture_output=True, text=True)


def test_generated_script_passes_bash_n(tmp_path):
    p = sg.write_sbatch(spec(), tmp_path / "job.sbatch")
    r = bash_n(p)
    assert r.returncode == 0, r.stderr


def test_variants_pass_bash_n(tmp_path):
    variants = [
        spec(dt=None, fan_duty=None),
        spec(fan="arctic-s8038-10k", account="hpc123", engine="2d"),
        spec(fabric_check=False, exclusive=False,
             ucx_net_devices=None, ucx_tls=None),
        spec(profile="My Server", mesh=2.5,
             ranks_per_node=96, use_hyperthreads=True),
    ]
    for i, sp in enumerate(variants):
        p = sg.write_sbatch(sp, tmp_path / f"v{i}.sbatch")
        r = bash_n(p)
        assert r.returncode == 0, r.stderr


def test_committed_example_passes_bash_n():
    example = REPO / "hpc" / "example.sbatch"
    assert example.exists()
    r = bash_n(example)
    assert r.returncode == 0, r.stderr


def test_committed_example_is_current():
    """hpc/example.sbatch must be regenerable byte-for-byte -- catches
    hand edits and generator drift."""
    example = (REPO / "hpc" / "example.sbatch").read_text()
    regenerated = sg.generate_sbatch(spec(
        output_dir="/scratch/asciistream/run1",
        sim_time=30.0, dt=0.002, fan_duty=0.8))
    assert example == regenerated


# ------------------------------------------------------------------- CLI

CLI = [sys.executable, str(REPO / "hpc" / "sbatch_gen.py")]
CLI_ARGS = ["--partition", "compute", "--nodes", "4",
            "--ranks-per-node", "48", "--walltime", "04:00:00",
            "--job-name", "chassis-6029U",
            "--output-dir", "/scratch/asciistream/run1",
            "--sif", "/shared/apptainer/asciistream-hpc.sif",
            "--repo-dir", "/shared/src/asciistream",
            "--profile", "6029U", "--mesh", "fine", "--engine", "3d",
            "--sim-time", "30", "--dt", "0.002", "--fan-duty", "0.8"]


def test_cli_writes_valid_script(tmp_path):
    out = tmp_path / "cli.sbatch"
    r = subprocess.run(CLI + CLI_ARGS + ["-o", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert bash_n(out).returncode == 0
    # CLI and API agree exactly
    assert out.read_text() == sg.generate_sbatch(spec())


def test_cli_stdout_default(tmp_path):
    r = subprocess.run(CLI + CLI_ARGS, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("#!/usr/bin/env bash")


def test_cli_rejects_invalid(tmp_path):
    bad = [a if a != "4" else "0" for a in CLI_ARGS]   # --nodes 0
    r = subprocess.run(CLI + bad, capture_output=True, text=True)
    assert r.returncode != 0
    assert "nodes" in r.stderr
    assert r.stdout == ""


def test_cli_missing_required_args():
    r = subprocess.run(CLI + ["--nodes", "4"],
                       capture_output=True, text=True)
    assert r.returncode != 0
