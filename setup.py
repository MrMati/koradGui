from cx_Freeze import setup, Executable

# Dependencies are automatically detected, but they might need fine-tuning.
build_exe_options = {
     "include_files": ["assets/"],
     "excludes": ["tkinter", "unittest"],
     "optimize": 2,
     "zip_include_packages": ["*"],
     "zip_exclude_packages": ["imgui_bundle"],
}

setup(
    name="koradGui",
    version="0.1",
    description="koradGui",
    options={"build_exe": build_exe_options},
    executables=[Executable("main.py", base="gui", target_name="koradGui", icon="icon")],
)