# Greeting CLI Tool

## Overview
A tiny Python script that prints a greeting message.

## Tasks

### Task 1: Create `greet.py`
- Create a file `greet.py` that:
  - Accepts an optional `--name` argument (default: "World")
  - Prints `Hello, <name>!`
- Example: `python greet.py --name Ralph` → `Hello, Ralph!`

### Task 2: Add tests
- Create `test_greet.py` with at least 2 tests:
  - Default greeting returns "Hello, World!"
  - Custom name greeting returns "Hello, <name>!"
- Tests must pass with `python -m pytest test_greet.py`
