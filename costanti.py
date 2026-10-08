# SPDX-License-Identifier: GPL-3.0-or-later
"""
Costanti fisiche condivise tra app.py e automazione.py.

Unica fonte di verita' per la tabella COP e l'interpolazione: prima era
duplicata nei due moduli e andava aggiornata in entrambi.
"""

KWH_PER_SMC = 10.691  # PCS metano (MISE Italia)

# COP pompa di calore Samsung AJ040TXJ2KG/EU (WindFree Comfort Dual)
# COP nominale certificato EN14511: 4.47 W/W a +7°C est. / +20°C int.
# SCOP stagionale: 4.61 W/W — classe A++
# Tabella ancorata al punto certificato con modello η=0.199 × COP_Carnot
COP_TABELLA = [
    (-15, 1.60), (-10, 1.95), (-7, 2.20), (-5, 2.40), (-2, 2.70),
    (0, 2.90), (2, 3.15), (5, 3.75), (7, 4.47), (10, 4.80),
    (15, 5.15), (20, 5.40),
]


def interpola_cop(temp: float) -> float:
    """COP della pompa di calore alla temperatura esterna data (interpolazione lineare)."""
    if temp <= COP_TABELLA[0][0]:
        return COP_TABELLA[0][1]
    if temp >= COP_TABELLA[-1][0]:
        return COP_TABELLA[-1][1]
    for i in range(len(COP_TABELLA) - 1):
        t0, c0 = COP_TABELLA[i]
        t1, c1 = COP_TABELLA[i + 1]
        if t0 <= temp <= t1:
            return round(c0 + (c1 - c0) * (temp - t0) / (t1 - t0), 2)
    return COP_TABELLA[-1][1]
