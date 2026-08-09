import meshio

print("Reading velocity XDMF/HDF5...")
# Read the FEniCSx output
mesh = meshio.read("velocity.xdmf")

print("Writing clean VTU file...")
# Export as a unified VTU file that ParaView loves
mesh.write("velocity_clean.vtu")
print("Done! Saved as velocity_clean.vtu")
