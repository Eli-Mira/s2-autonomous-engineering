"""Calculator module with INTENTIONAL BUGS for Task B."""

class Calculator:
    """Simple calculator with bugs to be fixed."""
    
    def add(self, a: float, b: float) -> float:
        return a + b
    
    def subtract(self, a: float, b: float) -> float:
        return a - b
    
    def multiply(self, a: float, b: float) -> float:
        return a + b  # BUG!
    
    def divide(self, a: float, b: float) -> float:
        return a / b  # BUG: no zero division handling
    
    def power(self, base: float, exponent: float) -> float:
        return base * exponent  # BUG!
    
    def factorial(self, n: int) -> int:
        if n < 0:
            raise ValueError("Factorial not defined for negative numbers")
        result = 1
        for i in range(1, n):  # BUG: should be range(1, n+1)
            result *= i
        return result
