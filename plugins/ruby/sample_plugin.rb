#!/usr/bin/env ruby
require 'json'

# OmniFlow Ruby Plugin
# This plugin multiplies a number by 2 and returns the result
#
# Input example:
# {
#   "number": 5
# }
#
# Output example:
# {
#   "message": "Ruby plugin executed successfully!",
#   "result": 10
# }

# Read input JSON from stdin
input = STDIN.read
begin
  event = JSON.parse(input)
rescue JSON::ParserError
  event = {}
end

# Process the event
number = event["number"]
result = number.is_a?(Numeric) ? number * 2 : nil

# Prepare output
output = {
  "message" => "Ruby plugin executed successfully!",
  "result" => result
}

# Write JSON output to stdout
puts JSON.pretty_generate(output)
