"""Numeric tokens shared by document checks and exact edit alignment."""
import re

# Commas belong to a number only between digits. Preserve signs, decimal
# precision, internal separators and percent units; sentence punctuation does
# not change a quantity. This is token comparison, not locale-aware arithmetic.
NUMBER = re.compile(r'(?<!\w)[+\-−]?\d+(?:,\d+)*(?:\.\d+)?(?:%|‰)?(?!\w)')
