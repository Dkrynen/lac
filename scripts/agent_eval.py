from backend.agent_eval.command import main as _main, parse_args


def main(argv=None, **kwargs):
    return _main(argv, _entrypoint_path=__file__, **kwargs)


if __name__ == "__main__":
    raise SystemExit(main())
