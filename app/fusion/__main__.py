"""CLI for the package-aware synthesis foundation. Run: python -m app.fusion --help."""
import argparse
import json
import os
from pathlib import Path
import sys

from .engine import prepare, preview, messages, read_packet, synthesize
from .packages import FusionError


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Do not overwrite an existing snapshot, reviewed request, or result.
    with path.open('x', encoding='utf-8', newline='\n') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description='AI--boom complete-package synthesis foundation (no package code execution)')
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare', help='Read A/B directory or ZIP; create a local snapshot; no network')
    p.add_argument('--a', required=True)
    p.add_argument('--b', required=True)
    p.add_argument('--goal', default='', help='Optional preference or constraint; omit to explore possibilities from both Skills')
    p.add_argument('--gap-a', action='append', default=[], help='Audited missing A resource; repeatable')
    p.add_argument('--gap-b', action='append', default=[], help='Audited missing B resource; repeatable')
    p.add_argument('--resource-a', action='append', default=[], help='Explicit mount=path for an A resource; repeatable')
    p.add_argument('--resource-b', action='append', default=[], help='Explicit mount=path for a B resource; repeatable')
    p.add_argument('--max-context-chars', type=int, default=160000)
    p.add_argument('--out', required=True)
    p = commands.add_parser('preview', help='Write the exact messages that run will send; no network')
    p.add_argument('--packet', required=True)
    p.add_argument('--out', required=True)
    p = commands.add_parser('directions', help='Save rule hints and score explanations; no model or network')
    p.add_argument('--packet', required=True)
    p.add_argument('--out', required=True)
    p = commands.add_parser('run', help='Send prepared text to the explicitly chosen endpoint; returns unverified proposals')
    p.add_argument('--packet', required=True)
    p.add_argument('--endpoint', required=True, help='Full HTTPS chat/completions URL, or numeric-loopback HTTP')
    p.add_argument('--model', required=True)
    p.add_argument('--key-env', default='SPARK_MODEL_API_KEY', help='Name of an environment variable; never pass a key here')
    p.add_argument('--timeout', type=float, default=90)
    p.add_argument('--max-tokens', type=int, default=8192)
    p.add_argument('--out', required=True)
    commands.add_parser('preread-agent', add_help=False, help='Invoke the read-only Jev specialist worker (preview, probe, run)')
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == 'preread-agent':
        from .jev_agent import main as agent_main
        return agent_main(arguments[1:])
    args = parser.parse_args(arguments)
    try:
        if Path(args.out).exists():
            raise FusionError('Output already exists; choose a new path to preserve prior work')
        if args.command == 'prepare':
            resources = {}
            for label, values in [('A', args.resource_a), ('B', args.resource_b)]:
                resources[label] = []
                for value in values:
                    mount, separator, path = value.partition('=')
                    if not separator:
                        raise FusionError('Resource format is mount=path')
                    resources[label].append({'mount': mount.strip(), 'path': path.strip()})
            packet = prepare(args.a, args.b, args.goal, args.max_context_chars, {'A': args.gap_a, 'B': args.gap_b}, resources)
            write_new(args.out, packet)
            print(json.dumps(preview(packet), ensure_ascii=False, indent=2))
        elif args.command == 'preview':
            packet = read_packet(args.packet)
            write_new(args.out, {'packet_sha256': packet['sha256'], 'messages': messages(packet)})
            print('Prepared request preview saved. No model request was made.')
        elif args.command == 'directions':
            packet = read_packet(args.packet)
            write_new(args.out, {'packet_sha256': packet['sha256'], **packet['rule_directions']})
            print('Rule directions saved; hints only, not verified capabilities.')
        else:
            packet = read_packet(args.packet)
            result = synthesize(packet, endpoint=args.endpoint, model=args.model,
                                api_key=os.environ.get(args.key_env, ''), timeout=args.timeout, max_tokens=args.max_tokens)
            write_new(args.out, result)
            print('proposal_needs_review: saved; executed=false; value_verified=false')
        return 0
    except KeyboardInterrupt:
        print('Cancelled; no successful synthesis result recorded.', file=sys.stderr)
        return 130
    except (FusionError, OSError) as exc:
        message = str(exc) if isinstance(exc, FusionError) else f'Local file access failed ({type(exc).__name__})'
        print(message, file=sys.stderr)
        return 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
