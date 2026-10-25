# Task C: StringProcessor Statistics Enhancement

## Depends on: Task A (StringProcessor base implementation)

## New Requirements

Add statistical analysis methods to StringProcessor:

1. `char_frequency(self, text: str) -> dict[str, int]`
   - Returns frequency count of each character
   - Case sensitive

2. `word_frequency(self, text: str) -> dict[str, int]`
   - Returns frequency count of each word (lowercase, stripped of punctuation)
   - Case insensitive

3. `entropy(self, text: str) -> float`
   - Calculate Shannon entropy of the string
   - Formula: -sum(p * log2(p)) for each character probability p
   - Return 0 for empty string

4. `longest_word(self, text: str) -> str`
   - Return the longest word (by character count)
   - Tie-breaker: first occurrence
   - Return empty string for empty input

## Acceptance Tests (to be added to test_string_processor.py)
- Tests will verify these new methods work correctly
