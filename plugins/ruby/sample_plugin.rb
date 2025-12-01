#!/usr/bin/env ruby
# frozen_string_literal: true
=begin
sample_plugin.rb

Production-ready Ruby plugin for OmniFlow (TheSkiF4er/OmniFlow)
License: Apache-2.0

Overview:
 - Communicates with host via newline-delimited JSON messages on stdin/stdout.
 - Uses stdlib 'json' for parsing/serialization to avoid external deps.
 - Structured logging to stderr (plain text or JSON via OMNIFLOW_LOG_JSON).
 - Graceful shutdown on SIGINT/SIGTERM or receiving a "shutdown" message.
 - Background heartbeat worker and safe timeouts for exec handlers (Timeout.timeout).
 - Configurable via environment variables.
 - Enforces a maximum incoming message size to mitigate DoS.
 - Implements safe built-in actions: echo, reverse, compute (sum).

Usage:
  echo '{"id":"1","type":"health"}' | ./sample_plugin.rb

Environment variables:
 - OMNIFLOW_PLUGIN_MAX_LINE (bytes, default 131072)
 - OMNIFLOW_PLUGIN_HEARTBEAT (seconds, default 5)
 - OMNIFLOW_LOG_JSON (if set -> JSON logs to stderr)
 - OMNIFLOW_EXEC_TIMEOUT (seconds, default 10)
 - OMNIFLOW_PLUGIN_DEBUG (if set -> debug logs)

Notes:
 - Timeout.timeout is best-effort for Ruby threads; if your exec handlers call blocking C extensions
   that don't check Ruby thread state, consider running handlers in external sandboxed processes.
 - For high-performance/production you may wrap this script into a supervised service or container
   and add SAST/static tools in CI.
=end

require 'json'
require 'timeout'
require 'thread'
require 'time'

# ----- Config -----
MAX_LINE = Integer(ENV['OMNIFLOW_PLUGIN_MAX_LINE'] || 131_072)
HEARTBEAT = Integer(ENV['OMNIFLOW_PLUGIN_HEARTBEAT'] || 5)
LOG_JSON = !ENV['OMNIFLOW_LOG_JSON'].to_s.empty?
EXEC_TIMEOUT = Integer(ENV['OMNIFLOW_EXEC_TIMEOUT'] || 10)
DEBUG = !ENV['OMNIFLOW_PLUGIN_DEBUG'].to_s.empty?

PLUGIN_NAME = 'OmniFlowRubyRelease'
PLUGIN_VERSION = '1.0.0'

# ----- Runtime state -----
$running = true
$shutdown_requested = false

# Thread-safe queue for incoming lines
$queue = Queue.new

# ----- Logging helpers -----
def now_iso
  Time.now.utc.iso8601
end

def log_raw(level, msg, extra = nil)
  if LOG_JSON
    rec = { time: now_iso, level: level, plugin: PLUGIN_NAME, message: msg }
    rec[:extra] = extra if extra
    STDERR.puts(JSON.generate(rec))
  else
    STDERR.puts("#{now_iso} [#{level}] #{PLUGIN_NAME}: #{msg}")
  end
  STDERR.flush
end

def info(msg, extra = nil)  log_raw('INFO', msg, extra) end
def warn(msg, extra = nil)  log_raw('WARN', msg, extra) end
def error_log(msg, extra = nil) log_raw('ERROR', msg, extra) end

def debug(msg)
  log_raw('DEBUG', msg) if DEBUG
end

# ----- Respond helpers -----
def respond(obj)
  begin
    puts(JSON.generate(obj))
    STDOUT.flush
  rescue => e
    STDERR.puts("#{now_iso} [ERROR] #{PLUGIN_NAME}: failed to serialize response - #{e}")
  end
end

def respond_ok(id = nil, body = nil)
  r = { 'status' => 'ok' }
  r['id'] = id if id
  r['body'] = body if body
  respond(r)
end

def respond_error(id = nil, code = nil, message = nil)
  r = { 'status' => 'error' }
  r['id'] = id if id
  r['code'] = code if code
  r['message'] = message if message
  respond(r)
end

# ----- Built-in actions -----
def action_echo(payload)
  msg = payload.is_a?(Hash) && payload['message'].is_a?(String) ? payload['message'] : ''
  { 'action' => 'echo', 'message' => msg }
end

def action_reverse(payload)
  msg = payload.is_a?(Hash) && payload['message'].is_a?(String) ? payload['message'] : ''
  # Unicode-safe reverse
  rev = msg.each_char.to_a.reverse.join
  { 'action' => 'reverse', 'message' => rev }
end

def action_compute(payload)
  arr = payload.is_a?(Hash) ? payload['numbers'] : nil
  raise ArgumentError, "missing or invalid 'numbers' array" unless arr.is_a?(Array)
  sum = 0.0
  arr.each do |el|
    raise ArgumentError, 'numbers must be numeric' unless el.is_a?(Numeric)
    sum += el.to_f
  end
  { 'action' => 'compute', 'sum' => sum }
end

# ----- Handlers -----
def handle_health(id)
  respond_ok(id, { 'status' => 'healthy', 'version' => PLUGIN_VERSION })
end

def handle_exec(id, payload)
  # Use Timeout to bound execution time
  begin
    Timeout.timeout(EXEC_TIMEOUT) do
      action = payload.is_a?(Hash) ? payload['action'] : nil
      unless action.is_a?(String)
        respond_error(id, 400, "missing or invalid 'action'")
        return
      end

      case action
      when 'echo'
        respond_ok(id, action_echo(payload))
      when 'reverse'
        respond_ok(id, action_reverse(payload))
      when 'compute'
        begin
          respond_ok(id, action_compute(payload))
        rescue ArgumentError => e
          respond_error(id, 400, e.message)
        end
      else
        respond_error(id, 422, 'unsupported action')
      end
    end
  rescue Timeout::Error
    respond_error(id, 408, 'exec timeout')
  rescue => e
    error_log("exec handler exception: #{e.class}: #{e}")
    respond_error(id, 500, 'internal error')
  end
end

# ----- Reader thread -----
reader_thread = Thread.new do
  begin
    while $running && (line = STDIN.gets)
      bytes = line.bytesize
      if bytes > MAX_LINE
        warn("incoming message too large (#{bytes} bytes), rejecting")
        respond_error(nil, 413, 'payload too large')
        # Drain remainder of line if stdin provided partial - STDIN.gets returns full line including newline
        next
      end
      # Trim newline
      qline = line.chomp
      $queue << qline
    end
  rescue => e
    error_log("stdin reader error: #{e}")
  ensure
    debug('stdin reader exiting')
    $running = false
  end
end

# ----- Background worker -----
bg_thread = Thread.new do
  info("background worker started (heartbeat=#{HEARTBEAT})")
  counter = 0
  while $running
    sleep HEARTBEAT
    break unless $running
    counter += 1
    info("heartbeat #{counter}")
  end
  info('background worker stopping')
end

# ----- Signal handling -----
Signal.trap('INT') do
  warn('SIGINT received, initiating shutdown')
  $shutdown_requested = true
  $running = false
end
Signal.trap('TERM') do
  warn('SIGTERM received, initiating shutdown')
  $shutdown_requested = true
  $running = false
end

# ----- Processor loop -----
while $running || !$queue.empty?
  begin
    line = nil
    begin
      line = $queue.pop(true)
    rescue ThreadError
      # queue empty
      sleep 0.05
      next
    end

    next if line.nil? || line.strip.empty?

    begin
      msg = JSON.parse(line)
    rescue JSON::ParserError
      warn('invalid JSON message')
      respond_error(nil, 400, 'invalid JSON')
      next
    end

    unless msg.is_a?(Hash)
      respond_error(nil, 400, 'invalid message shape')
      next
    end

    id = msg['id'] if msg['id'].is_a?(String)
    type = msg['type'] if msg['type'].is_a?(String)
    payload = msg['payload']

    if type.nil?
      respond_error(id, 400, "missing 'type'")
      next
    end

    case type.downcase
    when 'health'
      handle_health(id)
    when 'exec'
      handle_exec(id, payload)
    when 'shutdown', 'quit'
      respond_ok(id, { 'result' => 'shutting_down' })
      $shutdown_requested = true
      $running = false
      break
    else
      respond_error(id, 400, 'unknown type')
    end
  rescue => e
    error_log("processor unexpected error: #{e.class}: #{e}")
  end
end

# Wait for threads to finish
reader_thread.join(0.5) if reader_thread && reader_thread.alive?
bg_thread.join(0.5) if bg_thread && bg_thread.alive?

info('plugin shutdown complete')
exit(0)
