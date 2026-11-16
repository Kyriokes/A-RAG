import pytest

from app import bekker


def test_encode_example_from_plan():
    assert bekker.encode(1106, "b", 36) == 221_336


def test_roundtrip():
    for text in ["1094a1", "1106b36", "1462b18", "980a21"]:
        ref = bekker.parse(text)
        assert str(bekker.decode(ref.to_int())) == text


def test_order_preserved_across_columns_and_pages():
    seq = ["1094a1", "1094a27", "1094b1", "1094b30", "1095a1"]
    ints = [bekker.parse(s).to_int() for s in seq]
    assert ints == sorted(ints)


def test_parse_range_variants():
    assert bekker.parse_range("1106b36-1107a8") == (221_336, 221_408)
    assert bekker.parse_range("1106b36–40") == (221_336, 221_340)
    s, e = bekker.parse_range("1094a")
    assert bekker.decode(s).page == 1094 and e > s


def test_find_refs_in_text():
    assert bekker.find_refs("¿Qué dice en 1106b36?") == [(221_336, 221_336)]
    assert bekker.find_refs("ver 1097b22-1098a20 y nada más") == [
        (bekker.encode(1097, "b", 22), bekker.encode(1098, "a", 20))
    ]
    assert bekker.find_refs("en 2026 hubo 15 libros") == []


def test_overlap():
    assert bekker.overlap((10, 20), (15, 30)) == 6
    assert bekker.overlap((10, 20), (21, 30)) == 0


def test_format_range():
    assert bekker.format_range(221_336, 221_340) == "1106b36–40"
    assert bekker.format_range(221_336, 221_408, approximate=True) == "1106b36–1107a8 (aprox.)"


def test_rejects_bad_refs():
    with pytest.raises(bekker.BekkerError):
        bekker.parse("1500a1")
    with pytest.raises(bekker.BekkerError):
        bekker.parse("12c3")
