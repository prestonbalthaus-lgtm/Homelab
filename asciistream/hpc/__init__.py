"""asciistream HPC tooling: Apptainer packaging + Slurm job generation.

Host-side, stdlib-only helpers for running the solver on the Phase-2
target cluster (15x Dell R640, dual Xeon Platinum 8160, Mellanox
MCX-456A over InfiniBand, Rocky Linux + Slurm/OpenHPC).

NOTHING in this package has been executed against a real cluster --
there is no Slurm, Apptainer or InfiniBand where it was developed.
See hpc/README.md for the itemised list of untested assumptions.
"""
