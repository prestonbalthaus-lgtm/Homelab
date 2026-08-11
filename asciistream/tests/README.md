# ASCIISTREAM host regression suite

Baseline tests for the geometry/config/renderer layer of `chassis_cfd.py`.
They run **on the host, with no container**: the module imports its heavy
solver dependencies (gmsh/dolfinx/mpi4py/petsc4py) lazily inside worker
functions, so its top level needs only numpy + rich.
`test_import_hygiene.py` enforces exactly that invariant.

## Running

Provision the host venv once (Homebrew Python 3.12; also installs the
PyVista viewer stack), then run pytest from the repo root:

```sh
./setup_host_viewer.sh
.venv-viewer/bin/python -m pytest tests/ -q
```

Any Python >= 3.10 with `numpy`, `rich` and `pytest` also works - the
viewer venv is just the batteries-included way to get one.

## What is covered

| File | Guards |
|---|---|
| `test_geometry.py` | `build_geometry()` across **all** profiles in `server_configs.json`: well-formed boxes, chassis envelope, fan-wall rule, conditional hardware (0 PCIe slots / 0 bays / 0 CPU sockets). |
| `test_validation.py` | `_validate_geometry()` **rejects** bad input: escaping/inverted boxes, fan-wall straddling, overlapping porous zones, malformed custom zones. |
| `test_config_helpers.py` | `resolve_fan()` (incl. the custom-fan CLI path), `mesh_level_lc()` (incl. the 0.5 mm floor and NaN rejection), `est_cells()`, `apply_hw_overrides()`. |
| `test_fan_operating_point.py` | The fan-curve/system-impedance intersection: `0 < q_op < qmax` for every profile x fan, q_op falls as impedance rises. Replicates the inline formula from `worker_main()` - if you change the physics there, update both. |
| `test_tui_smoke.py` | Headless render of every dashboard panel (banner, top-down canvas, front/rear minis, legend, telemetry tables and the **isometric chassis panel**, which the plain-text report embeds). |
| `test_import_hygiene.py` | `import chassis_cfd` must not pull dolfinx/gmsh/mpi4py/petsc4py/basix/ufl. |

## Ground rules

- Tests must keep passing **against unmodified `chassis_cfd.py`** - they are
  the baseline other changes are checked against. Never weaken a test to
  make new code pass; fix the code or renegotiate the contract explicitly.
- Tests import `chassis_cfd` but must never write to `server_configs.json`
  or any repo file; mutate deep copies of profiles only.
- The solver itself (meshing + Navier-Stokes) still needs the
  `dolfinx/dolfinx:stable` container and is intentionally out of scope here.
