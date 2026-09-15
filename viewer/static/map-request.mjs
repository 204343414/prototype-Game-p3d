export async function getJSON(url, { timeout = 90000, retries = 2, delay = 1000 } = {}) {
  for (let attempt = 0; ; attempt++) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(timeout) });
      const text = await response.text();
      let data;
      try { data = JSON.parse(text); } catch { data = null; }
      if (!response.ok || data?.error || data === null) {
        const detail = data?.report?.errors?.[0];
        const error = new Error((data?.error || `HTTP ${response.status}: 无有效 JSON`) + (detail ? `：${detail}` : ''));
        error.retryable = response.status === 429 || response.status >= 500;
        throw error;
      }
      return data;
    } catch (error) {
      const transient = error.retryable || error.name === 'TimeoutError' || error.name === 'AbortError' || error instanceof TypeError;
      if (!transient || attempt >= retries) throw error;
      await new Promise(resolve => setTimeout(resolve, delay * (attempt + 1)));
    }
  }
}
