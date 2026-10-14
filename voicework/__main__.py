"""Run with python3 -m voicework."""
import argparse
import os
from pathlib import Path

from .provider import Provider
from .server import make_server
from .live import PROVIDER_MODES


def main():
    parser = argparse.ArgumentParser(description="Voicework — local writing calibration")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--data-dir", default=os.environ.get("VOICEWORK_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
    parser.add_argument("--provider", default="offline", choices=PROVIDER_MODES,
                        help="claude uses your Claude Code login; anthropic reads ANTHROPIC_API_KEY (never stored); codex uses the Codex CLI")
    parser.add_argument("--model", default=None)
    parser.add_argument("--codex-bin", default=None, help="Codex command or executable path (otherwise VOICEWORK_CODEX_BIN or PATH)")
    parser.add_argument("--claude-bin", default=None, help="Claude Code command or executable path (otherwise VOICEWORK_CLAUDE_BIN or PATH)")
    args = parser.parse_args()
    try:
        executable = args.claude_bin if args.provider == "claude" else args.codex_bin
        provider = Provider(mode=args.provider, model=args.model, executable=executable)
        server = make_server(args.host, args.port, args.data_dir, provider)
    except (ValueError, OSError) as exc:
        parser.exit(2, "Unable to start Voicework: %s\n" % exc)
    print("Voicework is running at http://%s:%d" % (args.host, server.server_address[1]), flush=True)
    print("Provider: %s. Discovery runs only when you request it. Press Ctrl+C to stop." % args.provider, flush=True)
    if args.provider == "anthropic" and not provider.available():
        print("Warning: ANTHROPIC_API_KEY is not set; generation will fail until it is.", flush=True)
    if args.provider == "claude" and not provider.available():
        print("Warning: the Claude Code CLI was not found; pass --claude-bin or set VOICEWORK_CLAUDE_BIN.", flush=True)
    if args.provider == "offline":
        print("Offline mode is heuristic rehearsal, not personalized model calibration.", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
