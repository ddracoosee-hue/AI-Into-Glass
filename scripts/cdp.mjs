#!/usr/bin/env node
// Drive headless Chrome over the DevTools protocol, with nothing to install (Node's own WebSocket).
// Long-polling pages never go idle, so screenshots wait a fixed time instead of for "network idle".
//
//   node scripts/cdp.mjs shot <url> <out.png> [width] [height] [waitMs]
//   node scripts/cdp.mjs eval <url> <expression> [waitMs]
//   node scripts/cdp.mjs sample <url> <expression> <out.jsonl> <seconds>   # every 250 ms: {"t": ms, "v": value}
import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync, appendFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const CHROME = process.env.CHROME ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PORT = 9333 + Math.floor(Math.random() * 500)
const sleep = ms => new Promise(r => setTimeout(r, ms))

async function launch(width, height) {
  const profile = join(tmpdir(), `deck-cdp-${PORT}`)
  mkdirSync(profile, { recursive: true })
  const proc = spawn(CHROME, ['--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, `--window-size=${width},${height}`, 'about:blank'], { stdio: 'ignore' })
  for (let i = 0; i < 50; i++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
      const page = targets.find(t => t.type === 'page')
      if (page) return { proc, ws: page.webSocketDebuggerUrl }
    } catch {}
    await sleep(200)
  }
  proc.kill()
  throw new Error('Chrome did not start')
}

function session(url) {
  const ws = new WebSocket(url)
  let id = 0
  const waiting = new Map()
  const events = []
  ws.onmessage = m => {
    const msg = JSON.parse(m.data)
    if (msg.id && waiting.has(msg.id)) {
      const { resolve, reject } = waiting.get(msg.id)
      waiting.delete(msg.id)
      msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result)
    } else if (msg.method) {
      events.push(msg)
    }
  }
  const ready = new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject })
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const n = ++id
    waiting.set(n, { resolve, reject })
    ws.send(JSON.stringify({ id: n, method, params }))
  })
  return { ready, send, events, close: () => ws.close() }
}

async function open(url, width = 1440, height = 1000) {
  const chrome = await launch(width, height)
  const s = session(chrome.ws)
  await s.ready
  await s.send('Page.enable')
  await s.send('Runtime.enable')
  await s.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false })
  await s.send('Page.navigate', { url })
  return { s, done: () => { s.close(); chrome.proc.kill() } }
}

async function evaluate(s, expression) {
  const r = await s.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text)
  return r.result.value
}

const [cmd, url, ...rest] = process.argv.slice(2)
try {
  if (cmd === 'shot') {
    const [out, w = '1440', h = '1000', wait = '3000'] = rest
    const { s, done } = await open(url, Number(w), Number(h))
    await sleep(Number(wait))
    const { data } = await s.send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(out, Buffer.from(data, 'base64'))
    const errors = s.events.filter(e => e.method === 'Runtime.exceptionThrown' || (e.method === 'Runtime.consoleAPICalled' && e.params.type === 'error'))
    const scroll = await evaluate(s, '[document.documentElement.scrollWidth > innerWidth, document.documentElement.scrollHeight > innerHeight]')
    console.log(JSON.stringify({ out, errors: errors.length, scrollbars: scroll }))
    done()
  } else if (cmd === 'eval') {
    const [expression, wait = '3000'] = rest
    const { s, done } = await open(url)
    await sleep(Number(wait))
    console.log(JSON.stringify(await evaluate(s, expression)))
    done()
  } else if (cmd === 'sample') {
    const [expression, out, seconds] = rest
    const { s, done } = await open(url)
    writeFileSync(out, '')
    const end = Date.now() + Number(seconds) * 1000
    while (Date.now() < end) {
      try {
        appendFileSync(out, JSON.stringify({ t: Date.now(), v: await evaluate(s, expression) }) + '\n')
      } catch {}
      await sleep(250)
    }
    done()
  } else {
    console.error('usage: shot <url> <out.png> [w] [h] [waitMs] | eval <url> <expr> [waitMs] | sample <url> <expr> <out.jsonl> <seconds>')
    process.exit(2)
  }
} catch (err) {
  console.error(String(err))
  process.exit(1)
}
process.exit(0)
