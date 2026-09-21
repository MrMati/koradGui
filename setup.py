import sys
import tomllib
from cx_Freeze import setup, Executable

with open("pyproject.toml", "rb") as f:
    _version = tomllib.load(f)["project"]["version"]

# Dependencies are automatically detected, but they might need fine-tuning.
build_exe_options = {
     "include_files": ["assets/"],
     "excludes": ["tkinter", "unittest"],
     "optimize": 2,
     "zip_include_packages": ["*"],
     "zip_exclude_packages": ["imgui_bundle", "numpy", "koradgui"],
}

_icon = "assets/app_settings/icon.png"
if sys.platform == "win32":
    # Windows needs .ico; bundle the VC++ runtime DLLs so the zip is self-contained
    _icon = "assets/app_settings/icon.ico"
    build_exe_options["include_msvcr"] = True

setup(
    name="koradGui",
    version=_version,
    description="koradGui",
    options={"build_exe": build_exe_options},
    executables=[Executable("koradgui/main.py", base="gui", target_name="koradGui",
                            icon=_icon)],
)