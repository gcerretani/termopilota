# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Avvio locale: `python -m termopilota` (in produzione c'e' gunicorn)."""

from termopilota.app import app


def main():
    print("\n  TermoPilota — controllo riscaldamento")
    print("─" * 42)
    print("  Apri il browser su:  http://localhost:5001")
    print("─" * 42)
    app.run(debug=False, port=5001, host="0.0.0.0")


if __name__ == "__main__":
    main()
