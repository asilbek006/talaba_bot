"""Isolated pdf2docx fallback; the parent enforces its wall-clock timeout."""

import sys


def main():
    from pdf2docx import Converter

    converter = Converter(sys.argv[1])
    try:
        converter.convert(sys.argv[2], start=0, end=None, multi_processing=False)
    finally:
        converter.close()


if __name__ == "__main__":
    main()
