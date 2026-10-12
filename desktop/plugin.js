import {
  atom,
  Button,
  Checkbox,
  compactNumber,
  ConfirmDialog,
  host,
  PALETTE_AREA,
  Popover,
  PopoverContent,
  PopoverTrigger,
  STATUSBAR_AREAS,
  useQuery,
  useValue,
} from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'usage-ledger'
export const TALLY_REFRESH_MS = 30_000
const $popoverOpen = atom(false)

const TALLY_FIELDS = [
  { key: 'input_tokens', status: 'In', label: 'Input tokens', kind: 'number' },
  { key: 'output_tokens', status: 'Out', label: 'Output tokens', kind: 'number' },
  { key: 'cache_read_tokens', status: 'CR', label: 'Cache-read tokens', kind: 'number' },
  { key: 'cache_write_tokens', status: 'CW', label: 'Cache-write tokens', kind: 'number' },
  { key: 'reasoning_tokens', status: 'Reason', label: 'Reasoning tokens', kind: 'number' },
  { key: 'total_tokens', status: 'Canonical', label: 'Canonical tokens', kind: 'number' },
  { key: 'expanded_activity_tokens', status: 'Activity', label: 'Activity tokens (expanded)', kind: 'number' },
  { key: 'calls', status: 'Calls', label: 'Calls', kind: 'number' },
  { key: 'thinking_seconds', status: 'Think', label: 'Thinking time', kind: 'duration' },
  { key: 'working_seconds', status: 'Work', label: 'Working time', kind: 'duration' },
  { key: 'words', status: 'Words', label: 'Words', kind: 'number' },
  { key: 'characters', status: 'Chars', label: 'Characters', kind: 'number' },
  { key: 'coding_characters', status: 'Code', label: 'Coding characters', kind: 'number' },
]

export const STATUSLINE_FIELDS_KEY = 'statusline.visibleFields'
const ALL_STATUSLINE_FIELDS = TALLY_FIELDS.map(({ key }) => key)
const $visibleFields = atom(ALL_STATUSLINE_FIELDS)

export function normalizeVisibleFields(raw) {
  if (!Array.isArray(raw)) return [...ALL_STATUSLINE_FIELDS]
  const requested = new Set(raw.filter(value => typeof value === 'string'))
  return TALLY_FIELDS.filter(({ key }) => requested.has(key)).map(({ key }) => key)
}

export function toggleVisibleField(visibleFields, key, checked) {
  const selected = new Set(normalizeVisibleFields(visibleFields))
  if (!TALLY_FIELDS.some(field => field.key === key)) return [...selected]
  if (checked === true) selected.add(key)
  else selected.delete(key)
  return TALLY_FIELDS
    .filter(({ key: fieldKey }) => selected.has(fieldKey))
    .map(({ key: fieldKey }) => fieldKey)
}

export function createStatuslineSelection(storage) {
  const canRead = storage && typeof storage.get === 'function'
  const canWrite = storage && typeof storage.set === 'function'
  let visibleFields = normalizeVisibleFields(
    canRead ? storage.get(STATUSLINE_FIELDS_KEY, ALL_STATUSLINE_FIELDS) : ALL_STATUSLINE_FIELDS,
  )
  return {
    get: () => [...visibleFields],
    set: (key, checked) => {
      visibleFields = toggleVisibleField(visibleFields, key, checked)
      if (canWrite) storage.set(STATUSLINE_FIELDS_KEY, visibleFields)
      return [...visibleFields]
    },
  }
}

export function tallyNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? value
    : null
}

function emptyScope() {
  return Object.fromEntries(TALLY_FIELDS.map(({ key }) => [key, null]))
}

function toScope(raw) {
  const source = raw && typeof raw === 'object' ? raw : {}
  return Object.fromEntries(TALLY_FIELDS.map(({ key }) => [key, tallyNumber(source[key])]))
}

export function toTallyView(raw) {
  const source = raw && typeof raw === 'object' ? raw : {}
  return {
    updated: typeof source.updated === 'string' && source.updated ? source.updated : null,
    current: toScope(source.current),
    running: toScope(source.running),
    working_time_missing_responses: diagnosticInteger(source.working_time_missing_responses),
  }
}

export function formatDuration(seconds) {
  const total = Math.floor(Math.max(0, seconds))
  const wholeSeconds = total % 60
  const minutes = Math.floor(total / 60) % 60
  const hours = Math.floor(total / 3600)
  const paddedSeconds = String(wholeSeconds).padStart(2, '0')
  const paddedMinutes = String(minutes).padStart(2, '0')
  return hours > 0 ? `${hours}:${paddedMinutes}:${paddedSeconds}` : `${minutes}:${paddedSeconds}`
}

function formatValue(value, field, formatNumber) {
  if (value === null) return 'N/A'
  return field.kind === 'duration' ? formatDuration(value) : formatNumber(value)
}

export function pairedValue(current, running, field, formatNumber) {
  const currentLabel = formatValue(current, field, formatNumber)
  const runningLabel = formatValue(running, field, formatNumber)
  return currentLabel === 'N/A' && runningLabel === 'N/A'
    ? 'N/A'
    : `${currentLabel}/${runningLabel}`
}

function visibleTallyFields(visibleFields) {
  if (visibleFields === null || visibleFields === undefined) return TALLY_FIELDS
  const selected = new Set(normalizeVisibleFields(visibleFields))
  return TALLY_FIELDS.filter(field => selected.has(field.key))
}

export function statusFields(view, formatNumber, visibleFields = null) {
  return visibleTallyFields(visibleFields).map(field => [
    field.key === 'working_seconds' && incompleteWorkingTime(view) ? 'Work*' : field.status,
    pairedValue(view.current[field.key], view.running[field.key], field, formatNumber),
  ])
}

export function statusSummary(view, formatNumber, visibleFields = null) {
  const fields = statusFields(view, formatNumber, visibleFields)
  return fields.length === 0
    ? 'No metrics selected'
    : fields
    .map(([label, value]) => `${label} ${value}`)
    .join(' · ')
}

function diagnosticInteger(value) {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

function diagnosticText(value) {
  return typeof value === 'string' && value ? value : 'N/A'
}

export function diagnosticsRows(diagnostics, selectedCount, totalCount) {
  const source = diagnostics && typeof diagnostics === 'object' ? diagnostics : {}
  const tallyState = source.tally_state && typeof source.tally_state === 'object'
    ? source.tally_state
    : null
  const ledgerState = source.observed_ledger && typeof source.observed_ledger === 'object'
    ? source.observed_ledger
    : null
  const refresh = diagnosticInteger(source.refresh_interval_seconds)
  const schema = diagnosticInteger(tallyState?.schema_version)
  const baseline = diagnosticInteger(tallyState?.baseline_sequence)
  const completed = diagnosticInteger(tallyState?.completed_turns)
  const pending = diagnosticInteger(tallyState?.pending_turns)
  const ledgerSchema = diagnosticInteger(ledgerState?.schema_version)
  const observed = diagnosticInteger(ledgerState?.observed_requests)
  const selected = diagnosticInteger(selectedCount)
  const total = diagnosticInteger(totalCount)
  return [
    ['Source', diagnosticText(source.source_scope)],
    ['Refresh', refresh === null ? 'N/A' : `every ${refresh} seconds`],
    ['Tally', tallyState === null || schema === null || baseline === null || completed === null || pending === null
      ? 'N/A'
      : `schema ${schema} · baseline ${baseline} · completed ${completed} · pending ${pending}`],
    ['Ledger', ledgerState === null || ledgerSchema === null || observed === null
      ? 'N/A'
      : `schema ${ledgerSchema} · ${observed} observed requests`],
    ['Statusline', selected === null || total === null ? 'N/A' : `${selected}/${total} metrics selected`],
  ]
}

export async function clearObservedLedger(rest) {
  const result = await rest('/clear', { method: 'POST', body: { confirm: true } })
  if (!result || result.cleared !== true || result.scope !== 'observed-request-ledger') {
    throw new Error('clear response was not verified')
  }
  const summary = await rest('/summary')
  if (!summary || summary.observed_requests !== 0) {
    throw new Error('clear did not verify an empty ledger')
  }
  return result
}

export function detailRows(view, formatNumber) {
  return TALLY_FIELDS.map(field => [
    field.label,
    formatValue(view.current[field.key], field, formatNumber),
    formatRunningValue(view, field, formatNumber),
  ])
}

function incompleteWorkingTime(view) {
  return view.working_time_missing_responses > 0 && tallyNumber(view.running.working_seconds) !== null
}

function formatRunningValue(view, field, formatNumber) {
  const value = formatValue(view.running[field.key], field, formatNumber)
  return field.key === 'working_seconds' && incompleteWorkingTime(view) ? `${value} (incomplete)` : value
}

export function workingTimeNote(view) {
  return incompleteWorkingTime(view)
    ? `Work* is an incomplete cumulative total: confirmed baseline plus measured durations. ${view.working_time_missing_responses} responses have no measured working time; none are estimated.`
    : ''
}

export async function readTallySummary(readTally) {
  try {
    return toTallyView(await readTally())
  } catch {
    return toTallyView(null)
  }
}

export function metricTable(view, visibleFields = null, onVisibilityChange = () => {}) {
  const selected = new Set(normalizeVisibleFields(visibleFields))
  return jsxs('table', {
    className: 'w-full text-xs',
    style: { width: '100%', tableLayout: 'fixed' },
    children: [
      jsx('thead', {
        children: jsxs('tr', {
          className: 'text-(--ui-text-tertiary)',
          children: [
            jsx('th', { className: 'pb-1 text-left font-normal', style: { width: '8%' }, children: 'Show' }),
            jsx('th', { className: 'pb-1 text-left font-normal', style: { width: '36%' }, children: 'Metric' }),
            jsx('th', { className: 'pb-1 text-right font-normal', style: { width: '24%' }, children: 'Current' }),
            jsx('th', { className: 'pb-1 text-right font-normal', style: { width: '32%' }, children: 'Running' }),
          ],
        }),
      }),
      jsx('tbody', {
        children: TALLY_FIELDS.map(field => {
          const current = formatValue(view.current[field.key], field, compactNumber)
          const running = formatRunningValue(view, field, compactNumber)
          return jsxs('tr', {
            children: [
              jsx('td', {
                className: 'py-0.5 pr-2 text-left',
                children: jsx(Checkbox, {
                  checked: selected.has(field.key),
                  onCheckedChange: checked => onVisibilityChange(field.key, checked === true),
                  'aria-label': `Show ${field.label} in status bar`,
                }),
              }),
              jsx('th', { className: 'py-0.5 text-left font-normal text-(--ui-text-tertiary)', children: field.label }),
              jsx('td', { className: 'py-0.5 text-right font-mono tabular-nums', children: current }),
              jsx('td', { className: 'py-0.5 text-right font-mono tabular-nums', children: running }),
            ],
          }, field.key)
        }),
      }),
    ],
  })
}

function MetricSection({ view, visibleFields, onVisibilityChange }) {
  return jsxs('section', {
    className: 'flex flex-col gap-1',
    children: [
      jsx('div', { className: 'font-medium', children: 'tally.md (current / running)' }),
      metricTable(view, visibleFields, onVisibilityChange),
    ],
  })
}

function DiagnosticsSection({ diagnostics, selectedCount, onClearRequest }) {
  return jsxs('section', {
    className: 'flex flex-col gap-1 border-t border-(--ui-stroke-tertiary) pt-2',
    children: [
      jsx('div', { className: 'font-medium', children: 'Diagnostics' }),
      ...diagnosticsRows(diagnostics, selectedCount, TALLY_FIELDS.length).map(([label, value]) =>
        jsxs('div', {
          className: 'flex items-baseline justify-between gap-3',
          children: [
            jsx('span', { className: 'text-(--ui-text-tertiary)', children: label }),
            jsx('span', { className: 'text-right font-mono tabular-nums', children: value }),
          ],
        }, label),
      ),
      jsx('div', {
        className: 'pt-1',
        children: jsx(Button, {
          type: 'button',
          size: 'xs',
          variant: 'destructive',
          onClick: onClearRequest,
          children: 'Clear observed ledger',
        }),
      }),
    ],
  })
}

function UsageDetails({ view, visibleFields, onVisibilityChange, diagnostics, onClearRequest, onRefresh, refreshing }) {
  return jsxs('div', {
    className: 'flex flex-col gap-3 text-xs',
    children: [
      jsx('div', {
        className: 'flex justify-end',
        children: jsx(Button, {
          type: 'button',
          size: 'xs',
          variant: 'secondary',
          onClick: onRefresh,
          disabled: refreshing,
          'aria-label': 'Refresh usage now',
          children: refreshing ? 'Refreshing…' : 'Refresh now',
        }),
      }),
      jsx(MetricSection, { view, visibleFields, onVisibilityChange }),
      jsx(DiagnosticsSection, {
        diagnostics,
        selectedCount: normalizeVisibleFields(visibleFields).length,
        onClearRequest,
      }),
      jsx('div', {
        className: 'text-(--ui-text-tertiary)',
        children: 'Every value is read from the active profile tally.md. Use Show to control the single status-bar summary; the popover always keeps every metric available. Canonical = input + cache-read + cache-write + output; Activity adds reasoning. N/A means a required field is unavailable in the tally.' + (workingTimeNote(view) ? ` ${workingTimeNote(view)}` : ''),
      }),
    ],
  })
}

function useTallyView(readTally) {
  return useQuery({
    queryKey: [ID, 'tally'],
    queryFn: () => readTallySummary(readTally),
    refetchInterval: TALLY_REFRESH_MS,
    retry: false,
    staleTime: 0,
  })
}

let statuslineSelection = createStatuslineSelection()

function configureStatuslineSelection(storage) {
  statuslineSelection = createStatuslineSelection(storage)
  $visibleFields.set(statuslineSelection.get())
}

function toggleStatuslineField(key, checked) {
  $visibleFields.set(statuslineSelection.set(key, checked))
}

function UsageStatusItem({ readTally, readDiagnostics = async () => null, clearLedger = async () => ({}) }) {
  const tallyQuery = useTallyView(readTally)
  const view = tallyQuery.data ?? toTallyView(null)
  const diagnosticsQuery = useQuery({
    queryKey: [ID, 'diagnostics'],
    queryFn: () => readDiagnostics(),
    refetchInterval: TALLY_REFRESH_MS,
    retry: false,
    staleTime: 0,
  })
  const open = useValue($popoverOpen)
  const visibleFields = useValue($visibleFields)
  const [clearOpen, setClearOpen] = useState(false)
  const summary = statusSummary(view, compactNumber, visibleFields)
  const ariaLabel = `Usage tally. Current/running: ${summary}. Open details.`
  const refreshNow = async () => {
    await Promise.all([tallyQuery.refetch(), diagnosticsQuery.refetch()])
  }
  const confirmClear = async () => {
    await clearLedger()
    await diagnosticsQuery.refetch?.()
  }

  return jsxs(Popover, {
    open,
    onOpenChange: next => $popoverOpen.set(next),
    children: [
      jsx(PopoverTrigger, {
        asChild: true,
        children: jsx('button', {
          type: 'button',
          title: ariaLabel,
          className: 'inline-flex h-full items-center gap-1 px-1 text-[0.5625rem] leading-none text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground whitespace-nowrap',
          'aria-label': ariaLabel,
          children: jsxs('span', {
            className: 'inline-flex items-center gap-1 font-mono tabular-nums',
            children: [
              jsx('span', { className: 'font-sans', children: 'Usage' }),
              ...statusFields(view, compactNumber, visibleFields).map(([label, value]) =>
                jsxs('span', {
                  className: 'inline-flex items-center gap-0.5',
                  children: [
                    jsx('span', { className: 'text-(--ui-text-quaternary)', children: label }),
                    jsx('span', { children: value }),
                  ],
                }, label),
              ),
            ],
          }),
        }),
      }),
      jsx(PopoverContent, {
        side: 'top',
        align: 'start',
        className: 'p-3',
        style: {
          width: 'min(32rem, calc(100vw - 1rem))',
          maxHeight: 'min(40rem, var(--radix-popover-content-available-height, calc(100vh - 1rem)))',
          boxSizing: 'border-box',
          whiteSpace: 'normal',
          overflowWrap: 'anywhere',
          overflowY: 'auto',
        },
        'aria-label': 'Usage tally details',
        children: jsx(UsageDetails, {
          view,
          visibleFields,
          onVisibilityChange: toggleStatuslineField,
          diagnostics: diagnosticsQuery.data ?? null,
          onClearRequest: () => setClearOpen(true),
          onRefresh: refreshNow,
          refreshing: Boolean(tallyQuery.isFetching || diagnosticsQuery.isFetching),
        }),
      }),
      jsx(ConfirmDialog, {
        open: clearOpen,
        onClose: () => setClearOpen(false),
        onConfirm: confirmClear,
        title: 'Clear observed ledger?',
        description: 'This clears only locally observed provider-request records. It does not change tally.md, confirmed tally totals, finalizer state, or statusline selections.',
        confirmLabel: 'Clear ledger',
        busyLabel: 'Clearing ledger…',
        doneLabel: 'Ledger cleared',
        destructive: true,
      }),
    ],
  })
}

export async function pluginEndpoint(rest, request, path, options) {
  const actions = new Set(['/tally', '/diagnostics', '/summary', '/clear'])
  if (!actions.has(path)) throw new Error('unknown numeric endpoint')
  if (path === '/clear' && (options?.method !== 'POST' || options?.body?.confirm !== true)) {
    throw new Error('explicit confirmation required')
  }
  try {
    return await rest(path, options)
  } catch (error) {
    // Hermes mounts REST routes at startup. Public commands hot-load safely;
    // use that SDK door only for an unmounted route, not an auth/network error.
    const message = String(error?.message ?? error)
    if (!/^(?:Error invoking remote method 'hermes:api': Error: )?(?:HTTP )?404(?:$|:)/.test(message)) throw error
    const body = { action: path.slice(1) }
    if (path === '/clear') body.confirm = true
    const reply = await request('command.dispatch', { name: ID, arg: JSON.stringify(body) })
    if (reply?.type !== 'plugin' || typeof reply?.output !== 'string') {
      throw new Error('numeric plugin command unavailable')
    }
    const value = JSON.parse(reply.output)
    if (!value || typeof value !== 'object' || Array.isArray(value) || value.error) {
      throw new Error('numeric plugin command failed')
    }
    return value
  }
}

export default {
  id: ID,
  name: 'Usage Ledger',
  description: 'Current and running usage accounting from tally.md.',
  defaultEnabled: false,
  register(ctx) {
    configureStatuslineSelection(ctx.storage)
    const rest = (path, options) => pluginEndpoint(ctx.rest, host.request, path, options)
    ctx.registerMany([
      {
        id: 'status',
        area: STATUSBAR_AREAS.left,
        order: 100,
        render: () => jsx(UsageStatusItem, {
          readTally: () => rest('/tally'),
          readDiagnostics: () => rest('/diagnostics'),
          clearLedger: () => clearObservedLedger(rest),
        }),
      },
      {
        id: 'show',
        area: PALETTE_AREA,
        data: {
          id: `${ID}.show`,
          label: 'Usage: show tally details',
          keywords: ['usage', 'tally', 'tokens', 'working', 'thinking', 'status'],
          detail: () => 'Show current and running tally.md accounting.',
          run: () => $popoverOpen.set(true),
        },
      },
    ])
  },
}
