# Dependencies and licenses

The core has no third-party runtime dependencies. The project uses the standard MIT
license. Optional components are installed by the user and are not included in this
repository; their own terms apply to their wheels and bundled native libraries.

`transcribe` installs faster-whisper and `av<19`; `links` installs `yt-dlp[default]`;
`all` combines them. yt-dlp has no upper pin, so it can be upgraded as sites change.
Its default extra includes mutagen under GPL terms. Users may choose plain yt-dlp
separately, taking responsibility for any additional site requirements.

The separate `gpu` extra selects `nvidia-cublas-cu12` and `nvidia-cudnn-cu12`.
Windows verification installed the cuBLAS 12.9, cuDNN 9.27 and transitive
NVRTC 12.9 wheel series, then ran CUDA inference on generated speech.
Their CUDA and cuDNN license terms remain the user's own choice; these packages
are separate from `all` and the core. See [GPU setup](gpu.md).

## Installed Python components

Observed after installing `.[all]` on Linux with Python 3.14.8 on 2026-10-10.
This table records installed metadata, not an independent license review. It excludes
the project and build/install tools; these versions are observations, not extra pins.
Windows and macOS wheels can carry different bundled libraries and notices.

| Distribution | Version | Declared license | Metadata field |
|---|---|---|---|
| anyio | 4.15.1 | MIT | License-Expression |
| av | 18.1.0 | BSD-3-Clause | License-Expression |
| brotli | 1.2.0 | MIT | License |
| certifi | 2026.7.22 | MPL-2.0 | License |
| charset-normalizer | 3.5.2 | MIT | License |
| click | 8.5.0 | BSD-3-Clause | License-Expression |
| ctranslate2 | 4.8.2 | MIT | License |
| faster-whisper | 1.2.1 | MIT | License |
| filelock | 4.1.0 | MIT | License-Expression |
| flatbuffers | 25.12.19 | Apache 2.0 | License |
| fsspec | 2026.9.0 | BSD-3-Clause | License-Expression |
| h11 | 0.16.0 | MIT | License |
| hf-xet | 1.7.0 | Apache-2.0 | License-Expression |
| httpcore2 | 2.13.1 | BSD-3-Clause | License-Expression |
| httpx2 | 2.13.1 | BSD-3-Clause | License-Expression |
| huggingface_hub | 2.2.0 | Apache-2.0 | License |
| idna | 3.20 | BSD-3-Clause | License-Expression |
| mutagen | 1.48.1 | GPL-2.0-or-later | License-Expression |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | License-Expression |
| onnxruntime | 1.31.0 | MIT License | License |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | License-Expression |
| protobuf | 7.36.2 | 3-Clause BSD License | License |
| pycryptodomex | 3.24.0 | BSD, Public Domain | License |
| PyYAML | 6.0.3 | MIT | License |
| requests | 2.34.2 | Apache-2.0 | License |
| tokenizers | 0.23.3 | Apache Software License | Classifier |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | License |
| truststore | 0.10.4 | MIT | License-Expression |
| typing_extensions | 4.16.0 | PSF-2.0 | License-Expression |
| urllib3 | 2.8.0 | MIT | License-Expression |
| websockets | 17.2 | BSD-3-Clause | License-Expression |
| yt-dlp | 2026.8.19 | Unlicense | License-Expression |
| yt-dlp-ejs | 0.8.0 | Unlicense AND MIT AND ISC | License-Expression |

## Other components

The system tool reports `ffmpeg version 8.0.1-3ubuntu2`. Its installed license notice states
GPL version 2 or later. Other ffmpeg builds may have different terms.

PyAV and other binary wheels may include native codec libraries with additional
notices. Consult the installed license files before redistributing those components.
`av<19` is retained because newer PyAV breaks decoding with faster-whisper 1.2.1;
`doctor` checks generated PCM decoding without a speech model.

Real-machine checks downloaded and loaded tiny and small speech weights in throwaway caches. Model files are separate
from the speech-engine code and must be checked under their own license before use.
No audio, model files or dependency wheels are committed to this repository.

The optional venv-local ffmpeg fallback uses `imageio-ffmpeg` 0.6.0 (BSD-2-Clause).
Its bundled FFmpeg build has its own native-library notices and GPL terms. It is an
optional setup helper, not a core or transcription dependency. The fallback was tested
on Linux, macOS and Windows; consult the installed wheel notices before redistribution.
