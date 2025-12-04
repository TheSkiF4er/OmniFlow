<?php
declare(strict_types=1);

/**
 * plugins/php/tests/test_plugin.php
 *
 * Production-ready PHPUnit integration tests for the OmniFlow PHP plugin.
 *
 * - Designed to be run with PHPUnit (phpunit).
 * - Spawns the plugin as a subprocess (php CLI script or built binary).
 * - Communicates over NDJSON (one JSON object per line) via stdin/stdout.
 * - Tests: health, exec (echo/reverse/compute), malformed JSON resilience,
 *   oversized payload handling, unsupported action behavior, graceful shutdown.
 *
 * Place at: OmniFlow/plugins/php/tests/test_plugin.php
 *
 * Usage:
 *   composer require --dev phpunit/phpunit
 *   ./vendor/bin/phpunit plugins/php/tests/test_plugin.php
 *
 * Notes:
 * - Adjust $this->pluginCmd in setUp() if your plugin entrypoint differs.
 * - Requires `jq` only if you want shell-style assertions; this file uses PHP's JSON handling.
 */

use PHPUnit\Framework\TestCase;

final class PluginIntegrationTest extends TestCase
{
    private $proc;          // resource returned by proc_open
    private $pipes;         // array of pipes [0 => stdin, 1 => stdout, 2 => stderr]
    private string $pluginCmd; // command to launch plugin (set in setUp)
    private int $startTimeout = 2; // seconds to wait for plugin to initialize
    private int $respTimeout = 5;  // seconds to wait for normal responses

    protected function setUp(): void
    {
        // Default plugin command; change this if your plugin binary / script path differs.
        // Examples:
        //  - PHP script: "php ../../plugins/php/sample_plugin.php"
        //  - Phar/binary: "/opt/omniflow/bin/sample_plugin_php"
        $this->pluginCmd = getenv('OMNIFLOW_PHP_PLUGIN_CMD') ?: 'php ' . __DIR__ . '/../sample_plugin.php';

        $this->startPlugin($this->pluginCmd);
    }

    protected function tearDown(): void
    {
        $this->stopPlugin();
    }

    // ----------------------
    // Subprocess helpers
    // ----------------------
    private function startPlugin(string $cmd): void
    {
        $descriptors = [
            0 => ['pipe', 'r'], // stdin
            1 => ['pipe', 'w'], // stdout
            2 => ['pipe', 'w'], // stderr
        ];

        $cwd = dirname(__DIR__) . '/..'; // plugin root
        $env = array_merge($_ENV, $_SERVER);

        $this->proc = proc_open($cmd, $descriptors, $this->pipes, $cwd, $env);

        if (!is_resource($this->proc)) {
            $this->fail("Failed to start plugin with command: $cmd");
        }

        // Set pipes to non-blocking mode for safe reads
        stream_set_blocking($this->pipes[0], false);
        stream_set_blocking($this->pipes[1], false);
        stream_set_blocking($this->pipes[2], false);

        // Give plugin short time to initialize (some plugins emit a ready/health event)
        $initDeadline = microtime(true) + $this->startTimeout;
        // Some plugins may emit a startup message; attempt to drain it but don't require it.
        while (microtime(true) < $initDeadline) {
            $line = $this->readLineFromStdout(0.05);
            if ($line !== null) {
                // Accept but do not assert here; tests will perform health probes explicitly.
                break;
            }
        }
    }

    private function stopPlugin(): void
    {
        if (!is_resource($this->proc)) {
            return;
        }

        // Attempt graceful close: send shutdown message
        $this->sendMessage(['id' => 'php-test-shutdown', 'type' => 'shutdown', 'payload' => null]);

        // Wait briefly for plugin to exit on its own
        $status = proc_get_status($this->proc);
        $deadline = microtime(true) + 3.0;
        while ($status['running'] && microtime(true) < $deadline) {
            usleep(100_000);
            $status = proc_get_status($this->proc);
        }

        if ($status['running']) {
            // Force close pipes and terminate
            foreach ($this->pipes as $p) {
                @fclose($p);
            }
            proc_terminate($this->proc, 9);
            proc_close($this->proc);
        } else {
            // Normal exit: close pipes and get exit code
            foreach ($this->pipes as $p) {
                @fclose($p);
            }
            proc_close($this->proc);
        }
    }

    private function sendMessage(array $obj): void
    {
        $json = json_encode($obj, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        if ($json === false) {
            $this->fail("Failed to json_encode message: " . var_export($obj, true));
        }
        // Append newline for NDJSON framing
        fwrite($this->pipes[0], $json . "\n");
        fflush($this->pipes[0]);
    }

    /**
     * Read one line (a single NDJSON JSON object) from stdout within timeout seconds.
     * Returns decoded associative array or null on timeout.
     */
    private function readResponse(float $timeoutSeconds = null): ?array
    {
        $timeoutSeconds = $timeoutSeconds ?? $this->respTimeout;
        $deadline = microtime(true) + $timeoutSeconds;
        $buffer = '';

        while (microtime(true) < $deadline) {
            $chunk = stream_get_contents($this->pipes[1], 8192);
            if ($chunk !== false && $chunk !== '') {
                $buffer .= $chunk;
                // Try to split by newline and extract the first non-empty line
                if (strpos($buffer, "\n") !== false) {
                    [$line, $rest] = preg_split("/\r?\n/", $buffer, 2);
                    // Preserve leftover in buffer for future reads by rewinding stream? Simpler: ignore leftovers.
                    // Attempt to decode JSON line
                    $line = trim($line);
                    if ($line === '') {
                        $buffer = $rest ?? '';
                        continue;
                    }
                    $decoded = json_decode($line, true);
                    if (json_last_error() === JSON_ERROR_NONE && is_array($decoded)) {
                        return $decoded;
                    } else {
                        // malformed JSON line — return full debug info
                        return ['__raw' => $line, '__json_error' => json_last_error_msg()];
                    }
                }
            }
            // also check stderr for indications of plugin failure
            $errchunk = stream_get_contents($this->pipes[2], 8192);
            if ($errchunk !== false && $errchunk !== '') {
                // keep reading but do not fail immediately
            }
            usleep(50_000); // 50 ms
        }
        return null;
    }

    // Short helper to attempt a very quick read (for startup drain)
    private function readLineFromStdout(float $timeoutSeconds = 0.05): ?string
    {
        $deadline = microtime(true) + $timeoutSeconds;
        $buffer = '';
        while (microtime(true) < $deadline) {
            $chunk = stream_get_contents($this->pipes[1], 8192);
            if ($chunk !== false && $chunk !== '') {
                $buffer .= $chunk;
                if (strpos($buffer, "\n") !== false) {
                    [$line, $rest] = preg_split("/\r?\n/", $buffer, 2);
                    return trim($line);
                }
            }
            usleep(10_000);
        }
        return null;
    }

    // ----------------------
    // Tests
    // ----------------------

    public function testHealth(): void
    {
        $id = 'php-health-1';
        $this->sendMessage(['id' => $id, 'type' => 'health', 'payload' => null]);

        $resp = $this->readResponse(4.0);
        $this->assertNotNull($resp, 'No health response received');
        $this->assertArrayHasKey('id', $resp);
        $this->assertSame($id, $resp['id']);
        // Accept either status "ok" or body.status == "healthy"
        $ok = (isset($resp['status']) && $resp['status'] === 'ok')
            || (isset($resp['body']['status']) && $resp['body']['status'] === 'healthy');
        $this->assertTrue($ok, 'Unexpected health response: ' . json_encode($resp));
    }

    public function testExecEcho(): void
    {
        $id = 'php-echo-1';
        $this->sendMessage([
            'id' => $id,
            'type' => 'exec',
            'payload' => ['action' => 'echo', 'args' => ['message' => 'hello php']]
        ]);
        $resp = $this->readResponse();
        $this->assertNotNull($resp, 'No echo response');
        $this->assertSame($id, $resp['id'] ?? null);
        $this->assertSame('ok', $resp['status'] ?? null, 'Echo response must be ok');
        $this->assertArrayHasKey('body', $resp);
        $this->assertSame('echo', $resp['body']['action'] ?? null);
        $this->assertSame('hello php', $resp['body']['message'] ?? null);
    }

    public function testExecReverseUnicode(): void
    {
        $id = 'php-rev-1';
        $this->sendMessage([
            'id' => $id,
            'type' => 'exec',
            'payload' => ['action' => 'reverse', 'args' => ['message' => 'Привет']]
        ]);
        $resp = $this->readResponse();
        $this->assertNotNull($resp, 'No reverse response');
        $this->assertSame($id, $resp['id'] ?? null);
        $this->assertSame('ok', $resp['status'] ?? null);
        $this->assertSame('reverse', $resp['body']['action'] ?? null);
        $this->assertIsString($resp['body']['message'] ?? null);
        $this->assertNotEmpty($resp['body']['message']);
        // verify reversing twice yields original
        $reversed = $resp['body']['message'];
        $double = $this->mb_strrev($this->mb_strrev($reversed));
        $this->assertSame('Привет', $double);
    }

    public function testExecComputeSum(): void
    {
        $id = 'php-calc-1';
        $this->sendMessage([
            'id' => $id,
            'type' => 'exec',
            'payload' => ['action' => 'compute', 'args' => ['numbers' => [1, 2, 3.5, -1.5]]]
        ]);
        $resp = $this->readResponse();
        $this->assertNotNull($resp, 'No compute response');
        $this->assertSame($id, $resp['id'] ?? null);
        $this->assertSame('ok', $resp['status'] ?? null);
        $this->assertSame('compute', $resp['body']['action'] ?? null);
        $sum = $resp['body']['sum'] ?? null;
        $this->assertNotNull($sum);
        $this->assertEqualsWithDelta(10.5, (float)$sum, 1e-9);
    }

    public function testMalformedJsonDoesNotCrash(): void
    {
        // Send a malformed JSON line and assert plugin stays alive
        fwrite($this->pipes[0], "this is not json\n");
        fflush($this->pipes[0]);

        // Give the plugin a moment to process; it must not crash
        usleep(300_000);
        $status = proc_get_status($this->proc);
        $this->assertTrue($status['running'], 'Plugin crashed on malformed JSON');
    }

    public function testOversizedPayloadSurvival(): void
    {
        // Construct a large message (200 KiB)
        $size = 200 * 1024;
        $big = str_repeat('A', $size);
        $id = 'php-large-1';
        $payload = [
            'id' => $id,
            'type' => 'exec',
            'payload' => ['action' => 'echo', 'args' => ['message' => $big]]
        ];
        $this->sendMessage($payload);

        // Wait a short while; plugin should survive. Some plugins may return an error response,
        // but must not crash or exit.
        usleep(600_000);

        $status = proc_get_status($this->proc);
        $this->assertTrue($status['running'], 'Plugin crashed on oversized payload');

        // Optionally, check for an error response or a graceful error code
        $resp = $this->readResponse(0.5);
        if ($resp !== null) {
            // Accept either ok (if plugin handles it) or error with code
            $this->assertTrue(
                ($resp['status'] === 'ok') || ($resp['status'] === 'error') || isset($resp['code']),
                'Unexpected response to oversized payload: ' . json_encode($resp)
            );
        }
    }

    public function testUnsupportedActionReturnsError(): void
    {
        $id = 'php-unk-1';
        $this->sendMessage([
            'id' => $id,
            'type' => 'exec',
            'payload' => ['action' => 'does_not_exist']
        ]);
        $resp = $this->readResponse(1.5);
        if ($resp === null) {
            // Accept missing explicit reply but plugin must remain alive
            $status = proc_get_status($this->proc);
            $this->assertTrue($status['running'], 'Plugin exited on unsupported action');
        } else {
            $this->assertSame($id, $resp['id'] ?? null);
            $this->assertTrue(
                ($resp['status'] === 'error') || ($resp['status'] === 'busy') || isset($resp['code']),
                'Unsupported action should yield an error-like response'
            );
        }
    }

    public function testGracefulShutdown(): void
    {
        $id = 'php-shutdown-1';
        $this->sendMessage(['id' => $id, 'type' => 'shutdown', 'payload' => null]);

        // Allow a few seconds for graceful shutdown
        $deadline = microtime(true) + 5.0;
        $exited = false;
        while (microtime(true) < $deadline) {
            $status = proc_get_status($this->proc);
            if (!$status['running']) {
                $exited = true;
                break;
            }
            usleep(100_000);
        }

        $this->assertTrue($exited, 'Plugin did not exit gracefully after shutdown request');

        // Optionally, verify shutdown response if plugin emitted one
        // (we try to read it but don't require it)
        $resp = $this->readResponse(0.5);
        if ($resp !== null) {
            $this->assertSame($id, $resp['id'] ?? null, 'Shutdown response must echo id');
        }
    }

    // ----------------------
    // Utility helpers
    // ----------------------

    /**
     * Multibyte-safe string reverse for Unicode (uses grapheme clusters where possible).
     */
    private function mb_strrev(string $s): string
    {
        // If intl extension installed, prefer grapheme_* functions
        if (function_exists('grapheme_strlen') && function_exists('grapheme_substr')) {
            $len = grapheme_strlen($s);
            $out = '';
            for ($i = $len - 1; $i >= 0; $i--) {
                $out .= grapheme_substr($s, $i, 1);
            }
            return $out;
        }

        // Fallback: reverse by codepoints
        $chars = preg_split('//u', $s, -1, PREG_SPLIT_NO_EMPTY);
        if ($chars === false) {
            return strrev($s);
        }
        return implode('', array_reverse($chars));
    }
}
