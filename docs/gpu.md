# GPU transcription

channelrecap was built to be used on an RTX 3090. CPU is the default and works:
it is slower, not different. The settings it was developed with are
`--model large-v3 --device cuda`. Native Windows CUDA was re-run on an RTX 3090 using short generated speech with tiny and small;
large-v3 and Linux/WSL CUDA remain unverified for this package.

## Linux or WSL 2

Install optional components from the clone folder in your active environment:

```sh
python -m pip install ".[all]"
python -m pip install ".[gpu]"
scripts/run_gpu.sh doctor --device cuda
scripts/run_gpu.sh run talk.mp4 --out my-library --model large-v3 --device cuda
```

The `gpu` extra installs NVIDIA cuBLAS and cuDNN wheels under their own terms.
It is separate from `all`. The launcher uses `PYTHON` when set, otherwise `python3`,
finds the wheel library folders, prepends them to `LD_LIBRARY_PATH`, and preserves
your existing path. It changes only its child process environment. If those modules
are absent, it prints the installation command and exits 3.

A missing `libcublas.so.12` or cuDNN library means the wheel folders are not on the
library path: use the launcher. Under WSL 2, check that `nvidia-smi` shows the card
inside Linux before changing anything in Python. For a long batch, run inside
`tmux` or `screen` so it survives a dropped connection.

## Windows (PowerShell)

With ffmpeg on PATH and the Python environment active:

```powershell
python -m pip install ".[all,gpu]"
channelrecap doctor --device cuda
channelrecap run talk.mp4 --out my-library --model tiny --device cuda
```

The Bash launcher is for Linux/WSL only. Native Windows uses the command directly.
An existing NVIDIA driver is required; this project does not install or change drivers.
The real-machine smoke used CUDA 12 wheels and an existing working driver. If a CUDA
DLL cannot load, check your driver and NVIDIA runtime setup; `doctor` alone cannot
prove that every inference-time library will load. Start with tiny before selecting
large-v3, which needs a separate model download and more GPU memory.

## Device behavior

CPU uses `int8`; CUDA uses `float16`. `--compute-type` overrides the choice. An
explicit CUDA request that cannot initialize exits 3 when processing media. Reused
cached media rows load no model and print a warning that CUDA was not validated;
a fully cached `--device cuda` run exits 0 with that warning even on a machine without a GPU,
as listed in the README exit-code table. Use `doctor --device cuda`
and a fresh media output folder or `--force` to check actual GPU transcription. `--device auto` selects CUDA
when available, reports the choice, and falls back to CPU if CUDA initialization fails.
`doctor` checks devices and decodes generated PCM without loading a speech model;
it does not establish that GPU transcription works.

`python scripts/check_gpu.py` checks GPU transcription on one second of generated
silence. It downloads `tiny.en` on first use. Run it with the same library path as
the launcher. Model files stay in your cache and have their own license terms.
GPU speed and recognition quality still require a run on your hardware.
