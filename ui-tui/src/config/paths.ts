import { homedir } from 'node:os'
import { join } from 'node:path'

export const getPicoHome = (env: NodeJS.ProcessEnv = process.env) => env.PICO_HOME?.trim() || join(homedir(), '.pico')

export const getPicoHomeLabel = (env: NodeJS.ProcessEnv = process.env) => env.PICO_HOME?.trim() || '~/.pico'
