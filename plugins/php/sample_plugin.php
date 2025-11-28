<?php
/**
 * OmniFlow PHP Plugin
 * This plugin multiplies a number by 2 and returns the result
 *
 * Input example:
 * {
 *   "number": 5
 * }
 *
 * Output example:
 * {
 *   "message": "PHP plugin executed successfully!",
 *   "result": 10
 */

// Read input from stdin
$input = file_get_contents('php://stdin');
$data = json_decode($input, true);

// Initialize result
$result = null;
if (isset($data['number']) && is_numeric($data['number'])) {
    $result = $data['number'] * 2;
}

// Prepare output
$output = [
    'message' => 'PHP plugin executed successfully!',
    'result' => $result
];

// Return output as JSON
echo json_encode($output, JSON_PRETTY_PRINT);
