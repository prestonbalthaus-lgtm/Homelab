import pyvista as pv

print("Reading velocity.xdmf using PyVista...")
# PyVista's VTK reader handles XDMF collections natively
data = pv.read("velocity.xdmf")

# If DOLFINx's export created a MultiBlock dataset, grab the primary mesh block
if isinstance(data, pv.MultiBlock):
    print(f"MultiBlock dataset found with {len(data)} blocks. Extracting mesh...")
    grid = data[0] if data[0] is not None else data
else:
    grid = data

print("Mesh loaded successfully!")

# Force off-screen rendering
pv.OFF_SCREEN = True
plotter = pv.Plotter(window_size=[1920, 1080])
plotter.set_background("white")

print("Generating 3D velocity vectors (glyphs)...")
# Dynamically find the velocity array name
vec_name = "velocity" if "velocity" in grid.point_data else list(grid.point_data.keys())[0]

glyphs = grid.glyph(orient=vec_name, scale=vec_name, factor=0.02)
plotter.add_mesh(glyphs, scalars=vec_name, cmap="coolwarm")
plotter.add_mesh(grid.outline(), color="black")

print("Rendering 3D model to server_airflow.png...")
plotter.show(screenshot="server_airflow.png")
print("Done! Open server_airflow.png to view your simulation.")
