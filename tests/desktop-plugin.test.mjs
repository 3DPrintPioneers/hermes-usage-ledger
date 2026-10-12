import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const pluginPath = path.join(projectRoot, 'desktop', 'plugin.js')
const plain = value => JSON.parse(JSON.stringify(value))

async function loadPlugin(queryRefetches = null) {
  const source = await readFile(pluginPath, 'utf8')
  const context = vm.createContext({ console })
  const stateAtom = initial => {
    let value = initial
    return {
      get: () => value,
      set: next => { value = next },
    }
  }
  const sdk = new vm.SyntheticModule(
    [
      'atom',
      'Button',
      'Checkbox',
      'compactNumber',
      'ConfirmDialog',
      'host',
      'PALETTE_AREA',
      'Popover',
      'PopoverContent',
      'PopoverTrigger',
      'STATUSBAR_AREAS',
      'useQuery',
      'useValue',
    ],
    function () {
      this.setExport('atom', stateAtom)
      this.setExport('Button', 'Button')
      this.setExport('Checkbox', 'Checkbox')
      this.setExport('compactNumber', value => `${value}`)
      this.setExport('ConfirmDialog', 'ConfirmDialog')
      this.setExport('host', { state: { focusedUsage: stateAtom(null) } })
      this.setExport('PALETTE_AREA', 'palette')
      this.setExport('Popover', 'Popover')
      this.setExport('PopoverContent', 'PopoverContent')
      this.setExport('PopoverTrigger', 'PopoverTrigger')
      this.setExport('STATUSBAR_AREAS', { left: 'statusBar.left' })
      this.setExport('useQuery', options => ({
        data: undefined,
        isFetching: false,
        refetch: async () => {
          queryRefetches?.push(options.queryKey)
          return { data: undefined }
        },
      }))
      this.setExport('useValue', atom => atom.get())
    },
    { context },
  )
  const jsxRuntime = new vm.SyntheticModule(
    ['jsx', 'jsxs'],
    function () {
      this.setExport('jsx', (type, props) => ({ type, props }))
      this.setExport('jsxs', (type, props) => ({ type, props }))
    },
    { context },
  )
  const react = new vm.SyntheticModule(
    ['useEffect', 'useState'],
    function () {
      this.setExport('useEffect', () => {})
      this.setExport('useState', initial => [initial, () => {}])
    },
    { context },
  )
  const module = new vm.SourceTextModule(source, { context })
  await module.link(specifier => {
    if (specifier === '@hermes/plugin-sdk') return sdk
    if (specifier === 'react') return react
    if (specifier === 'react/jsx-runtime') return jsxRuntime
    throw new Error(`unexpected import: ${specifier}`)
  })
  await module.evaluate()
  return module.namespace
}

test('uses the public plugin command when REST routes await a backend restart', async () => {
  const plugin = await loadPlugin()
  const requests=[]
  for (const message of ['HTTP 404', '404: {"detail":"Not Found"}', "Error invoking remote method 'hermes:api': Error: 404: Not Found"]) {
    const read=async () => { throw new Error(message) }
    const request=async (method,params) => { requests.push([method,plain(params)]); return {type:'plugin',output:JSON.stringify({current:{input_tokens:12}})} }
    assert.deepEqual(plain(await plugin.pluginEndpoint(read,request,'/tally')),{current:{input_tokens:12}})
  }
  const read=async () => { throw new Error('HTTP 404') }
  const request=async () => { throw new Error('must not dispatch unconfirmed clear') }
  assert.equal(requests[0][0],'command.dispatch')
  assert.equal(requests[0][1].name,'usage-ledger')
  assert.deepEqual(JSON.parse(requests[0][1].arg),{action:'tally'})
  await assert.rejects(()=>plugin.pluginEndpoint(read,request,'/clear',{method:'POST',body:{confirm:false}}),/confirmation/)
  assert.equal(requests.length,3)
})

test('rejects non-404 auth and network errors without dispatching commands', async () => {
  const plugin = await loadPlugin()
  for (const message of ['HTTP 401: API key not found', 'HTTP 403: resource not found', 'Network error: host not found', 'HTTP 500: upstream 404', '4040', 'not found']) {
    let dispatched = false
    const failure = new Error(message)
    await assert.rejects(() => plugin.pluginEndpoint(async () => { throw failure }, async () => {
      dispatched = true
      return { type: 'plugin', output: '{}' }
    }, '/tally'), error => error === failure)
    assert.equal(dispatched, false, message)
  }
})

test('confirmed clear fallback verifies the emptied summary', async () => {
  const plugin = await loadPlugin()
  const actions = []
  const rest = async () => { throw new Error('404: {"detail":"Not Found"}') }
  const request = async (_method, params) => {
    const body = JSON.parse(params.arg)
    actions.push(body)
    return { type: 'plugin', output: JSON.stringify(body.action === 'clear' ? { cleared: true, scope: 'observed-request-ledger' } : { observed_requests: 0 }) }
  }
  await plugin.clearObservedLedger((path, options) => plugin.pluginEndpoint(rest, request, path, options))
  assert.deepEqual(actions, [{ action: 'clear', confirm: true }, { action: 'summary' }])
})

test('prefers existing REST routes and never invokes command fallback on success', async () => {
  const plugin=await loadPlugin()
  let fallbackCalls=0
  const result=await plugin.pluginEndpoint(async()=>({status:'ok'}),async()=>{fallbackCalls++;return {}},'/diagnostics')
  assert.deepEqual(plain(result),{status:'ok'})
  assert.equal(fallbackCalls,0)
})

test('marks incomplete cumulative working time without changing current duration', async () => {
  const plugin = await loadPlugin()
  const view = plugin.toTallyView({
    current: { working_seconds: 20 },
    running: { working_seconds: 120 },
    working_time_missing_responses: 8,
  })
  assert.deepEqual(plain(plugin.detailRows(view, String).find(row => row[0] === 'Working time')),
    ['Working time', '0:20', '2:00 (incomplete)'])
  assert.equal(plugin.statusSummary(view, String, ['working_seconds']), 'Work* 0:20/2:00')
  const table = plugin.metricTable(view)
  const working = table.props.children[1].props.children[9]
  assert.equal(working.props.children[3].props.children, '2:00 (incomplete)')
  assert.equal(plugin.workingTimeNote(view),
    'Work* is an incomplete cumulative total: confirmed baseline plus measured durations. 8 responses have no measured working time; none are estimated.')
  for (const missing of [0, null, -1, 1.5, '8']) {
    const complete = plugin.toTallyView({ ...view, working_time_missing_responses: missing })
    assert.equal(plugin.statusSummary(complete, String, ['working_seconds']), 'Work 0:20/2:00')
  }
  const unavailable = plugin.toTallyView({ running: { working_seconds: null }, working_time_missing_responses: 8 })
  assert.equal(plugin.statusSummary(unavailable, String, ['working_seconds']), 'Work N/A')
})

test('bounds the native popover without relying on external Tailwind width generation', async () => {
  const plugin = await loadPlugin()
  const contributions = []
  plugin.default.register({
    registerMany: entries => contributions.push(...entries),
    storage: { get: (_key, fallback) => fallback, set: () => {} },
    rest: async () => ({ current: {}, running: {} }),
  })
  const status = contributions[0].render()
  const popover = status.type(status.props)
  const content = popover.props.children[1]
  assert.equal(content.props.style?.width, 'min(32rem, calc(100vw - 1rem))')
  assert.equal(content.props.style?.whiteSpace, 'normal')
  assert.equal(content.props.style?.overflowWrap, 'anywhere')
  assert.equal(content.props.style?.overflowY, 'auto')
  assert.equal(content.props.style?.boxSizing, 'border-box')
  assert.match(content.props.style?.maxHeight ?? '', /--radix-popover-content-available-height/)
  const table = plugin.metricTable(plugin.toTallyView(null))
  assert.equal(table.props.style?.tableLayout, 'fixed')
})

test('formats every tally metric and each running tally for the status bar', async () => {
  const plugin = await loadPlugin()
  const format = value => `${value}k`
  const view = plugin.toTallyView({
    updated: '2026-09-09T04:30:34-04:00',
    current: {
      input_tokens: 12,
      output_tokens: 34,
      cache_read_tokens: null,
      cache_write_tokens: 56,
      reasoning_tokens: 78,
      total_tokens: 102,
      expanded_activity_tokens: 180,
      calls: 2,
      thinking_seconds: null,
      working_seconds: 90.5,
      words: 10,
      characters: 20,
      coding_characters: 3,
    },
    running: {
      input_tokens: 112,
      output_tokens: 334,
      cache_read_tokens: null,
      cache_write_tokens: 556,
      reasoning_tokens: 778,
      total_tokens: 1002,
      expanded_activity_tokens: 1780,
      calls: 12,
      thinking_seconds: null,
      working_seconds: 1090.5,
      words: 110,
      characters: 220,
      coding_characters: 33,
    },
  })

  assert.deepEqual(plain(plugin.statusFields(view, format)), [
    ['In', '12k/112k'],
    ['Out', '34k/334k'],
    ['CR', 'N/A'],
    ['CW', '56k/556k'],
    ['Reason', '78k/778k'],
    ['Canonical', '102k/1002k'],
    ['Activity', '180k/1780k'],
    ['Calls', '2k/12k'],
    ['Think', 'N/A'],
    ['Work', '1:30/18:10'],
    ['Words', '10k/110k'],
    ['Chars', '20k/220k'],
    ['Code', '3k/33k'],
  ])
  assert.equal(
    plugin.statusSummary(view, format),
    'In 12k/112k · Out 34k/334k · CR N/A · CW 56k/556k · Reason 78k/778k · Canonical 102k/1002k · Activity 180k/1780k · Calls 2k/12k · Think N/A · Work 1:30/18:10 · Words 10k/110k · Chars 20k/220k · Code 3k/33k',
  )
})

test('filters only the checked metrics while preserving their defined order', async () => {
  const plugin = await loadPlugin()
  const view = plugin.toTallyView({
    current: { input_tokens: 12, calls: 2, expanded_activity_tokens: 180 },
    running: { input_tokens: 112, calls: 12, expanded_activity_tokens: 1780 },
  })

  assert.deepEqual(plain(plugin.statusFields(view, value => `${value}`, [
    'calls',
    'input_tokens',
  ])), [
    ['In', '12/112'],
    ['Calls', '2/12'],
  ])
  assert.equal(plugin.statusSummary(view, value => `${value}`, []), 'No metrics selected')
})

test('persists a validated statusline selection in plugin storage', async () => {
  const plugin = await loadPlugin()
  const storage = {
    value: ['output_tokens', 'not-a-field'],
    get(key, fallback) {
      assert.equal(key, plugin.STATUSLINE_FIELDS_KEY)
      return this.value ?? fallback
    },
    set(key, value) {
      assert.equal(key, plugin.STATUSLINE_FIELDS_KEY)
      this.value = value
    },
  }

  const selection = plugin.createStatuslineSelection(storage)
  assert.deepEqual(plain(selection.get()), ['output_tokens'])
  assert.deepEqual(plain(selection.set('input_tokens', true)), ['input_tokens', 'output_tokens'])
  assert.deepEqual(plain(storage.value), ['input_tokens', 'output_tokens'])
  assert.deepEqual(plain(plugin.createStatuslineSelection(storage).get()), ['input_tokens', 'output_tokens'])
  assert.deepEqual(plain(selection.set('output_tokens', false)), ['input_tokens'])
})

test('formats safe diagnostics without turning unavailable state into zero', async () => {
  const plugin = await loadPlugin()
  assert.deepEqual(plain(plugin.diagnosticsRows({
    source_scope: 'active profile tally.md',
    refresh_interval_seconds: 30,
    tally_updated: '2026-09-09T04:30:34-04:00',
    tally_state: {
      schema_version: 2,
      baseline_sequence: 10,
      completed_turns: 11,
      pending_turns: 0,
    },
    observed_ledger: { schema_version: 1, observed_requests: 4 },
  }, 12, 13)), [
    ['Source', 'active profile tally.md'],
    ['Refresh', 'every 30 seconds'],
    ['Tally', 'schema 2 · baseline 10 · completed 11 · pending 0'],
    ['Ledger', 'schema 1 · 4 observed requests'],
    ['Statusline', '12/13 metrics selected'],
  ])
  assert.deepEqual(plain(plugin.diagnosticsRows(null, 0, 13)), [
    ['Source', 'N/A'],
    ['Refresh', 'N/A'],
    ['Tally', 'N/A'],
    ['Ledger', 'N/A'],
    ['Statusline', '0/13 metrics selected'],
  ])
})

test('clears the observed ledger only after confirmation and verifies the empty result', async () => {
  const plugin = await loadPlugin()
  const calls = []
  const result = await plugin.clearObservedLedger(async (path, options) => {
    calls.push({ path, options })
    return path === '/clear'
      ? { cleared: true, scope: 'observed-request-ledger', deleted_records: 4 }
      : { observed_requests: 0 }
  })
  assert.deepEqual(plain(result), { cleared: true, scope: 'observed-request-ledger', deleted_records: 4 })
  assert.deepEqual(plain(calls), [
    { path: '/clear', options: { method: 'POST', body: { confirm: true } } },
    { path: '/summary' },
  ])

  await assert.rejects(
    plugin.clearObservedLedger(async path => path === '/clear'
      ? { cleared: true, scope: 'observed-request-ledger' }
      : { observed_requests: 2 }),
    /did not verify an empty ledger/,
  )
})

test('uses N/A for missing tally fields without replacing explicit zeroes', async () => {
  const plugin = await loadPlugin()
  const view = plugin.toTallyView({
    current: { words: 0 },
    running: { words: 0 },
  })

  assert.deepEqual(plain(plugin.statusFields(view, value => `${value}`)), [
    ['In', 'N/A'],
    ['Out', 'N/A'],
    ['CR', 'N/A'],
    ['CW', 'N/A'],
    ['Reason', 'N/A'],
    ['Canonical', 'N/A'],
    ['Activity', 'N/A'],
    ['Calls', 'N/A'],
    ['Think', 'N/A'],
    ['Work', 'N/A'],
    ['Words', '0/0'],
    ['Chars', 'N/A'],
    ['Code', 'N/A'],
  ])
  assert.deepEqual(plain(await plugin.readTallySummary(async () => ({
    current: { words: 4 },
    running: { words: 14 },
  }))), {
    updated: null,
    working_time_missing_responses: null,
    current: {
      input_tokens: null,
      output_tokens: null,
      cache_read_tokens: null,
      cache_write_tokens: null,
      reasoning_tokens: null,
      total_tokens: null,
      expanded_activity_tokens: null,
      calls: null,
      thinking_seconds: null,
      working_seconds: null,
      words: 4,
      characters: null,
      coding_characters: null,
    },
    running: {
      input_tokens: null,
      output_tokens: null,
      cache_read_tokens: null,
      cache_write_tokens: null,
      reasoning_tokens: null,
      total_tokens: null,
      expanded_activity_tokens: null,
      calls: null,
      thinking_seconds: null,
      working_seconds: null,
      words: 14,
      characters: null,
      coding_characters: null,
    },
  })
  assert.deepEqual(plain(await plugin.readTallySummary(async () => {
    throw new Error('offline')
  })), plain(plugin.toTallyView(null)))
})

test('lays popover metrics out with a checkbox column and two value columns', async () => {
  const plugin = await loadPlugin()
  const table = plugin.metricTable(plugin.toTallyView({
    current: { words: 1 },
    running: { words: 2 },
  }))

  assert.equal(table.type, 'table')
  assert.equal(table.props.children[0].type, 'thead')
  assert.equal(table.props.children[0].props.children.props.children.length, 4)
  assert.equal(table.props.children[1].type, 'tbody')
  assert.equal(table.props.children[1].props.children.length, 13)
  assert.equal(table.props.children[1].props.children[0].props.children.length, 4)
  assert.equal(table.props.children[1].props.children[0].props.children[0].type, 'td')
  assert.equal(table.props.children[1].props.children[0].props.children[0].props.children.type, 'Checkbox')
})

test('checkbox changes immediately filter the live status contribution', async () => {
  const plugin = await loadPlugin()
  const stored = {}
  const contributions = []
  plugin.default.register({
    registerMany: entries => contributions.push(...entries),
    storage: {
      get: (_key, fallback) => stored.value ?? fallback,
      set: (_key, value) => { stored.value = value },
    },
    rest: () => Promise.resolve({ current: { words: 1 }, running: { words: 2 } }),
  })

  const renderStatus = () => {
    const status = contributions[0].render()
    const popover = status.type(status.props)
    const details = popover.props.children[1].props.children
    const detailsElement = details.type(details.props)
    const section = detailsElement.props.children[1]
    const table = section.type(section.props).props.children[1]
    return { status, table }
  }

  const first = renderStatus()
  const firstCheckbox = first.table.props.children[1].props.children[0].props.children[0].props.children
  firstCheckbox.props.onCheckedChange(false)

  const after = renderStatus()
  const trigger = after.status.type(after.status.props).props.children[0]
  const button = trigger.props.children
  assert.equal(button.props.children.props.children.length, 13)
  assert.deepEqual(plain(stored.value), [
    'output_tokens',
    'cache_read_tokens',
    'cache_write_tokens',
    'reasoning_tokens',
    'total_tokens',
    'expanded_activity_tokens',
    'calls',
    'thinking_seconds',
    'working_seconds',
    'words',
    'characters',
    'coding_characters',
  ])
})

test('popover exposes diagnostics and a destructive confirmation boundary', async () => {
  const plugin = await loadPlugin()
  const contributions = []
  plugin.default.register({
    registerMany: entries => contributions.push(...entries),
    storage: { get: (_key, fallback) => fallback, set: () => {}, remove: () => {} },
    rest: () => Promise.resolve({ current: {}, running: {} }),
  })

  const status = contributions[0].render()
  const popover = status.type(status.props)
  const details = popover.props.children[1].props.children
  const detailsElement = details.type(details.props)
  const diagnostics = detailsElement.props.children[2]
  const diagnosticsElement = diagnostics.type(diagnostics.props)
  const button = diagnosticsElement.props.children.at(-1).props.children
  const confirmation = popover.props.children[2]

  assert.equal(button.type, 'Button')
  assert.equal(button.props.children, 'Clear observed ledger')
  assert.equal(confirmation.type, 'ConfirmDialog')
  assert.equal(confirmation.props.open, false)
  assert.equal(confirmation.props.destructive, true)
  assert.match(confirmation.props.description, /does not change tally\.md/)
})

test('refreshes tally data no more frequently than every 30 seconds', async () => {
  const plugin = await loadPlugin()

  assert.equal(plugin.TALLY_REFRESH_MS, 30_000)
})

test('refreshes tally and diagnostics immediately from the popover', async () => {
  const queryRefetches = []
  const plugin = await loadPlugin(queryRefetches)
  const contributions = []
  plugin.default.register({
    registerMany: entries => contributions.push(...entries),
    storage: { get: (_key, fallback) => fallback, set: () => {}, remove: () => {} },
    rest: () => Promise.resolve({ current: {}, running: {} }),
  })

  const status = contributions[0].render()
  const popover = status.type(status.props)
  const details = popover.props.children[1].props.children
  const detailsElement = details.type(details.props)
  const refreshRow = detailsElement.props.children[0]
  const button = refreshRow?.props?.children

  assert.equal(button?.type, 'Button')
  assert.equal(button?.props?.children, 'Refresh now')
  await button.props.onClick()
  assert.deepEqual(plain(queryRefetches), [
    ['usage-ledger', 'tally'],
    ['usage-ledger', 'diagnostics'],
  ])
})

test('registers one tally-backed left status contribution and one palette command', async () => {
  const plugin = await loadPlugin()
  const contributions = []
  const restCalls = []
  const context = {
    registerMany: entries => contributions.push(...entries),
    rest: path => {
      restCalls.push(path)
      return Promise.resolve({ current: { words: 1 }, running: { words: 1 } })
    },
    storage: {
      get: (_key, fallback) => fallback,
      set: () => {},
      remove: () => {},
    },
  }

  plugin.default.register(context)

  assert.equal(plugin.default.id, 'usage-ledger')
  assert.equal(plugin.default.defaultEnabled, false)
  assert.deepEqual(
    contributions.map(entry => entry.area),
    ['statusBar.left', 'palette'],
  )
  const statusElement = contributions[0].render()
  await statusElement.props.readTally()
  assert.deepEqual(restCalls, ['/tally'])
  assert.equal(contributions[1].data.label, 'Usage: show tally details')
})
