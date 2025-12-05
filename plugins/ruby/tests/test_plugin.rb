# plugins/ruby/tests/test_plugin.rb
#
# Production-ready RSpec integration tests for the OmniFlow Ruby plugin.
#
# - Spawns the plugin process (default: ruby ../sample_plugin.rb)
# - Communicates via NDJSON (one JSON object per line) over stdin/stdout
# - Tests: health, exec (echo/reverse/compute), malformed JSON resilience,
#          oversized payload handling, unsupported action, graceful shutdown
#
# Place at: OmniFlow/plugins/ruby/tests/test_plugin.rb
# Run:
#   cd <repo-root>
#   bundle install --path vendor/bundle   # if you use bundler and need rspec
#   rspec plugins/ruby/tests/test_plugin.rb
#
# Requirements:
#   - Ruby 2.7+ (3.x recommended)
#   - rspec gem (add to dev dependencies)
#
require 'json'
require 'open3'
require 'timeout'
require 'thread'
require 'rspec'

RSpec.describe 'OmniFlow Ruby Plugin Integration' do
  # Location of the plugin script under test.
  # Override with ENV['OMNIFLOW_RUBY_PLUGIN_CMD'] if needed.
  PLUGIN_CMD = ENV.fetch('OMNIFLOW_RUBY_PLUGIN_CMD', "ruby #{File.expand_path('../../sample_plugin.rb', __dir__)}")

  # How long to wait for responses (seconds)
  RESP_TIMEOUT = 6
  SHORT_WAIT = 0.25

  before(:each) do
    @stdout_lines = Queue.new
    @stderr_lines = Queue.new
    @stdout_buffer = []
    @stderr_buffer = []
    @mutex = Mutex.new

    # Start plugin process
    @stdin, @stdout, @stderr, @wait_thr = Open3.popen3(PLUGIN_CMD)

    # Ensure non-blocking reads via reader threads
    @stdout_reader = Thread.new do
      begin
        @stdout.each_line do |line|
          line = line.chomp
          @stdout_lines << line
          @mutex.synchronize { @stdout_buffer << line }
        end
      rescue IOError
        # STDERR/STDOUT may be closed when process exits
      end
    end

    @stderr_reader = Thread.new do
      begin
        @stderr.each_line do |line|
          line = line.chomp
          @stderr_lines << line
          @mutex.synchronize { @stderr_buffer << line }
        end
      rescue IOError
      end
    end

    # Give the process a short moment to start
    sleep SHORT_WAIT
    unless process_alive?
      dump_debug
      raise "Plugin failed to start (exit code: #{@wait_thr.value.exitstatus})"
    end
  end

  after(:each) do
    # Attempt graceful shutdown
    begin
      send_message({ id: 'rspec-shutdown', type: 'shutdown', payload: nil })
      # Wait briefly for graceful exit
      begin
        Timeout.timeout(3) do
          @wait_thr.join(0.1) while process_alive?
        end
      rescue Timeout::Error
        # Fall through to forced kill
      end
    rescue StandardError
      # ignore
    ensure
      if process_alive?
        Process.kill('KILL', @wait_thr.pid) rescue nil
      end
      # Close pipes and join threads
      begin; @stdin.close unless @stdin.closed?; rescue; end
      begin; @stdout.close unless @stdout.closed?; rescue; end
      begin; @stderr.close unless @stderr.closed?; rescue; end
      @stdout_reader.kill if @stdout_reader&.alive?
      @stderr_reader.kill if @stderr_reader&.alive?
    end
  end

  def process_alive?
    @wait_thr && @wait_thr.alive?
  end

  def dump_debug
    warn "=== plugin stderr (last 100 lines) ==="
    @mutex.synchronize { @stderr_buffer.last(100).each { |l| warn l } }
    warn "=== plugin stdout (last 100 lines) ==="
    @mutex.synchronize { @stdout_buffer.last(100).each { |l| warn l } }
  end

  def send_message(obj)
    json = JSON.generate(obj)
    @stdin.puts json
    @stdin.flush
  end

  # Wait for a response with matching id. Returns parsed JSON object or nil on timeout.
  def wait_for_response(id, timeout_secs = RESP_TIMEOUT)
    deadline = Time.now + timeout_secs
    # First check existing buffer
    loop do
      @mutex.synchronize do
        @stdout_buffer.each do |line|
          begin
            parsed = JSON.parse(line)
            return parsed if parsed['id'] == id
          rescue JSON::ParserError
            # skip malformed lines here
          end
        end
      end
      break if Time.now >= deadline
      sleep 0.05
    end
    nil
  end

  it 'responds to health probe' do
    send_message({ id: 'rb-health-1', type: 'health', payload: nil })
    resp = wait_for_response('rb-health-1', 5)
    expect(resp).not_to be_nil, -> { "No health response; stderr:\n#{@stderr_buffer.last(50).join("\n")}" }
    expect(resp['status'] == 'ok' || (resp.dig('body', 'status') == 'healthy')).to be true
  end

  it 'exec echo returns doubled content or echoes message' do
    send_message({ id: 'rb-echo-1', type: 'exec', payload: { action: 'echo', args: { message: 'hello ruby' } } })
    resp = wait_for_response('rb-echo-1')
    expect(resp).not_to be_nil
    expect(resp['status']).to eq('ok')
    expect(resp.dig('body', 'action')).to eq('echo')
    expect(resp.dig('body', 'message')).to eq('hello ruby')
  end

  it 'exec reverse handles unicode strings' do
    send_message({ id: 'rb-rev-1', type: 'exec', payload: { action: 'reverse', args: { message: 'Привет, 世界! 👋' } } })
    resp = wait_for_response('rb-rev-1')
    expect(resp).not_to be_nil
    expect(resp['status']).to eq('ok')
    expect(resp.dig('body', 'action')).to eq('reverse')
    message = resp.dig('body', 'message')
    expect(message).to be_a(String)
    expect(message.length).to be > 0
    # double reverse should approximate original
    send_message({ id: 'rb-rev-1b', type: 'exec', payload: { action: 'reverse', args: { message: message } } })
    resp2 = wait_for_response('rb-rev-1b')
    expect(resp2).not_to be_nil
    roundtrip = resp2.dig('body', 'message')
    expect(roundtrip).to eq('Привет, 世界! 👋')
  end

  it 'exec compute returns correct sum' do
    send_message({ id: 'rb-calc-1', type: 'exec', payload: { action: 'compute', args: { numbers: [1, 2, 3.5, -1.5] } } })
    resp = wait_for_response('rb-calc-1')
    expect(resp).not_to be_nil
    expect(resp['status']).to eq('ok')
    expect(resp.dig('body', 'action')).to eq('compute')
    sum = resp.dig('body', 'sum')
    expect(sum).to be_within(1e-9).of(10.5)
  end

  it 'does not crash on malformed json' do
    # send a non-json line
    @stdin.puts 'this is not json'
    @stdin.flush
    sleep 0.3
    expect(process_alive?).to be true
    # plugin should optionally emit an error object; if so, ensure it has type 'error' or status 'error'
    # But we primarily assert the process stays alive
  end

  it 'survives oversized payloads' do
    large = 'A' * (200 * 1024) # 200 KiB
    send_message({ id: 'rb-large-1', type: 'exec', payload: { action: 'echo', args: { message: large } } })
    # plugin may respond with error or ok, but must not crash
    sleep 0.6
    expect(process_alive?).to be true
    # Check optional response
    resp = wait_for_response('rb-large-1', 1.0)
    if resp
      expect(['ok', 'error']).to include(resp['status'])
    end
  end

  it 'unsupported action returns error-like response or keeps plugin alive' do
    send_message({ id: 'rb-unk-1', type: 'exec', payload: { action: 'does_not_exist' } })
    resp = wait_for_response('rb-unk-1', 2.0)
    if resp
      expect(resp['status'] == 'error' || resp['status'] == 'busy' || !resp['code'].nil?).to be true
    else
      # acceptable if plugin returns nothing but remains alive
      expect(process_alive?).to be true
    end
  end

  it 'exits gracefully on shutdown' do
    send_message({ id: 'rb-shutdown-1', type: 'shutdown', payload: nil })
    # Wait briefly for either a shutdown response or process exit
    begin
      Timeout.timeout(5) do
        loop do
          break unless process_alive?
          sleep 0.1
        end
      end
    rescue Timeout::Error
      # didn't exit in time
    end
    expect(process_alive?).to be false, -> { "Plugin failed to exit after shutdown; stderr:\n#{@stderr_buffer.last(50).join("\n")}" }
  end
end
