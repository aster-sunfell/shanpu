import { optionsFor, request, summary } from './lib/workload.js';
export const options = optionsFor('kafka-buffering');
export default request;
export const handleSummary = summary;
