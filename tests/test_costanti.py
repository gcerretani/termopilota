# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
from termopilota.costanti import COP_TABELLA, interpola_cop


def test_punto_certificato():
    assert interpola_cop(7) == 4.47


def test_clamp_sotto_minimo():
    assert interpola_cop(-30) == COP_TABELLA[0][1]


def test_clamp_sopra_massimo():
    assert interpola_cop(30) == COP_TABELLA[-1][1]


def test_interpolazione_lineare():
    # A meta' tra 5 (3.75) e 7 (4.47) il COP e' la media dei due punti
    assert interpola_cop(6) == round((3.75 + 4.47) / 2, 2)


def test_monotonia_crescente():
    valori = [interpola_cop(t) for t in range(-15, 21)]
    assert valori == sorted(valori)
