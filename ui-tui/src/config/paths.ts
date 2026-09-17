import { homedir } from 'node:os'
import { join } from 'node:path'

export const getLooprailHome = (env: NodeJS.ProcessEnv = process.env) => env.LOOPRAIL_HOME?.trim() || join(homedir(), '.looprail')

export const getLooprailHomeLabel = (env: NodeJS.ProcessEnv = process.env) => env.LOOPRAIL_HOME?.trim() || '~/.looprail'
