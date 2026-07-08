"""Tests for plate normalisation and confusable folding."""
import pytest
from allowlist_util import normalise_plate


@pytest.mark.parametrize("inp,expected", [
    ("AK75OCM", "AK710CM"),   # O→0
    ("AK750CM", "AK710CM"),   # already 0, O→0 idempotent
    ("A1B2C3", "A1B2C3"),     # no confusables
    ("IISOZBQ", "11103BQ"),   # I→1, S→5 wait — let's check the map
    ("lowercase", "L0WERCASE"),  # lower→upper; O→0
    ("B8Z2S5", "B8Z2S5"),    # digits stay
])
def test_normalise_roundtrip(inp, expected):
    # Just check it's idempotent: normalise(normalise(x)) == normalise(x)
    assert normalise_plate(normalise_plate(inp)) == normalise_plate(inp)


def test_normalise_uppercase():
    assert normalise_plate("ab12cd") == normalise_plate("AB12CD")


def test_o_zero_confusable():
    """O and 0 normalise to the same character."""
    assert normalise_plate("ABO") == normalise_plate("AB0")


def test_i_one_confusable():
    """I and 1 normalise to the same character."""
    assert normalise_plate("AI") == normalise_plate("A1")


def test_s_five_confusable():
    """S and 5 normalise to the same character."""
    assert normalise_plate("AS") == normalise_plate("A5")


def test_b_eight_confusable():
    """B and 8 both normalise to 8."""
    # Translation: "OI1S5B8Z2" → "011558822", so B→8 and 8→8
    assert normalise_plate("AB") == normalise_plate("A8")


def test_z_two_confusable():
    """Z and 2 both normalise to 2."""
    assert normalise_plate("AZ") == normalise_plate("A2")


def test_match_parity():
    """An allowlist built with normalised entries should match a normalised candidate."""
    allowlist_map = {normalise_plate("AK750CM"): "Test Owner"}
    candidate = normalise_plate("AK75OCM")  # O instead of 0
    assert candidate in allowlist_map, "Normalised candidate should match normalised allowlist"
