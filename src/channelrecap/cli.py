"""Command-line entry point and expected-error exit codes."""
import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
import sys
from typing import Any
from . import NAME, __version__
from .errors import InputError, ToolError, validate_text
from .manifest import read_manifest, read_sources
from .pipeline import RunOptions, transcribe
from .transcriber import Settings
from .tools import doctor
from .summary import prepare
from .citations import check
from .fillers import load_entries, report_fillers
from .words import package_text


def speech_device(value: str) -> str:
    if value.casefold() in {"mps", "metal"}:
        raise argparse.ArgumentTypeError("Apple GPU acceleration (MPS/Metal) is not supported; use --device cpu.")
    if value not in {"cpu", "cuda", "auto"}:
        raise argparse.ArgumentTypeError("device must be cpu, cuda or auto")
    return value


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog=NAME, description="Turn your own videos into transcripts and agent-ready notes.")
    root.add_argument("--version", action="version", version=f"{NAME} {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    command = argparse.ArgumentParser(add_help=False)
    command.add_argument("sources", nargs="*", help="Your local files or individual video links")
    command.add_argument("--manifest", type=Path, help="CSV of sources and optional time ranges")
    command.add_argument("--out", "--output", type=Path, default=Path(f"{NAME}-library"), help="Library folder")
    command.add_argument("--language", help="Language code; otherwise detect it")
    command.add_argument("--force", action="store_true", help="Process finished rows again")
    command.add_argument("--fail-fast", action="store_true", help="Stop after the first failed row")
    command.add_argument("--limit", type=int, help="Maximum pending rows to process")
    command.add_argument("--model", default="small", help="Local model name or folder (default: small)")
    command.add_argument("--device", type=speech_device, default="cpu", help="Speech device (default: cpu)")
    command.add_argument("--compute-type", help="Model precision; defaults to int8 on CPU")
    command.add_argument("--prompt-file", "--initial-prompt-file", type=Path, help="Text containing your names and terms")
    command.add_argument("--verbatim-hint", action="store_true", help="Opt in to an unproven filler preservation hint")
    command.add_argument("--save-site-captions", action="store_true", help="Save optional reference captions for links")
    command.add_argument("--keep-audio", action="store_true", help="Retain temporary WAV clips")
    command.add_argument("--keep-downloads", action="store_true", help="Retain downloaded audio")
    commands.add_parser("transcribe", parents=[command], help="Make or update transcripts only", description="Make or update transcripts only.")
    run = commands.add_parser("run", parents=[command], help="Transcribe and prepare your library", description="Transcribe sources, then prepare summary and agent files.")
    run.add_argument("--fillers-file", type=Path, help="Replace the default filler list")
    citations = commands.add_parser("check-citations", help="Check every report quote and timestamp", description="Verify report quotations against your transcripts.")
    citations.add_argument("file", type=Path, help="Agent report to check")
    citations.add_argument("--out", type=Path, default=Path(f"{NAME}-library"), help="Existing library folder")
    notes = commands.add_parser("prepare", help="Write summary, counts and agent files", description="Prepare notes from finished transcripts.")
    notes.add_argument("--out", type=Path, default=Path(f"{NAME}-library"), help="Existing library folder")
    notes.add_argument("--fillers-file", type=Path, help="Replace the default filler list")
    fillers = commands.add_parser("fillers", help="Report filler counts", description="Count filler words in finished transcripts.")
    fillers.add_argument("--out", type=Path, default=Path(f"{NAME}-library"), help="Existing library folder")
    fillers.add_argument("--fillers-file", type=Path, help="Replace the default filler list")
    fillers.add_argument("--print-default-list", action="store_true", help="Print the editable default list and exit")
    check = commands.add_parser("doctor", help="Check optional tools and audio decoding offline", description="Check your local setup without a network or speech model.")
    check.add_argument("--device", choices=("cpu", "cuda"), default="cpu", help="Also check GPU availability with cuda")
    return root


def main(argv: Sequence[str] | None = None, *, transcriber_factory: Callable[..., Any] | None = None) -> int:
    try:
        argv = list(sys.argv[1:] if argv is None else argv)
        for value in argv:
            validate_text(value, "Command argument")
        try:
            args = parser().parse_args(argv)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 2
        if args.command == "fillers":
            if args.print_default_list:
                print(package_text("fillers_en.txt"), end="")
                return 0
            count = report_fillers(args.out, args.fillers_file)
            print(f"fillers videos={count} csv={args.out / 'fillers.csv'}")
            return 0 if count else 1
        if args.command == "check-citations":
            return check(args.file, args.out)
        if args.command == "prepare":
            count = prepare(args.out, args.fillers_file)
            print(f"prepared videos={count} summary={args.out / 'summary.md'}")
            return 0 if count else 1
        if args.command == "doctor":
            return doctor(args.device)
        if bool(args.manifest) == bool(args.sources):
            raise InputError("Give either --manifest or positional sources.")
        if args.limit is not None and args.limit < 1:
            raise InputError("--limit must be positive.")
        if args.command == "run" and args.fillers_file:
            load_entries(args.fillers_file)
        rows = read_manifest(args.manifest) if args.manifest else read_sources(args.sources)
        prompt = ""
        if args.prompt_file:
            try:
                prompt = args.prompt_file.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise InputError(f"Cannot read prompt file: {exc}") from None
            validate_text(prompt, "Prompt file")
            if len(prompt) > 600:
                print("warning: prompt file is over 600 characters.")
        settings = Settings(args.model, args.device, args.compute_type, prompt, args.verbatim_hint)
        options = RunOptions(args.force, args.fail_fast, args.limit, args.language, args.keep_audio, args.keep_downloads, settings, save_site_captions=args.save_site_captions)
        done, skipped, failed = transcribe(rows, args.out, options, transcriber_factory=transcriber_factory)
        if args.command == "run":
            count = prepare(args.out, args.fillers_file)
            print(f"done={done} cached={skipped} failed={failed} prepared={count} out={args.out}")
            return 2 if options.invalid_input else options.failure_code if failed else 1 if not count else 0
        print(f"done={done} cached={skipped} failed={failed} out={args.out}")
        return 2 if options.invalid_input else options.failure_code if failed else 0
    except (InputError, ToolError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except UnicodeError:
        print("error: Input must contain valid Unicode text.", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: Cannot access input or output: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("error: Interrupted. Finished videos are kept; rerun to continue.", file=sys.stderr)
        return 130
