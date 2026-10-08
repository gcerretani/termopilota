#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Genera le icone PWA di TermoPilota in static/icons/.

Uso (solo in sviluppo, i file generati sono committati):
    pip install cairosvg
    python scripts/genera_icone.py

Design: sfondo navy (#1a1a2e), fiamma arancio (--gas-color) e fiocco di neve
blu (--ac-color) affiancati — i due simboli del confronto caldaia/pompa di
calore. I path provengono da Bootstrap Icons (fire, snow — licenza MIT).
"""

import os

import cairosvg

CARTELLA = os.path.join(os.path.dirname(__file__), "..", "static", "icons")

NAVY = "#1a1a2e"
ARANCIO = "#e07b39"
BLU = "#2f80ed"

# Path Bootstrap Icons (viewBox 0 0 16 16)
PATH_FIRE = (
    "M8 16c3.314 0 6-2 6-5.5 0-1.5-.5-4-2.5-6 .25 1.5-1.25 2-1.25 2C11 4 9 .5 6 0"
    "c.357 2 .5 4-2 6-1.25 1-2 2.729-2 4.5C2 14 4.686 16 8 16m0-1c-1.657 0-3-1-3-2.75"
    " 0-.75.25-2 1.25-3C6.125 10 7 10.5 7 10.5c-.375-1.25.5-3.25 2-3.5-.179 1-.25 2 1 3"
    " .625.5 1 1.364 1 2.25C11 14 9.657 15 8 15"
)
PATH_SNOW = (
    "M8 16a.5.5 0 0 1-.5-.5v-1.293l-.646.647a.5.5 0 0 1-.707-.708L7.5 12.793V8.866"
    "l-3.4 1.963-.496 1.85a.5.5 0 1 1-.966-.26l.237-.882-1.12.646a.5.5 0 0 1-.5-.866"
    "l1.12-.646-.884-.237a.5.5 0 1 1 .26-.966l1.848.495L7 8 3.6 6.037l-1.85.495"
    "a.5.5 0 0 1-.258-.966l.883-.237-1.12-.646a.5.5 0 1 1 .5-.866l1.12.646-.237-.883"
    "a.5.5 0 1 1 .966-.258l.495 1.849L7.5 7.134V3.207L6.147 1.854a.5.5 0 1 1 .707-.708"
    "l.646.647V.5a.5.5 0 1 1 1 0v1.293l.647-.647a.5.5 0 1 1 .707.708L8.5 3.207v3.927"
    "l3.4-1.963.496-1.85a.5.5 0 1 1 .966.26l-.236.882 1.12-.646a.5.5 0 0 1 .5.866"
    "l-1.12.646.883.237a.5.5 0 1 1-.26.966l-1.848-.495L9 8l3.4 1.963 1.849-.495"
    "a.5.5 0 0 1 .259.966l-.883.237 1.12.646a.5.5 0 0 1-.5.866l-1.12-.646.236.883"
    "a.5.5 0 1 1-.966.258l-.495-1.849-3.4-1.963v3.927l1.353 1.353a.5.5 0 0 1-.707.708"
    "l-.647-.647V15.5a.5.5 0 0 1-.5.5z"
)


def svg_icona(raggio_angoli: int, scala_contenuto: float = 1.0) -> str:
    """SVG 512x512. scala_contenuto < 1 restringe i simboli verso il centro
    (usato per la variante maskable, che richiede una zona di sicurezza)."""
    s = 14.5 * scala_contenuto            # scala dei path 16x16
    lato = 16 * s                          # dimensione simbolo scalato
    y = (512 - lato) / 2
    x_fire = 256 - lato - 6 * scala_contenuto
    x_snow = 256 + 6 * scala_contenuto
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="{raggio_angoli}" fill="{NAVY}"/>
  <g transform="translate({x_fire:.1f},{y:.1f}) scale({s:.2f})">
    <path fill="{ARANCIO}" d="{PATH_FIRE}"/>
  </g>
  <g transform="translate({x_snow:.1f},{y:.1f}) scale({s:.2f})">
    <path fill="{BLU}" d="{PATH_SNOW}"/>
  </g>
</svg>
"""


def main() -> None:
    os.makedirs(CARTELLA, exist_ok=True)

    svg_master = svg_icona(raggio_angoli=100)
    svg_quadrata = svg_icona(raggio_angoli=0)          # apple-touch (iOS arrotonda da se')
    svg_maskable = svg_icona(raggio_angoli=0, scala_contenuto=0.72)

    with open(os.path.join(CARTELLA, "icon.svg"), "w", encoding="utf-8") as f:
        f.write(svg_master)
    with open(os.path.join(CARTELLA, "favicon.svg"), "w", encoding="utf-8") as f:
        f.write(svg_master)

    def png(svg: str, nome: str, dimensione: int) -> None:
        cairosvg.svg2png(
            bytestring=svg.encode(),
            write_to=os.path.join(CARTELLA, nome),
            output_width=dimensione,
            output_height=dimensione,
        )
        print(f"  {nome} ({dimensione}x{dimensione})")

    png(svg_master, "icon-192.png", 192)
    png(svg_master, "icon-512.png", 512)
    png(svg_maskable, "icon-maskable-512.png", 512)
    png(svg_quadrata, "apple-touch-icon.png", 180)
    print("Icone generate in", os.path.abspath(CARTELLA))


if __name__ == "__main__":
    main()
