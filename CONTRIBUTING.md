# Contributing

Create and activate a Python 3.11 or newer virtual environment, then install the core:

```sh
python -m venv .venv
python -m pip install -e .
python -m unittest discover -s tests -q
python -m compileall -q src tests scripts
```

Activate the environment between the first and second commands using your shell's
activation command. The core has no third-party runtime or test dependencies.

Tests must be offline. Import `helpers` before the package under test; it blocks socket
connections and sets the model hub offline. Do not load a real speech model, download
one, or import the actual optional speech or link modules in tests. Use the provided
fake transcriber and executable stubs. Run suites one process at a time, both with
the core alone and with extras installed. Test writes belong in temporary directories.
The helper places these under the ignored `scratch/` folder to keep generated files
inside the checkout. Temporary test folders are removed; the empty parent may remain.

Fixtures must be invented text with no real people, recordings, channels or links.
Use `example.com` for link examples. Generate test audio locally at test time; do not
commit media or binary files. Review expected counts before updating golden files.

Features for collecting other people's videos will not be accepted.
Maintainers can point `FORBIDDEN_TERMS_FILE` at a private word list to check a release.

Characterization tests may change only with a stated reason tied to an approved
behavior change. Keep the GPU constructor, speech arguments, word probabilities
and missing word times covered with engine doubles.
