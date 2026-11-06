import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import zipfile

from tests.conftest import requires_checkout


ROOT = Path(__file__).resolve().parents[1]


@requires_checkout
def test_built_wheel_runs_outside_the_checkout(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(ROOT / name, project / name)
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    roots = {p.split(".")[0] for p in config["tool"]["setuptools"]["packages"]}
    for name in roots:
        shutil.copytree(
            ROOT / name, project / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    wheels = tmp_path / "wheels"
    built = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation",
         "--outdir", str(wheels)],
        cwd=project, capture_output=True, text=True, timeout=120,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    installed = tmp_path / "installed"
    with zipfile.ZipFile(next(wheels.glob("*.whl"))) as wheel:
        wheel.extractall(installed)
    # -I excludes the checkout/PYTHONPATH; dependencies come from the venv.
    script = """
import json
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
import kanban.web as web
import workshop
import fulfillment_scopes
from kanban.trigger import fulfillment_scope_assemblies
from boxes.wms_system.runner import load_baseline
root = Path(sys.argv[1]).resolve()
assert Path(web.__file__).resolve().is_relative_to(root)
assert Path(workshop.__file__).resolve().is_relative_to(root)
assert Path(fulfillment_scopes.__file__).resolve().is_relative_to(root)
assert load_baseline()['baseline_id']
vendor = root / 'kanban' / 'vendor'
assert (vendor / 'three.module.js').stat().st_size > 0
assert (vendor / 'beebox3d.js').stat().st_size > 0
assert (vendor / 'addons' / 'environments' / 'RoomEnvironment.js').is_file()
assemblies = fulfillment_scope_assemblies()
assert all(a.ok for a in assemblies.values())
print(json.dumps(sorted(assemblies)))
"""
    smoke = subprocess.run(
        [sys.executable, "-I", "-c", script, str(installed)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr
    assert json.loads(smoke.stdout) == ["Learning", "WMS"]
