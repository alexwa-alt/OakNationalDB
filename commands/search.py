"""Simple search command which will be extended to use SQLite FTS5.
"""
import sys

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python commands/search.py \"query\"")
    else:
        print("Search is not implemented yet. Will use SQLite FTS5 in a future step.")
