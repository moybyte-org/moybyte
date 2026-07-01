#!/usr/bin/env python3
"""Check kid project code against the portable Moybyte subset."""

import argparse
import sys

from moybyte_cli.portable import check_path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)

    issues = []
    for path in args.paths:
        issues.extend(check_path(path))

    for issue in issues:
        print(issue)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
