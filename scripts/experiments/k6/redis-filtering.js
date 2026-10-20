import { optionsFor, request, summary } from './lib/workload.js';
export const options = optionsFor('redis-filtering');
export default request;
export const handleSummary = summary;
