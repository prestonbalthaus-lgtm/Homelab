# asciistream on the cluster (Slurm + Apptainer + InfiniBand)

Phase-2 tooling (see `roadmap.md`) for running the dolfinx solver as a
true distributed-memory job on the 15-node Dell R640 cluster (dual Xeon
Platinum 8160, Mellanox MCX-456A over InfiniBand, Rocky Linux,
Slurm/OpenHPC).

> **NONE OF THIS HAS BEEN EXECUTED AGAINST A REAL CLUSTER.**
> It was developed on a macOS/arm64 machine with no Slurm, no Apptainer
> and no InfiniBand. The container *contents* were verified by inspecting
> the base images with docker; the *cluster behaviour* (image build, PMIx
> launch, RDMA engagement, NUMA pinning) is a best-informed draft that
> must be validated on the real system. The full assumption list is at
> the bottom of this file.

## What's here

| File | Purpose |
| --- | --- |
| `Apptainer.def` | Container definition: dolfinx 0.11.0 + PETSc/hypre + gmsh + Open MPI 5.0.10 rebuilt with UCX/PMIx/Slurm + PyVista for offline rendering |
| `sbatch_gen.py` | Stdlib-only, host-testable generator that emits a headless `.sbatch` job script |
| `example.sbatch` | One generated example (committed for reference; regenerate rather than edit) |

## 1. Build the image

On an **x86-64** Linux box with Apptainer (not on the Mac — the cluster
is x86-64 and the def file builds compilers-on-metal), with network
access. Building needs root or user namespaces:

```sh
sudo apptainer build asciistream-hpc.sif hpc/Apptainer.def
# or unprivileged, where user namespaces are enabled:
apptainer build --fakeroot asciistream-hpc.sif hpc/Apptainer.def
```

Expect 1–2 h (it compiles UCX, Open MPI and dolfinx). Copy the resulting
`asciistream-hpc.sif` to a shared filesystem path every compute node can
see, next to a checkout of this repo.

Why not just convert the `dolfinx/dolfinx:stable` image `run.sh` uses?
**Verified (2026-08-11, by inspecting the image):** it ships MPICH with an
embedded libfabric whose runtime-registered providers are tcp/sockets/shm
only — no verbs provider, no UCX, no libibverbs. It is physically
incapable of RDMA, and no bind-mount of host OFED libraries can fix a
provider that was never compiled in; it would silently run inter-node
traffic over TCP. Hence the rebuild in `Apptainer.def` (base:
`dolfinx/dev-env:v0.11.0-openmpi`, whose Open MPI 5.0.10 also lacks UCX —
also verified — so the def file rebuilds the same Open MPI version with
UCX enabled).

## 2. Generate a job script

`hpc/sbatch_gen.py` is plain Python (stdlib only), importable and fully
unit-tested on a host with no Slurm (`tests/test_sbatch_gen.py`):

```sh
python3 hpc/sbatch_gen.py \
    --partition compute --nodes 4 --ranks-per-node 48 \
    --walltime 04:00:00 --job-name chassis-6029U \
    --output-dir /scratch/$USER/asciistream/run1 \
    --sif /shared/apptainer/asciistream-hpc.sif \
    --repo-dir /shared/src/asciistream \
    --profile 6029U --mesh fine --engine 3d \
    --sim-time 30 --dt 0.002 --fan-duty 0.8 \
    -o run1.sbatch
```

Or from Python: build an `hpc.sbatch_gen.SbatchSpec` and call
`generate_sbatch(spec)` / `write_sbatch(spec, path)`. Bad inputs (0
nodes, negative ranks, `walltime="banana"`, `dt > sim_time`, more ranks
per node than the R640's 48 physical cores, >15 nodes, fan duty outside
the solver's [0.05, 1.5]…) raise `ValueError` at generation time instead
of dying in the queue.

What the generated script does:

* `#SBATCH` topology for dual-8160 nodes: `--ntasks-per-socket` splits
  ranks across the two NUMA domains, `--hint=nomultithread` pins to the
  48 physical cores (drop with `--use-hyperthreads`), and the launch uses
  `srun --cpu-bind=cores --distribution=block:block`.
* Sets the UCX environment (`UCX_NET_DEVICES=mlx5_0:1` by default) and —
  the important guard — `OMPI_MCA_pml=ucx`, so the job **aborts** if UCX
  cannot initialise instead of silently falling back to TCP.
* A pre-flight step runs `ucx_info -d` in the container on every node and
  fails the job if no RC/DC (RDMA) transport is visible
  (`ASCIISTREAM_SKIP_FABRIC_CHECK=1` skips it).
* Launches ranks with `srun --mpi=pmix apptainer exec …` — Slurm starts
  every rank natively and wires them via PMIx; no `mpirun`, no host MPI
  libraries in the container processes.
* Binds the repo read-only at `/work`, your output dir at `/out` (the
  container CWD — the solver writes its VTU/PVTU series relative to CWD),
  plus `/dev/infiniband`, `/sys/class/infiniband`, `/etc/libibverbs.d`.
* **Headless by construction**: it runs `chassis_cfd.py --worker` with no
  `--callback-port` (TUI socket) and no `--viz-every` (host-viewer
  export). No dashboard, no Qt, no PyVista window. The product is the
  `<field>_pNN_NNNNNN.vtu` + `<field>NNNNNN.pvtu` series in your output
  dir, which you can post-process anywhere (e.g. with PyVista inside the
  image under `xvfb-run`, or copy to a workstation).

## 3. Submit

```sh
mkdir -p /scratch/$USER/asciistream/run1   # Slurm opens the log here at
sbatch run1.sbatch                         # job start: dir must pre-exist
squeue --me
```

## The version-matching constraint (read this before your first run)

A container MPI that cannot reach the fabric does not fail — it quietly
runs over TCP and you lose the entire point of the cluster. What has to
line up depends on the launch model:

**This setup uses the hybrid model** (`srun --mpi=pmix` + container-owned
MPI/UCX). No host MPI or UCX library is ever loaded into the solver
processes, so their versions do *not* need to match the container's.
What must hold instead:

1. **Slurm ↔ PMIx**: Slurm must have the PMIx plugin
   (`srun --mpi=list` must show `pmix`; OpenHPC builds normally do), and
   Slurm's PMIx must cross-talk with the container Open MPI's bundled
   PMIx. PMIx ≥ 2.2 on both sides negotiates cross-version automatically;
   check the host side with `srun --mpi=list` and `pmix_info --version`
   (host) vs `apptainer exec image.sif pmix_info --version` (container).
2. **Kernel ↔ container rdma-core**: the host's Mellanox OFED *kernel*
   modules expose `/dev/infiniband/*` and `/sys/class/infiniband/*`; the
   container brings its own `rdma-core`/`libibverbs` userspace, which
   works across OFED versions because the kernel uverbs ABI is stable
   (this is how NVIDIA's HPC containers work — an informed assumption
   here, not something we could test). Sanity check on a compute node:
   `apptainer exec --bind /dev/infiniband image.sif ibv_devinfo` must
   list `mlx5_0` in state `PORT_ACTIVE`.
3. **UCX ↔ NIC**: the container UCX must support the mlx5 generation —
   any recent UCX supports ConnectX-4/MCX-456A. Check:
   `apptainer exec ... ucx_info -d | grep Transport` must list `rc_verbs`
   / `rc_mlx5` (or `dc_`) transports, not just `tcp`/`sm`.

**If you instead use the bind model** — host `mpirun`, or bind-mounting
the host's Open MPI/UCX/OFED libraries over the container's — version
matching *does* bite, hard:

* Host and container **Open MPI must match to the same minor series**
  (5.0.x with 5.0.x; `libmpi.so.40` soname compatible) — check
  `ompi_info --version` on both sides.
* Bound **UCX libraries must carry the sonames the container was linked
  against** (`libucp.so.0`, `libuct.so.0`, `libucs.so.0`) at the same or
  newer minor version — check `ucx_info -v` on both sides.
* Bound **MOFED userspace must match the host's MOFED release** —
  `ofed_info -s` on the host names it.
* A mismatch usually does **not** crash: components fail to load and
  Open MPI falls back to TCP. That is why the generated jobs pin
  `OMPI_MCA_pml=ucx` — mismatches become loud aborts.

## Verifying RDMA actually engaged

* The pre-flight step in every generated job (above) — first line of
  defence.
* `UCX_LOG_LEVEL=info` in the job environment makes UCX print the
  selected transport per endpoint; look for `rc_mlx5`/`rc_verbs`, not
  `tcp`.
* Crude but effective: a `fine`-mesh 3-D run whose step time does not
  improve going 1 node → 4 nodes is running over TCP no matter what the
  logs say.

## Itemised list of untested assumptions

Verified facts (docker inspection of the real images, 2026-08-11) are in
`Apptainer.def`'s header. Everything below is an **assumption** — nothing
in this directory has ever run on real HPC hardware:

1. `apptainer build` completes: apt package names (Ubuntu 24.04), the
   UCX 1.18.1 and Open MPI 5.0.10 download URLs, and the
   basix/ufl/ffcx/dolfinx source-build steps (modelled on the upstream
   end-user Dockerfile) are all unexecuted. The `v0.11.0.post0` git tag
   is assumed (falls back to `v0.11.0`).
2. Rebuilding Open MPI 5.0.10 with UCX into the same prefix is
   ABI-compatible with the PETSc/HDF5/mpi4py/petsc4py binaries already in
   the base image (standard Open MPI versioning policy — untested).
3. `srun --mpi=pmix` on the site's Slurm/OpenHPC wires ranks into the
   container Open MPI's bundled PMIx (cross-version PMIx handshake).
4. The container's own rdma-core userspace works against the host's
   MOFED kernel modules via the stable uverbs ABI, with only
   `/dev/infiniband` + `/sys/class/infiniband` bound in.
5. UCX 1.18.1 `rc_x`/`sm`/`self` transports initialise on MCX-456A
   (ConnectX-4) and `UCX_NET_DEVICES=mlx5_0:1` names the right port on
   the R640s (check `ibv_devinfo`; dual-port cards may need `mlx5_1:1`).
6. The NUMA directives (`--ntasks-per-socket`, `--hint=nomultithread`,
   `--cpu-bind=cores`, `--distribution=block:block`) are accepted by the
   site's Slurm version and produce the intended socket-packed pinning on
   dual-8160 nodes (24 cores/socket per Intel ARK — also unverified on
   the actual nodes, as is the node count of 15).
7. Apptainer's default config passes the host environment and mounts
   host `/dev` (the script also binds the IB paths explicitly, and does
   not use `--cleanenv`, to survive stricter site configs — untested).
8. The pre-flight grep pattern (`Transport: *(rc|dc)`) matches the
   `ucx_info -d` output format of the built UCX version.
9. PyVista/VTK render offline under `xvfb-run` in this image
   (`pip install pyvista` unpinned at build time).
10. Multi-node scaling: the solver's per-step `<field>_pNN` VTU writes
    from hundreds of ranks land on a shared filesystem — correctness is
    expected (rank-private files + rank-0 index), filesystem load is
    unmeasured.

If one of these breaks on the real cluster, fix the recipe and move the
item into the verified list — do not delete the list.
