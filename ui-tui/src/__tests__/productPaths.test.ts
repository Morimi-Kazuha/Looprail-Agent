import { homedir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import { getLooprailHome, getLooprailHomeLabel } from '../config/paths.js'

describe('Looprail product paths', () => {
  it('uses the Looprail home override for persistent TUI state', () => {
    expect(getLooprailHome({ LOOPRAIL_HOME: '/tmp/looprail-home' })).toBe('/tmp/looprail-home')
    expect(getLooprailHomeLabel({ LOOPRAIL_HOME: '/tmp/looprail-home' })).toBe('/tmp/looprail-home')
  })

  it('uses ~/.looprail when the override is empty', () => {
    expect(getLooprailHome({ LOOPRAIL_HOME: '  ' })).toBe(join(homedir(), '.looprail'))
    expect(getLooprailHomeLabel({ LOOPRAIL_HOME: '  ' })).toBe('~/.looprail')
  })
})
