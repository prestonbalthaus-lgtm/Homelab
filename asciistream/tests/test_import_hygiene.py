"""Import hygiene: `import chassis_cfd` must NOT pull in the container-only
solver stack. The whole host test suite (and the launcher architecture -
the TUI process must never carry MPI state) depends on gmsh/dolfinx/mpi4py/
petsc4py staying lazy imports inside worker functions."""
import subprocess
import sys

from conftest import REPO

FORBIDDEN = ("dolfinx", "gmsh", "mpi4py", "petsc4py", "basix", "ufl")


def test_fresh_import_pulls_no_solver_stack():
    """Fresh interpreter, so the check cannot be masked by test ordering."""
    code = (
        "import sys\n"
        "import chassis_cfd\n"
        f"forbidden = {FORBIDDEN!r}\n"
        "bad = sorted(m for m in sys.modules"
        " if m.split('.')[0] in forbidden)\n"
        "print(','.join(bad))\n"
    )
    res = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, (
        f"importing chassis_cfd failed:\n{res.stderr}")
    assert res.stdout.strip() == "", (
        "importing chassis_cfd pulled in solver-stack modules "
        f"(lazy-import invariant broken): {res.stdout.strip()}")


def test_this_process_stayed_clean():
    """Belt and braces: after the whole suite has exercised the geometry,
    config and TUI layers in-process, the solver stack must still be
    absent from sys.modules."""
    assert "chassis_cfd" in sys.modules
    bad = sorted(m for m in sys.modules if m.split(".")[0] in FORBIDDEN)
    assert bad == [], f"solver-stack modules leaked into the host: {bad}"
