#!/usr/bin/env python
"""
Riepiloga i log JSON dell'app: costo, latenza, esiti, token.

Uso:
    python scripts/analyze_logs.py app.log        # da un file
    LOG_FORMAT=json streamlit run main.py 2>&1 | python scripts/analyze_logs.py -   # da stdin

Emette i log JSON con `LOG_FORMAT=json`; le righe non-JSON vengono ignorate.
"""
import sys

from nlda.log_analysis import format_summary, parse_lines, summarize


def main(argv: list[str]) -> None:
    if len(argv) > 1 and argv[1] != "-":
        with open(argv[1], encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = sys.stdin.readlines()
    print(format_summary(summarize(parse_lines(lines))))


if __name__ == "__main__":
    main(sys.argv)
