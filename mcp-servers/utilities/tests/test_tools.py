import pytest
from mcp.server.mcpserver.exceptions import ToolError

from utilities_mcp.server import calculate, convert_units, current_time


class TestCalculate:
    @pytest.mark.parametrize(
        "expression,expected",
        [
            ("1 + 2 * 3", 7),
            ("(349.5 * 3) * 1.18", 1237.23),
            ("-5 + 2", -3),
            ("2 ** 10", 1024),
            ("10 // 3", 3),
            ("10 % 4", 2),
            ("sqrt(16)", 4),
            ("abs(-3.5)", 3.5),
            ("round(2.567, 1)", 2.6),
        ],
    )
    def test_arithmetic(self, expression, expected):
        assert calculate(expression)["result"] == pytest.approx(expected)

    @pytest.mark.parametrize(
        "expression",
        [
            "__import__('os').system('echo hacked')",
            "().__class__.__bases__",
            "open('/etc/passwd').read()",
            "exec('print(1)')",
            "eval('1+1')",
            "lambda: 1",
            "[x for x in range(10)]",
            "x + 1",
            "'a' * 5",
            "True + 1",
            "None",
            "1 if True else 2",
            "(1).__add__(2)",
            "sqrt.__globals__",
            "abs(1, 2, 3)",
            "9 ** 9 ** 9",
            "2 ** 1001",
            "",
            "1 +",
            "1; 2",
        ],
    )
    def test_anything_but_arithmetic_is_rejected(self, expression):
        with pytest.raises(ToolError):
            calculate(expression)

    def test_division_by_zero_is_a_clean_error(self):
        with pytest.raises(ToolError, match="zero"):
            calculate("1 / 0")
        with pytest.raises(ToolError, match="zero"):
            calculate("5 % 0")

    def test_the_expression_is_echoed_back(self):
        assert calculate("  2 + 2 ")["expression"] == "2 + 2"


class TestConvertUnits:
    @pytest.mark.parametrize(
        "value,source,target,expected",
        [
            (10, "km", "mi", 6.213712),
            (1, "mi", "km", 1.609344),
            (100, "c", "f", 212),
            (32, "f", "c", 0),
            (0, "c", "k", 273.15),
            (1, "kg", "lb", 2.204623),
            (500, "ml", "l", 0.5),
            (12, "IN", "Cm", 30.48),
        ],
    )
    def test_conversions(self, value, source, target, expected):
        assert convert_units(value, source, target)["result"] == pytest.approx(expected, rel=1e-5)

    @pytest.mark.parametrize("source,target", [("kg", "km"), ("c", "kg"), ("l", "m"), ("parsec", "km"), ("km", ""), ("", "")])
    def test_incompatible_or_unknown_units_are_rejected(self, source, target):
        with pytest.raises(ToolError, match="Cannot convert"):
            convert_units(1, source, target)


class TestCurrentTime:
    def test_known_timezone(self):
        result = current_time("Asia/Kolkata")
        assert result["iso"].endswith("+05:30") and result["timezone"] == "Asia/Kolkata"

    def test_default_is_utc(self):
        assert current_time()["iso"].endswith("+00:00")

    @pytest.mark.parametrize("zone", ["Mars/Olympus", "", "../../etc/passwd", "UTC+5", "x" * 500])
    def test_unknown_timezones_are_clean_errors(self, zone):
        with pytest.raises(ToolError, match="Unknown timezone"):
            current_time(zone)
