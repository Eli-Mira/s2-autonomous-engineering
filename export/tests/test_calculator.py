"""Acceptance tests for Calculator - PROTECTED, do not modify."""

import pytest
from src.calculator import Calculator


class TestCalculator:
    """Test suite for Calculator module."""
    
    def test_add(self):
        calc = Calculator()
        assert calc.add(2, 3) == 5
        assert calc.add(-1, 1) == 0
        assert calc.add(0, 0) == 0
        assert calc.add(2.5, 3.5) == 6.0
    
    def test_subtract(self):
        calc = Calculator()
        assert calc.subtract(10, 3) == 7
        assert calc.subtract(0, 5) == -5
        assert calc.subtract(-2, -3) == 1
    
    def test_multiply(self):
        calc = Calculator()
        assert calc.multiply(3, 4) == 12
        assert calc.multiply(0, 5) == 0
        assert calc.multiply(-2, 3) == -6
        assert calc.multiply(2.5, 2) == 5.0
    
    def test_divide(self):
        calc = Calculator()
        assert calc.divide(10, 2) == 5.0
        assert calc.divide(7, 2) == 3.5
        assert calc.divide(-6, 2) == -3.0
        with pytest.raises(ZeroDivisionError):
            calc.divide(1, 0)
    
    def test_power(self):
        calc = Calculator()
        assert calc.power(2, 3) == 8
        assert calc.power(5, 0) == 1
        assert calc.power(2, 0.5) == pytest.approx(1.414, rel=1e-3)
        assert calc.power(-2, 3) == -8
    
    def test_factorial(self):
        calc = Calculator()
        assert calc.factorial(0) == 1
        assert calc.factorial(1) == 1
        assert calc.factorial(5) == 120
        assert calc.factorial(3) == 6
        with pytest.raises(ValueError):
            calc.factorial(-1)
