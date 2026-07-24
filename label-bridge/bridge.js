#!/usr/bin/env node
/**
 * WhatsApp label bridge — labels ONLY, never messages.
 *
 * Connects to the owner's WhatsApp Business app as a linked device (like
 * WhatsApp Web) and keeps the app's chat labels in sync with the bot's
 * customer categories (workspace/data/customers.json, written by db.py).
 * All customer messaging stays on the official Kapso transport.
 *
 * First run: prints a QR to progress/label-bridge-qr.png — scan it from
 * WhatsApp Business app > Settings > Linked devices > Link a device.
 * Auth persists in label-bridge/auth/ (gitignored); later runs need no QR.
 *
 * Design lessons from the old Baileys reconciler: mutate ONLY on change,
 * ~1.5s gap between mutations, never spam re-asserts — WhatsApp silently
 * ignores high-frequency label churn.
 */

const fs = require('fs')
const path = require('path')
const { execFile } = require('child_process')
const pino = require('pino')
const QR = require('qrcode')
const { default: makeWASocket, useMultiFileAuthState, fetchLatestBaileysVersion, DisconnectReason, ALL_WA_PATCH_NAMES } = require('@whiskeysockets/baileys')
const PATCH_NAMES = ALL_WA_PATCH_NAMES || ['critical_block', 'critical_unblock_low', 'regular_high', 'regular_low', 'regular']

const REPO = path.dirname(__dirname)
const CUSTOMERS = path.join(REPO, 'workspace', 'data', 'customers.json')
const AUTH_DIR = path.join(__dirname, 'auth')
const QR_PNG = path.join(REPO, 'progress', 'label-bridge-qr.png')
const POLL_MS = 60_000
const MUTATION_GAP_MS = 1500

// DB category -> acceptable WhatsApp label names (first match wins; matching
// is case/space/dash-insensitive). "complains" covers the old phone's spelling.
const LABEL_FOR = {
  'new customer': ['new customer'],
  'important': ['important'],
  'hot leads': ['hot leads', 'hot lead'],
  'followup': ['followup', 'follow up'],
  'junk': ['junk'],
  'complaints': ['complaints', 'complains'],
  'vendor': ['vendor', 'vendors'],
  'ahsan': ['ahsan'],
  'ahmed': ['ahmed'],
  'imran': ['imran'],
  'rafay': ['rafay'],
}

const norm = s => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
const sleep = ms => new Promise(r => setTimeout(r, ms))
const log = (...a) => console.log(new Date().toISOString(), ...a)

const labels = new Map()      // labelId -> name (discovered from app state)
const chatLabels = new Map()  // jid -> Set(labelId) (discovered + our writes)
const warnedMissing = new Set()
let sock = null
let connected = false
let reconciling = false

// App state only replays label data on a FULL sync (first link), so persist
// what we've learned — restarts would otherwise see zero labels and go blind.
const STATE_FILE = path.join(__dirname, 'state.json')
try {
  const s = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'))
  for (const [id, name] of Object.entries(s.labels || {})) labels.set(id, name)
  for (const [jid, ids] of Object.entries(s.chats || {})) chatLabels.set(jid, new Set(ids))
} catch {}
let saveTimer = null
function saveState() {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    const s = {
      labels: Object.fromEntries(labels),
      chats: Object.fromEntries([...chatLabels].map(([j, set]) => [j, [...set]])),
    }
    try { fs.writeFileSync(STATE_FILE, JSON.stringify(s)) } catch {}
  }, 500)
}

// ---- write-back: owner changes a label in the APP -> update the bot DB ----
// Suppressed briefly after every connect: boot resyncs replay ALL historical
// label associations as "add" events, which must not rewrite categories.
const WRITEBACK_QUIET_MS = 90_000
let connectedAt = 0

function categoryForLabel(labelId) {
  const n = norm(labels.get(labelId) || '')
  if (!n) return null
  for (const [cat, cands] of Object.entries(LABEL_FOR)) if (cands.includes(n)) return cat
  return null
}

function writeBack(jid, labelId) {
  if (!connected || Date.now() - connectedAt < WRITEBACK_QUIET_MS) return
  if (!jid.endsWith('@s.whatsapp.net')) return
  const cat = categoryForLabel(labelId)
  if (!cat) return                                   // not one of our lists
  let data
  try { data = JSON.parse(fs.readFileSync(CUSTOMERS, 'utf8')) } catch { return }
  const digits = jid.split('@')[0]
  const cust = (data.customers || []).find(c => String(c.phone || '').replace(/\D/g, '') === digits)
  if (!cust) return                                  // unknown number — never create rows
  if (norm(cust.category) === cat) return            // echo of our own sync — no-op
  log(`app label change: ${digits} -> "${cat}" — updating bot database`)
  execFile('python3', [path.join(REPO, 'workspace', 'db.py'), 'set-category', '--phone', cust.phone, '--category', cat],
    { timeout: 60000 }, (err, _out, serr) => {
    if (err) log('[warn] write-back failed:', String(serr || err.message || '').slice(0, 200))
  })
}

function writeBackRemoval(jid, labelId) {
  // Owner stripped a label without filing the chat elsewhere. If it was the
  // label matching the bot's current category, re-open the chat: adopt any
  // remaining managed label, else fall back to "new customer".
  if (!connected || Date.now() - connectedAt < WRITEBACK_QUIET_MS) return
  if (!jid.endsWith('@s.whatsapp.net')) return
  const cat = categoryForLabel(labelId)
  if (!cat) return
  let data
  try { data = JSON.parse(fs.readFileSync(CUSTOMERS, 'utf8')) } catch { return }
  const digits = jid.split('@')[0]
  const cust = (data.customers || []).find(c => String(c.phone || '').replace(/\D/g, '') === digits)
  if (!cust || norm(cust.category) !== cat) return   // our own cleanup removals land here
  const remaining = [...(chatLabels.get(jid) || new Set())].map(categoryForLabel).filter(Boolean)
  const next = remaining[0] || 'new customer'
  if (norm(next) === cat) return
  log(`app label removed: ${digits} "${cat}" -> "${next}" — updating bot database`)
  execFile('python3', [path.join(REPO, 'workspace', 'db.py'), 'set-category', '--phone', cust.phone, '--category', next],
    { timeout: 60000 }, (err, _out, serr) => {
    if (err) log('[warn] removal write-back failed:', String(serr || err.message || '').slice(0, 200))
  })
}

function labelIdFor(category) {
  const cands = LABEL_FOR[norm(category)]
  if (!cands) return null                    // category we don't manage
  // candidate order = priority (exact name beats loose variants)
  for (const cand of cands) {
    for (const [id, name] of labels) if (norm(name) === cand) return id
  }
  return undefined                           // managed, but label absent in app
}

function managedIds() {
  const all = new Set()
  const names = Object.values(LABEL_FOR).flat()
  for (const [id, name] of labels) if (names.includes(norm(name))) all.add(id)
  return all
}

async function reconcile() {
  if (!connected || reconciling) return
  reconciling = true
  try {
    let data
    try { data = JSON.parse(fs.readFileSync(CUSTOMERS, 'utf8')) } catch { return }
    const managed = managedIds()
    for (const c of data.customers || []) {
      const digits = String(c.phone || '').replace(/\D/g, '')
      if (!digits) continue
      const jid = `${digits}@s.whatsapp.net`
      const want = labelIdFor(c.category)
      if (want === null) continue
      if (want === undefined) {
        const key = norm(c.category)
        if (!warnedMissing.has(key)) {
          warnedMissing.add(key)
          log(`[missing-label] no list named "${c.category}" in the app — create it once in WhatsApp Business (Tools > Lists) and it will sync automatically`)
        }
        continue
      }
      const have = chatLabels.get(jid) || new Set()
      if (!have.has(want)) {
        log(`label + "${labels.get(want)}" -> ${digits}`)
        await sock.addChatLabel(jid, want)
        have.add(want); chatLabels.set(jid, have)
        saveState()
        await sleep(MUTATION_GAP_MS)
      }
      for (const id of [...have]) {
        if (id !== want && managed.has(id)) {
          log(`label - "${labels.get(id)}" -> ${digits}`)
          await sock.removeChatLabel(jid, id)
          have.delete(id)
          saveState()
          await sleep(MUTATION_GAP_MS)
        }
      }
    }
  } catch (e) {
    log('[warn] reconcile error:', e?.message || e)
  } finally {
    reconciling = false
  }
}

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)
  const { version } = await fetchLatestBaileysVersion()
  sock = makeWASocket({
    version,
    auth: state,
    logger: pino({ level: 'warn' }),
    markOnlineOnConnect: false,
    syncFullHistory: false,
    shouldSyncHistoryMessage: () => false,
    browser: ['Label Bridge', 'Chrome', '1.0'],
  })

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', async (u) => {
    if (u.qr) {
      await QR.toFile(QR_PNG, u.qr, { scale: 8 })
      log(`QR ready -> open ${path.relative(REPO, QR_PNG)} and scan: WhatsApp Business > Settings (or ⋮) > Linked devices > Link a device`)
    }
    if (u.connection === 'open') {
      connected = true
      connectedAt = Date.now()
      try { fs.unlinkSync(QR_PNG) } catch {}
      log('connected as linked device — label sync active')
      // Baileys only replays labels on the FIRST full sync; if we boot with an
      // empty label map (fresh restart, no state.json), request a full resync.
      if (labels.size === 0) {
        log('no labels known — requesting full app-state resync')
        try { await sock.resyncAppState(PATCH_NAMES, true) } catch (e) { log('[warn] resync failed:', e?.message || e) }
      }
    }
    if (u.connection === 'close') {
      connected = false
      const code = u.lastDisconnect?.error?.output?.statusCode
      if (code === DisconnectReason.loggedOut) {
        log('logged out from the phone — delete label-bridge/auth/ and re-run to link again')
        process.exit(1)
      }
      log(`connection closed (code ${code}) — reconnecting in 5s`)
      setTimeout(start, 5000)
    }
  })

  sock.ev.on('labels.edit', (l) => {
    if (l.deleted) labels.delete(l.id)
    else labels.set(l.id, l.name)
    log(`label seen: "${l.name || ''}" (id ${l.id}${l.deleted ? ', deleted' : ''})`)
    warnedMissing.clear()   // a new label may resolve an earlier "missing" warning
    saveState()
  })

  sock.ev.on('labels.association', ({ association, type }) => {
    const a = association || {}
    // DIAGNOSTIC: log every association event the instant it arrives, before any
    // filtering — this is how we confirm whether WhatsApp even delivers manual
    // Business-app label changes to this linked device (coexistence question).
    const quiet = Date.now() - connectedAt < WRITEBACK_QUIET_MS
    log(`[app-event] labels.association type=${type} assocType=${a.type} chat=${String(a.chatId||'').split('@')[0]} label="${labels.get(a.labelId)||a.labelId}"${quiet ? ' (within quiet window — writeback suppressed)' : ''}`)
    if (!a || !a.chatId || !a.labelId) return
    if (a.type && a.type !== 'label_jid') return   // ignore message-level labels
    const set = chatLabels.get(a.chatId) || new Set()
    if (type === 'add') set.add(a.labelId)
    else set.delete(a.labelId)
    chatLabels.set(a.chatId, set)
    saveState()
    if (type === 'add') writeBack(a.chatId, a.labelId)
    else writeBackRemoval(a.chatId, a.labelId)
  })
}

setInterval(reconcile, POLL_MS)
start().catch((e) => { log('fatal:', e?.message || e); process.exit(1) })
