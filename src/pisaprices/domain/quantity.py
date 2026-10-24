"""How much product a package holds, in base units (g, ml, pz)."""

from dataclasses import dataclass

from pisaprices.domain.enums import Unit


@dataclass(frozen=True)
class Quantity:
    amount: float                 # total in the base unit; for variable weight, the reference amount (1 kg = 1000 g)
    unit: Unit
    pieces: int | None = None     # number of packs in a multipack ("6 x 125 g" -> 6)
    variable_weight: bool = False  # sold by weight ("sfuso al kg"): compare only on €/kg
    drained: bool = False         # amount is the drained weight ("sgocciolato")
    approximate: bool = False     # a range was given ("190/200 g"); amount is the lower bound

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValueError(f"quantity must be positive, got {self.amount}")
