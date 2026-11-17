"""Pure weight distribution. No DB, no I/O — the numbers everyone checks first."""

from collections.abc import Hashable, Sequence
from dataclasses import dataclass

TOTAL = 100


class WeightsError(ValueError):
    """Invalid weight configuration (e.g. pinned shares exceed 100%)."""


@dataclass(frozen=True)
class Slot:
    """One offer in a stream, in display order.

    ``pinned_share`` is ``None`` for offers whose weight floats; removed offers
    have ``active=False`` and always get 0. ``current`` is the share the slot has
    right now (``None`` for a new offer); it lets ``distribute`` keep an already
    even split instead of moving the rounding remainder around.
    """

    key: Hashable
    active: bool = True
    pinned_share: int | None = None
    current: int | None = None


def distribute(slots: Sequence[Slot]) -> dict[Hashable, int]:
    """Return a share for every slot.

    - removed → 0;
    - pinned → kept as is (sum of pinned > 100 is an error);
    - the rest ``R = 100 - Σpinned`` is split evenly between unpinned active
      slots; the first ``R % n`` of them (in the given order) get +1;
    - stability: if the floating slots already hold an even split of ``R``
      (sum is ``R``, max − min ≤ 1), they keep it — e.g. unpinning one of
      33/33/34 doesn't turn it into 34/33/33 and mark the stream dirty.

    When every active slot is pinned the result may not sum to 100; the caller
    decides whether that is publishable.
    """
    result: dict[Hashable, int] = {}
    pinned_total = 0
    floating: list[Slot] = []

    for slot in slots:
        if not slot.active:
            result[slot.key] = 0
        elif slot.pinned_share is not None:
            if not 0 <= slot.pinned_share <= TOTAL:
                raise WeightsError(f"Вес должен быть от 0 до {TOTAL}%, получено {slot.pinned_share}%")
            result[slot.key] = slot.pinned_share
            pinned_total += slot.pinned_share
        else:
            floating.append(slot)

    if pinned_total > TOTAL:
        raise WeightsError(f"Сумма закреплённых весов {pinned_total}% больше {TOTAL}%")

    rest = TOTAL - pinned_total
    if floating and _is_even_split([s.current for s in floating], rest):
        result.update({s.key: s.current for s in floating})
    elif floating:
        base, rem = divmod(rest, len(floating))
        for i, slot in enumerate(floating):
            result[slot.key] = base + (1 if i < rem else 0)

    return result


def _is_even_split(current: list[int | None], total: int) -> bool:
    if any(c is None for c in current):
        return False
    values = [c for c in current if c is not None]
    return sum(values) == total and max(values) - min(values) <= 1
