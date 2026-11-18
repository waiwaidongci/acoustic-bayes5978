import {runStates,reviewStates,teardownStates} from '../../constants/enums.js';import {esc} from '../../utils/format.js';
export function status(state){return '<span class="status '+esc(state)+'">'+esc(runStates[state]||reviewStates[state]||teardownStates[state]||state)+'</span>'}
