// Pack your own .ehpk. The network whitelist is fixed at pack time and must name your
// relay's address, so every user builds their own (see docs/discord-questions.md).
//
//   npm run pack -- --relay https://my-pc.my-tailnet.ts.net --id com.yourname.approvalgate
//
// Writes app.local.json (gitignored) from app.json, builds, then runs `evenhub pack`.
import { execSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'
import { parseArgs } from 'node:util'

const usage = 'npm run pack -- --relay https://<pc>.<tailnet>.ts.net [--id com.<you>.approvalgate] [--out file.ehpk]'
const { values } = parseArgs({
  options: {
    relay: { type: 'string' },
    id: { type: 'string' },
    out: { type: 'string', default: 'approval-gate.ehpk' },
  },
})

function fail(message) {
  console.error(`${message}\n\nUsage: ${usage}`)
  process.exit(1)
}

if (!values.relay) fail('Missing --relay.')
let relay
try {
  relay = new URL(values.relay)
} catch {
  fail(`Not a URL: ${values.relay}`)
}
if (relay.protocol !== 'https:') fail('The relay URL must be https. `tailscale serve` gives you one.')
if (values.id && !/^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+$/.test(values.id)) fail(`Invalid package id: ${values.id}`)
if (!/^[\w.-]+\.ehpk$/.test(values.out)) fail(`--out must be a plain file name ending in .ehpk: ${values.out}`) // it goes into a shell command

const manifest = JSON.parse(readFileSync('app.json', 'utf8'))
// The phone can't reach the PC's loopback, so the packed build whitelists only your relay.
manifest.permissions = manifest.permissions.map(p => (p.name === 'network' ? { ...p, whitelist: [relay.origin] } : p))
if (values.id) manifest.package_id = values.id
writeFileSync('app.local.json', JSON.stringify(manifest, null, 2) + '\n')
console.log(`app.local.json: ${manifest.package_id}, network whitelist ${relay.origin}`)

execSync('npm run build', { stdio: 'inherit' })
execSync(`npx evenhub pack app.local.json dist -o "${values.out}"`, { stdio: 'inherit' })
