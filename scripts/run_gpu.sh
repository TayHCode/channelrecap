#!/usr/bin/env bash
set -euo pipefail

python_bin="${PYTHON:-python3}"
gpu_unavailable() {
  echo 'Install the GPU libraries from the clone folder: python -m pip install ".[gpu]"' >&2
  exit 3
}
trap gpu_unavailable ERR

nvidia_library_path=$(
  "$python_bin" -c '
import importlib.util

paths = []
for module_name in ("nvidia.cublas.lib", "nvidia.cudnn.lib"):
    spec = importlib.util.find_spec(module_name)
    if spec is None or spec.submodule_search_locations is None:
        raise SystemExit(f"Could not locate {module_name}")
    paths.extend(spec.submodule_search_locations)
print(":".join(paths))
'
)
export LD_LIBRARY_PATH="$nvidia_library_path${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
trap - ERR

exec "$python_bin" -m channelrecap "$@"
