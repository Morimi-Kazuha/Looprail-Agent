import { homedir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import { getPicoHome, getPicoHomeLabel } from '../config/paths.js'

describe('Pico product paths', () => {
  it('uses the Pico home override for persistent TUI state', () => {
    expect(getPicoHome({ PICO_HOME: '/tmp/pico-home' })).toBe('/tmp/pico-home')
    expect(getPicoHomeLabel({ PICO_HOME: '/tmp/pico-home' })).toBe('/tmp/pico-home')
  })

  it('uses ~/.pico when the override is empty', () => {
    expect(getPicoHome({ PICO_HOME: '  ' })).toBe(join(homedir(), '.pico'))
    expect(getPicoHomeLabel({ PICO_HOME: '  ' })).toBe('~/.pico')
  })
})
