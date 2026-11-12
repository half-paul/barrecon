// temporary: verification server pointed at :8001 (delete after the check)
import base from "./vite.config";
export default { ...base, server: { proxy: { "/api": "http://localhost:8001" } } };
