#!/usr/bin/env python3
"""
OmniFlow Python Plugin
This plugin multiplies a number by 2 and returns the result.

Input example:
{
    "number": 5
}

Output example:
{
    "message": "Python plugin executed successfully!",
    "result": 10
}
"""

import json
import sys

def main():
    # Read input JSON from stdin
    input_data = sys.stdin.read()
    try:
        event = json.loads(input_data)
    except json.JSONDecodeError:
        event = {}

    # Process the event
    number = event.get("number")
    result = number * 2 if isinstance(number, (int, float)) else None

    # Prepare output
    output = {
        "message": "Python plugin executed successfully!",
        "result": result
    }

    # Write JSON output to stdout
    print(json.dumps(output, indent=4))

if __name__ == "__main__":
    main()
  
