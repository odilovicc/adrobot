import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.services.weights import Slot, WeightsError, distribute


def shares(*slots: Slot) -> list[int]:
    result = distribute(slots)
    return [result[s.key] for s in slots]


@pytest.mark.parametrize(
    ("n", "expected"),
    [(1, [100]), (2, [50, 50]), (3, [34, 33, 33]), (4, [25, 25, 25, 25]), (6, [17, 17, 17, 17, 16, 16])],
)
def test_even_split(n, expected):
    assert shares(*(Slot(i) for i in range(n))) == expected


def test_pinned_25_and_two_floating_gives_38_37():
    assert shares(Slot("a", pinned_share=25), Slot("b"), Slot("c")) == [25, 38, 37]


def test_removed_gets_zero_and_is_excluded():
    assert shares(Slot("a"), Slot("b", active=False), Slot("c"), Slot("d")) == [34, 0, 33, 33]


def test_pinned_order_does_not_matter_for_floating_order():
    assert shares(Slot("a"), Slot("b", pinned_share=25), Slot("c")) == [38, 25, 37]


def test_pinned_over_100_is_error():
    with pytest.raises(WeightsError):
        distribute([Slot("a", pinned_share=60), Slot("b", pinned_share=50)])


@pytest.mark.parametrize("bad", [-1, 101])
def test_pinned_out_of_range_is_error(bad):
    with pytest.raises(WeightsError):
        distribute([Slot("a", pinned_share=bad)])


def test_all_pinned_may_not_sum_to_100():
    assert shares(Slot("a", pinned_share=30), Slot("b", pinned_share=30)) == [30, 30]


def test_pinned_exactly_100_leaves_zero_for_floating():
    assert shares(Slot("a", pinned_share=100), Slot("b"), Slot("c")) == [100, 0, 0]


def test_empty_and_all_removed():
    assert distribute([]) == {}
    assert shares(Slot("a", active=False)) == [0]


slot_lists = st.lists(
    st.tuples(st.booleans(), st.one_of(st.none(), st.integers(0, 40))), min_size=1, max_size=12
).map(lambda xs: [Slot(i, active=a, pinned_share=p) for i, (a, p) in enumerate(xs)])


@given(slot_lists)
def test_properties(slots):
    pinned_total = sum(s.pinned_share for s in slots if s.active and s.pinned_share is not None)
    if pinned_total > 100:
        with pytest.raises(WeightsError):
            distribute(slots)
        return
    result = distribute(slots)
    floating = [s for s in slots if s.active and s.pinned_share is None]
    for s in slots:
        if not s.active:
            assert result[s.key] == 0
        elif s.pinned_share is not None:
            assert result[s.key] == s.pinned_share
    if floating:
        assert sum(result.values()) == 100
        values = [result[s.key] for s in floating]
        assert max(values) - min(values) <= 1
        assert values == sorted(values, reverse=True)
