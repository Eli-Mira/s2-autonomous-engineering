import math

class Calculator:
    def __init__(self):
        self.history = []

    def add(self, a: float, b: float) -> float:
        result = a + b
        self.history.append(('add', a, b, result))
        return result

    def subtract(self, a: float, b: float) -> float:
        result = a - b
        self.history.append(('subtract', a, b, result))
        return result

    def multiply(self, a: float, b: float) -> float:
        # Bug fix: was using addition (+) instead of multiplication (*)
        result = a * b
        self.history.append(('multiply', a, b, result))
        return result

    def divide(self, a: float, b: float) -> float:
        # Bug fix: added ZeroDivisionError check as required
        if b == 0:
            raise ZeroDivisionError("Cannot divide by zero")
        result = a / b
        self.history.append(('divide', a, b, result))
        return result

    def power(self, base: float, exponent: float) -> float:
        # Bug fix: used correct ** operator instead of incorrect logic or missing implementation
        result = base ** exponent
        self.history.append(('power', base, exponent, result))
        return result

    def factorial(self, n: int) -> int:
        # Bug fix: implemented iterative factorial correctly to handle edge cases and avoid recursion depth issues
        if n < 0:
            raise ValueError("Factorial is not defined for negative numbers")
        if n == 0 or n == 1:
            return 1
        result = 1
        for i in range(2, n + 1):
            result *= i
        self.history.append(('factorial', n, result))
        return result

    def get_history(self):
        return self.history
